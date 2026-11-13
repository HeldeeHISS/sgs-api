"""Portas do módulo de cadastro.

Os repositórios trabalham sobre a transação de quem os usa (a da requisição, aberta pela
dependência `SessaoDeNegocio` da camada api): quem chama abre, confirma ou desfaz.
"""

from typing import Protocol
from uuid import UUID

from src.modules.registry.domain.referencia import (
    Alteracao,
    DadosDaReferencia,
    Referencia,
    TipoDeReferencia,
)
from src.modules.registry.domain.unidade import DadosDaUnidade, Desativacao, Unidade


class RepositorioDeReferencias(Protocol):
    async def listar(self, tipo: TipoDeReferencia) -> list[Referencia]:
        """Todos os itens da tabela, ativos e desativados, pelo id (ordem da semeadura)."""
        ...

    async def area_ativa(self, area_tecnica_id: int) -> bool:
        """Se a área técnica existe e está ativa."""
        ...

    async def inserir(self, tipo: TipoDeReferencia, dados: DadosDaReferencia) -> Referencia | None:
        """Grava o item novo, ativo; `None`, sem gravar nada, se o código já é de outro."""
        ...

    async def atualizar(
        self, tipo: TipoDeReferencia, id_: int, mudancas: dict[str, object]
    ) -> Alteracao | None:
        """Aplica os campos enviados (nomes da API) e devolve o item antes e depois; `None` se
        ele não existe. Levanta `CodigoRepetidoError` se o código novo já é de outro item, sem
        mudar nada. No tipo de equipamento, o código novo chega aos equipamentos pela FK com ON
        UPDATE CASCADE (011/R9.2)."""
        ...

    async def equipamentos_do_tipo(self, codigo: str) -> int:
        """Quantos equipamentos apontam o tipo de equipamento com esse código."""
        ...


class RepositorioDeUnidades(Protocol):
    async def area_do_tipo(self, unit_type_id: int) -> int | None:
        """Área técnica do tipo de unidade; `None` se o tipo não existe ou está desativado."""
        ...

    async def distrito_ativo(self, district_id: int) -> bool:
        """Se o distrito sanitário existe e está ativo."""
        ...

    async def inserir(self, id_: UUID, dados: DadosDaUnidade) -> Unidade | None:
        """Grava a unidade nova e a devolve como ficou no banco; `None`, sem gravar nada, se ela
        repete o CNES ou o nome normalizado no distrito de outra (006/R1.3, R1.4)."""
        ...

    async def com_cnes(self, cnes: str) -> UUID | None:
        """Id da unidade com esse CNES, se houver."""
        ...

    async def com_nome(self, district_id: int, name: str) -> UUID | None:
        """Id da unidade do distrito com o mesmo nome normalizado (`fn_normalize_name`), se
        houver."""
        ...

    async def desativar(self, id_: UUID) -> Desativacao | None:
        """Desativa a unidade pelo procedimento do banco, `sp_deactivate_unit` (011/R6.1), e a
        devolve como ficou, com o status que tinha. Já inativa, nada muda (011/R6.4). `None` se
        ela não existe (011/R6.3)."""
        ...
