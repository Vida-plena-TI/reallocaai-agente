"""Dependências FastAPI: autenticação por API key e injeção das fontes de dados.

As instâncias de `ScheduleDataSource`/`ContinuidadeDataSource` são construídas
no `lifespan` de `app.main` (nunca em import-time) e guardadas em
`app.state`. `obter_fonte`/`obter_continuidade` só leem de lá, o que permite
substituí-las nos testes via `app.dependency_overrides`, sem tocar em
credencial real do Google.

O chat model (Fase 6b) é diferente: `obter_chat_model` o constrói sob
demanda, na primeira requisição que precisar dele (ver a própria função).
"""

import secrets
import threading
from typing import Annotated, cast

from fastapi import Depends, Header, HTTPException, Request, status
from langchain_core.language_models import BaseChatModel

from app.ai.agente import criar_chat_model
from app.api.sessoes import ArmazenamentoConversas
from app.config import Settings, exigir, get_settings
from app.data_sources.continuidade import ContinuidadeDataSource
from app.domain import ScheduleDataSource

#: Protege a construção do chat model contra dupla inicialização quando duas
#: requisições concorrentes chegam antes da primeira terminar de construí-lo.
#: Um único lock de módulo basta: só há um chat model por processo.
_lock_chat_model = threading.Lock()


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

    # Comparação em tempo constante (em bytes: `compare_digest` com `str` só aceita ASCII).
    if not x_api_key or not secrets.compare_digest(
        x_api_key.encode("utf-8"), chave_esperada.encode("utf-8")
    ):
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


def obter_armazenamento_conversas(request: Request) -> ArmazenamentoConversas:
    """`ArmazenamentoConversas` construído no `lifespan` da aplicação (`app.state`)."""
    return cast(ArmazenamentoConversas, request.app.state.conversas)


def obter_chat_model(request: Request) -> BaseChatModel:
    """Chat model da OpenAI (`criar_chat_model`, Fase 5b), preguiçoso e único por processo.

    Diferente de `obter_fonte`/`obter_continuidade`, não é construído no
    `lifespan`: `criar_chat_model` exige `OPENAI_API_KEY`/`OPENAI_MODEL`
    configurados, e o `lifespan` roda mesmo em ambientes de teste sem esse
    segredo. Em vez disso, a primeira requisição que chega até esta
    dependency o constrói e guarda em `app.state`; chamadas seguintes só
    reaproveitam a instância já pronta. `_lock_chat_model` evita duas
    requisições concorrentes construírem duas instâncias na primeira
    chamada (double-checked locking: o caminho comum, já construído, nem
    chega a disputar o lock).
    """
    estado = request.app.state
    if hasattr(estado, "chat_model"):
        return cast(BaseChatModel, estado.chat_model)

    with _lock_chat_model:
        if not hasattr(estado, "chat_model"):
            try:
                estado.chat_model = criar_chat_model()
            except ValueError as erro:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(erro)
                ) from erro
        return cast(BaseChatModel, estado.chat_model)
