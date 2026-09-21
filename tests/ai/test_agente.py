"""Testes do agente RealocAI (Fase 5b), sem nenhuma chamada real à OpenAI.

Usa `FakeToolCallingChatModel` (`tests/support/fake_chat_model.py`), que
roteiriza as respostas do "modelo": a primeira emite uma chamada de tool
simulada, a segunda devolve o texto final — validando que a fiação entre
`create_agent`, as tools (Parte B) e o prompt de sistema (Parte C) está
correta, independente de qualquer qualidade de modelo real.
"""

from datetime import date, time

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.ai.agente import criar_agente, perguntar
from app.data_sources.base import EntradaGrade
from app.data_sources.continuidade import SemHistoricoContinuidadeDataSource
from app.domain import Atendimento, Especialidade, Profissional, Slot
from tests.support.fake_chat_model import FakeToolCallingChatModel
from tests.support.fake_schedule_data_source import FakeScheduleDataSource

DIA = date(2026, 9, 8)


def grade_completa(
    sala_id: str, profissional_id: str, especialidade: Especialidade
) -> list[EntradaGrade]:
    return [
        EntradaGrade(
            sala_id=sala_id,
            profissional_id=profissional_id,
            especialidade=especialidade,
            slot=slot,
        )
        for slot in Slot.slots_do_dia(DIA)
    ]


def test_perguntar_executa_o_ciclo_de_tool_call_e_retorna_o_texto_final() -> None:
    origem = FakeScheduleDataSource()
    continuidade = SemHistoricoContinuidadeDataSource()

    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "consultar_ocupacao", "args": {"data": "2026-09-08"}, "id": "call-1"}
                ],
            ),
            AIMessage(content="Não há nenhuma escala registrada para hoje."),
        ]
    )

    agente = criar_agente(origem, continuidade, fake_model, DIA)
    resposta = perguntar(agente, [HumanMessage("Como está a ocupação hoje?")])

    assert resposta == "Não há nenhuma escala registrada para hoje."


def test_agente_chama_a_tool_real_com_os_argumentos_da_llm() -> None:
    """A `ToolMessage` do meio do ciclo carrega o resultado real da tool.

    Prova que a fiação passa pela camada de serviço de verdade (Fase 5a): o
    texto abaixo só existe se `consultar_ocupacao_tool` tiver de fato lido
    `origem` e feito as contas de ocupação, não é algo que o script de
    respostas do modelo falso poderia inventar sozinho.
    """
    original = Atendimento(
        id="at-1",
        paciente_ids=["pac-1"],
        profissional_id="prof-1",
        sala_id="sala-1",
        especialidade=Especialidade.PSICOLOGIA,
        slots=[Slot(data=DIA, hora_inicio=time(8, 0))],
    )
    ana = Profissional(id="prof-1", nome="Ana", especialidade=Especialidade.PSICOLOGIA)
    origem = FakeScheduleDataSource(
        grade={DIA: grade_completa("sala-1", "prof-1", Especialidade.PSICOLOGIA)},
        profissionais={DIA: [ana]},
        atendimentos={DIA: [original]},
    )
    continuidade = SemHistoricoContinuidadeDataSource()

    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "consultar_ocupacao", "args": {"data": "2026-09-08"}, "id": "call-1"}
                ],
            ),
            AIMessage(content="A ocupação de psicologia está abaixo da meta hoje."),
        ]
    )

    agente = criar_agente(origem, continuidade, fake_model, DIA)
    resultado = agente.invoke({"messages": [HumanMessage("Como está a ocupação hoje?")]})

    mensagens_de_tool = [
        mensagem for mensagem in resultado["messages"] if isinstance(mensagem, ToolMessage)
    ]
    assert len(mensagens_de_tool) == 1
    assert "abaixo da meta de 80%" in mensagens_de_tool[0].content
