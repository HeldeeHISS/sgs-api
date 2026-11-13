"""Schemas de saída das consultas do dashboard (design da 011 § Rotas).

Lidos dos valores do domínio (`model_validate` com `from_attributes`), nunca de linhas do banco.
"""

from uuid import UUID

from pydantic import BaseModel, ConfigDict

from src.modules.dashboard.application.consultas import SituacaoConciliacao, UnidadeForaCnes


class UnidadeResumoOut(BaseModel):
    """Unidade numa consulta de qualidade; `tipo` e `distrito` são os nomes, para leitura."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nome: str
    cnes: str | None
    tipo: str
    distrito: str


class UnidadeForaCnesOut(UnidadeResumoOut):
    """Unidade com serviço fora do CNES e a quantidade desses serviços (011/R2.4)."""

    servicos_fora_cnes: int

    @classmethod
    def de(cls, item: UnidadeForaCnes) -> "UnidadeForaCnesOut":
        resumo = UnidadeResumoOut.model_validate(item.unidade).model_dump()
        return cls(**resumo, servicos_fora_cnes=item.servicos_fora_cnes)


class ConciliacaoOut(BaseModel):
    """Linha da conciliação unidade x equipamento; o lado que faltar vem nulo. `distrito` é o
    nome do distrito da unidade e `equipamento_tipo`, o nome do tipo do equipamento."""

    model_config = ConfigDict(from_attributes=True)

    unidade_id: UUID | None
    unidade_nome: str | None
    distrito: str | None
    equipamento_id: UUID | None
    equipamento_nome: str | None
    equipamento_tipo: str | None
    situacao: SituacaoConciliacao


class DistritoCnesOut(BaseModel):
    """Distrito acima da rede em unidades sem CNES; `percentual` de 0 a 100, duas casas."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    nome: str
    total_unidades: int
    sem_cnes: int
    percentual: float


class DistritosCnesOut(BaseModel):
    """Percentual de unidades sem CNES da rede (as unidades no escopo e no filtro; nulo se não há
    nenhuma) e os distritos acima dele, do maior para o menor percentual."""

    model_config = ConfigDict(from_attributes=True)

    percentual_rede: float | None
    distritos: list[DistritoCnesOut]
