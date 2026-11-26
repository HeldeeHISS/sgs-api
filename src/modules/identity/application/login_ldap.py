"""Login por LDAP (spec 004, T5; design § Fluxo, Login LDAP).

Como o login local (`login.entrar`), as falhas voltam como `Falha` para a rota confirmar a
transação (contador, bloqueio, auditoria) antes de responder. A mensagem ao cliente é a mesma da
002 nos casos 401; o diretório fora vira 503.

Toda chamada ao diretório acontece antes da primeira escrita de provisionamento: se ele cair no
meio, nada fica criado pela metade.
"""

from dataclasses import dataclass
from uuid import UUID

from src.modules.access.application.mapeamentos import recalcular_derivadas_do_usuario
from src.modules.access.domain.ports import (
    RepositorioDeAtribuicoes,
    RepositorioDeMapeamentos,
    RepositorioDePermissoes,
)
from src.modules.audit.application.registrar import Ator
from src.modules.identity.application.login import (
    ORIGEM_API,
    Falha,
    Identidade,
    Origem,
    ResultadoDoLogin,
    _auditar,
    desafiar_ou_abrir_sessao,
)
from src.modules.identity.domain.diretorio import (
    DirectoryGroup,
    DirectoryPort,
    DirectoryUnavailable,
    DirectoryUser,
)
from src.modules.identity.domain.ports import DirectoryRepository
from src.modules.identity.domain.users import User, UserSource, normalize_username

__all__ = ["DIRETORIO_INDISPONIVEL", "Diretorio", "entrar_por_ldap"]

DIRETORIO_INDISPONIVEL = "Serviço de diretório indisponível"


@dataclass(frozen=True)
class Diretorio:
    """O que o login LDAP usa além da `Identidade`, preso à mesma sessão do banco.

    `porta` é None com o LDAP desligado (sem LDAP_URL).
    """

    porta: DirectoryPort | None
    repositorio: DirectoryRepository
    mapeamentos: RepositorioDeMapeamentos
    atribuicoes: RepositorioDeAtribuicoes
    permissoes: RepositorioDePermissoes


@dataclass(frozen=True)
class _Lido:
    usuario: DirectoryUser
    grupos: tuple[DirectoryGroup, ...]


async def entrar_por_ldap(
    username: str, senha: str, *, origem: Origem, deps: Identidade, diretorio: Diretorio
) -> ResultadoDoLogin | Falha:
    """R2.1 a R2.4, R4.1, R4.3, R4.4 (design § Fluxo, Login LDAP, passos 1 a 7)."""
    if not senha:  # R2.4: nem chega ao diretório (evita bind anônimo aceito por engano)
        await _auditar(deps, "auth.login_failed", origem, None, {"motivo": "credenciais"})
        return Falha()
    agora = deps.relogio()
    username = normalize_username(username)  # como o login local e o adaptador guardam
    local = await deps.usuarios.get_by_username(username)
    if local is not None and local.source is not UserSource.LDAP:  # R4.1
        await _auditar(deps, "auth.login_failed", origem, None, {"motivo": "credenciais"})
        return Falha()
    if local is not None:
        estado = await deps.estados.estado(local.id)
        if estado is not None and estado.bloqueado(agora):  # antes do bind (design § Segurança)
            await _auditar(deps, "auth.login_failed", origem, local.id, {"motivo": "bloqueado"})
            return Falha()
    if diretorio.porta is None:
        return await _indisponivel(deps, origem, local)

    try:
        lido = await _ler_do_diretorio(diretorio.porta, username, senha)
    except DirectoryUnavailable:
        return await _indisponivel(deps, origem, local)
    if lido is None:
        await _falha_de_credencial(deps, origem, local)
        return Falha()

    usuario = await _provisionar(lido.usuario, origem=origem, deps=deps, diretorio=diretorio)
    if isinstance(usuario, Falha):
        return usuario
    await _espelhar_grupos(usuario, lido.grupos, origem=origem, deps=deps, diretorio=diretorio)
    await deps.estados.registrar_sucesso(usuario.id, agora)
    estado = await deps.estados.estado(usuario.id)
    if estado is None:  # pragma: no cover - acabou de ser gravado
        return Falha()
    return await desafiar_ou_abrir_sessao(usuario, estado, origem=origem, deps=deps)


async def _ler_do_diretorio(porta: DirectoryPort, username: str, senha: str) -> _Lido | None:
    """Passos 3, 4 e a leitura do 6: None se o usuário não está no diretório ou a senha errou."""
    encontrado = await porta.find_user(username)  # R2.2: o adaptador escapa o filtro
    if encontrado is None or not await porta.verify_password(encontrado.dn, senha):
        return None
    return _Lido(usuario=encontrado, grupos=await porta.groups_of(encontrado))


async def _provisionar(
    dado: DirectoryUser, *, origem: Origem, deps: Identidade, diretorio: Diretorio
) -> User | Falha:
    """Passo 5: conta por (`ldap`, `external_id`); cria, atualiza ou recusa."""
    agora = deps.relogio()
    conta = await deps.usuarios.get_by_external_id(UserSource.LDAP, dado.external_id)
    if conta is not None and not conta.is_active:  # R4.4: o login nunca reativa
        await _auditar(deps, "auth.login_failed", origem, conta.id, {"motivo": "desativado"})
        return Falha()
    if conta is None:
        if await _ocupado(deps, dado.login, dado.email):  # R4.3: nada é unido nem criado
            await _auditar(deps, "auth.login_failed", origem, None, {"motivo": "conflito_de_conta"})
            return Falha()
        nova = User(
            id=deps.gerar_id(),
            username=dado.login,
            display_name=dado.display_name,
            source=UserSource.LDAP,
            email=dado.email,
            external_id=dado.external_id,
        )
        await diretorio.repositorio.add_synced_user(nova, agora)
        await _auditar(
            deps, "usuario.provisionado", origem, nova.id, {"origem": "ldap"}, tipo="sistema"
        )
        return nova
    # E-mail novo que já é de outra conta: fica o antigo, como na sincronização (R4.2).
    email = dado.email
    if email is not None and email != conta.email:
        dono = await deps.usuarios.get_by_email(email)
        if dono is not None and dono.id != conta.id:
            email = conta.email
    atualizada = User(
        id=conta.id,
        username=conta.username,
        display_name=dado.display_name,
        source=UserSource.LDAP,
        email=email,
        external_id=conta.external_id,
    )
    await diretorio.repositorio.update_synced_user(
        conta.id, display_name=atualizada.display_name, email=atualizada.email, agora=agora
    )
    return atualizada


async def _ocupado(deps: Identidade, username: str, email: str | None) -> bool:
    if await deps.usuarios.get_by_username(username) is not None:
        return True
    return email is not None and await deps.usuarios.get_by_email(email) is not None


async def _espelhar_grupos(
    usuario: User,
    grupos: tuple[DirectoryGroup, ...],
    *,
    origem: Origem,
    deps: Identidade,
    diretorio: Diretorio,
) -> None:
    """Passo 6 (R2.3): grupos do diretório, associações `ldap` do usuário e derivadas da 003."""
    agora = deps.relogio()
    ids: set[UUID] = set()
    for grupo in grupos:
        gravado = await diretorio.repositorio.upsert_group(
            "ldap", grupo.external_id, grupo.name, agora
        )
        ids.add(gravado.id)
    await diretorio.repositorio.replace_user_groups(usuario.id, "ldap", ids)
    await recalcular_derivadas_do_usuario(
        usuario.id,
        frozenset(ids),
        # O recálculo é ato do sistema, disparado pelo login (013/R1.1).
        ator=Ator(
            tipo="sistema",
            origem=ORIGEM_API,
            ip=origem.ip,
            user_agent=origem.user_agent,
            request_id=origem.request_id,
        ),
        mapeamentos=diretorio.mapeamentos,
        atribuicoes=diretorio.atribuicoes,
        permissoes=diretorio.permissoes,
        auditoria=deps.auditoria,
        gerar_id=deps.gerar_id,
    )


async def _falha_de_credencial(deps: Identidade, origem: Origem, local: User | None) -> None:
    """Senha errada ou usuário fora do diretório: conta a falha se houver conta (002/R1.4)."""
    if local is None:
        await _auditar(deps, "auth.login_failed", origem, None, {"motivo": "credenciais"})
        return
    agora = deps.relogio()
    depois = await deps.estados.registrar_falha(local.id, agora)
    await _auditar(deps, "auth.login_failed", origem, local.id, {"motivo": "credenciais"})
    if depois.bloqueado(agora):
        await _auditar(deps, "auth.locked", origem, local.id, {}, tipo="sistema")


async def _indisponivel(deps: Identidade, origem: Origem, local: User | None) -> Falha:
    await _auditar(
        deps,
        "auth.login_failed",
        origem,
        None if local is None else local.id,
        {"motivo": "diretorio_indisponivel"},
    )
    return Falha(DIRETORIO_INDISPONIVEL, status=503)
