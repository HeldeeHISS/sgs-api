"""Prova de uso dos índices da 0011 com volume de demonstração (011/T9).

A massa (1.000 unidades, 3.000 serviços e a trilha que os casos de uso da 006 gravariam, pelo
`audit.registrar`) é semeada pelo dono do banco; depois do `ANALYZE`, o `EXPLAIN` roda pelo login
da API, que é quem faz as consultas. Sem `enable_seqscan = off`: o plano é o que o planejador
escolhe sozinho com o volume, não um plano forçado.

Roda à parte (`uv run pytest tests/integration/db/test_indices.py -m slow`), fora do `make check`:
a massa leva alguns segundos para semear.
"""

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.sql import consulta
from src.modules.audit.application.registrar import Ator, Entidade, Mudanca, registrar
from src.modules.audit.infrastructure.repository import SqlAuditStore
from tests.integration.postgres import BancoDeTeste, motor
from tests.integration.registry.cadastro import inserir_servico, inserir_unidade

pytestmark = pytest.mark.slow

UNIDADES = 1_000
SERVICOS_POR_UNIDADE = 3
DISTRITOS = ("DS-I", "DS-II", "DS-III", "DS-IV", "DS-V", "DS-VI", "DS-VII", "DS-VIII")

RAIZ = Path(__file__).resolve().parents[3]
HISTORICO = RAIZ / "src/modules/audit/infrastructure/sql/unit_history.sql"
SQL_DOS_INDICES = {
    "ix_audit_log_entity": RAIZ / "migrations/sql/0011_03/06_ix_audit_log_entity.sql",
    "ix_services_unit_id": RAIZ / "migrations/sql/0011_03/07_ix_services_unit_id.sql",
}

# Serviços de uma unidade, como a rota de serviços da unidade os lista (006/T8, 011/R1.2).
SERVICOS_DA_UNIDADE = RAIZ / "src/modules/registry/infrastructure/sql/services_by_unit.sql"


@dataclass(frozen=True)
class Volume:
    banco: BancoDeTeste
    unidade: UUID


async def _semear(banco: BancoDeTeste) -> UUID:
    """Cadastro e trilha de demonstração; devolve uma unidade do meio da massa."""
    engine = motor(banco.url_dono)
    servicos: dict[UUID, list[UUID]] = {}
    async with engine.begin() as conexao:
        for n in range(UNIDADES):
            unidade = await inserir_unidade(
                conexao, f"Unidade de Demonstração {n:04d}", distrito=DISTRITOS[n % len(DISTRITOS)]
            )
            servicos[unidade] = [
                await inserir_servico(conexao, unidade, nome=f"Serviço {s} da unidade {n:04d}")
                for s in range(SERVICOS_POR_UNIDADE)
            ]

    # A trilha que a 006 grava: criação da unidade e de cada serviço, no nível da unidade
    # (006/R5.3), pelo mesmo caso de uso dos módulos.
    sessoes = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    ator = Ator(papeis=("GESTOR_DADOS",))
    async with sessoes() as sessao, sessao.begin():
        store = SqlAuditStore(sessao)
        for unidade, dela in servicos.items():
            entidade = Entidade(tipo="unidade", id=str(unidade))
            await registrar(
                store,
                acao="unidade.criada",
                ator=ator,
                entidade=entidade,
                mudancas=[Mudanca("status", None, "ativa")],
            )
            for servico in dela:
                await registrar(
                    store,
                    acao="unidade.servico_criado",
                    ator=ator,
                    entidade=entidade,
                    mudancas=[Mudanca("servico.status", None, "ativo")],
                    metadados={"servico_id": str(servico)},
                )

    async with engine.connect() as conexao:
        await conexao.execute(text("ANALYZE"))
    await engine.dispose()
    return list(servicos)[UNIDADES // 2]


@pytest.fixture
async def volume(banco: BancoDeTeste) -> Volume:
    return Volume(banco=banco, unidade=await _semear(banco))


async def _plano(banco: BancoDeTeste, consulta_sql: str, parametros: dict[str, object]) -> str:
    engine = motor(banco.url_api)
    async with engine.connect() as conexao:
        linhas = await conexao.execute(text(f"EXPLAIN (COSTS OFF) {consulta_sql}"), parametros)
        plano = "\n".join(linha for (linha,) in linhas)
    await engine.dispose()
    return plano


def _cabecalho(arquivo: Path) -> str:
    linhas = arquivo.read_text(encoding="utf-8").splitlines()
    return "\n".join(linha for linha in linhas if linha.startswith("--"))


async def test_historico_de_uma_unidade_usa_ix_audit_log_entity(volume: Volume) -> None:
    """011/R1.1, R1.3: com volume, o histórico de uma unidade (a consulta da rota) chega à trilha
    pelo índice, não por leitura da tabela inteira."""
    parametros: dict[str, object] = {
        "unit_id": volume.unidade,
        "occurred_from": None,
        "occurred_to": None,
        "actor_user_id": None,
        "changed_field": None,
        "limit": 50,
        "offset": 0,
    }

    plano = await _plano(volume.banco, consulta(HISTORICO).text, parametros)

    assert "ix_audit_log_entity" in plano, plano
    assert "Seq Scan on audit_log" not in plano, plano


async def test_servicos_de_uma_unidade_usam_ix_services_unit_id(volume: Volume) -> None:
    """011/R1.2, R1.3: com volume, os serviços de uma unidade são achados pelo índice."""
    plano = await _plano(
        volume.banco, consulta(SERVICOS_DA_UNIDADE).text, {"unit_id": volume.unidade}
    )

    assert "ix_services_unit_id" in plano, plano
    assert "Seq Scan on services" not in plano, plano


@pytest.mark.parametrize("indice", SQL_DOS_INDICES)
def test_cabecalho_do_indice_registra_o_plano(indice: str) -> None:
    """011/R1.4: o cabeçalho do `.sql` do índice traz o plano de execução que comprova o uso."""
    cabecalho = _cabecalho(SQL_DOS_INDICES[indice])

    assert "EXPLAIN" in cabecalho
    # "Index Scan using <índice>" ou "Bitmap Index Scan on <índice>", conforme o plano.
    assert any("Index Scan" in linha and indice in linha for linha in cabecalho.splitlines())
