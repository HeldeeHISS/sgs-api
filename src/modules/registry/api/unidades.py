"""Rotas da unidade de saúde (design da 006 § Rotas)."""

import re
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response

from src.core.errors import PreconditionFailed, PreconditionRequired
from src.core.ids import uuid7
from src.core.problem import PROBLEM_JSON
from src.modules.access.api.deps import (
    ResourceRef,
    SessaoDeNegocio,
    UsuarioAtual,
    ator_da_requisicao,
    require,
)
from src.modules.registry.api.schemas import (
    UnidadeCreate,
    UnidadeDesativacao,
    UnidadeOut,
    UnidadeUpdate,
)
from src.modules.registry.application.unidades import (
    atualizar_unidade,
    criar_unidade,
    desativar_unidade,
)

router = APIRouter(prefix="/api/v1", tags=["unidades"])

# O `ETag` da unidade é a versão entre aspas, como a rota de cadastro o devolve (`"1"`).
_ETAG = re.compile(r'"([1-9][0-9]{0,9})"')


def _problema(descricao: str) -> dict[str, Any]:
    """Resposta de erro em problem+json (spec 001), para o contrato."""
    return {"description": descricao, "content": {PROBLEM_JSON: {}}}


@router.post(
    "/unidades",
    status_code=201,
    summary="Cadastrar unidade de saúde",
    responses={
        401: _problema("Sem access token válido"),
        403: _problema("Sem `unidade:create` na área técnica do tipo informado"),
        409: _problema("Já existe unidade com o mesmo CNES ou o mesmo nome no distrito"),
        422: _problema("Campos inválidos, ou tipo de unidade ou distrito inexistente"),
    },
)
async def criar(
    request: Request,
    response: Response,
    corpo: UnidadeCreate,
    usuario: Annotated[UsuarioAtual, Depends(require("unidade:create"))],
    sessao: SessaoDeNegocio,
) -> UnidadeOut:
    """006/R1. Responde com a unidade criada, o `Location` dela e o `ETag` da versão."""
    estado = request.app.state
    unidade = await criar_unidade(
        corpo.dados(),
        novo_id=uuid7(),
        usuario=usuario,
        ator=ator_da_requisicao(request, usuario),
        unidades=estado.repositorio_de_unidades(sessao),
        auditoria=estado.auditoria(sessao),
    )
    response.headers["Location"] = f"/api/v1/unidades/{unidade.id}"
    response.headers["ETag"] = f'"{unidade.version}"'
    return UnidadeOut.de(unidade)


@router.patch(
    "/unidades/{id}",
    summary="Atualizar unidade de saúde",
    responses={
        401: _problema("Sem access token válido"),
        403: _problema(
            "Sem `unidade:update` no escopo da unidade, ou alteração de distrito, tipo ou CNES "
            "sem escopo global ou na área técnica (os campos vêm em `campos`)"
        ),
        404: _problema("A unidade não existe"),
        409: _problema("Já existe unidade com o mesmo CNES ou o mesmo nome no distrito"),
        412: _problema("A unidade mudou depois da versão informada em `If-Match`"),
        422: _problema("Campos inválidos, ou tipo de unidade ou distrito inexistente"),
        428: _problema("Falta o cabeçalho `If-Match`"),
    },
)
async def atualizar(
    request: Request,
    response: Response,
    id: UUID,
    corpo: UnidadeUpdate,
    usuario: Annotated[UsuarioAtual, Depends(require("unidade:update", ResourceRef("unidade")))],
    sessao: SessaoDeNegocio,
    if_match: Annotated[
        str | None,
        Header(
            alias="If-Match",
            description='A versão em mãos, como veio no `ETag` (ex.: `"3"`). Sem ele, 428.',
        ),
    ] = None,
) -> UnidadeOut:
    """006/R3, 011/R8.3. Aplica só os campos enviados e responde com a unidade e o `ETag` da
    versão nova, que o banco incrementa. Sem mudança, responde igual, sem alterar nem auditar."""
    estado = request.app.state
    unidade = await atualizar_unidade(
        id,
        corpo.alteracoes(),
        versao_esperada=_versao_esperada(if_match),
        usuario=usuario,
        ator=ator_da_requisicao(request, usuario),
        unidades=estado.repositorio_de_unidades(sessao),
        auditoria=estado.auditoria(sessao),
    )
    response.headers["ETag"] = f'"{unidade.version}"'
    return UnidadeOut.de(unidade)


def _versao_esperada(if_match: str | None) -> int:
    """A versão do `If-Match` (006/R3.2). Sem o cabeçalho, 428; com um valor que não é o `ETag`
    de uma versão (`*`, lista, `W/`), 412: ele não casa com a versão atual."""
    if if_match is None:
        raise PreconditionRequired("Envie If-Match com o ETag da versão que você leu.")
    casou = _ETAG.fullmatch(if_match.strip())
    if casou is None:
        raise PreconditionFailed("If-Match não corresponde a uma versão da unidade.")
    return int(casou.group(1))


@router.post(
    "/unidades/{id}/desativar",
    summary="Desativar unidade de saúde",
    responses={
        401: _problema("Sem access token válido"),
        403: _problema("Sem `unidade:deactivate` no escopo da unidade"),
        404: _problema("A unidade não existe"),
        422: _problema("Motivo ausente ou inválido"),
    },
)
async def desativar(
    request: Request,
    response: Response,
    id: UUID,
    corpo: UnidadeDesativacao,
    usuario: Annotated[
        UsuarioAtual, Depends(require("unidade:deactivate", ResourceRef("unidade")))
    ],
    sessao: SessaoDeNegocio,
) -> UnidadeOut:
    """006/R4, 011/R6.1 a R6.4. Responde com a unidade inativa e o `ETag` da versão. Já inativa,
    responde igual, sem alterar nada nem auditar."""
    estado = request.app.state
    unidade = await desativar_unidade(
        id,
        motivo=corpo.motivo,
        ator=ator_da_requisicao(request, usuario),
        unidades=estado.repositorio_de_unidades(sessao),
        auditoria=estado.auditoria(sessao),
    )
    response.headers["ETag"] = f'"{unidade.version}"'
    return UnidadeOut.de(unidade)
