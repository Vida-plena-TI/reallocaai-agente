"""Blocos no POST/GET, seleção do prompt e histórico textual para o modelo."""

from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatResult
from pydantic import Field

from app.ai.prompts import PROMPT_SISTEMA, PROMPT_SISTEMA_COM_RELATORIOS
from app.ai.tools import DIAS_DA_SEMANA
from app.api.dependencies import obter_chat_model, obter_fonte
from app.main import app
from tests.api.conftest import HEADERS_AUTENTICADOS
from tests.support.agenda_semanal import SEGUNDA
from tests.support.fake_chat_model import FakeToolCallingChatModel
from tests.support.relatorios import fonte_relatorios
from tests.support.resend import configurar_resend


class _ModeloComCaptura(FakeToolCallingChatModel):
    entradas: list[list[BaseMessage]] = Field(default_factory=list)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.entradas.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


@pytest.mark.parametrize(
    "nome,args,tipo",
    [
        (
            "consultar_ocupacao_profissional",
            {"profissional": "Ana", "data": SEGUNDA.isoformat()},
            "ocupacao_profissional",
        ),
        (
            "consultar_pacientes_por_profissional",
            {"data": SEGUNDA.isoformat()},
            "pacientes_por_profissional",
        ),
        ("consultar_ocupacao", {"data": SEGUNDA.isoformat()}, "ocupacao_agregada"),
    ],
)
def test_post_get_e_historico_do_modelo(
    client: TestClient,
    nome: str,
    args: dict[str, Any],
    tipo: str,
) -> None:
    modelo = _ModeloComCaptura(
        responses=[
            AIMessage("", tool_calls=[{"name": nome, "args": args, "id": "c1"}]),
            AIMessage("Resumo da consulta."),
            AIMessage("Segunda resposta."),
        ]
    )
    app.dependency_overrides[obter_chat_model] = lambda: modelo
    app.dependency_overrides[obter_fonte] = fonte_relatorios
    resposta = client.post(
        "/agenda/chat",
        headers=HEADERS_AUTENTICADOS,
        json={"mensagem": "Consulta", "renderiza_relatorios": True},
    )
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["resposta"] == "Resumo da consulta."
    assert len(corpo["blocos"]) == 1 and corpo["blocos"][0]["tipo"] == tipo
    if tipo == "ocupacao_agregada":
        esperado = client.get(
            "/agenda/ocupacao",
            params={"data": SEGUNDA.isoformat()},
            headers=HEADERS_AUTENTICADOS,
        ).json()
        for chave in ("por_sala", "por_especialidade"):
            assert corpo["blocos"][0]["dados"][chave] == esperado[chave]
    conversa_id = corpo["conversa_id"]
    historico = client.get(f"/agenda/chat/{conversa_id}", headers=HEADERS_AUTENTICADOS).json()
    assert historico[0]["blocos"] == []
    assert historico[1]["blocos"] == corpo["blocos"]
    assert (
        client.post(
            "/agenda/chat",
            headers=HEADERS_AUTENTICADOS,
            json={"mensagem": "Continue", "conversa_id": conversa_id},
        ).json()["blocos"]
        == []
    )
    entrada = modelo.entradas[-1]
    assert not any(isinstance(m, ToolMessage) for m in entrada)
    assert [m.content for m in entrada if not isinstance(m, SystemMessage)] == [
        "Consulta",
        "Resumo da consulta.",
        "Continue",
    ]
    for mensagem in entrada:
        assert mensagem.additional_kwargs == {}
        assert "tabelas" not in str(mensagem.content)


@pytest.mark.parametrize("flag", [None, False, True])
def test_prompt_renderizado_na_chamada_da_api(client: TestClient, flag: bool | None) -> None:
    modelo = _ModeloComCaptura(responses=[AIMessage("Olá")])
    app.dependency_overrides[obter_chat_model] = lambda: modelo
    pedido: dict[str, Any] = {"mensagem": "Oi"}
    if flag is not None:
        pedido["renderiza_relatorios"] = flag
    assert client.post("/agenda/chat", headers=HEADERS_AUTENTICADOS, json=pedido).status_code == 200
    prompt = str(modelo.entradas[0][0].content)
    assert "não gera arquivos, não exporta dados" in prompt
    assert "NUNCA diga que exportou ou enviou um arquivo" in prompt
    assert ("Responda em 1 a 3 frases" in prompt) == (flag is True)
    assert ("mantendo a lista por dia e o total da semana" in prompt) == (flag is not True)
    assert ("mantendo o agrupamento por especialidade" in prompt) == (flag is not True)
    if flag is True:
        assert 'botões "Exportar"' in prompt
    else:
        assert "exportação não está disponível" in prompt


@pytest.mark.parametrize("renderiza", [False, True])
def test_sem_resend_o_prompt_diz_que_o_email_nao_esta_disponivel(
    client: TestClient, renderiza: bool
) -> None:
    modelo = _ModeloComCaptura(responses=[AIMessage("Olá")])
    app.dependency_overrides[obter_chat_model] = lambda: modelo
    pedido = {"mensagem": "Oi", "renderiza_relatorios": renderiza}
    assert client.post("/agenda/chat", headers=HEADERS_AUTENTICADOS, json=pedido).status_code == 200
    prompt = str(modelo.entradas[0][0].content)
    assert "envio do relatório por e-mail não está disponível neste ambiente" in prompt
    assert "responda em uma frase que o envio por e-mail" in prompt
    assert "enviar_relatorio" not in prompt
    assert "corpo do e-mail" not in prompt
    assert "não gera arquivos, não exporta dados" in prompt


@pytest.mark.parametrize("renderiza", [False, True])
def test_com_resend_o_prompt_mantem_o_texto_atual(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, renderiza: bool
) -> None:
    configurar_resend(monkeypatch, ativo=True)
    modelo = _ModeloComCaptura(responses=[AIMessage("Olá")])
    app.dependency_overrides[obter_chat_model] = lambda: modelo
    pedido = {"mensagem": "Oi", "renderiza_relatorios": renderiza}
    assert client.post("/agenda/chat", headers=HEADERS_AUTENTICADOS, json=pedido).status_code == 200
    prompt = str(modelo.entradas[0][0].content)
    variante = PROMPT_SISTEMA_COM_RELATORIOS if renderiza else PROMPT_SISTEMA
    hoje = date.today()
    assert prompt == variante.format(
        data_referencia=hoje.strftime("%d/%m/%Y"), dia_da_semana=DIAS_DA_SEMANA[hoje.weekday()]
    )
    assert "não está disponível neste ambiente" not in prompt
    assert "Só chame `enviar_relatorio`" in prompt
    if renderiza:
        assert "Não ofereça enviar o relatório por e-mail por conta própria" in prompt


def test_variante_com_renderizacao_tem_regras_de_brevidade_tools_e_email() -> None:
    com = PROMPT_SISTEMA_COM_RELATORIOS.format(
        data_referencia="05/10/2026", dia_da_semana="segunda-feira"
    )
    sem = PROMPT_SISTEMA.format(data_referencia="05/10/2026", dia_da_semana="segunda-feira")
    assert "no máximo 3 frases" in com
    assert "sem listas e sem tabelas" in com
    assert "Não chame tools extras" in com
    assert "Não ofereça enviar o relatório por e-mail por conta própria" in com
    assert "Exemplo de resposta boa" in com and "Exemplo de resposta ruim" in com
    assert "no máximo 3 frases" not in sem
    assert "Exemplo de resposta" not in sem
    assert "tools extras" not in sem


def test_variantes_renderizadas_e_openapi(client: TestClient) -> None:
    for variante in [PROMPT_SISTEMA, PROMPT_SISTEMA_COM_RELATORIOS]:
        prompt = variante.format(data_referencia="28/09/2026", dia_da_semana="segunda-feira")
        assert "não gera arquivos, não exporta dados" in prompt
        assert "NUNCA diga que exportou ou enviou um arquivo" in prompt
        assert "Não ofereça exportação por conta própria" in prompt
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    flag = schemas["ChatRequest"]["properties"]["renderiza_relatorios"]
    assert flag["default"] is False and "cliente" in flag["description"]
    assert "blocos" in schemas["ChatResponse"]["properties"]
    assert "blocos" in schemas["MensagemHistoricoResponse"]["properties"]
    assert (
        schemas["BlocoRelatorio"]["properties"]["dados"]["discriminator"]["propertyName"] == "tipo"
    )


def test_duas_tools_devolvem_dois_blocos_na_ordem(client: TestClient) -> None:
    modelo = _ModeloComCaptura(
        responses=[
            AIMessage(
                "",
                tool_calls=[
                    {
                        "name": "consultar_ocupacao_profissional",
                        "args": {"profissional": "Ana", "data": SEGUNDA.isoformat()},
                        "id": "c1",
                    },
                    {
                        "name": "consultar_pacientes_por_profissional",
                        "args": {"data": SEGUNDA.isoformat()},
                        "id": "c2",
                    },
                ],
            ),
            AIMessage("Dois relatórios."),
        ]
    )
    app.dependency_overrides[obter_chat_model] = lambda: modelo
    app.dependency_overrides[obter_fonte] = fonte_relatorios
    resposta = client.post(
        "/agenda/chat", headers=HEADERS_AUTENTICADOS, json={"mensagem": "Consulta"}
    )
    assert resposta.status_code == 200
    assert [b["tipo"] for b in resposta.json()["blocos"]] == [
        "ocupacao_profissional",
        "pacientes_por_profissional",
    ]


def test_tool_sem_resultado_devolve_texto_e_lista_vazia(client: TestClient) -> None:
    modelo = _ModeloComCaptura(
        responses=[
            AIMessage(
                "",
                tool_calls=[
                    {
                        "name": "consultar_ocupacao",
                        "args": {"data": SEGUNDA.isoformat()},
                        "id": "c1",
                    }
                ],
            ),
            AIMessage("Sem agenda."),
        ]
    )
    app.dependency_overrides[obter_chat_model] = lambda: modelo
    resposta = client.post(
        "/agenda/chat", headers=HEADERS_AUTENTICADOS, json={"mensagem": "Consulta"}
    )
    assert resposta.status_code == 200 and resposta.json()["blocos"] == []
