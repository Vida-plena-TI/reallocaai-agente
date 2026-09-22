"""Testes de `construir_relatorio_ocupacao_do_dia` e das agregações derivadas."""

from datetime import date, time

import pytest

from app.domain import Atendimento, EntradaGrade, Especialidade, Profissional
from app.domain.slot import Slot
from app.engine.ocupacao import OcupacaoAgregada, construir_relatorio_ocupacao_do_dia
from tests.support.fake_schedule_data_source import FakeScheduleDataSource

DIA = date(2026, 9, 8)


def slot(hora: time) -> Slot:
    return Slot(data=DIA, hora_inicio=hora)


def entrada(
    sala_id: str, profissional_id: str, especialidade: Especialidade, hora: time
) -> EntradaGrade:
    return EntradaGrade(
        sala_id=sala_id,
        profissional_id=profissional_id,
        especialidade=especialidade,
        slot=slot(hora),
    )


def atendimento(
    profissional_id: str,
    sala_id: str,
    especialidade: Especialidade,
    horas: list[time],
) -> Atendimento:
    return Atendimento(
        id=f"at-{profissional_id}-{horas[0]}",
        paciente_ids=["pac-1"],
        profissional_id=profissional_id,
        sala_id=sala_id,
        especialidade=especialidade,
        slots=[slot(hora) for hora in horas],
    )


def profissional(id_: str, nome: str, especialidade: Especialidade) -> Profissional:
    return Profissional(id=id_, nome=nome, especialidade=especialidade)


def test_ocupacao_agregada_abaixo_da_meta_quando_percentual_menor_que_a_meta() -> None:
    agregada = OcupacaoAgregada(slots_escalados=10, slots_ocupados=7)

    assert agregada.percentual == 0.7
    assert agregada.abaixo_da_meta is True


def test_ocupacao_agregada_nao_abaixo_da_meta_quando_percentual_igual_ou_maior_que_a_meta() -> None:
    agregada = OcupacaoAgregada(slots_escalados=10, slots_ocupados=8)

    assert agregada.percentual == 0.8
    assert agregada.abaixo_da_meta is False


def test_profissional_com_ocupacao_parcial() -> None:
    origem = FakeScheduleDataSource(
        grade={
            DIA: [
                entrada("sala-1", "prof-1", Especialidade.FONOAUDIOLOGIA, time(9, 0)),
                entrada("sala-1", "prof-1", Especialidade.FONOAUDIOLOGIA, time(9, 30)),
                entrada("sala-1", "prof-1", Especialidade.FONOAUDIOLOGIA, time(10, 0)),
                entrada("sala-1", "prof-1", Especialidade.FONOAUDIOLOGIA, time(10, 30)),
            ]
        },
        profissionais={DIA: [profissional("prof-1", "Ana", Especialidade.FONOAUDIOLOGIA)]},
        atendimentos={
            DIA: [
                atendimento(
                    "prof-1",
                    "sala-1",
                    Especialidade.FONOAUDIOLOGIA,
                    [time(9, 0), time(9, 30), time(10, 0)],
                )
            ]
        },
    )

    relatorio = construir_relatorio_ocupacao_do_dia(origem, DIA)

    [ocupacao] = relatorio.por_profissional
    assert ocupacao.profissional_id == "prof-1"
    assert ocupacao.nome == "Ana"
    assert ocupacao.slots_escalados == 4
    assert ocupacao.slots_ocupados == 3
    assert ocupacao.percentual == 0.75


def test_profissional_sem_grade_tem_percentual_zero_sem_dividir_por_zero() -> None:
    origem = FakeScheduleDataSource(
        profissionais={DIA: [profissional("prof-1", "Ana", Especialidade.FONOAUDIOLOGIA)]},
    )

    relatorio = construir_relatorio_ocupacao_do_dia(origem, DIA)

    [ocupacao] = relatorio.por_profissional
    assert ocupacao.slots_escalados == 0
    assert ocupacao.slots_ocupados == 0
    assert ocupacao.percentual == 0.0


def test_por_sala_soma_profissional_presente_em_duas_salas() -> None:
    origem = FakeScheduleDataSource(
        grade={
            DIA: [
                entrada("sala-1", "prof-1", Especialidade.FONOAUDIOLOGIA, time(9, 0)),
                entrada("sala-1", "prof-1", Especialidade.FONOAUDIOLOGIA, time(9, 30)),
                entrada("sala-2", "prof-1", Especialidade.FONOAUDIOLOGIA, time(10, 0)),
                entrada("sala-2", "prof-1", Especialidade.FONOAUDIOLOGIA, time(10, 30)),
            ]
        },
        profissionais={DIA: [profissional("prof-1", "Ana", Especialidade.FONOAUDIOLOGIA)]},
        atendimentos={
            DIA: [
                atendimento(
                    "prof-1", "sala-1", Especialidade.FONOAUDIOLOGIA, [time(9, 0), time(9, 30)]
                )
            ]
        },
    )

    relatorio = construir_relatorio_ocupacao_do_dia(origem, DIA)
    por_sala = relatorio.por_sala()

    assert por_sala["sala-1"].slots_escalados == 2
    assert por_sala["sala-1"].slots_ocupados == 2
    assert por_sala["sala-1"].abaixo_da_meta is False

    assert por_sala["sala-2"].slots_escalados == 2
    assert por_sala["sala-2"].slots_ocupados == 0
    assert por_sala["sala-2"].abaixo_da_meta is True

    [ocupacao_profissional] = relatorio.por_profissional
    assert ocupacao_profissional.slots_escalados == 4
    assert ocupacao_profissional.slots_ocupados == 2


def test_por_especialidade_soma_dois_profissionais_mesma_especialidade_salas_diferentes() -> None:
    origem = FakeScheduleDataSource(
        grade={
            DIA: [
                entrada("sala-1", "prof-1", Especialidade.FONOAUDIOLOGIA, time(9, 0)),
                entrada("sala-1", "prof-1", Especialidade.FONOAUDIOLOGIA, time(9, 30)),
                entrada("sala-2", "prof-2", Especialidade.FONOAUDIOLOGIA, time(9, 0)),
                entrada("sala-2", "prof-2", Especialidade.FONOAUDIOLOGIA, time(9, 30)),
            ]
        },
        profissionais={
            DIA: [
                profissional("prof-1", "Ana", Especialidade.FONOAUDIOLOGIA),
                profissional("prof-2", "Bia", Especialidade.FONOAUDIOLOGIA),
            ]
        },
        atendimentos={
            DIA: [
                atendimento(
                    "prof-1", "sala-1", Especialidade.FONOAUDIOLOGIA, [time(9, 0), time(9, 30)]
                ),
                atendimento("prof-2", "sala-2", Especialidade.FONOAUDIOLOGIA, [time(9, 0)]),
            ]
        },
    )

    relatorio = construir_relatorio_ocupacao_do_dia(origem, DIA)
    por_especialidade = relatorio.por_especialidade()

    ocupacao = por_especialidade[Especialidade.FONOAUDIOLOGIA]
    assert ocupacao.slots_escalados == 4
    assert ocupacao.slots_ocupados == 3
    assert ocupacao.percentual == 0.75
    assert ocupacao.abaixo_da_meta is True


def test_atendimento_sem_grade_correspondente_gera_warning_e_conta_como_ocupado(
    caplog: pytest.LogCaptureFixture,
) -> None:
    origem = FakeScheduleDataSource(
        grade={DIA: [entrada("sala-1", "prof-1", Especialidade.FONOAUDIOLOGIA, time(9, 0))]},
        profissionais={DIA: [profissional("prof-1", "Ana", Especialidade.FONOAUDIOLOGIA)]},
        atendimentos={
            DIA: [
                atendimento(
                    "prof-1", "sala-1", Especialidade.FONOAUDIOLOGIA, [time(9, 0), time(9, 30)]
                )
            ]
        },
    )

    with caplog.at_level("WARNING"):
        relatorio = construir_relatorio_ocupacao_do_dia(origem, DIA)

    [ocupacao] = relatorio.por_profissional
    assert ocupacao.slots_escalados == 1
    assert ocupacao.slots_ocupados == 2
    assert "sem entrada" in caplog.text

    por_sala = relatorio.por_sala()
    assert por_sala["sala-1"].slots_escalados == 1
    assert por_sala["sala-1"].slots_ocupados == 1


def test_profissional_presente_so_na_grade_usa_id_e_especialidade_da_grade() -> None:
    """Profissional escalado mas ausente de `listar_profissionais` (ex.: cadastro
    incompleto na planilha): nome cai para o id e a especialidade vem da grade.
    """
    origem = FakeScheduleDataSource(
        grade={DIA: [entrada("sala-1", "prof-sem-cadastro", Especialidade.PSICOLOGIA, time(9, 0))]},
    )

    relatorio = construir_relatorio_ocupacao_do_dia(origem, DIA)

    [ocupacao] = relatorio.por_profissional
    assert ocupacao.profissional_id == "prof-sem-cadastro"
    assert ocupacao.nome == "prof-sem-cadastro"
    assert ocupacao.especialidade is Especialidade.PSICOLOGIA
