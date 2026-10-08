"""Colunas de profissional descartadas de propósito (`COLUNAS_IGNORADAS_PROFISSIONAL`).

Nomes fictícios: o conjunto de produção é substituído no próprio módulo do
parser via `monkeypatch`, para exercitar o mecanismo sem depender de quem a
clínica pediu para ignorar.
"""

import logging
from datetime import date

import pytest

from app.data_sources import google_sheets_parser
from app.data_sources.google_sheets_parser import DadosAgendaDoDia, parse_worksheet_data
from app.domain import COLUNAS_IGNORADAS_PROFISSIONAL, MAPA_ALIAS_PROFISSIONAL, Especialidade
from app.engine.disponibilidade import listar_disponibilidade
from app.engine.ocupacao import construir_relatorio_ocupacao_do_dia
from tests.support.fake_schedule_data_source import FakeScheduleDataSource

SABADO = date(2026, 10, 3)

#: Sala 1 mesclada em B:C, com a coluna C ignorada; Sala 2 (D) só com a coluna
#: ignorada. A coluna C tem alias para a profissional da coluna B — prova que a
#: decisão de ignorar vem antes do alias.
VALORES = [
    ["", "Sala 1", "", "Sala 2"],
    ["", "Pietra (Fono)", "Quésia Extra (Fono)", "Rui Avulso (Nutri)"],
    ["09:00", "Paciente Um", "Paciente Dois", "Paciente Três"],
    ["09:30", "", "Paciente Dois", ""],
]
MERGES = ["B1:C1"]


@pytest.fixture
def dados(monkeypatch: pytest.MonkeyPatch) -> DadosAgendaDoDia:
    monkeypatch.setattr(
        google_sheets_parser,
        "COLUNAS_IGNORADAS_PROFISSIONAL",
        frozenset({"quesia-extra", "rui-avulso"}),
    )
    monkeypatch.setitem(MAPA_ALIAS_PROFISSIONAL, "quesia-extra", "pietra")
    return parse_worksheet_data(SABADO, VALORES, MERGES, {})


def _fonte(dados: DadosAgendaDoDia) -> FakeScheduleDataSource:
    return FakeScheduleDataSource(
        salas={SABADO: dados.salas},
        profissionais={SABADO: dados.profissionais},
        grade={SABADO: dados.grade},
        atendimentos={SABADO: dados.atendimentos},
        pacientes={SABADO: dados.pacientes},
    )


def test_coluna_ignorada_nao_gera_profissional_grade_nem_atendimento(
    dados: DadosAgendaDoDia,
) -> None:
    assert [profissional.id for profissional in dados.profissionais] == ["pietra"]
    assert {(entrada.sala_id, entrada.indice_posto) for entrada in dados.grade} == {("sala-1", 0)}
    assert {(item.sala_id, item.indice_posto) for item in dados.atendimentos} == {("sala-1", 0)}
    assert "paciente-dois" not in {paciente.id for paciente in dados.pacientes}
    assert "paciente-tres" not in {paciente.id for paciente in dados.pacientes}


def test_outras_colunas_inclusive_da_mesma_sala_nao_sao_afetadas(
    dados: DadosAgendaDoDia,
) -> None:
    pietra = [entrada for entrada in dados.grade if entrada.profissional_id == "pietra"]
    assert len(pietra) == 2
    assert [atendimento.paciente_ids for atendimento in dados.atendimentos] == [["paciente-um"]]
    # A sala mesclada continua com a geometria do cabeçalho.
    assert [(sala.id, sala.capacidade_simultanea) for sala in dados.salas] == [("sala-1", 2)]


def test_sala_coberta_so_pela_coluna_ignorada_some_da_agenda(dados: DadosAgendaDoDia) -> None:
    assert "sala-2" not in {sala.id for sala in dados.salas}


def test_coluna_ignorada_gera_log_info_e_nenhum_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(
        google_sheets_parser, "COLUNAS_IGNORADAS_PROFISSIONAL", frozenset({"rui-avulso"})
    )

    with caplog.at_level(logging.INFO, logger=google_sheets_parser.__name__):
        parse_worksheet_data(SABADO, VALORES, MERGES, {})

    mensagens = [registro.getMessage() for registro in caplog.records]
    registros = [
        registro
        for registro in caplog.records
        if "COLUNAS_IGNORADAS_PROFISSIONAL" in registro.getMessage()
    ]
    assert [registro.levelno for registro in registros] == [logging.INFO]
    assert not [mensagem for mensagem in mensagens if "Rui Avulso" in mensagem]
    assert not [registro for registro in caplog.records if registro.levelno >= logging.WARNING]


def test_ocupacao_e_disponibilidade_nao_contam_a_coluna_ignorada(
    dados: DadosAgendaDoDia,
) -> None:
    fonte = _fonte(dados)
    relatorio = construir_relatorio_ocupacao_do_dia(fonte, SABADO)

    por_sala = relatorio.por_sala()
    assert set(por_sala) == {"sala-1"}
    assert (por_sala["sala-1"].slots_escalados, por_sala["sala-1"].slots_ocupados) == (2, 1)
    por_especialidade = relatorio.por_especialidade()
    assert set(por_especialidade) == {Especialidade.FONOAUDIOLOGIA}
    assert Especialidade.TERAPIA_ALIMENTAR not in por_especialidade
    assert {
        (vaga.profissional_id, vaga.sala_id, vaga.indice_posto)
        for vaga in listar_disponibilidade(fonte, SABADO)
    } == {("pietra", "sala-1", 0)}


def test_constantes_reais_ignoram_larissa_consultas_sem_alias() -> None:
    assert "larissa-consultas" in COLUNAS_IGNORADAS_PROFISSIONAL
    assert "larissa-consultas" not in MAPA_ALIAS_PROFISSIONAL
    assert MAPA_ALIAS_PROFISSIONAL["larissa"] == "laryssa"
