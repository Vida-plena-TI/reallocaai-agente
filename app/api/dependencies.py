"""Dependências FastAPI: autenticação por API key e injeção das fontes de dados.

As instâncias de `ScheduleDataSource`/`ContinuidadeDataSource` são construídas
no `lifespan` de `app.main` (nunca em import-time) e guardadas em
`app.state`. `obter_fonte`/`obter_continuidade` só leem de lá, o que permite
substituí-las nos testes via `app.dependency_overrides`, sem tocar em
credencial real do Google.
"""

from typing import Annotated, cast

from fastapi import Depends, Header, HTTPException, Request, status

from app.config import Settings, exigir, get_settings
from app.data_sources.base import ScheduleDataSource
from app.data_sources.continuidade import ContinuidadeDataSource


def validar_api_key(
    settings: Annotated[Settings, Depends(get_settings)],
    x_api_key: Annotated[str | None, Header()] = None,
) -> None:
    """Exige o header `X-API-Key` igual a `settings.internal_api_key`.

    `internal_api_key` é `None` em `Settings` até aqui — validado só neste
    ponto de uso, não na classe, para `import app.main` continuar funcionando
    sem nenhum segredo configurado (ver `exigir`). Sem configuração é um
    problema operacional (500), diferente de uma chave errada (401).
    """
    try:
        chave_esperada = exigir(settings.internal_api_key, "INTERNAL_API_KEY")
    except ValueError as erro:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(erro)
        ) from erro

    if not x_api_key or x_api_key != chave_esperada:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-API-Key ausente ou inválida.",
        )


def obter_fonte(request: Request) -> ScheduleDataSource:
    """`ScheduleDataSource` construída no `lifespan` da aplicação (`app.state`)."""
    return cast(ScheduleDataSource, request.app.state.fonte_agenda)


def obter_continuidade(request: Request) -> ContinuidadeDataSource:
    """`ContinuidadeDataSource` construída no `lifespan` da aplicação (`app.state`)."""
    return cast(ContinuidadeDataSource, request.app.state.continuidade)
