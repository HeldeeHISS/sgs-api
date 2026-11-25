"""`OidcPort` com Authlib e PyJWT (spec 004, design § Portas e adaptadores, `authlib_oidc.py`).

- A descoberta vem de OIDC_DISCOVERY_URL, e o `issuer` dela tem de ser igual a OIDC_ISSUER.
- O `AsyncOAuth2Client` da Authlib monta a URL de autorização (PKCE S256, `acr_values`) e troca o
  código (`client_secret_basic`).
- O PyJWT confere o `id_token` (D004-3): algoritmos fixos, chave pelo `kid` no JWKS em cache,
  `iss`, `aud`/`azp`, `exp`/`iat` com 60 s de tolerância e `nonce`.
- Tudo por HTTPS, com a CA de OIDC_CA_FILE (ou as do sistema) e timeout de 10 s.

As recusas levam só o motivo de lista fechada, sem a exceção original encadeada: nenhum código,
token ou segredo vai para exceção ou log.
"""

import hmac
import ssl
import time
import warnings
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self

import httpx
import jwt
from authlib.deprecate import AuthlibDeprecationWarning
from jwt import PyJWK
from jwt import exceptions as jwt_errors

from src.core.config import Settings
from src.core.errors import ServiceUnavailable
from src.modules.identity.domain.oidc import MotivoDeRecusa, OidcIdentity, OidcRejected
from src.modules.identity.domain.users import (
    DISPLAY_NAME_MAX,
    EXTERNAL_ID_MAX,
    USERNAME_MAX,
    normalize_username,
)

with warnings.catch_warnings():
    # A Authlib 1.9 prefere o pacote `httpx2`; a stack usa o `httpx` 0.28 (ADR-0007, emenda 2).
    warnings.simplefilter("ignore", AuthlibDeprecationWarning)
    from authlib.integrations.base_client import OAuthError
    from authlib.integrations.httpx_client import AsyncOAuth2Client

ALGORITHMS = ("RS256", "ES256")  # nunca `none` nem HS*
SCOPE = "openid profile email"
TIMEOUT_SECONDS = 10.0
LEEWAY_SECONDS = 60
JWKS_RELOAD_SECONDS = 60.0
REQUIRED_CLAIMS = ["exp", "iat", "iss", "aud", "sub", "nonce"]
# Claim obrigatória ausente → motivo da recusa; as demais caem em `claims`.
_MISSING_CLAIM_REASON: dict[str, MotivoDeRecusa] = {
    "iss": "iss",
    "aud": "aud",
    "exp": "exp",
    "iat": "exp",
    "nonce": "nonce",
}


@dataclass(frozen=True)
class _Provider:
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str


class _ProviderError(Exception):
    """Provedor fora do ar, resposta inválida ou descoberta de outro emissor."""


class AuthlibOidc:
    """Guarda em memória a descoberta e o JWKS; um `kid` desconhecido recarrega o JWKS uma vez,
    no máximo uma vez por minuto."""

    def __init__(
        self,
        *,
        issuer: str,
        discovery_url: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        groups_claim: str = "groups",
        required_acr: Sequence[str] = (),
        ca_file: Path | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._issuer = issuer
        self._discovery_url = discovery_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._groups_claim = groups_claim
        self._required_acr = tuple(required_acr)
        self._tls = ssl.create_default_context(cafile=ca_file)
        self._tls.minimum_version = ssl.TLSVersion.TLSv1_2
        self._transport = transport
        self._monotonic = monotonic
        self._provider: _Provider | None = None
        self._keys: dict[str, PyJWK] = {}
        self._keys_loaded_at: float | None = None

    @classmethod
    def from_settings(
        cls, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> Self:
        """Com a configuração já validada por `Settings`; só com o OIDC ligado."""
        discovery = settings.oidc_discovery_endpoint
        client_id, secret = settings.oidc_client_id, settings.oidc_client_secret
        redirect_uri = settings.oidc_redirect_uri
        if not (settings.oidc_issuer and discovery and client_id and secret and redirect_uri):
            raise ValueError("OIDC não configurado (OIDC_ISSUER)")
        return cls(
            issuer=settings.oidc_issuer,
            discovery_url=discovery,
            client_id=client_id,
            client_secret=secret.get_secret_value(),
            redirect_uri=redirect_uri,
            groups_claim=settings.oidc_groups_claim,
            required_acr=settings.oidc_required_acr,
            ca_file=settings.oidc_ca_file,
            transport=transport,
        )

    async def authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        try:
            provider = await self._discover()
        except _ProviderError:
            raise ServiceUnavailable("Provedor de identidade indisponível.") from None
        extra = {"acr_values": " ".join(self._required_acr)} if self._required_acr else {}
        async with self._oauth_client() as client:
            url, _ = client.create_authorization_url(
                provider.authorization_endpoint,
                state=state,
                nonce=nonce,
                code_challenge=code_challenge,
                code_challenge_method="S256",
                **extra,
            )
        return str(url)

    async def exchange(self, code: str, *, code_verifier: str, nonce: str) -> OidcIdentity:
        try:
            provider = await self._discover()
            id_token = await self._fetch_id_token(provider, code, code_verifier)
        except _ProviderError:
            raise OidcRejected("troca") from None
        claims = await self._verified_claims(provider, id_token, nonce)
        if self._required_acr and claims.get("acr") not in self._required_acr:
            raise OidcRejected("acr")
        return _identity(claims, self._groups_claim)

    async def _discover(self) -> _Provider:
        if self._provider is None:
            document = await self._get_json(self._discovery_url)
            if document.get("issuer") != self._issuer:
                raise _ProviderError
            names = ("authorization_endpoint", "token_endpoint", "jwks_uri")
            endpoints = [
                url
                for url in (document.get(name) for name in names)
                if isinstance(url, str) and url.startswith("https://")
            ]
            if len(endpoints) != len(names):
                raise _ProviderError
            self._provider = _Provider(*endpoints)
        return self._provider

    async def _fetch_id_token(self, provider: _Provider, code: str, code_verifier: str) -> str:
        async with self._oauth_client() as client:
            try:
                token = await client.fetch_token(
                    provider.token_endpoint,
                    grant_type="authorization_code",
                    code=code,
                    code_verifier=code_verifier,
                )
            except (httpx.HTTPError, OAuthError, ValueError):
                raise _ProviderError from None
        id_token = token.get("id_token")
        if not isinstance(id_token, str):
            raise _ProviderError
        return id_token

    async def _verified_claims(
        self, provider: _Provider, id_token: str, nonce: str
    ) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError:
            raise OidcRejected("assinatura") from None
        kid = header.get("kid")
        # Algoritmo fora da lista é recusado antes de qualquer recarga do JWKS.
        if header.get("alg") not in ALGORITHMS or not isinstance(kid, str):
            raise OidcRejected("assinatura")
        key = await self._key(provider, kid)
        try:
            claims: dict[str, Any] = jwt.decode(
                id_token,
                key,
                algorithms=list(ALGORITHMS),
                audience=self._client_id,
                issuer=self._issuer,
                leeway=LEEWAY_SECONDS,
                options={"require": REQUIRED_CLAIMS},
            )
        except jwt.PyJWTError as error:
            raise OidcRejected(_reason(error)) from None
        audiences = claims["aud"]
        several = isinstance(audiences, list) and len(audiences) > 1
        if (several or "azp" in claims) and claims.get("azp") != self._client_id:
            raise OidcRejected("aud")
        received = claims["nonce"]
        if not isinstance(received, str) or not hmac.compare_digest(
            received.encode(), nonce.encode()
        ):
            raise OidcRejected("nonce")
        return claims

    async def _key(self, provider: _Provider, kid: str) -> PyJWK:
        if kid not in self._keys and self._may_reload_keys():
            # A hora vai antes da busca: chamadas simultâneas não recarregam de novo.
            self._keys_loaded_at = self._monotonic()
            try:
                self._keys = _signing_keys(await self._get_json(provider.jwks_uri))
            except _ProviderError:
                raise OidcRejected("troca") from None
        try:
            return self._keys[kid]
        except KeyError:
            raise OidcRejected("assinatura") from None

    def _may_reload_keys(self) -> bool:
        loaded_at = self._keys_loaded_at
        return loaded_at is None or self._monotonic() - loaded_at >= JWKS_RELOAD_SECONDS

    async def _get_json(self, url: str) -> Mapping[str, Any]:
        try:
            async with httpx.AsyncClient(
                verify=self._tls, timeout=TIMEOUT_SECONDS, transport=self._transport
            ) as client:
                response = await client.get(url)
                response.raise_for_status()
                document = response.json()
        except (httpx.HTTPError, ValueError):
            raise _ProviderError from None
        if not isinstance(document, dict):
            raise _ProviderError
        return document

    def _oauth_client(self) -> Any:
        return AsyncOAuth2Client(
            client_id=self._client_id,
            client_secret=self._client_secret,
            token_endpoint_auth_method="client_secret_basic",  # noqa: S106 - nome do método
            scope=SCOPE,
            redirect_uri=self._redirect_uri,
            verify=self._tls,
            timeout=TIMEOUT_SECONDS,
            transport=self._transport,
        )


def _signing_keys(jwks: Mapping[str, Any]) -> dict[str, PyJWK]:
    """Só chaves de assinatura com `kid`, nos algoritmos aceitos. As demais (de cifra,
    simétricas, de outras curvas) ficam de fora."""
    entries = jwks.get("keys")
    if not isinstance(entries, list):
        raise _ProviderError
    keys: dict[str, PyJWK] = {}
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("use", "sig") != "sig":
            continue
        kid = entry.get("kid")
        try:
            key = PyJWK(entry)
        except (jwt.PyJWTError, KeyError, TypeError, ValueError):
            continue
        if isinstance(kid, str) and key.algorithm_name in ALGORITHMS:
            keys[kid] = key
    return keys


def _reason(error: jwt.PyJWTError) -> MotivoDeRecusa:
    match error:
        case jwt_errors.InvalidIssuerError():
            return "iss"
        case jwt_errors.InvalidAudienceError():
            return "aud"
        case jwt_errors.ExpiredSignatureError() | jwt_errors.ImmatureSignatureError():
            return "exp"
        case jwt_errors.MissingRequiredClaimError(claim=claim):
            return _MISSING_CLAIM_REASON.get(claim, "claims")
        case (
            jwt_errors.InvalidIssuedAtError()
            | jwt_errors.InvalidSubjectError()
            | jwt_errors.InvalidJTIError()
        ):
            return "claims"
        case _:
            return "assinatura"


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _identity(claims: Mapping[str, Any], groups_claim: str) -> OidcIdentity:
    """Claims já conferidas → `OidcIdentity`. Motivo `claims`: `sub` ou `preferred_username`
    ausente ou longo demais, ou claim de grupos que não é lista de textos."""
    sub = claims["sub"]
    username = normalize_username(_text(claims.get("preferred_username")))
    groups = claims.get(groups_claim, [])
    if (
        not isinstance(sub, str)
        or not 0 < len(sub) <= EXTERNAL_ID_MAX
        or not 0 < len(username) <= USERNAME_MAX
        or not isinstance(groups, list)
        or not all(isinstance(group, str) for group in groups)
    ):
        raise OidcRejected("claims")
    # D004-12: um e-mail não verificado poderia tomar o lugar do de outra conta.
    email = _text(claims.get("email")).lower() if claims.get("email_verified") is True else ""
    acr = claims.get("acr")
    return OidcIdentity(
        sub=sub,
        username=username,
        email=email or None,
        display_name=_text(claims.get("name"))[:DISPLAY_NAME_MAX] or username,
        groups=tuple(groups),
        acr=acr if isinstance(acr, str) else None,
    )
