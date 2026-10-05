"""Testes de `/agenda/chat` e `/agenda/chat/{conversa_id}` (Fase 6b).

Usa `FakeToolCallingChatModel` (`tests/support/fake_chat_model.py`) via
override de `obter_chat_model` — nenhum teste aqui chama a API real da
OpenAI.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatResult

from app.api.dependencies import obter_chat_model
from app.main import app
from tests.api.conftest import HEADERS_AUTENTICADOS
from tests.support.fake_chat_model import FakeToolCallingChatModel


class _ModeloComErro(FakeToolCallingChatModel):
    """Chat model falso que sempre levanta erro, para simular falha do modelo/rede."""

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise RuntimeError("segredo interno: chave de API, stack trace")


def test_primeira_mensagem_sem_conversa_id_cria_conversa_nova(client: TestClient) -> None:
    resposta = client.post(
        "/agenda/chat",
        json={"mensagem": "Como está a agenda hoje?"},
        headers=HEADERS_AUTENTICADOS,
    )

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["conversa_id"]
    assert corpo["resposta"] == "Resposta padrão do agente de teste."


def test_segunda_mensagem_com_mesmo_conversa_id_reaproveita_o_historico(
    client: TestClient,
) -> None:
    primeira = client.post(
        "/agenda/chat", json={"mensagem": "Primeira pergunta."}, headers=HEADERS_AUTENTICADOS
    )
    conversa_id = primeira.json()["conversa_id"]

    segunda = client.post(
        "/agenda/chat",
        json={"conversa_id": conversa_id, "mensagem": "Segunda pergunta."},
        headers=HEADERS_AUTENTICADOS,
    )

    assert segunda.status_code == 200
    assert segunda.json()["conversa_id"] == conversa_id

    historico = client.get(f"/agenda/chat/{conversa_id}", headers=HEADERS_AUTENTICADOS)
    assert historico.status_code == 200
    mensagens = historico.json()
    assert [item["conteudo"] for item in mensagens] == [
        "Primeira pergunta.",
        "Resposta padrão do agente de teste.",
        "Segunda pergunta.",
        "Resposta padrão do agente de teste.",
    ]
    assert [item["papel"] for item in mensagens] == ["usuario", "agente", "usuario", "agente"]


def test_conversa_id_inexistente_retorna_404_com_mensagem_clara(client: TestClient) -> None:
    resposta = client.post(
        "/agenda/chat",
        json={"conversa_id": "id-que-nao-existe", "mensagem": "Oi"},
        headers=HEADERS_AUTENTICADOS,
    )

    assert resposta.status_code == 404
    assert "não encontrada ou expirada" in resposta.json()["detail"]


@pytest.mark.parametrize("mensagem", ["", "   "])
def test_mensagem_vazia_ou_em_branco_retorna_422(client: TestClient, mensagem: str) -> None:
    resposta = client.post(
        "/agenda/chat", json={"mensagem": mensagem}, headers=HEADERS_AUTENTICADOS
    )

    assert resposta.status_code == 422


def test_erro_no_agente_retorna_502_sem_vazar_detalhes_internos(client: TestClient) -> None:
    app.dependency_overrides[obter_chat_model] = lambda: _ModeloComErro(responses=[])

    resposta = client.post(
        "/agenda/chat", json={"mensagem": "Vai dar erro"}, headers=HEADERS_AUTENTICADOS
    )

    assert resposta.status_code == 502
    detalhe = resposta.json()["detail"]
    assert detalhe == "erro ao processar a conversa com o agente, tente novamente"
    assert "segredo interno" not in detalhe
    assert "RuntimeError" not in detalhe


def test_get_historico_de_conversa_devolve_mensagens_esperadas(client: TestClient) -> None:
    criacao = client.post(
        "/agenda/chat", json={"mensagem": "Pergunta única."}, headers=HEADERS_AUTENTICADOS
    )
    conversa_id = criacao.json()["conversa_id"]

    resposta = client.get(f"/agenda/chat/{conversa_id}", headers=HEADERS_AUTENTICADOS)

    assert resposta.status_code == 200
    assert resposta.json() == [
        {"papel": "usuario", "conteudo": "Pergunta única.", "blocos": []},
        {"papel": "agente", "conteudo": "Resposta padrão do agente de teste.", "blocos": []},
    ]


def test_get_historico_de_conversa_inexistente_retorna_404(client: TestClient) -> None:
    resposta = client.get("/agenda/chat/id-que-nao-existe", headers=HEADERS_AUTENTICADOS)

    assert resposta.status_code == 404
    assert "não encontrada ou expirada" in resposta.json()["detail"]


def test_post_chat_retorna_401_sem_api_key(client: TestClient) -> None:
    resposta = client.post("/agenda/chat", json={"mensagem": "Oi"})

    assert resposta.status_code == 401


def test_get_historico_retorna_401_sem_api_key(client: TestClient) -> None:
    resposta = client.get("/agenda/chat/qualquer-id")

    assert resposta.status_code == 401
