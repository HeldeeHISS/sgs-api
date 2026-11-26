"""Configuração por ambiente (spec 001, R2), com as chaves JWT da spec 002 (T4)."""

import base64
import json
import re
from collections.abc import Callable
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    load_pem_private_key,
)

from src.core.config import AppEnv, Settings, SettingsError, load_settings
from src.tools import jwt_keys
from tests.factories import make_jwt_private_keys, make_pem_privado

type DefinirAmbiente = Callable[[dict[str, str]], None]

VARIAVEIS_DEV = {
    "APP_ENV": "dev",
    "DATABASE_URL": "postgresql+asyncpg://sgs_api:sgs_api@db:5432/sgs",
    "DATABASE_MIGRATION_URL": "postgresql+asyncpg://sgs:sgs@db:5432/sgs",
    "DATABASE_SSL_MODE": "disable",
    "JWT_PRIVATE_KEYS": make_jwt_private_keys("dev"),
    "CRYPTO_KEYS": json.dumps({"1": base64.b64encode(bytes(range(32))).decode()}),
    "CRYPTO_ACTIVE_KEY": "1",
}

VARIAVEIS_PROD = {
    "APP_ENV": "prod",
    "DATABASE_URL": "postgresql+asyncpg://sgs_api:Kx9-segredo-de-verdade@db.interno:5432/sgs",
    "DATABASE_SSL_MODE": "verify-full",
    "JWT_PRIVATE_KEYS": make_jwt_private_keys("2026-11"),
    "CRYPTO_KEYS": json.dumps({"2": base64.b64encode(bytes(range(1, 33))).decode()}),
    "CRYPTO_ACTIVE_KEY": "2",
}


@pytest.fixture
def ambiente(monkeypatch: pytest.MonkeyPatch) -> DefinirAmbiente:
    """Apaga as variáveis conhecidas e devolve uma função que define as do teste."""
    for campo in Settings.model_fields:
        monkeypatch.delenv(campo.upper(), raising=False)

    def definir(variaveis: dict[str, str]) -> None:
        for nome, valor in variaveis.items():
            monkeypatch.setenv(nome, valor)

    return definir


def test_le_a_configuracao_das_variaveis_de_ambiente(ambiente: DefinirAmbiente) -> None:
    """001/R2.1"""
    ambiente(
        {
            **VARIAVEIS_DEV,
            "CORS_ORIGINS": "https://localhost:3000, https://sgs.localhost",
            "LOG_LEVEL": "DEBUG",
            "DATABASE_POOL_SIZE": "3",
        }
    )

    settings = load_settings()

    assert settings.app_env is AppEnv.DEV
    assert settings.cors_origins == ("https://localhost:3000", "https://sgs.localhost")
    assert settings.log_level == "DEBUG"
    assert settings.database_pool_size == 3
    assert settings.database_url.get_secret_value() == VARIAVEIS_DEV["DATABASE_URL"]


@pytest.mark.parametrize("app_env", ["dev", "test", "prod"])
def test_aceita_os_tres_ambientes(ambiente: DefinirAmbiente, app_env: str) -> None:
    """001/R2.1"""
    variaveis = VARIAVEIS_PROD if app_env == "prod" else VARIAVEIS_DEV
    ambiente({**variaveis, "APP_ENV": app_env})

    assert load_settings().app_env == app_env


@pytest.mark.parametrize(
    "variavel", ["APP_ENV", "DATABASE_URL", "DATABASE_MIGRATION_URL", "JWT_PRIVATE_KEYS"]
)
def test_recusa_iniciar_sem_variavel_obrigatoria_e_nomeia_a_variavel(
    ambiente: DefinirAmbiente, variavel: str
) -> None:
    """001/R2.2 (a URL de migração é obrigatória em dev por causa de 001/R1.2; as chaves JWT,
    pela 002/R3.1)"""
    ambiente({nome: valor for nome, valor in VARIAVEIS_DEV.items() if nome != variavel})

    with pytest.raises(SettingsError, match=variavel):
        load_settings()


@pytest.mark.parametrize(
    ("variavel", "valor"),
    [
        ("APP_ENV", "homologacao"),
        ("DEBUG", "talvez"),
        ("DATABASE_URL", "mysql://sgs_api:sgs_api@db:3306/sgs"),
        ("DATABASE_URL", "postgresql+asyncpg://sgs_api:sgs_api@db:5432/sgs?ssl=disable"),
        ("DATABASE_MIGRATION_URL", "postgresql+asyncpg://sgs:sgs@db:5432"),
        ("DATABASE_SSL_MODE", "prefer"),
        ("DATABASE_POOL_SIZE", "0"),
        ("CORS_ORIGINS", "*"),
        ("CORS_ORIGINS", "https://localhost:3000/caminho"),
    ],
)
def test_recusa_iniciar_com_variavel_invalida_e_nomeia_a_variavel(
    ambiente: DefinirAmbiente, variavel: str, valor: str
) -> None:
    """001/R2.2"""
    ambiente({**VARIAVEIS_DEV, variavel: valor})

    with pytest.raises(SettingsError, match=variavel):
        load_settings()


def _pem_publico() -> str:
    chave = load_pem_private_key(make_pem_privado().encode(), password=None)
    return chave.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode()


CHAVES_JWT_INVALIDAS = {
    "fora_de_json": "kid=1;pem=nao-e-json",
    "lista_vazia": "[]",
    "objeto_em_vez_de_lista": json.dumps({"kid": "1", "pem": make_pem_privado()}),
    "sem_pem": json.dumps([{"kid": "1"}]),
    "sem_kid": json.dumps([{"pem": make_pem_privado()}]),
    "kid_vazio": json.dumps([{"kid": "", "pem": make_pem_privado()}]),
    "kid_com_espaco": json.dumps([{"kid": "chave 1", "pem": make_pem_privado()}]),
    "pem_ilegivel": json.dumps([{"kid": "1", "pem": "nao-e-um-pem"}]),
    "chave_publica": json.dumps([{"kid": "1", "pem": _pem_publico()}]),
    "curva_p384": json.dumps([{"kid": "1", "pem": make_pem_privado(ec.SECP384R1())}]),
    "kid_repetido": json.dumps(
        [{"kid": "1", "pem": make_pem_privado()}, {"kid": "1", "pem": make_pem_privado()}]
    ),
}


@pytest.mark.parametrize("valor", CHAVES_JWT_INVALIDAS.values(), ids=CHAVES_JWT_INVALIDAS.keys())
def test_recusa_iniciar_com_chaves_jwt_invalidas_e_nomeia_a_variavel(
    ambiente: DefinirAmbiente, valor: str
) -> None:
    """001/R2.2; 002/R3.1 (só chave privada EC P-256 assina em ES256, com `kid` único)"""
    ambiente({**VARIAVEIS_DEV, "JWT_PRIVATE_KEYS": valor})

    with pytest.raises(SettingsError, match="JWT_PRIVATE_KEYS") as erro:
        load_settings()

    assert "PRIVATE KEY" not in str(erro.value)


def test_le_as_chaves_jwt_na_ordem_e_a_primeira_assina(ambiente: DefinirAmbiente) -> None:
    """002/R3.1 (design, § Chaves JWT: a chave ativa assina, as demais seguem no JWKS)"""
    ambiente({**VARIAVEIS_DEV, "JWT_PRIVATE_KEYS": make_jwt_private_keys("2026-11", "2026-05")})

    settings = load_settings()

    assert [chave.kid for chave in settings.jwt_signing_keys] == ["2026-11", "2026-05"]


def _sem_jwt_private_keys(variaveis: dict[str, str]) -> dict[str, str]:
    return {nome: valor for nome, valor in variaveis.items() if nome != "JWT_PRIVATE_KEYS"}


def test_le_as_chaves_jwt_de_arquivo_montado(ambiente: DefinirAmbiente, tmp_path: Path) -> None:
    """001/R2.1; design da 002, § Chaves JWT (variável de ambiente ou arquivo montado)"""
    arquivo = tmp_path / "jwt-keys.json"
    arquivo.write_text(make_jwt_private_keys("2026-11"), encoding="utf-8")
    ambiente({**_sem_jwt_private_keys(VARIAVEIS_DEV), "JWT_PRIVATE_KEYS_FILE": str(arquivo)})

    settings = load_settings()

    assert [chave.kid for chave in settings.jwt_signing_keys] == ["2026-11"]


@pytest.mark.parametrize("conteudo", [None, "[]", json.dumps([{"kid": "1", "pem": "nao-e-pem"}])])
def test_recusa_iniciar_com_arquivo_de_chaves_jwt_ausente_ou_invalido(
    ambiente: DefinirAmbiente, tmp_path: Path, conteudo: str | None
) -> None:
    """001/R2.2"""
    arquivo = tmp_path / "jwt-keys.json"
    if conteudo is not None:
        arquivo.write_text(conteudo, encoding="utf-8")
    ambiente({**_sem_jwt_private_keys(VARIAVEIS_DEV), "JWT_PRIVATE_KEYS_FILE": str(arquivo)})

    with pytest.raises(SettingsError, match="JWT_PRIVATE_KEYS_FILE"):
        load_settings()


def test_recusa_iniciar_com_chaves_jwt_na_variavel_e_no_arquivo(
    ambiente: DefinirAmbiente, tmp_path: Path
) -> None:
    """001/R2.2 (duas fontes para a mesma chave deixariam dúvida sobre qual assina)"""
    arquivo = tmp_path / "jwt-keys.json"
    arquivo.write_text(make_jwt_private_keys("arquivo"), encoding="utf-8")
    ambiente({**VARIAVEIS_DEV, "JWT_PRIVATE_KEYS_FILE": str(arquivo)})

    with pytest.raises(SettingsError, match="JWT_PRIVATE_KEYS_FILE"):
        load_settings()


def test_chave_gerada_pela_ferramenta_e_aceita_em_prod(
    ambiente: DefinirAmbiente, capsys: pytest.CaptureFixture[str]
) -> None:
    """002/R3.1 (`python -m src.tools.jwt_keys` gera JWT_PRIVATE_KEYS no formato esperado)"""
    jwt_keys.main(["--kid", "2026-11"])
    ambiente({**VARIAVEIS_PROD, "JWT_PRIVATE_KEYS": capsys.readouterr().out})

    assert [chave.kid for chave in load_settings().jwt_signing_keys] == ["2026-11"]


def test_ferramenta_grava_arquivo_de_chaves_jwt_para_montar(
    ambiente: DefinirAmbiente, tmp_path: Path
) -> None:
    """002/R3.1 (arquivo para JWT_PRIVATE_KEYS_FILE, sem depender da codificação do shell)"""
    arquivo = tmp_path / "jwt-keys.json"
    jwt_keys.main(["--kid", "dev", "--output", str(arquivo)])
    ambiente({**_sem_jwt_private_keys(VARIAVEIS_DEV), "JWT_PRIVATE_KEYS_FILE": str(arquivo)})

    assert [chave.kid for chave in load_settings().jwt_signing_keys] == ["dev"]


def test_prod_com_configuracao_segura_inicia(ambiente: DefinirAmbiente) -> None:
    """001/R2.3"""
    ambiente(VARIAVEIS_PROD)

    assert load_settings().app_env is AppEnv.PROD


@pytest.mark.parametrize(
    ("variavel", "valor"),
    [
        ("DEBUG", "true"),
        ("DATABASE_SSL_MODE", "disable"),
        ("DATABASE_SSL_MODE", "require"),
        ("DATABASE_URL", "postgresql+asyncpg://sgs_api:sgs_api@db.interno:5432/sgs"),
        ("DATABASE_URL", "postgresql+asyncpg://app:troque-por-uma-senha@db.interno:5432/sgs"),
        ("DATABASE_MIGRATION_URL", "postgresql+asyncpg://sgs:sgs@db.interno:5432/sgs"),
    ],
)
def test_prod_recusa_iniciar_com_debug_sem_tls_ou_segredo_de_exemplo(
    ambiente: DefinirAmbiente, variavel: str, valor: str
) -> None:
    """001/R2.3"""
    ambiente({**VARIAVEIS_PROD, variavel: valor})

    with pytest.raises(SettingsError, match=variavel):
        load_settings()


@pytest.mark.parametrize(
    "kids", [("exemplo",), ("2026-11", "troque-esta-chave")], ids=["ativa", "antiga"]
)
def test_prod_recusa_iniciar_com_chave_jwt_de_exemplo(
    ambiente: DefinirAmbiente, kids: tuple[str, ...]
) -> None:
    """001/R2.3 (a chave de exemplo se reconhece pelo `kid`, como as senhas de exemplo)"""
    ambiente({**VARIAVEIS_PROD, "JWT_PRIVATE_KEYS": make_jwt_private_keys(*kids)})

    with pytest.raises(SettingsError, match="JWT_PRIVATE_KEYS: chave de exemplo"):
        load_settings()


def test_prod_recusa_chave_jwt_de_exemplo_vinda_de_arquivo(
    ambiente: DefinirAmbiente, tmp_path: Path
) -> None:
    """001/R2.3"""
    arquivo = tmp_path / "jwt-keys.json"
    arquivo.write_text(make_jwt_private_keys("chave-de-exemplo"), encoding="utf-8")
    ambiente({**_sem_jwt_private_keys(VARIAVEIS_PROD), "JWT_PRIVATE_KEYS_FILE": str(arquivo)})

    with pytest.raises(SettingsError, match="JWT_PRIVATE_KEYS_FILE"):
        load_settings()


def test_chave_jwt_de_exemplo_vale_fora_de_prod(ambiente: DefinirAmbiente) -> None:
    """001/R2.3 (a recusa de segredo de exemplo vale só em prod)"""
    ambiente({**VARIAVEIS_DEV, "JWT_PRIVATE_KEYS": make_jwt_private_keys("exemplo")})

    assert [chave.kid for chave in load_settings().jwt_signing_keys] == ["exemplo"]


def test_erro_de_configuracao_nao_expoe_o_valor_recebido(ambiente: DefinirAmbiente) -> None:
    """001/R2.2"""
    senha = "troque-esta-senha-secreta"
    ambiente({**VARIAVEIS_PROD, "DATABASE_URL": f"postgresql+asyncpg://app:{senha}@db/sgs"})

    with pytest.raises(SettingsError) as erro:
        load_settings()

    assert senha not in str(erro.value)
    assert erro.value.__cause__ is None
    assert erro.value.__suppress_context__


CHAVE_ZERO = base64.b64encode(bytes(32)).decode()


@pytest.mark.parametrize(
    ("variaveis", "trecho"),
    [
        ({"CRYPTO_KEYS": None}, "CRYPTO_KEYS: variável obrigatória"),
        ({"CRYPTO_KEYS": "não é json"}, "CRYPTO_KEYS: use um objeto JSON"),
        ({"CRYPTO_KEYS": json.dumps({"1": "@@@"})}, "CRYPTO_KEYS: versão inteira"),
        (
            {"CRYPTO_KEYS": json.dumps({"1": base64.b64encode(b"curta").decode()})},
            "precisa de 32 bytes",
        ),
        ({"CRYPTO_ACTIVE_KEY": "9"}, "CRYPTO_ACTIVE_KEY: não há chave"),
        ({"CRYPTO_ACTIVE_KEY": None}, "CRYPTO_ACTIVE_KEY: variável obrigatória"),
    ],
)
def test_chaves_de_cifra_invalidas_recusam_a_subida(
    ambiente: DefinirAmbiente, variaveis: dict[str, str | None], trecho: str
) -> None:
    """002/T5; 001/R2.2: chave ausente, fora de base64, sem 32 bytes ou sem a versão ativa."""
    base = {k: v for k, v in (VARIAVEIS_DEV | variaveis).items() if v is not None}
    ambiente(base)

    with pytest.raises(SettingsError, match=trecho):
        load_settings()


def test_chave_de_cifra_de_exemplo_recusada_em_prod(ambiente: DefinirAmbiente) -> None:
    """002/T5; 001/R2.3."""
    ambiente(VARIAVEIS_PROD | {"CRYPTO_KEYS": json.dumps({"2": CHAVE_ZERO})})

    with pytest.raises(SettingsError, match="chave de exemplo"):
        load_settings()


def test_mfa_so_pode_ser_desligado_em_dev(ambiente: DefinirAmbiente) -> None:
    """002, design § Decisões."""
    ambiente(VARIAVEIS_DEV | {"MFA_OBRIGATORIO": "false"})
    assert load_settings().mfa_obrigatorio is False

    ambiente(VARIAVEIS_PROD | {"MFA_OBRIGATORIO": "false"})
    with pytest.raises(SettingsError, match="MFA_OBRIGATORIO"):
        load_settings()


# OIDC (spec 004, design § Configuração): com OIDC_ISSUER, as demais variáveis passam a valer.
VARIAVEIS_OIDC = {
    "OIDC_ISSUER": "https://keycloak.localhost/realms/sgs",
    "OIDC_CLIENT_ID": "sgs-api",
    "OIDC_CLIENT_SECRET": "sgs-lab-exemplo-oidc",
    "OIDC_REDIRECT_URI": "https://localhost/api/v1/auth/oidc/callback",
    "FRONTEND_URL": "https://localhost/",
    "CORS_ORIGINS": "https://localhost,https://localhost:3000",
}


def test_oidc_fica_desligado_sem_oidc_issuer(ambiente: DefinirAmbiente) -> None:
    """004/R3.2 (design § Configuração: sem OIDC_ISSUER, o OIDC fica desligado e as demais
    variáveis OIDC não são conferidas)"""
    ambiente({**VARIAVEIS_DEV, "OIDC_ISSUER": "", "FRONTEND_URL": "http://fora-do-cors"})

    settings = load_settings()

    assert settings.oidc_issuer is None
    assert settings.oidc_discovery_endpoint is None


def test_oidc_completo_e_aceito_com_descoberta_padrao(ambiente: DefinirAmbiente) -> None:
    """004/R3.2, R3.6, R3.8 (a descoberta padrão é a do emissor; os `acr` vêm em lista)"""
    ambiente({**VARIAVEIS_DEV, **VARIAVEIS_OIDC, "OIDC_REQUIRED_ACR": "mfa, gold"})

    settings = load_settings()

    assert settings.oidc_issuer == "https://keycloak.localhost/realms/sgs"
    assert settings.oidc_discovery_endpoint == (
        "https://keycloak.localhost/realms/sgs/.well-known/openid-configuration"
    )
    assert settings.oidc_redirect_uri == "https://localhost/api/v1/auth/oidc/callback"
    assert settings.frontend_url == "https://localhost/"
    assert settings.oidc_groups_claim == "groups"
    assert settings.oidc_required_acr == ("mfa", "gold")


def test_oidc_usa_a_url_de_descoberta_configurada(ambiente: DefinirAmbiente) -> None:
    """004/R3.2 (a descoberta pode ir pela rede interna; o `iss` continua o público)"""
    interna = "https://keycloak:8443/realms/sgs/.well-known/openid-configuration"
    ambiente({**VARIAVEIS_DEV, **VARIAVEIS_OIDC, "OIDC_DISCOVERY_URL": interna})

    assert load_settings().oidc_discovery_endpoint == interna


@pytest.mark.parametrize(
    "variavel", ["OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET", "OIDC_REDIRECT_URI", "FRONTEND_URL"]
)
def test_oidc_incompleto_recusa_a_subida_e_nomeia_a_variavel(
    ambiente: DefinirAmbiente, variavel: str
) -> None:
    """004/R3.8 (retorno e destino só da configuração, obrigatórios com OIDC_ISSUER)"""
    ambiente({**VARIAVEIS_DEV, **VARIAVEIS_OIDC, variavel: ""})

    with pytest.raises(SettingsError, match=f"{variavel}: obrigatória com OIDC_ISSUER"):
        load_settings()


@pytest.mark.parametrize(
    ("variavel", "valor"),
    [
        ("OIDC_ISSUER", "http://keycloak.localhost/realms/sgs"),
        ("OIDC_DISCOVERY_URL", "http://keycloak:8080/realms/sgs/.well-known/openid-configuration"),
        ("OIDC_REDIRECT_URI", "http://localhost/api/v1/auth/oidc/callback"),
        ("OIDC_REDIRECT_URI", "https://localhost/api/v1/auth/oidc/outro"),
        ("OIDC_REDIRECT_URI", "https://localhost/api/v1/auth/oidc/callback/"),
        ("FRONTEND_URL", "http://localhost/"),
        ("FRONTEND_URL", "https://localhost"),
        ("FRONTEND_URL", "https://localhost/?volta=https://atacante.example"),
        ("FRONTEND_URL", "https://localhost/#inicio"),
        ("OIDC_CA_FILE", "/nao/existe/ca.crt"),
    ],
)
def test_oidc_recusa_endereco_sem_https_ou_fora_do_formato(
    ambiente: DefinirAmbiente, variavel: str, valor: str
) -> None:
    """004/R3.2, R3.8 (HTTPS até o provedor; callback no caminho fixo; destino sem query)"""
    ambiente({**VARIAVEIS_DEV, **VARIAVEIS_OIDC, variavel: valor})

    with pytest.raises(SettingsError, match=variavel):
        load_settings()


def test_oidc_recusa_frontend_url_fora_de_cors_origins(ambiente: DefinirAmbiente) -> None:
    """004/R3.8, R3.9 (o front renova a sessão da origem de FRONTEND_URL, que o anti-CSRF do
    refresh confere em CORS_ORIGINS)"""
    ambiente({**VARIAVEIS_DEV, **VARIAVEIS_OIDC, "FRONTEND_URL": "https://sgs.localhost/"})

    with pytest.raises(SettingsError, match=r"FRONTEND_URL: a origem https://sgs\.localhost "):
        load_settings()


@pytest.mark.parametrize("segredo", ["sgs-lab-exemplo-oidc", "changeme", "admin"])
def test_prod_recusa_segredo_oidc_de_exemplo(ambiente: DefinirAmbiente, segredo: str) -> None:
    """004/R5.4 (a mesma regra das senhas de exemplo da 001/R2.3)"""
    ambiente({**VARIAVEIS_PROD, **VARIAVEIS_OIDC, "OIDC_CLIENT_SECRET": segredo})

    with pytest.raises(SettingsError, match="OIDC_CLIENT_SECRET: segredo de exemplo") as erro:
        load_settings()

    assert segredo not in str(erro.value)


def test_prod_aceita_segredo_oidc_de_verdade(ambiente: DefinirAmbiente) -> None:
    """004/R5.4"""
    ambiente({**VARIAVEIS_PROD, **VARIAVEIS_OIDC, "OIDC_CLIENT_SECRET": "Qm7-segredo-real-9x"})

    assert load_settings().oidc_client_secret is not None


def test_segredo_oidc_de_exemplo_vale_fora_de_prod(ambiente: DefinirAmbiente) -> None:
    """004/R5.4 (o laboratório usa o segredo de exemplo; a recusa vale só em prod)"""
    ambiente({**VARIAVEIS_DEV, **VARIAVEIS_OIDC})

    segredo = load_settings().oidc_client_secret

    assert segredo is not None
    assert segredo.get_secret_value() == "sgs-lab-exemplo-oidc"


@pytest.mark.parametrize(
    ("variavel", "valor"),
    [
        ("OIDC_ISSUER", "https://keycloak.localhost/realms/sgs?tenant=1"),
        ("OIDC_ISSUER", "https://keycloak.localhost/realms/sgs#inicio"),
        ("OIDC_ISSUER", "https://keycloak.localhost/realms/sgs?"),
        (
            "OIDC_REDIRECT_URI",
            "https://localhost/api/v1/auth/oidc/callback?volta=https://x.example",
        ),
        ("OIDC_REDIRECT_URI", "https://localhost/api/v1/auth/oidc/callback#inicio"),
    ],
)
def test_oidc_recusa_query_ou_fragmento_no_emissor_e_no_retorno(
    ambiente: DefinirAmbiente, variavel: str, valor: str
) -> None:
    """004/R3.2, R3.8 (o `iss` é comparado byte a byte e dá a descoberta padrão; o retorno é
    fixo, registrado no provedor)"""
    ambiente({**VARIAVEIS_DEV, **VARIAVEIS_OIDC, variavel: valor})

    with pytest.raises(SettingsError, match=f"{variavel}: não use query nem fragmento"):
        load_settings()


def test_oidc_ca_file_so_e_conferido_com_o_oidc_ligado(
    ambiente: DefinirAmbiente, tmp_path: Path
) -> None:
    """004/R3.2 (design § Configuração: sem OIDC_ISSUER, as demais variáveis OIDC não são
    conferidas; com ela, OIDC_CA_FILE tem de ser um arquivo existente)"""
    ambiente({**VARIAVEIS_DEV, "OIDC_CA_FILE": "/nao/existe/ca.crt"})
    assert load_settings().oidc_issuer is None

    ambiente({**VARIAVEIS_OIDC, "OIDC_CA_FILE": str(tmp_path)})  # pasta, não arquivo
    with pytest.raises(SettingsError, match="OIDC_CA_FILE: arquivo não encontrado"):
        load_settings()

    ca = tmp_path / "ca.crt"
    ca.write_text("certificado de teste\n", encoding="utf-8")
    ambiente({"OIDC_CA_FILE": str(ca)})
    assert load_settings().oidc_ca_file == ca


@pytest.mark.parametrize("valor", ["", "   "], ids=["vazia", "so_espacos"])
def test_oidc_recusa_claim_de_grupos_vazia(ambiente: DefinirAmbiente, valor: str) -> None:
    """004/R3.3 (os grupos vêm da claim OIDC_GROUPS_CLAIM; vazia, todos sumiriam calados)"""
    ambiente({**VARIAVEIS_DEV, "OIDC_GROUPS_CLAIM": valor})
    assert load_settings().oidc_issuer is None  # com o OIDC desligado, não é conferida

    ambiente(VARIAVEIS_OIDC)
    with pytest.raises(SettingsError, match="OIDC_GROUPS_CLAIM: informe o nome da claim"):
        load_settings()


@pytest.mark.parametrize(
    "frontend_url",
    [
        "https://LOCALHOST/",
        "HTTPS://localhost/",
        "https://localhost:443/",
        "https://Localhost:443/",
    ],
)
def test_oidc_compara_a_origem_de_frontend_url_como_o_navegador_a_manda(
    ambiente: DefinirAmbiente, frontend_url: str
) -> None:
    """004/R3.8, R3.9 (o navegador manda a origem com esquema e host em minúsculas e sem a
    porta 443; é ela que o refresh procura, texto a texto, em CORS_ORIGINS)"""
    ambiente({**VARIAVEIS_DEV, **VARIAVEIS_OIDC, "FRONTEND_URL": frontend_url})

    assert load_settings().frontend_url == frontend_url


def test_oidc_recusa_cors_origins_que_o_navegador_nunca_manda(ambiente: DefinirAmbiente) -> None:
    """004/R3.8, R3.9 (com a porta 443 explícita nos dois lados, o navegador manda
    `https://localhost`, que não está em CORS_ORIGINS: o refresh falharia depois do login)"""
    ambiente(
        {
            **VARIAVEIS_DEV,
            **VARIAVEIS_OIDC,
            "OIDC_REDIRECT_URI": "https://localhost:443/api/v1/auth/oidc/callback",
            "FRONTEND_URL": "https://localhost:443/",
            "CORS_ORIGINS": "https://localhost:443",
        }
    )

    with pytest.raises(
        SettingsError,
        match=r"FRONTEND_URL: a origem https://localhost precisa estar em CORS_ORIGINS",
    ):
        load_settings()


@pytest.mark.parametrize(
    ("retorno", "origem"),
    [
        ("https://api.sgs.example/api/v1/auth/oidc/callback", "https://sgs.example"),
        ("https://sgs.example:8443/api/v1/auth/oidc/callback", "https://sgs.example"),
    ],
    ids=["outro_host", "outra_porta"],
)
def test_oidc_recusa_retorno_fora_da_origem_de_frontend_url(
    ambiente: DefinirAmbiente, retorno: str, origem: str
) -> None:
    """004/R3.8"""
    ambiente(
        {
            **VARIAVEIS_DEV,
            **VARIAVEIS_OIDC,
            "OIDC_REDIRECT_URI": retorno,
            "FRONTEND_URL": "https://sgs.example/",
            "CORS_ORIGINS": "https://sgs.example",
        }
    )

    with pytest.raises(
        SettingsError,
        match=rf"OIDC_REDIRECT_URI: use a mesma origem de FRONTEND_URL \({re.escape(origem)}\)",
    ):
        load_settings()


@pytest.mark.parametrize(
    ("retorno", "frontend_url"),
    [
        ("https://SGS.Example:443/api/v1/auth/oidc/callback", "https://sgs.example/"),
        ("https://sgs.example/api/v1/auth/oidc/callback", "HTTPS://sgs.EXAMPLE:443/"),
        ("https://sgs.example:8443/api/v1/auth/oidc/callback", "https://SGS.example:8443/"),
    ],
    ids=["retorno_com_maiusculas_e_443", "frontend_com_maiusculas_e_443", "porta_8443"],
)
def test_oidc_aceita_retorno_na_mesma_origem_escrita_de_outro_jeito(
    ambiente: DefinirAmbiente, retorno: str, frontend_url: str
) -> None:
    """004/R3.8"""
    origem = "https://sgs.example:8443" if "8443" in retorno else "https://sgs.example"
    ambiente(
        {
            **VARIAVEIS_DEV,
            **VARIAVEIS_OIDC,
            "OIDC_REDIRECT_URI": retorno,
            "FRONTEND_URL": frontend_url,
            "CORS_ORIGINS": origem,
        }
    )

    assert load_settings().oidc_redirect_uri == retorno


# LDAP (spec 004, design § Configuração): com LDAP_URL, as demais variáveis passam a valer.
VARIAVEIS_LDAP = {
    "LDAP_URL": "ldaps://ldap:636",
    "LDAP_BIND_DN": "cn=sgs-sync,ou=servicos,dc=recife,dc=pe,dc=gov,dc=br",
    "LDAP_BIND_PASSWORD": "sgs-lab-exemplo-bind",
    "LDAP_USER_BASE": "ou=pessoas,dc=recife,dc=pe,dc=gov,dc=br",
    "LDAP_USER_FILTER": "(objectClass=inetOrgPerson)",
    "LDAP_GROUP_BASE": "ou=grupos,dc=recife,dc=pe,dc=gov,dc=br",
    "LDAP_GROUP_FILTER": "(objectClass=groupOfNames)",
}


@pytest.fixture
def ldap(tmp_path: Path) -> dict[str, str]:
    """Variáveis LDAP do laboratório, com uma CA que existe."""
    ca = tmp_path / "ca.crt"
    ca.write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")
    return {**VARIAVEIS_LDAP, "LDAP_CA_FILE": str(ca)}


def test_ldap_fica_desligado_sem_ldap_url(ambiente: DefinirAmbiente) -> None:
    """004/R1.2 (design § Configuração: sem LDAP_URL, o LDAP fica desligado e as demais variáveis
    LDAP não são conferidas)"""
    ambiente({**VARIAVEIS_DEV, "LDAP_URL": "", "LDAP_CA_FILE": "", "LDAP_ATTR_LOGIN": "x)("})

    assert load_settings().ldap_url is None


def test_ldap_completo_e_aceito_com_os_atributos_padrao(
    ambiente: DefinirAmbiente, ldap: dict[str, str]
) -> None:
    """004/R1.2, R1.3, R1.4"""
    ambiente({**VARIAVEIS_DEV, **ldap})

    settings = load_settings()

    assert settings.ldap_url == "ldaps://ldap:636"
    assert settings.ldap_start_tls is False
    assert settings.ldap_bind_password is not None
    assert (settings.ldap_attr_login, settings.ldap_attr_email) == ("uid", "mail")
    assert (settings.ldap_attr_name, settings.ldap_attr_id) == ("cn", "entryUUID")
    assert settings.ldap_attr_member == "member"
    assert settings.ldap_page_size == 500


def test_ldap_aceita_ldap_com_starttls(ambiente: DefinirAmbiente, ldap: dict[str, str]) -> None:
    """004/R1.2"""
    ambiente({**VARIAVEIS_DEV, **ldap, "LDAP_URL": "ldap://ldap:389", "LDAP_START_TLS": "true"})

    assert load_settings().ldap_start_tls is True


def test_ldap_recusa_ldaps_com_starttls(ambiente: DefinirAmbiente, ldap: dict[str, str]) -> None:
    """004/R1.2 (StartTLS numa conexão que já é TLS é recusado pelo servidor: toda conexão
    falharia)"""
    ambiente({**VARIAVEIS_DEV, **ldap, "LDAP_START_TLS": "true"})

    with pytest.raises(SettingsError, match="LDAP_START_TLS: só com ldap://"):
        load_settings()


@pytest.mark.parametrize(
    "url",
    ["ldap://ldap:389", "ldapi:///var/run/slapd", "https://ldap", "ldaps://", "ldaps://ldap:x"],
)
def test_ldap_recusa_conexao_sem_tls(
    ambiente: DefinirAmbiente, ldap: dict[str, str], url: str
) -> None:
    """004/R1.2 (só LDAPS, ou ldap:// com StartTLS)"""
    ambiente({**VARIAVEIS_DEV, **ldap, "LDAP_URL": url})

    with pytest.raises(SettingsError, match="LDAP_URL: use ldaps://"):
        load_settings()


@pytest.mark.parametrize(
    "variavel",
    [
        "LDAP_CA_FILE",
        "LDAP_BIND_DN",
        "LDAP_BIND_PASSWORD",
        "LDAP_USER_BASE",
        "LDAP_USER_FILTER",
        "LDAP_GROUP_BASE",
        "LDAP_GROUP_FILTER",
    ],
)
def test_ldap_incompleto_recusa_a_subida_e_nomeia_a_variavel(
    ambiente: DefinirAmbiente, ldap: dict[str, str], variavel: str
) -> None:
    """004/R1.2, R1.3, R1.4 (CA, conta de serviço, bases e filtros obrigatórios com LDAP_URL)"""
    ambiente({**VARIAVEIS_DEV, **ldap, variavel: ""})

    with pytest.raises(SettingsError, match=f"{variavel}: obrigatória com LDAP_URL"):
        load_settings()


@pytest.mark.parametrize(
    ("variavel", "valor"),
    [
        ("LDAP_CA_FILE", "/nao/existe/ca.crt"),
        ("LDAP_ATTR_LOGIN", "uid)(cn=*"),
        ("LDAP_ATTR_EMAIL", "1mail"),
        ("LDAP_ATTR_MEMBER", ""),
        ("LDAP_PAGE_SIZE", "0"),
        ("LDAP_PAGE_SIZE", "1001"),
    ],
)
def test_ldap_recusa_valor_fora_do_formato(
    ambiente: DefinirAmbiente, ldap: dict[str, str], variavel: str, valor: str
) -> None:
    """004/R1.2, R1.4 (CA existente, nomes de atributo e página de 1 a 1000)"""
    ambiente({**VARIAVEIS_DEV, **ldap, variavel: valor})

    with pytest.raises(SettingsError, match=variavel):
        load_settings()


def test_ldap_attr_id_vazio_usa_o_dn(ambiente: DefinirAmbiente, ldap: dict[str, str]) -> None:
    """004/R1.5 (LDAP_ATTR_ID vazio usa o DN como identificador externo)"""
    ambiente({**VARIAVEIS_DEV, **ldap, "LDAP_ATTR_ID": ""})

    assert load_settings().ldap_attr_id == ""


@pytest.mark.parametrize("senha", ["sgs-lab-exemplo-bind", "changeme", "admin"])
def test_prod_recusa_senha_ldap_de_exemplo(
    ambiente: DefinirAmbiente, ldap: dict[str, str], senha: str
) -> None:
    """004/R5.4 (a mesma regra das senhas de exemplo da 001/R2.3)"""
    ambiente({**VARIAVEIS_PROD, **ldap, "LDAP_BIND_PASSWORD": senha})

    with pytest.raises(SettingsError, match="LDAP_BIND_PASSWORD: senha de exemplo") as erro:
        load_settings()

    assert senha not in str(erro.value)


def test_prod_aceita_senha_ldap_de_verdade(ambiente: DefinirAmbiente, ldap: dict[str, str]) -> None:
    """004/R5.4"""
    ambiente({**VARIAVEIS_PROD, **ldap, "LDAP_BIND_PASSWORD": "Qm7-senha-real-9x"})

    assert load_settings().ldap_bind_password is not None


def test_senha_ldap_de_exemplo_vale_fora_de_prod(
    ambiente: DefinirAmbiente, ldap: dict[str, str]
) -> None:
    """004/R5.4 (o laboratório usa a senha de exemplo; a recusa vale só em prod)"""
    ambiente({**VARIAVEIS_DEV, **ldap})

    senha = load_settings().ldap_bind_password

    assert senha is not None
    assert senha.get_secret_value() == "sgs-lab-exemplo-bind"
