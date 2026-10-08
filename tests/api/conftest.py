"""Fixtures compartilhadas dos testes da API HTTP (Fase 6a/6b).

`client` sobrescreve `obter_fonte`/`obter_continuidade`/`obter_chat_model`/
`obter_armazenamento_conversas`/`get_settings` via `app.dependency_overrides`,
então nenhum teste aqui toca o `lifespan` real (que constrói
`GoogleSheetsDataSource` e `criar_chat_model`, exigindo credenciais reais) nem
exige `INTERNAL_API_KEY` real.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.api.dependencies import (
    obter_armazenamento_conversas,
    obter_chat_model,
    obter_continuidade,
    obter_fonte,
)
from app.api.sessoes import ArmazenamentoConversas
from app.config import Settings, get_settings
from app.main import app
from tests.support.fake_chat_model import FakeToolCallingChatModel
from tests.support.fake_continuidade_data_source import FakeContinuidadeDataSource
from tests.support.fake_schedule_data_source import FakeScheduleDataSource

#: Chave usada nos testes — nunca a `INTERNAL_API_KEY` real do `.env`.
API_KEY = "chave-de-teste"

HEADERS_AUTENTICADOS = {"X-API-Key": API_KEY}


#: Resend "configurado" com valores falsos: o envio real é sempre substituído nos
#: testes. Sem isso, `POST /relatorio/enviar` devolveria 503 ou não conforme o `.env`.
RESEND_API_KEY_TESTE = "re_test_key"
REPORT_EMAIL_FROM_TESTE = "relatorios@exemplo.com.br"


@pytest.fixture
def fonte() -> FakeScheduleDataSource:
    return FakeScheduleDataSource()


@pytest.fixture
def continuidade() -> FakeContinuidadeDataSource:
    return FakeContinuidadeDataSource()


@pytest.fixture
def conversas() -> ArmazenamentoConversas:
    return ArmazenamentoConversas()


@pytest.fixture
def chat_model() -> FakeToolCallingChatModel:
    """Chat model falso, sem chamada de rede — nunca a OpenAI real (ver `criar_chat_model`)."""
    return FakeToolCallingChatModel(
        responses=[AIMessage(content="Resposta padrão do agente de teste.")]
    )


@pytest.fixture
def client(
    fonte: FakeScheduleDataSource,
    continuidade: FakeContinuidadeDataSource,
    conversas: ArmazenamentoConversas,
    chat_model: FakeToolCallingChatModel,
) -> Iterator[TestClient]:
    app.dependency_overrides[obter_fonte] = lambda: fonte
    app.dependency_overrides[obter_continuidade] = lambda: continuidade
    app.dependency_overrides[obter_armazenamento_conversas] = lambda: conversas
    app.dependency_overrides[obter_chat_model] = lambda: chat_model
    app.dependency_overrides[get_settings] = lambda: Settings(
        openai_model="gpt-4o-mini",
        internal_api_key=API_KEY,
        resend_api_key=RESEND_API_KEY_TESTE,
        report_email_from=REPORT_EMAIL_FROM_TESTE,
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
