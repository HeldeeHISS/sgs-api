"""Portas do módulo de auditoria."""

from typing import Protocol

from src.modules.audit.domain.entry import AuditEntry
from src.modules.audit.domain.log_banco import AlteracaoBanco, Pagina, Resultado


class AuditStore(Protocol):
    """Armazenamento da trilha, preso à transação do caso de uso que registra o evento."""

    async def lock_chain_head(self) -> str:
        """Trava a cabeça da cadeia até o fim da transação e devolve o hash do último registro."""
        ...

    async def append(self, entry: AuditEntry) -> None:
        """Insere o registro e move a cabeça da cadeia para o hash dele."""
        ...


class LogDoBanco(Protocol):
    """Leitura do log do banco (`db_change_log`, 011/R7) sobre a sessão de quem o consulta; a
    aplicação só lê o log (011/R7.6)."""

    async def listar(self, pagina: Pagina) -> Resultado[AlteracaoBanco]:
        """Registros do log, do mais recente para o mais antigo (011/R7.7)."""
        ...
