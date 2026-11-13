"""Consulta do log do banco pela API (spec 011, R7.7; design § Rotas)."""

from src.modules.access.application.autorizacao import UsuarioAtual
from src.modules.audit.domain.log_banco import AlteracaoBanco, Pagina, Resultado
from src.modules.audit.domain.ports import LogDoBanco

# Reexportados para a camada api, que só importa a application.
__all__ = [
    "PERMISSAO",
    "AlteracaoBanco",
    "LogDoBanco",
    "Pagina",
    "Resultado",
    "listar_alteracoes",
]

PERMISSAO = "auditoria:read"


async def listar_alteracoes(
    log: LogDoBanco, usuario: UsuarioAtual, pagina: Pagina
) -> Resultado[AlteracaoBanco]:
    """011/R7.7: registros do log, do mais recente para o mais antigo."""
    return await log.listar(pagina)
