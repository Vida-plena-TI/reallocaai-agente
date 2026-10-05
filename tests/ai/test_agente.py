"""Testes do agente RealocAI (Fase 5b), sem nenhuma chamada real à OpenAI.

Usa `FakeToolCallingChatModel` (`tests/support/fake_chat_model.py`), que
roteiriza as respostas do "modelo": a primeira emite uma chamada de tool
simulada, a segunda devolve o texto final — validando que a fiação entre
`create_agent`, as tools (Parte B) e o prompt de sistema (Parte C) está
correta, independente de qualquer qualidade de modelo real.

Os testes de `criar_chat_model` (suporte a dois provedores de IA, Fase 8)
validam só a lógica de seleção/validação de configuração, via monkeypatch em
`app.ai.agente.get_settings` — nunca constroem um chat model de verdade nem
fazem chamada de rede, mesmo padrão já usado em `tests/reports/test_envio.py`.
"""

from datetime import date, time
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.ai.agente import _extrair_texto_da_resposta, criar_agente, criar_chat_model, perguntar
from app.ai.prompts import PROMPT_SISTEMA
from app.config import Settings
from app.data_sources.continuidade import SemHistoricoContinuidadeDataSource
from app.domain import Atendimento, EntradaGrade, Especialidade, Profissional, Slot
from tests.support.agenda_semanal import LUCIANA, SEGUNDA, atendimentos, grade, horas_da_manha
from tests.support.fake_chat_model import FakeToolCallingChatModel
from tests.support.fake_schedule_data_source import FakeScheduleDataSource

DIA = date(2026, 9, 8)


def _enviar_relatorio_nao_usado(fonte: Any, data: date, destinatarios: list[str] | None) -> None:
    """Nenhum teste deste arquivo exercita a tool `enviar_relatorio`."""
    raise AssertionError("enviar_relatorio não deveria ter sido chamado neste teste")


def _settings(**overrides: Any) -> Settings:
    """`Settings` com os campos de IA em branco, sobrepostos por `overrides`.

    Precisa zerar `ai_provider`/`openai_*`/`google_*` explicitamente: sem
    isso, valores presentes no `.env` local (lido pela config real) vazariam
    para os testes, mascarando os cenários "variável não configurada".
    """
    base: dict[str, Any] = {
        "ai_provider": None,
        "openai_api_key": None,
        "openai_model": None,
        "google_api_key": None,
        "google_model": None,
    }
    base.update(overrides)
    return Settings(**base)


def grade_completa(
    sala_id: str, profissional_id: str, especialidade: Especialidade
) -> list[EntradaGrade]:
    return [
        EntradaGrade(
            sala_id=sala_id,
            profissional_id=profissional_id,
            especialidade=especialidade,
            slot=slot,
            indice_posto=0,
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

    agente = criar_agente(origem, continuidade, fake_model, DIA, _enviar_relatorio_nao_usado)
    resposta = perguntar(agente, [HumanMessage("Como está a ocupação hoje?")])

    assert resposta == "Não há nenhuma escala registrada para hoje."


def test_extrair_texto_da_resposta_com_content_string() -> None:
    mensagem = AIMessage(content="Não há nenhuma escala registrada para hoje.")

    assert _extrair_texto_da_resposta(mensagem) == "Não há nenhuma escala registrada para hoje."


def test_extrair_texto_da_resposta_com_content_lista_de_um_bloco_de_texto() -> None:
    """Formato usado por alguns provedores (ex: Gemini): `content` vira lista de blocos.

    Sem a normalização, o resultado seria a `repr` do dicionário/lista Python
    (com chaves e colchetes) em vez do texto puro — daí a asserção explícita
    de que nenhum desses caracteres de estrutura sobra na resposta.
    """
    mensagem = AIMessage(content=[{"type": "text", "text": "A ocupação está estável hoje."}])

    resultado = _extrair_texto_da_resposta(mensagem)

    assert resultado == "A ocupação está estável hoje."
    assert "{" not in resultado
    assert "[" not in resultado


def test_extrair_texto_da_resposta_ignora_bloco_que_nao_e_de_texto() -> None:
    mensagem = AIMessage(
        content=[
            {"type": "text", "text": "A ocupação está estável hoje."},
            {"extras": {"signature": "assinatura-interna-do-provedor"}},
        ]
    )

    resultado = _extrair_texto_da_resposta(mensagem)

    assert resultado == "A ocupação está estável hoje."
    assert "assinatura-interna-do-provedor" not in resultado


def test_extrair_texto_da_resposta_concatena_multiplos_blocos_de_texto() -> None:
    mensagem = AIMessage(
        content=[
            {"type": "text", "text": "A ocupação está estável hoje."},
            {"type": "text", "text": "Nenhuma sala vaga no período da tarde."},
        ]
    )

    resultado = _extrair_texto_da_resposta(mensagem)

    assert "A ocupação está estável hoje." in resultado
    assert "Nenhuma sala vaga no período da tarde." in resultado


def test_extrair_texto_da_resposta_sem_bloco_de_texto_devolve_string_vazia() -> None:
    mensagem = AIMessage(content=[{"extras": {"signature": "assinatura-interna-do-provedor"}}])

    assert _extrair_texto_da_resposta(mensagem) == ""


def test_perguntar_com_content_estruturado_devolve_so_o_texto() -> None:
    """Ponta a ponta via `perguntar`, com o "modelo" simulando o formato do Gemini."""
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
            AIMessage(
                content=[
                    {"type": "text", "text": "Não há nenhuma escala registrada para hoje."},
                    {"extras": {"signature": "assinatura-interna-do-provedor"}},
                ]
            ),
        ]
    )

    agente = criar_agente(origem, continuidade, fake_model, DIA, _enviar_relatorio_nao_usado)
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

    agente = criar_agente(origem, continuidade, fake_model, DIA, _enviar_relatorio_nao_usado)
    resultado = agente.invoke({"messages": [HumanMessage("Como está a ocupação hoje?")]})

    mensagens_de_tool = [
        mensagem for mensagem in resultado["messages"] if isinstance(mensagem, ToolMessage)
    ]
    assert len(mensagens_de_tool) == 1
    assert "abaixo da meta de 80%" in mensagens_de_tool[0].content


def test_criar_chat_model_sem_ai_provider_levanta_erro_claro(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.ai.agente.get_settings", lambda: _settings())

    with pytest.raises(ValueError, match="AI_PROVIDER"):
        criar_chat_model()


def test_criar_chat_model_openai_sem_api_key_levanta_erro_claro(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.ai.agente.get_settings",
        lambda: _settings(ai_provider="openai", openai_model="gpt-4o-mini"),
    )

    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        criar_chat_model()


def test_criar_chat_model_openai_sem_model_levanta_erro_claro(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.ai.agente.get_settings",
        lambda: _settings(ai_provider="openai", openai_api_key="sk-fake"),
    )

    with pytest.raises(ValueError, match="OPENAI_MODEL"):
        criar_chat_model()


def test_criar_chat_model_google_sem_api_key_levanta_erro_claro(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.ai.agente.get_settings",
        lambda: _settings(ai_provider="google", google_model="gemini-2.5-flash"),
    )

    with pytest.raises(ValueError, match="GOOGLE_API_KEY"):
        criar_chat_model()


def test_criar_chat_model_google_sem_model_levanta_erro_claro(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.ai.agente.get_settings",
        lambda: _settings(ai_provider="google", google_api_key="fake-key"),
    )

    with pytest.raises(ValueError, match="GOOGLE_MODEL"):
        criar_chat_model()


def test_criar_chat_model_provider_invalido_levanta_erro_claro(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.ai.agente.get_settings", lambda: _settings(ai_provider="azure"))

    with pytest.raises(ValueError, match='"openai" ou "google"'):
        criar_chat_model()


def test_encaixe_com_paciente_fora_da_agenda_termina_com_resposta_final() -> None:
    """Paciente sem atendimento no dia não interrompe o encaixe: a tool devolve
    a vaga (com a observação informativa) e o ciclo chega à resposta final."""
    ana = Profissional(id="prof-1", nome="Ana", especialidade=Especialidade.PSICOLOGIA)
    origem = FakeScheduleDataSource(
        grade={DIA: grade_completa("sala-1", "prof-1", Especialidade.PSICOLOGIA)},
        profissionais={DIA: [ana]},
    )
    continuidade = SemHistoricoContinuidadeDataSource()

    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "buscar_encaixe",
                        "args": {
                            "paciente": "Maria Nova",
                            "data": "2026-09-08",
                            "itens": [{"especialidade": "psicologia", "duracao_minutos": 60}],
                            "horario_desejado": "10:00",
                        },
                        "id": "call-1",
                    }
                ],
            ),
            AIMessage(content="Dá para encaixar Maria Nova às 10:00 com Ana."),
        ]
    )

    agente = criar_agente(origem, continuidade, fake_model, DIA, _enviar_relatorio_nao_usado)
    resultado = agente.invoke(
        {"messages": [HumanMessage("Consigo encaixar a Maria Nova em psicologia às 10h?")]}
    )

    mensagens_de_tool = [
        mensagem for mensagem in resultado["messages"] if isinstance(mensagem, ToolMessage)
    ]
    assert len(mensagens_de_tool) == 1
    assert "Horário encontrado" in mensagens_de_tool[0].content
    assert "não tem atendimentos na agenda" in mensagens_de_tool[0].content
    assert resultado["messages"][-1].content == "Dá para encaixar Maria Nova às 10:00 com Ana."


def test_prompt_de_sistema_diz_que_nao_existe_cadastro_de_pacientes() -> None:
    prompt = PROMPT_SISTEMA.format(data_referencia="08/09/2026", dia_da_semana="terça-feira")

    assert "Não existe cadastro de pacientes" in prompt
    assert "Hoje é 08/09/2026, terça-feira." in prompt


def test_prompt_de_sistema_proibe_buscar_paciente_antes_do_encaixe() -> None:
    """Regressão: o agente travava chamando `buscar_paciente` antes de
    `buscar_encaixe` para um paciente sem atendimento no dia pedido."""
    prompt = PROMPT_SISTEMA.format(data_referencia="08/09/2026", dia_da_semana="terça-feira")

    assert "chame `buscar_encaixe` diretamente e NUNCA chame `buscar_paciente` antes" in prompt
    assert "nunca use esse resultado para interromper um encaixe" in prompt
    assert "pergunte uma única vez se é a mesma pessoa" in prompt
    assert "confirmar o nome ou o id" not in prompt


def test_agente_consulta_a_ocupacao_de_uma_profissional_pela_tool_dedicada() -> None:
    manha = horas_da_manha(SEGUNDA)
    origem = FakeScheduleDataSource(
        profissionais={SEGUNDA: [LUCIANA]},
        grade={SEGUNDA: grade(SEGUNDA, manha)},
        atendimentos={SEGUNDA: atendimentos(SEGUNDA, manha[:7])},
    )
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "consultar_ocupacao_profissional",
                        "args": {"profissional": "Luciana"},
                        "id": "call-1",
                    }
                ],
            ),
            AIMessage(content="A Luciana está com 70,0% de ocupação na semana."),
        ]
    )

    agente = criar_agente(
        origem,
        SemHistoricoContinuidadeDataSource(),
        fake_model,
        SEGUNDA,
        _enviar_relatorio_nao_usado,
    )
    resultado = agente.invoke({"messages": [HumanMessage("Qual a ocupação da Luciana?")]})

    mensagens_de_tool = [
        mensagem for mensagem in resultado["messages"] if isinstance(mensagem, ToolMessage)
    ]
    assert len(mensagens_de_tool) == 1
    assert "Ocupação de Luciana (Psicologia)" in mensagens_de_tool[0].content
    assert "Semana: 70,0% — 7 de 10 slots ocupados" in mensagens_de_tool[0].content
    assert resultado["messages"][-1].content == "A Luciana está com 70,0% de ocupação na semana."


def test_prompt_de_sistema_manda_usar_a_tool_de_ocupacao_da_profissional() -> None:
    prompt = PROMPT_SISTEMA.format(data_referencia="08/09/2026", dia_da_semana="terça-feira")

    assert "chame `consultar_ocupacao_profissional`" in prompt
    assert "nunca calcule essa taxa a partir de outras ferramentas" in prompt
    assert "sem recalcular nem" in prompt
