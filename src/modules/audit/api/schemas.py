"""Schemas de saída do log do banco (design da 011 § Rotas).

Lidos dos valores do domínio (`model_validate` com `from_attributes`), nunca de linhas do banco.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, JsonValue


class DeParaOut(BaseModel):
    """Valor de uma coluna antes (`de`) e depois (`para`) da alteração; nulo do lado que não
    existe (inclusão ou exclusão). O nome do gestor vem mascarado (`"***"`, 011/R7.3)."""

    de: JsonValue
    para: JsonValue


class AlteracaoBancoOut(BaseModel):
    """Um registro do log do banco (011/R7.1, R7.7). `via_api` falso é alteração feita fora da
    API, por outro usuário de banco ou por superusuário (011/R7.2)."""

    model_config = ConfigDict(from_attributes=True)

    ocorrido_em: datetime
    tabela: str
    operacao: str
    linha_id: UUID
    colunas_alteradas: dict[str, DeParaOut]
    usuario_banco: str
    via_api: bool
