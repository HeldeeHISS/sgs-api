"""Repositórios do diretório e do login OIDC (spec 004, design § Portas e adaptadores).

SQLAlchemy Core sobre a sessão de quem chama: quem chama abre, confirma ou desfaz a transação.
"""

from dataclasses import asdict
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, func, literal_column, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.ids import uuid7
from src.modules.identity.domain.espelho_diretorio import (
    OrigemDeGrupo,
    StatusDaExecucao,
    SyncConflict,
    SyncCounts,
    SyncRun,
    UpsertedGroup,
)
from src.modules.identity.domain.oidc import OidcLoginRequest
from src.modules.identity.infrastructure.tables import (
    SYNC_COUNT_COLUMNS,
    directory_sync_runs,
    groups,
    oidc_login_requests,
    user_groups,
    users,
)

_OIDC_COLUMNS = (
    oidc_login_requests.c.id,
    oidc_login_requests.c.state_hash,
    oidc_login_requests.c.binding_hash,
    oidc_login_requests.c.nonce,
    oidc_login_requests.c.code_verifier_enc,
    oidc_login_requests.c.created_at,
    oidc_login_requests.c.expires_at,
)


class SqlDirectoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_group(
        self, source: OrigemDeGrupo, external_id: str, name: str, agora: datetime
    ) -> UpsertedGroup:
        comando = insert(groups).values(
            id=uuid7(), source=source, external_id=external_id, name=name, synced_at=agora
        )
        comando = comando.on_conflict_do_update(
            index_elements=[groups.c.source, groups.c.external_id],
            set_={"name": name, "is_active": True, "synced_at": agora, "updated_at": func.now()},
        )
        # `xmax = 0` só na linha recém-inserida: distingue criação de atualização num passo só.
        criado = (literal_column("xmax") == text("0")).label("criado")
        linha = (await self._session.execute(comando.returning(groups.c.id, criado))).one()
        return UpsertedGroup(id=linha.id, created=bool(linha.criado))

    async def deactivate_groups_except(
        self, source: OrigemDeGrupo, external_ids: set[str], agora: datetime
    ) -> int:
        comando = (
            update(groups)
            .where(
                groups.c.source == source,
                groups.c.is_active,
                groups.c.external_id.not_in(external_ids),
            )
            .values(is_active=False, synced_at=agora)
            .returning(groups.c.id)
        )
        return len((await self._session.execute(comando)).all())

    async def replace_user_groups(
        self, user_id: UUID, source: OrigemDeGrupo, group_ids: set[UUID]
    ) -> None:
        await self._session.execute(
            delete(user_groups).where(
                user_groups.c.user_id == user_id, user_groups.c.source == source
            )
        )
        if group_ids:
            linhas = [{"user_id": user_id, "group_id": g, "source": source} for g in group_ids]
            await self._session.execute(insert(user_groups), linhas)

    async def groups_of_user(self, user_id: UUID, source: OrigemDeGrupo) -> frozenset[UUID]:
        consulta = select(user_groups.c.group_id).where(
            user_groups.c.user_id == user_id, user_groups.c.source == source
        )
        return frozenset((await self._session.scalars(consulta)).all())

    async def active_member_counts(self, group_ids: set[UUID]) -> dict[UUID, int]:
        if not group_ids:
            return {}
        juncao = groups.outerjoin(user_groups, user_groups.c.group_id == groups.c.id).outerjoin(
            users, and_(users.c.id == user_groups.c.user_id, users.c.is_active)
        )
        consulta = (
            select(groups.c.id, func.count(users.c.id))
            .select_from(juncao)
            .where(groups.c.id.in_(group_ids))
            .group_by(groups.c.id)
        )
        return dict((await self._session.execute(consulta)).all())

    async def start_run(self, run_id: UUID, *, triggered_by: UUID | None, agora: datetime) -> bool:
        # O índice único parcial decide (R1.11, D004-1); ON CONFLICT não aborta a transação.
        comando = (
            insert(directory_sync_runs)
            .values(
                id=run_id,
                kind="ldap",
                status="em_andamento",
                started_at=agora,
                triggered_by=triggered_by,
            )
            .on_conflict_do_nothing(
                index_elements=[directory_sync_runs.c.kind],
                index_where=directory_sync_runs.c.status == "em_andamento",
            )
            .returning(directory_sync_runs.c.id)
        )
        return (await self._session.scalar(comando)) is not None

    async def finish_run(
        self,
        run_id: UUID,
        status: StatusDaExecucao,
        agora: datetime,
        *,
        counts: SyncCounts | None = None,
        conflicts: tuple[SyncConflict, ...] = (),
        error: str | None = None,
    ) -> None:
        conflitos = [
            asdict(c) | {"usuario_existente_id": str(c.usuario_existente_id)} for c in conflicts
        ]
        await self._session.execute(
            update(directory_sync_runs)
            .where(directory_sync_runs.c.id == run_id)
            .values(
                status=status,
                finished_at=agora,
                conflicts=conflitos,
                error=error,
                **asdict(counts or SyncCounts()),
            )
        )

    async def get_run(self, run_id: UUID) -> SyncRun | None:
        consulta = select(directory_sync_runs).where(directory_sync_runs.c.id == run_id)
        linha = (await self._session.execute(consulta)).mappings().one_or_none()
        return None if linha is None else _execucao(linha)


def _execucao(linha: Any) -> SyncRun:
    conflitos = tuple(
        SyncConflict(
            tipo=c["tipo"],
            external_id=c["external_id"],
            usuario_existente_id=UUID(c["usuario_existente_id"]),
        )
        for c in linha["conflicts"]
    )
    return SyncRun(
        id=linha["id"],
        kind=linha["kind"],
        status=linha["status"],
        started_at=linha["started_at"],
        finished_at=linha["finished_at"],
        triggered_by=linha["triggered_by"],
        counts=SyncCounts(**{nome: linha[nome] for nome in SYNC_COUNT_COLUMNS}),
        conflicts=conflitos,
        error=linha["error"],
    )


class SqlOidcLoginRequests:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, request: OidcLoginRequest) -> None:
        await self._session.execute(insert(oidc_login_requests).values(**asdict(request)))

    async def consume(self, state_hash: str, agora: datetime) -> OidcLoginRequest | None:
        # Um UPDATE só: duas chamadas simultâneas com o mesmo `state` não acham a linha as duas
        # (R3.7, design § Fluxo, Login OIDC, passo 1).
        comando = (
            update(oidc_login_requests)
            .where(
                oidc_login_requests.c.state_hash == state_hash,
                oidc_login_requests.c.used_at.is_(None),
                oidc_login_requests.c.expires_at > agora,
            )
            .values(used_at=agora)
            .returning(*_OIDC_COLUMNS)
        )
        linha = (await self._session.execute(comando)).mappings().one_or_none()
        return None if linha is None else OidcLoginRequest(**linha)

    async def delete_expired_before(self, limite: datetime) -> int:
        comando = (
            delete(oidc_login_requests)
            .where(oidc_login_requests.c.expires_at < limite)
            .returning(oidc_login_requests.c.id)
        )
        return len((await self._session.execute(comando)).all())
