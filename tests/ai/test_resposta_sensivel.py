"""`resposta_sensivel`: a rodada que chamou tool restrita, com ou sem bloco (nomes fictícios).

A flag vai na resposta do chat, fora dos blocos, para o chatbot trocar o texto
quando o usuário não é da coordenação. Decide pelo nome das tools chamadas no
turno, nunca pelo bloco: a pergunta de ambiguidade, o panorama sem cenário e a
realocação sem solução também são restritos.
"""

import re
from datetime import date
from pathlib import Path
from typing import Any, cast

import gspread
import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from app.ai import relatorios, tools_enxugamento, tools_pacientes_profissional, tools_realocacao
from app.ai.agente import criar_agente, perguntar_com_mensagens, rodada_sensivel
from app.ai.relatorios import (
    RELATORIOS_COM_NOMES_DE_PACIENTES,
    RELATORIOS_RESTRITOS_DE_GERAR_RELATORIO,
    TOOL_DO_RELATORIO_COM_NOMES,
    TOOL_GERAR_RELATORIO,
    TOOLS_RESTRITAS,
    BlocoRelatorio,
    chamada_restrita,
)
from app.ai.tools import criar_tools
from app.data_sources.continuidade import SemHistoricoContinuidadeDataSource
from app.data_sources.google_sheets import GoogleSheetsDataSource
from app.domain import ScheduleDataSource
from tests.casos_reais.planilha import PlanilhaContada, payload_da_semana
from tests.support.agenda_clinica import PACIENTES, fonte_clinica
from tests.support.fake_chat_model import FakeToolCallingChatModel
from tests.support.fake_schedule_data_source import FakeScheduleDataSource

HOJE = date(2026, 9, 28)
RESPOSTA = "Pronto, veja o resultado na tela."

PEDIDO_C06: dict[str, Any] = {
    "dias_semana": ["quarta"],
    "especialidade": "psico",
    "duracao_minutos": 60,
    "horario_desejado": "10:00",
    "horario_minimo": "10:00",
    "horario_maximo": "11:00",
}


def _planilha(cenario: str | None = None) -> ScheduleDataSource:
    planilha = PlanilhaContada(lambda _params: payload_da_semana(cenario))
    return GoogleSheetsDataSource(cast(gspread.Spreadsheet, planilha))


def _rodada(
    fonte: ScheduleDataSource, nome: str, args: dict[str, Any]
) -> tuple[list[BaseMessage], list[BlocoRelatorio]]:
    chamada = AIMessage("", tool_calls=[{"name": nome, "args": args, "id": "r1"}])
    modelo = FakeToolCallingChatModel(responses=[chamada, AIMessage(RESPOSTA)])
    agente = criar_agente(
        fonte, SemHistoricoContinuidadeDataSource(), modelo, HOJE, renderiza_relatorios=True
    )
    _, mensagens, blocos = perguntar_com_mensagens(agente, [HumanMessage("Pergunta fictícia.")])
    assert any(isinstance(m, ToolMessage) and m.name == nome for m in mensagens)
    return mensagens, blocos


# --- a constante não diverge dos relatórios com nomes -------------------------


def test_toda_tool_de_relatorio_com_nomes_e_restrita() -> None:
    assert set(TOOL_DO_RELATORIO_COM_NOMES) == RELATORIOS_COM_NOMES_DE_PACIENTES
    assert set(TOOL_DO_RELATORIO_COM_NOMES.values()) <= TOOLS_RESTRITAS
    assert {
        tools_enxugamento.NOME_DA_TOOL,
        tools_realocacao.NOME_DA_TOOL,
        tools_pacientes_profissional.NOME_DA_TOOL,
        "pacientes_por_plano",
    } <= TOOLS_RESTRITAS
    registradas = {
        t.name
        for t in criar_tools(FakeScheduleDataSource(), SemHistoricoContinuidadeDataSource(), HOJE)
    }
    assert registradas >= TOOLS_RESTRITAS
    assert TOOL_GERAR_RELATORIO in registradas


def test_todo_modulo_que_monta_bloco_com_nomes_e_de_tool_restrita() -> None:
    """Só os módulos de blocos das tools restritas montam bloco com um `RELATORIO_*` com
    nomes (uma tool nova que montasse um desses blocos faria este teste falhar)."""
    constantes = "|".join(
        nome
        for nome, valor in vars(relatorios).items()
        if nome.startswith("RELATORIO_") and valor in RELATORIOS_COM_NOMES_DE_PACIENTES
    )
    assert constantes.count("|") == len(RELATORIOS_COM_NOMES_DE_PACIENTES) - 1
    pasta = Path(__file__).parents[2] / "app" / "ai"
    montam = {
        arquivo.name
        for arquivo in pasta.glob("*.py")
        if re.search(rf"relatorio=({constantes})\b", arquivo.read_text("utf-8"))
    }
    assert montam == {"blocos_gerais.py", "blocos_realocacao.py", "blocos_enxugamento.py"}


@pytest.mark.parametrize(
    "fonte,nome,args",
    [
        (lambda: _planilha("planos_por_cor"), "pacientes_por_plano", {}),
        (
            lambda: _planilha("nomes_por_profissional"),
            "listar_pacientes_por_profissional",
            {},
        ),
        (lambda: _planilha(), "planejar_realocacao", PEDIDO_C06),
        (lambda: _planilha("enxugamento"), "planejar_enxugamento", {}),
        (
            fonte_clinica,
            "gerar_relatorio",
            {"relatorio": "agenda_paciente", "paciente": PACIENTES["A"][0]},
        ),
    ],
    ids=["plano", "nomes-por-profissional", "realocacao", "enxugamento", "agenda-paciente"],
)
def test_toda_tool_cujo_bloco_traz_nomes_esta_na_constante(
    fonte: Any, nome: str, args: dict[str, Any]
) -> None:
    mensagens, blocos = _rodada(fonte(), nome, args)

    assert blocos and blocos[0].traz_nomes_de_pacientes
    assert chamada_restrita(nome, args)
    if blocos[0].dados.tipo == "tabela_generica":
        assert TOOL_DO_RELATORIO_COM_NOMES[blocos[0].dados.relatorio] == nome
    else:
        assert args["relatorio"] in RELATORIOS_RESTRITOS_DE_GERAR_RELATORIO
    assert rodada_sensivel(mensagens)


# --- presente com e sem bloco -------------------------------------------------


@pytest.mark.parametrize(
    "cenario,nome,args,com_bloco",
    [
        ("enxugamento", "planejar_enxugamento", {}, True),
        ("enxugamento", "planejar_enxugamento", {"dias_semana": ["sexta"]}, False),
        (
            "enxugamento",
            "planejar_enxugamento",
            {"profissionais_foco": ["Ficticia"], "liberar_turno": "manha"},
            False,
        ),
        (None, "planejar_realocacao", PEDIDO_C06, True),
        (
            None,
            "planejar_realocacao",
            {
                "paciente": "Paciente Ficticio Bravo",
                "dias_semana": ["quarta"],
                "horario_minimo": "08:00",
            },
            True,
        ),
        (None, "planejar_realocacao", {"dias_semana": ["quarta"], "especialidade": "psico"}, False),
        (
            "realocacao",
            "planejar_realocacao",
            {**PEDIDO_C06, "dias_semana": ["quinta"], "especialidade": "TO"},
            True,
        ),
        ("planos_por_cor", "pacientes_por_plano", {}, True),
        (None, "pacientes_por_plano", {}, False),
        (
            "nomes_por_profissional",
            "listar_pacientes_por_profissional",
            {"profissionais": ["Elo"], "dias_semana": ["quarta"]},
            False,
        ),
    ],
    ids=[
        "panorama-com-cenario",
        "panorama-sem-cenario",
        "panorama-foco-ambiguo",
        "realocacao-plano",
        "realocacao-paciente-ambiguo",
        "realocacao-pergunta-sem-bloco",
        "realocacao-sem-solucao",
        "plano-com-bloco",
        "plano-sem-bloco",
        "nomes-por-profissional-sem-bloco",
    ],
)
def test_presente_com_e_sem_bloco(
    cenario: str | None, nome: str, args: dict[str, Any], com_bloco: bool
) -> None:
    fonte = FakeScheduleDataSource() if nome == "pacientes_por_plano" and cenario is None else None
    mensagens, blocos = _rodada(fonte or _planilha(cenario), nome, args)

    assert bool(blocos) is com_bloco
    assert rodada_sensivel(mensagens)


def test_agenda_paciente_sem_o_paciente_na_agenda_tambem_e_sensivel() -> None:
    args = {"relatorio": "agenda_paciente", "paciente": "Paciente Ficticio Ausente"}
    mensagens, blocos = _rodada(fonte_clinica(), "gerar_relatorio", args)

    assert blocos == []
    assert rodada_sensivel(mensagens)


# --- ausente nas demais -------------------------------------------------------


@pytest.mark.parametrize(
    "nome,args",
    [
        ("consultar_disponibilidade", {"data": HOJE.isoformat()}),
        (
            "buscar_encaixe",
            {
                "paciente": "Paciente Ficticio Novo",
                "data": HOJE.isoformat(),
                "itens": [{"especialidade": "psicologia", "duracao_minutos": 60}],
                "horario_desejado": "10:00",
            },
        ),
        ("planejar_semana", {"dias_semana": ["quarta"]}),
        ("consultar_profissionais", {}),
        ("gerar_relatorio", {"relatorio": "vagas_livres"}),
    ],
    ids=["disponibilidade", "encaixe", "plano-semanal", "profissionais", "relatorio-sem-nomes"],
)
def test_ausente_nas_tools_nao_restritas(nome: str, args: dict[str, Any]) -> None:
    mensagens, _ = _rodada(fonte_clinica(), nome, args)

    assert not rodada_sensivel(mensagens)


def test_ausente_quando_o_modelo_so_fala() -> None:
    modelo = FakeToolCallingChatModel(responses=[AIMessage("Olá! Em que posso ajudar?")])
    agente = criar_agente(fonte_clinica(), SemHistoricoContinuidadeDataSource(), modelo, HOJE)
    _, mensagens, _ = perguntar_com_mensagens(agente, [HumanMessage("Oi")])

    assert not rodada_sensivel(mensagens)


def test_so_o_turno_atual_conta() -> None:
    """A tool restrita de um turno anterior não marca a rodada seguinte."""
    anterior = [
        HumanMessage("Pergunta fictícia anterior."),
        AIMessage("", tool_calls=[{"name": "planejar_enxugamento", "args": {}, "id": "a1"}]),
        ToolMessage("contagens", tool_call_id="a1", name="planejar_enxugamento"),
        AIMessage("Resposta anterior."),
    ]
    assert rodada_sensivel(anterior)
    assert not rodada_sensivel([*anterior, HumanMessage("E amanhã?"), AIMessage("Sim.")])
