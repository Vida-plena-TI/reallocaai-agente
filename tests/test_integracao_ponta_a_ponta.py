"""Teste de integração ponta a ponta (Fase 8).

Diferente dos testes de unidade de cada camada (`tests/ai/test_tools.py`,
`tests/engine/test_encaixe.py`, `tests/api/test_chat.py` etc.), o objetivo
aqui é provar que a costura entre elas está correta: `POST /agenda/chat` ->
`app/ai/agente.py` -> as tools (`app/ai/tools.py`) -> a camada de serviço
(`app/ai/servico_agenda.py`) -> a engine (`app/engine/...`) -> o dublê de
`ScheduleDataSource`, tudo no mesmo processo, sem nenhuma chamada de rede real
(nem OpenAI/Google, nem Google Sheets).

A parte delicada é provar que a resposta HTTP final reflete o dado *real* do
dublê, e não um texto combinado de antemão no roteiro do "modelo" — um chat
model falso comum (`FakeToolCallingChatModel`, como em `tests/ai/test_agente.py`)
teria a resposta final fixa no script, então bater com o dado esperado não
provaria que a costura está certa. `_ModeloQueEcoaOResultadoDaTool` resolve
isso: ele só roteiriza a *chamada* da tool; a resposta final é sintetizada a
partir do conteúdo real da `ToolMessage` que a tool devolveu (é o
`create_agent`, dentro de `criar_agente`, quem invoca a tool de verdade e
injeta esse resultado na conversa) — então os fatos que aparecem na resposta
HTTP (nome do profissional, sala, horário) só chegam lá se toda a cadeia de
camadas tiver rodado de verdade.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, time
from typing import Any

from fastapi.testclient import TestClient
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.api.dependencies import (
    obter_armazenamento_conversas,
    obter_chat_model,
    obter_continuidade,
    obter_fonte,
)
from app.api.sessoes import ArmazenamentoConversas
from app.config import Settings, get_settings
from app.domain import EntradaGrade, Especialidade, Paciente, Profissional, Sala, Slot
from app.main import app
from tests.support.fake_chat_model import FakeToolCallingChatModel
from tests.support.fake_continuidade_data_source import FakeContinuidadeDataSource
from tests.support.fake_schedule_data_source import FakeScheduleDataSource

API_KEY = "chave-de-teste-integracao"
HEADERS_AUTENTICADOS = {"X-API-Key": API_KEY}
DIA = date(2026, 9, 8)


class _ModeloQueEcoaOResultadoDaTool(FakeToolCallingChatModel):
    """Roteiriza só a chamada de tool (via `responses`); a resposta final não
    é um texto combinado de antemão — ela ecoa o conteúdo real da
    `ToolMessage` que a tool devolveu, para provar que o dado exibido veio de
    fato da fonte, passando pela engine e pelo serviço."""

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        ultima = messages[-1]
        if isinstance(ultima, ToolMessage):
            resposta = AIMessage(content=f"Segundo a agenda: {ultima.content}")
            return ChatResult(generations=[ChatGeneration(message=resposta)])
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def _entrada(
    sala_id: str, profissional_id: str, especialidade: Especialidade, hora: time
) -> EntradaGrade:
    return EntradaGrade(
        sala_id=sala_id,
        profissional_id=profissional_id,
        especialidade=especialidade,
        slot=Slot(data=DIA, hora_inicio=hora),
        indice_posto=0,
    )


@contextmanager
def _client_com(
    fonte: FakeScheduleDataSource, chat_model: FakeToolCallingChatModel
) -> Iterator[TestClient]:
    """Monta um `TestClient` com as quatro dependencies de dados/IA substituídas
    por dublês (mesmo padrão de `tests/api/conftest.py`), sem tocar no
    `lifespan` real nem exigir `INTERNAL_API_KEY` verdadeira."""
    continuidade = FakeContinuidadeDataSource()
    conversas = ArmazenamentoConversas()
    app.dependency_overrides[obter_fonte] = lambda: fonte
    app.dependency_overrides[obter_continuidade] = lambda: continuidade
    app.dependency_overrides[obter_armazenamento_conversas] = lambda: conversas
    app.dependency_overrides[obter_chat_model] = lambda: chat_model
    app.dependency_overrides[get_settings] = lambda: Settings(
        openai_model="gpt-4o-mini", internal_api_key=API_KEY
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_chat_consultar_disponibilidade_reflete_o_dado_real_do_dublê() -> None:
    """API -> agente -> `consultar_disponibilidade_tool` -> `consultar_disponibilidade_do_dia`
    (`app/ai/servico_agenda.py`) -> `listar_disponibilidade` (`app/engine/disponibilidade.py`,
    Fase 4a) -> o dublê de `ScheduleDataSource`."""
    fonte = FakeScheduleDataSource(
        salas={DIA: [Sala(id="sala-1", nome="Sala 1")]},
        grade={DIA: [_entrada("sala-1", "prof-1", Especialidade.PSICOLOGIA, time(9, 0))]},
        profissionais={
            DIA: [Profissional(id="prof-1", nome="Ana", especialidade=Especialidade.PSICOLOGIA)]
        },
    )
    chat_model = _ModeloQueEcoaOResultadoDaTool(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "consultar_disponibilidade",
                        "args": {"data": DIA.isoformat()},
                        "id": "call-1",
                    }
                ],
            )
        ]
    )

    with _client_com(fonte, chat_model) as client:
        resposta = client.post(
            "/agenda/chat",
            json={"mensagem": "Tem vaga de psicologia hoje?"},
            headers=HEADERS_AUTENTICADOS,
        )

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["conversa_id"]
    # Nenhum destes fatos está em texto fixo no teste: só existem porque a
    # tool leu `fonte` de verdade e a engine calculou a disponibilidade real.
    assert "09:00 às 09:30" in corpo["resposta"]
    assert "Ana" in corpo["resposta"]
    assert "Sala 1" in corpo["resposta"]
    assert "Psicologia" in corpo["resposta"]


def test_chat_buscar_encaixe_reflete_o_dado_real_do_dublê_e_reaproveita_historico() -> None:
    """API -> agente -> `buscar_encaixe_tool` -> `buscar_encaixe` (`app/ai/servico_agenda.py`)
    -> `buscar_melhor_encaixe` (`app/engine/encaixe.py`, Fase 4b) -> o dublê de
    `ScheduleDataSource`. Também confirma que o `conversa_id` devolvido permite uma
    segunda chamada reaproveitando o histórico (mesmo padrão de
    `tests/api/test_chat.py::test_segunda_mensagem_com_mesmo_conversa_id_reaproveita_o_historico`)."""
    paciente = Paciente(id="paciente-um", nome="Paciente Um")
    fonte = FakeScheduleDataSource(
        pacientes={DIA: [paciente]},
        salas={DIA: [Sala(id="sala-1", nome="Sala 1")]},
        grade={DIA: [_entrada("sala-1", "prof-1", Especialidade.PSICOLOGIA, time(9, 0))]},
        profissionais={
            DIA: [Profissional(id="prof-1", nome="Ana", especialidade=Especialidade.PSICOLOGIA)]
        },
    )
    chat_model = _ModeloQueEcoaOResultadoDaTool(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "buscar_encaixe",
                        "args": {
                            "paciente": "Paciente Um",
                            "data": DIA.isoformat(),
                            "itens": [{"especialidade": "psicologia", "duracao_minutos": 30}],
                            "horario_desejado": "09:00",
                        },
                        "id": "call-1",
                    }
                ],
            )
        ]
    )

    with _client_com(fonte, chat_model) as client:
        primeira = client.post(
            "/agenda/chat",
            json={"mensagem": "Tem encaixe de psicologia pro Paciente Um hoje às 9h?"},
            headers=HEADERS_AUTENTICADOS,
        )
        conversa_id = primeira.json()["conversa_id"]

        segunda = client.post(
            "/agenda/chat",
            json={"conversa_id": conversa_id, "mensagem": "Confirma de novo?"},
            headers=HEADERS_AUTENTICADOS,
        )

        historico = client.get(f"/agenda/chat/{conversa_id}", headers=HEADERS_AUTENTICADOS)

    assert primeira.status_code == 200
    corpo_primeira = primeira.json()
    # De novo: "Ana", "Sala 1" e "09:00 às 09:30" só aparecem porque a tool
    # rodou de verdade contra `fonte` e a engine encontrou o horário exato.
    assert "Horário encontrado" in corpo_primeira["resposta"]
    assert "09:00 às 09:30" in corpo_primeira["resposta"]
    assert "Ana" in corpo_primeira["resposta"]
    assert "Sala 1" in corpo_primeira["resposta"]

    assert segunda.status_code == 200
    assert segunda.json()["conversa_id"] == conversa_id

    assert historico.status_code == 200
    mensagens = historico.json()
    assert [item["papel"] for item in mensagens] == ["usuario", "agente", "usuario", "agente"]
    assert mensagens[0]["conteudo"] == "Tem encaixe de psicologia pro Paciente Um hoje às 9h?"
    assert mensagens[1]["conteudo"] == corpo_primeira["resposta"]
    assert mensagens[2]["conteudo"] == "Confirma de novo?"
    assert mensagens[3]["conteudo"] == segunda.json()["resposta"]
