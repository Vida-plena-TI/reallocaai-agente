"""Verificação de fundamentação: horários, contagens, nomes e vocabulário da resposta."""

import logging
from datetime import date
from typing import cast

import gspread
import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from app.ai.agente import Agente, criar_agente, perguntar_com_mensagens
from app.ai.fundamentacao import termos_proibidos, verificar
from app.data_sources.continuidade import SemHistoricoContinuidadeDataSource
from app.data_sources.google_sheets import GoogleSheetsDataSource
from tests.casos_reais.planilha import PlanilhaContada
from tests.support.fake_chat_model import FakeToolCallingChatModel

SEGUNDA = date(2026, 9, 28)

TOOL = ToolMessage(
    "Horários livres em 01/10/2026 (10:00 às 12:00):\n"
    "- 10:00 às 10:30: Fonoaudiologia com Bia na Sala 1.\n"
    "- 10:30 às 11:00: Fonoaudiologia com Bia na Sala 1.\n"
    "Total: 2 vagas.",
    tool_call_id="t1",
)
PERGUNTA = HumanMessage("a beatriz tem vaga na quinta a partir das 9h30?")


def _verificar(resposta: str, turno: list[BaseMessage] | None = None) -> object:
    return verificar(resposta, [TOOL] if turno is None else turno, [PERGUNTA], ["Bia", "Carol"])


def test_faixas_seguidas_podem_virar_uma_so() -> None:
    assert verificar("A Bia tem vaga das 10:00 às 11:00.", [TOOL], [PERGUNTA], ["Bia"]).ok


def test_horario_inexistente_e_rejeitado() -> None:
    problemas = verificar("A Bia tem vaga às 09:00.", [TOOL], [PERGUNTA], ["Bia"])
    assert problemas.horarios == ["09:00"]


def test_horario_dito_na_pergunta_e_horario_da_clinica_valem() -> None:
    texto = "Depois das 09:30 a Bia tem 2 vagas; a clínica fecha às 18:00."
    assert verificar(texto, [TOOL], [PERGUNTA], ["Bia"]).ok


def test_contagem_e_nome_sem_fonte() -> None:
    problemas = verificar("A Carol tem 12 vagas.", [TOOL], [PERGUNTA], ["Bia", "Carol"])
    assert problemas.numeros == ["12"]
    assert problemas.nomes == ["Carol"]


def test_sem_ferramenta_so_confere_vocabulario() -> None:
    problemas = verificar("Encaixei o paciente no slot das 09:00.", [], [PERGUNTA], ["Bia"])
    assert problemas.vocabulario == ["slot", "encaixei"]
    assert problemas.horarios == []


def test_vocabulario_proibido() -> None:
    assert termos_proibidos("Parâmetros inválidos: veja o bloco e as tools.") == [
        "tool",
        "bloco",
        "parâmetros inválidos",
    ]
    assert termos_proibidos("Posso registrar? Agendei e marquei.") == [
        "registrar",
        "agendei",
        "marquei",
    ]
    # Substantivo e particípio que descrevem o que já existe continuam valendo.
    assert termos_proibidos("O encaixe sugerido; o paciente já está agendado.") == []


# ---- nova geração e resumo determinístico, no agente ----

CHAMADA = AIMessage(
    "",
    tool_calls=[{"name": "consultar_disponibilidade", "args": {"data": "2026-09-28"}, "id": "c1"}],
)


def _agente(respostas: list[AIMessage]) -> Agente:
    fonte = GoogleSheetsDataSource(cast(gspread.Spreadsheet, PlanilhaContada()))
    modelo = FakeToolCallingChatModel(responses=respostas)
    return criar_agente(
        fonte, SemHistoricoContinuidadeDataSource(), modelo, SEGUNDA, renderiza_relatorios=True
    )


def test_resposta_sem_fonte_e_refeita_uma_vez(caplog: pytest.LogCaptureFixture) -> None:
    agente = _agente(
        [
            CHAMADA,
            AIMessage("Há 45 vagas na segunda; a primeira é às 06:30."),
            AIMessage("Há 45 vagas na segunda, a partir das 07:00."),
        ]
    )
    with caplog.at_level(logging.WARNING):
        resposta, mensagens, blocos = perguntar_com_mensagens(
            agente, [HumanMessage("quais horários vagos na segunda-feira?")]
        )

    assert resposta == "Há 45 vagas na segunda, a partir das 07:00."
    assert [b.tipo for b in blocos] == ["disponibilidade"]
    assert not any("Verificação interna" in str(m.content) for m in mensagens)
    assert "1 item(ns) sem fonte na resposta; nova geração" in caplog.text
    assert "06:30" not in caplog.text


def test_duas_falhas_usam_o_resumo_do_relatorio() -> None:
    agente = _agente(
        [
            CHAMADA,
            AIMessage("Há 99 vagas na segunda."),
            AIMessage("Encaixei tudo: há 98 vagas."),
        ]
    )
    resposta, _, blocos = perguntar_com_mensagens(
        agente, [HumanMessage("quais horários vagos na segunda-feira?")]
    )

    assert [b.tipo for b in blocos] == ["disponibilidade"]
    assert resposta == (
        "Vagas livres de 28/09/2026: Vagas livres: 45; Horas livres: 22,5 h. "
        "O detalhe está no relatório."
    )


def test_vocabulario_proibido_forca_nova_geracao(caplog: pytest.LogCaptureFixture) -> None:
    agente = _agente([AIMessage("Pronto, encaixei!"), AIMessage("Posso sugerir um horário?")])
    with caplog.at_level(logging.WARNING):
        resposta, _, _ = perguntar_com_mensagens(agente, [HumanMessage("oi")])

    assert resposta == "Posso sugerir um horário?"
    assert "Vocabulário proibido forçou nova geração" in caplog.text
    assert "encaixei" not in caplog.text.lower()
