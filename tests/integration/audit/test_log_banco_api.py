"""Log do banco pela API: `GET /api/v1/auditoria/banco` (spec 011, R7.7 e R7.8; design § Rotas).

O cadastro e os registros do log entram direto no banco, pelo dono (superusuário): o trigger grava
o log das alterações do cadastro, e os registros com data escolhida, para os filtros por período,
são inseridos no log pelo próprio dono. A leitura é sempre pela API, com o login dela.
"""

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncConnection, async_sessionmaker

from src.main import create_app
from tests.factories import make_settings
from tests.integration.acesso import GLOBAL, Sessoes, bearer, logado
from tests.integration.postgres import BancoDeTeste, motor
from tests.integration.registry.cadastro import inserir_unidade

LOG = "/api/v1/auditoria/banco"


@pytest.fixture
async def app(banco: BancoDeTeste) -> AsyncIterator[FastAPI]:
    app = create_app(make_settings(database_url=SecretStr(banco.url_api)))
    yield app
    await app.state.engine.dispose()


@pytest.fixture
async def cliente(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transporte = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transporte, base_url="http://teste") as cliente:
        yield cliente


@pytest.fixture
async def sessoes(banco: BancoDeTeste) -> AsyncIterator[Sessoes]:
    """Sessões com o login da API (papel sgs_app), para criar usuários e atribuições."""
    engine = motor(banco.url_api)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def dono(banco: BancoDeTeste) -> AsyncIterator[AsyncConnection]:
    """Conexão do dono do banco (superusuário), para semear o cadastro e o log. O teste confirma
    com `commit()` antes de chamar a API."""
    engine = motor(banco.url_dono)
    async with engine.connect() as conexao:
        yield conexao
    await engine.dispose()


async def _listar(
    app: FastAPI, cliente: httpx.AsyncClient, sessoes: Sessoes, **params: Any
) -> dict[str, Any]:
    """A resposta da listagem para um auditor com `auditoria:read` global."""
    auditor = await logado(sessoes, ("AUDITOR", GLOBAL))
    resposta = await cliente.get(LOG, params=params, headers=bearer(app, auditor))
    assert resposta.status_code == 200, resposta.text
    corpo: dict[str, Any] = resposta.json()
    return corpo


async def test_lista_as_alteracoes_do_cadastro_da_mais_recente_para_a_mais_antiga(
    app: FastAPI,
    cliente: httpx.AsyncClient,
    sessoes: Sessoes,
    dono: AsyncConnection,
    banco: BancoDeTeste,
) -> None:
    """011/R7.7: cada registro do log com data e hora, tabela, operação, linha, colunas alteradas
    (de/para), usuário de banco e origem; o mais recente primeiro. A alteração pelo superusuário
    vem como fora da API (R7.2)."""
    unidade = await inserir_unidade(dono, "CAPS Espaço Azul")
    await dono.commit()
    await dono.execute(
        text("UPDATE health_units SET phone = '8133551234' WHERE id = :id"), {"id": unidade}
    )
    await dono.commit()

    corpo = await _listar(app, cliente, sessoes)

    assert corpo["total"] == 2
    assert (corpo["pagina"], corpo["tamanho"]) == (1, 20)
    alteracao, inclusao = corpo["itens"]
    superusuario = make_url(banco.url_dono).username
    assert {chave: valor for chave, valor in alteracao.items() if chave != "ocorrido_em"} == {
        "tabela": "health_units",
        "operacao": "UPDATE",
        "linha_id": str(unidade),
        "colunas_alteradas": {"phone": {"de": None, "para": "8133551234"}},
        "usuario_banco": superusuario,
        "via_api": False,
    }
    assert (inclusao["tabela"], inclusao["operacao"]) == ("health_units", "INSERT")
    assert inclusao["colunas_alteradas"]["name"] == {"de": None, "para": "CAPS Espaço Azul"}
    assert datetime.fromisoformat(alteracao["ocorrido_em"]) > datetime.fromisoformat(
        inclusao["ocorrido_em"]
    )
    assert datetime.fromisoformat(alteracao["ocorrido_em"]).utcoffset() is not None
