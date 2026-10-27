"""Resumo da equipe no prompt do sistema: conteúdo, limite, estabilidade e falha da planilha."""

import logging
from datetime import date, timedelta
from typing import Any, cast

import gspread
import pytest
from langchain.agents import create_agent

from app.ai.agente import criar_agente
from app.ai.catalogo import ler_catalogo
from app.ai.prompts import LIMITE_TOKENS_EQUIPE, resumo_da_equipe
from app.data_sources.continuidade import SemHistoricoContinuidadeDataSource
from app.data_sources.google_sheets import GoogleSheetsDataSource
from app.domain import Especialidade
from app.domain.catalogo_agenda import CatalogoAgenda, ProfissionalCatalogo
from tests.casos_reais.planilha import PROFISSIONAIS, PlanilhaContada
from tests.support.fake_chat_model import FakeToolCallingChatModel
from tests.support.fake_schedule_data_source import FakeScheduleDataSource

QUARTA = date(2026, 9, 30)


def _catalogo_ficticio() -> CatalogoAgenda:
    fonte = GoogleSheetsDataSource(cast(gspread.Spreadsheet, PlanilhaContada()))
    return ler_catalogo(fonte, QUARTA)


def test_resumo_por_especialidade() -> None:
    assert resumo_da_equipe(_catalogo_ficticio()) == (
        "\n## Equipe da semana\n\n"
        "Profissionais por especialidade, com o nome como está na planilha:\n"
        "- Terapia Ocupacional: Assizely\n"
        "- Fonoaudiologia: Bia, Elo\n"
        "- Psicologia: Carol\n"
        "- Psicomotricidade: Duda\n"
        "Apelidos confirmados: beatriz = Bia.\n\n"
        "Para dias, turnos, salas e postos, consulte `consultar_profissionais`; não adivinhe. "
        "Passe às ferramentas o nome como a pessoa disse (apelido ou outra grafia): elas "
        "resolvem. Se vierem candidatos, pergunte qual é; nunca escolha sozinho.\n"
    )


def test_resumo_sem_paciente_e_estavel() -> None:
    resumo = resumo_da_equipe(_catalogo_ficticio())
    assert "Paciente" not in resumo
    assert resumo == resumo_da_equipe(_catalogo_ficticio())
    assert len(resumo) // 4 <= LIMITE_TOKENS_EQUIPE


def test_equipe_grande_respeita_o_limite() -> None:
    profissionais = {
        f"profissional-{n:04d}": ProfissionalCatalogo(
            id=f"profissional-{n:04d}",
            nome=f"Profissional Com Nome Comprido {n:04d}",
            especialidade=list(Especialidade)[n % len(Especialidade)],
        )
        for n in range(2_000)
    }
    catalogo = CatalogoAgenda(dias=(QUARTA,), profissionais=profissionais, salas={})

    resumo = resumo_da_equipe(catalogo)

    assert len(resumo) <= LIMITE_TOKENS_EQUIPE * 4
    assert " e mais " in resumo
    assert resumo.count("\n- ") == len(Especialidade)


def test_planilha_ilegivel_nao_derruba_o_chat(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    capturado: dict[str, Any] = {}

    def capturar(**kwargs: Any) -> Any:
        capturado.update(kwargs)
        return create_agent(**kwargs)

    monkeypatch.setattr("app.ai.agente.create_agent", capturar)
    semana = {date(2026, 9, 28) + timedelta(days=i) for i in range(6)}
    fonte = FakeScheduleDataSource(dias_com_falha=semana)

    with caplog.at_level(logging.WARNING):
        criar_agente(
            fonte,
            SemHistoricoContinuidadeDataSource(),
            FakeToolCallingChatModel(responses=[]),
            QUARTA,
        )

    assert "## Equipe da semana" not in capturado["system_prompt"]
    assert "Prompt montado sem o resumo da equipe" in caplog.text
    # O log diz só o que falhou: nem texto da planilha nem nomes.
    for celula in PROFISSIONAIS.values():
        assert celula.split()[0] not in caplog.text


def test_com_catalogo_o_prompt_traz_o_resumo(monkeypatch: pytest.MonkeyPatch) -> None:
    capturado: dict[str, Any] = {}

    def capturar(**kwargs: Any) -> Any:
        capturado.update(kwargs)
        return create_agent(**kwargs)

    monkeypatch.setattr("app.ai.agente.create_agent", capturar)
    fonte = GoogleSheetsDataSource(cast(gspread.Spreadsheet, PlanilhaContada()))
    criar_agente(
        fonte, SemHistoricoContinuidadeDataSource(), FakeToolCallingChatModel(responses=[]), QUARTA
    )

    assert capturado["system_prompt"].endswith(resumo_da_equipe(_catalogo_ficticio()))
