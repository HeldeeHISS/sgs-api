"""Schemas de saída do histórico da unidade (design da 011 § Rotas; 008/R1.2).

Lidos dos valores do domínio (`model_validate` com `from_attributes`), nunca de linhas do banco.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class MudancaOut(BaseModel):
    """Um campo alterado, com o valor de antes (`de`) e o de depois (`para`)."""

    model_config = ConfigDict(from_attributes=True)

    campo: str
    de: Any
    para: Any


class EventoHistoricoOut(BaseModel):
    """Um evento da unidade (ou de um equipamento ou serviço dela): data e hora, autor com os
    papéis no momento, ação e a lista {campo, de, para} (008/R1.2, 011/R4.4). Sem autor (tarefa
    do sistema), `autor_id` e `autor_nome` vêm nulos."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    ocorrido_em: datetime
    acao: str
    autor_id: UUID | None
    autor_nome: str | None
    autor_papeis: list[str]
    mudancas: list[MudancaOut]
    metadados: dict[str, Any]
