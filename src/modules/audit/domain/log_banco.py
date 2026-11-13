"""Log do banco: o que o PostgreSQL registra das alterações do cadastro (spec 011, R7).

O log é gravado pelo trigger `trg_registry_change_log` (011/T7), venha a alteração de onde vier,
e só é lido pela aplicação. Aqui ficam os valores que entram e saem da porta `LogDoBanco`, sem
banco nem HTTP.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Pagina:
    """Página pedida: número (a partir de 1) e quantidade de itens por página."""

    numero: int
    tamanho: int

    @property
    def offset(self) -> int:
        return (self.numero - 1) * self.tamanho


@dataclass(frozen=True, slots=True)
class Resultado[T]:
    """Os itens de uma página e o total de itens da consulta inteira."""

    itens: tuple[T, ...]
    total: int


@dataclass(frozen=True, slots=True)
class AlteracaoBanco:
    """Um registro do log: quando, em que tabela, qual operação, em que linha, as colunas
    alteradas (`{coluna: {"de": v, "para": v}}`), o usuário de banco e se veio da API."""

    ocorrido_em: datetime
    tabela: str
    operacao: str
    linha_id: UUID
    colunas_alteradas: Mapping[str, Any]
    usuario_banco: str
    via_api: bool
