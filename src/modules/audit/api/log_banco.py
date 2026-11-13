"""Rotas do log do banco (spec 011, R7.7; design § Rotas).

`GET` de leitura com `require("auditoria:read")`. A sessão da requisição vem da
`SessaoDeNegocio`; nada é gravado.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from src.core.paginacao import TAMANHO_PADRAO, NumeroDaPagina, PaginaOut, TamanhoDaPagina
from src.core.problem import PROBLEM_JSON
from src.modules.access.api.deps import SessaoDeNegocio, UsuarioAtual, require
from src.modules.audit.api.schemas import AlteracaoBancoOut
from src.modules.audit.application import log_banco
from src.modules.audit.application.log_banco import LogDoBanco, Pagina


def _problema(descricao: str) -> dict[str, Any]:
    """Resposta de erro em problem+json (spec 001), para o contrato."""
    return {"description": descricao, "content": {PROBLEM_JSON: {}}}


router = APIRouter(
    prefix="/api/v1/auditoria/banco",
    tags=["auditoria"],
    responses={
        401: _problema("Sem access token válido"),
        403: _problema("Sem `auditoria:read` em escopo global"),
    },
)

Auditor = Annotated[UsuarioAtual, Depends(require(log_banco.PERMISSAO))]


@router.get("", summary="Log do banco: alterações do cadastro registradas pelo PostgreSQL")
async def listar(
    request: Request,
    usuario: Auditor,
    sessao: SessaoDeNegocio,
    pagina: NumeroDaPagina = 1,
    tamanho: TamanhoDaPagina = TAMANHO_PADRAO,
) -> PaginaOut[AlteracaoBancoOut]:
    """011/R7.7: registros do log, do mais recente para o mais antigo, paginados."""
    porta: LogDoBanco = request.app.state.log_do_banco(sessao)
    pedido = Pagina(numero=pagina, tamanho=tamanho)
    resultado = await log_banco.listar_alteracoes(porta, usuario, pedido)
    return PaginaOut(
        itens=[AlteracaoBancoOut.model_validate(item) for item in resultado.itens],
        total=resultado.total,
        pagina=pagina,
        tamanho=tamanho,
    )
