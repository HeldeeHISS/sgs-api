"""Leitura do log do banco em SQL escrito à mão (ADR-0009; design da 011 § Consultas).

Cada consulta está em `sql/<nome>.sql`, com o cabeçalho que a explica, e é lida uma vez, na carga
do módulo. Os filtros entram só como parâmetros nomeados: nenhum valor é interpolado no texto.
"""

from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.sql import consulta
from src.modules.audit.domain.log_banco import AlteracaoBanco, Pagina, Resultado

_SQL = Path(__file__).parent / "sql"

_LISTAR = consulta(_SQL / "db_change_log_list.sql")


class SqlLogDoBanco:
    """`LogDoBanco` sobre a sessão SQLAlchemy de quem o consulta."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def listar(self, pagina: Pagina) -> Resultado[AlteracaoBanco]:
        linhas, total = await self._pagina({}, pagina)
        return Resultado(
            itens=tuple(
                AlteracaoBanco(
                    ocorrido_em=linha["occurred_at"],
                    tabela=linha["table_name"],
                    operacao=linha["operation"],
                    linha_id=linha["row_id"],
                    colunas_alteradas=linha["changed_columns"],
                    usuario_banco=linha["db_user"],
                    via_api=linha["via_api"],
                )
                for linha in linhas
            ),
            total=total,
        )

    async def _pagina(
        self, parametros: dict[str, Any], pagina: Pagina
    ) -> tuple[list[dict[str, Any]], int]:
        """Linhas da página e total da consulta, que vem em cada linha (`count(*) OVER ()`).

        Numa página depois da última não volta linha nenhuma, e o total sai da primeira página.
        """
        parametros = {**parametros, "limit": pagina.tamanho, "offset": pagina.offset}
        linhas = [
            dict(linha) for linha in (await self._session.execute(_LISTAR, parametros)).mappings()
        ]
        if linhas:
            return linhas, int(linhas[0]["total"])
        if pagina.offset == 0:
            return [], 0
        primeira = await self._session.execute(_LISTAR, {**parametros, "limit": 1, "offset": 0})
        linha = primeira.mappings().first()
        return [], 0 if linha is None else int(linha["total"])
