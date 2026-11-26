"""Raiz de composição da API: cria o app e liga as camadas dos módulos."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from importlib.metadata import version

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.core import health
from src.core.config import AppEnv, Settings, load_settings
from src.core.crypto import FieldCipher
from src.core.db import create_engine, run_migrations
from src.core.ids import uuid7
from src.core.limite import LimiteDeRequisicoes
from src.core.logging import configure_logging
from src.core.problem import install_error_handlers
from src.core.request_context import RequestContextMiddleware
from src.core.security_headers import SecurityHeadersMiddleware
from src.modules.access.api import rotas as acesso
from src.modules.access.application.permissoes import ResolvedorDePermissoes
from src.modules.access.infrastructure.repository import (
    SemMembrosDeGrupo,
    SqlRepositorioDeAtribuicoes,
    SqlRepositorioDeMapeamentos,
    SqlRepositorioDePermissoes,
    SqlResolvedorDeUnidade,
)
from src.modules.audit.api import rotas as auditoria
from src.modules.audit.infrastructure.consultas import SqlConsultasDeAuditoria
from src.modules.audit.infrastructure.repository import SqlAuditStore
from src.modules.dashboard.api import rotas as dashboard
from src.modules.dashboard.infrastructure.consultas import SqlConsultasDashboard
from src.modules.identity.api import auth, jwks
from src.modules.identity.api.sincronizacao import transacoes_da_sincronizacao
from src.modules.identity.application.sincronizacao import marcar_interrompidas
from src.modules.identity.infrastructure.directory_repository import SqlDirectoryRepository
from src.modules.identity.infrastructure.ldap3_directory import Ldap3Directory, LdapConfig
from src.modules.identity.infrastructure.passwords import Argon2PasswordHasher
from src.modules.identity.infrastructure.repository import (
    SqlLoginStateRepository,
    SqlRefreshTokenRepository,
    SqlRefreshTokenWriter,
    SqlSessionRepository,
    SqlSessionRevoker,
    SqlUserRepository,
)
from src.modules.identity.infrastructure.tokens import JwtTokenService
from src.modules.identity.infrastructure.totp import PyotpVerificador
from src.modules.registry.api import referencias, unidades, vinculos
from src.modules.registry.infrastructure.repository import (
    SqlRepositorioDeReferencias,
    SqlRepositorioDeUnidades,
    SqlRepositorioDeVinculos,
)
from src.modules.reports.api import jwks as relatorios_jwks
from src.modules.reports.infrastructure.assinatura import Es256AssinadorDeRelatorio


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    if settings.app_env is AppEnv.DEV and settings.database_migration_url is not None:
        # Em dev a subida aplica as migrações (R1.2); em test e prod elas são um passo à parte.
        await run_migrations(settings.database_migration_url.get_secret_value(), settings)
    if settings.ldap_url is not None:
        # Spec 004, design § Fluxo, Sincronização, passo 6: o que ficou em andamento morreu
        # com o processo anterior (a API roda numa instância só).
        await marcar_interrompidas(
            transacoes=transacoes_da_sincronizacao(app.state), agora=lambda: datetime.now(UTC)
        )
    try:
        yield
    finally:
        await app.state.engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Fábrica do app. Sem argumento, lê a configuração do ambiente (`uvicorn --factory`)."""
    settings = settings or load_settings()
    configure_logging(settings.log_level)

    # Documentação OpenAPI só em dev e test (R6.3).
    public_docs = settings.app_env is not AppEnv.PROD
    app = FastAPI(
        title="SGS · API de Governança de Dados de Saúde",
        version=version("sgs-api"),
        lifespan=_lifespan,
        openapi_url="/openapi.json" if public_docs else None,
        docs_url="/docs" if public_docs else None,
        redoc_url="/redoc" if public_docs else None,
    )
    app.state.settings = settings
    # A engine só abre conexões quando usada; criar o app não exige banco no ar.
    app.state.engine = create_engine(settings.database_url.get_secret_value(), settings)
    app.state.sessoes = async_sessionmaker(app.state.engine, expire_on_commit=False)
    app.state.token_service = JwtTokenService.from_settings(settings)
    # Assinatura dos relatórios (005/R5): None sem REPORT_SIGNING_KEYS, e então nada é assinado.
    app.state.assinador_de_relatorio = Es256AssinadorDeRelatorio.from_settings(settings)
    # Fábricas de repositórios: recebem a sessão SQLAlchemy de quem as usa (a camada api não
    # importa a infrastructure; design da 002, Decisões).
    app.state.repositorio_de_usuarios = SqlUserRepository
    app.state.repositorio_de_sessoes = SqlSessionRepository
    app.state.repositorio_de_login = SqlLoginStateRepository
    app.state.repositorio_de_refresh = SqlRefreshTokenRepository
    app.state.escrita_de_refresh = SqlRefreshTokenWriter
    app.state.revogacao_de_sessao = SqlSessionRevoker
    # Login local (spec 002): hasher Argon2id, TOTP, cifra do segredo e limite por IP (R1.5).
    app.state.hasher_de_senha = Argon2PasswordHasher()
    app.state.verificador_totp = PyotpVerificador()
    app.state.cifra_de_campo = FieldCipher(
        settings.chaves_de_cifra, active_version=settings.crypto_active_key or 0
    )
    app.state.gerar_id = uuid7
    app.state.limite_de_login = LimiteDeRequisicoes("10/minute")
    app.state.repositorio_de_permissoes = SqlRepositorioDePermissoes
    app.state.repositorio_de_atribuicoes = SqlRepositorioDeAtribuicoes
    app.state.repositorio_de_mapeamentos = SqlRepositorioDeMapeamentos
    app.state.repositorio_do_diretorio = SqlDirectoryRepository
    # Spec 004: sem LDAP_URL, o LDAP fica desligado (login LDAP e sincronização respondem 503).
    ldap = LdapConfig.from_settings(settings)
    app.state.diretorio = None if ldap is None else Ldap3Directory(ldap)
    # Membros dos grupos do diretório: sem `user_groups` até a 004/T1; a 004/T4 troca pela
    # leitura real (003/T8, Perguntas em aberto).
    app.state.membros_de_grupo = SemMembrosDeGrupo
    app.state.auditoria = SqlAuditStore
    app.state.consultas_auditoria = SqlConsultasDeAuditoria
    app.state.repositorio_de_unidades = SqlRepositorioDeUnidades
    app.state.repositorio_de_vinculos = SqlRepositorioDeVinculos
    app.state.repositorio_de_referencias = SqlRepositorioDeReferencias
    app.state.consultas_dashboard = SqlConsultasDashboard
    # Um resolvedor por processo: o cache de permissões é dele (design da 003, passo 3).
    app.state.resolvedor_de_permissoes = ResolvedorDePermissoes()
    # `ResourceRef.tipo` → fábrica do `ResolvedorDeAlvo` daquele tipo de recurso (design da 003,
    # § Verificação).
    app.state.resolvedores_de_alvo = {"unidade": SqlResolvedorDeUnidade}
    install_error_handlers(app)
    app.include_router(health.router)
    app.include_router(jwks.router)
    app.include_router(relatorios_jwks.router)
    app.include_router(auth.router)
    app.include_router(acesso.router)
    app.include_router(unidades.router)
    app.include_router(vinculos.router)
    app.include_router(referencias.router)
    app.include_router(dashboard.router)
    app.include_router(dashboard.painel)
    app.include_router(auditoria.router)

    # O último middleware adicionado é o mais externo: contexto > cabeçalhos > CORS > rotas.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),  # só as origens configuradas (R6.2)
        allow_credentials=True,  # o refresh token da 002 viaja em cookie
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Requested-With", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware)
    return app
