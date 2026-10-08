"""Testes de `construir_ocupacao_semanal_profissional` e de `slots_para_meta`."""

import logging
from datetime import time

import pytest

from app.domain import Sala
from app.engine import ocupacao_profissional
from app.engine.ocupacao import construir_relatorio_ocupacao_do_dia
from app.engine.ocupacao_profissional import (
    OcupacaoDiaProfissional,
    construir_ocupacao_semanal_profissional,
    slots_para_meta,
)
from tests.support.agenda_semanal import (
    LUCIANA,
    OUTRA,
    QUARTA,
    QUINTA,
    SABADO,
    SEGUNDA,
    SEMANA,
    SEXTA,
    TERCA,
    atendimentos,
    grade,
    horas_da_manha,
    horas_da_tarde,
)
from tests.support.fake_schedule_data_source import FakeScheduleDataSource


def test_percentual_da_semana_e_ponderado_pelos_slots_de_cada_dia() -> None:
    """10/10 na segunda e 5/20 na terça: 15/30 = 50%, não a média 62,5%."""
    manha_segunda = horas_da_manha(SEGUNDA)
    dia_inteiro_terca = horas_da_manha(TERCA) + horas_da_tarde(TERCA)
    origem = FakeScheduleDataSource(
        profissionais={SEGUNDA: [LUCIANA], TERCA: [LUCIANA]},
        grade={SEGUNDA: grade(SEGUNDA, manha_segunda), TERCA: grade(TERCA, dia_inteiro_terca)},
        atendimentos={
            SEGUNDA: atendimentos(SEGUNDA, manha_segunda),
            TERCA: atendimentos(TERCA, dia_inteiro_terca[:5]),
        },
    )

    ocupacao = construir_ocupacao_semanal_profissional(origem, "luciana", QUARTA)

    assert [dia.percentual for dia in ocupacao.dias] == [1.0, 0.25]
    assert ocupacao.slots_escalados == 30
    assert ocupacao.slots_ocupados == 15
    assert ocupacao.slots_livres == 15
    assert ocupacao.percentual == 0.5
    assert ocupacao.abaixo_da_meta is True
    assert ocupacao.slots_para_meta == 9
    assert ocupacao.nome == "Luciana"
    assert ocupacao.especialidade == LUCIANA.especialidade
    assert (ocupacao.semana_inicio, ocupacao.semana_fim) == (SEGUNDA, SABADO)


def test_contagens_de_manha_e_tarde_separadas() -> None:
    manha = horas_da_manha(SEGUNDA)
    tarde = horas_da_tarde(SEGUNDA)
    origem = FakeScheduleDataSource(
        grade={SEGUNDA: grade(SEGUNDA, manha + tarde)},
        atendimentos={SEGUNDA: atendimentos(SEGUNDA, manha[:9] + tarde[:8])},
    )

    dia = construir_ocupacao_semanal_profissional(origem, "luciana", SEGUNDA).dias[0]

    assert (dia.manha_escalados, dia.manha_ocupados) == (10, 9)
    assert (dia.tarde_escalados, dia.tarde_ocupados) == (10, 8)
    assert (dia.slots_escalados, dia.slots_ocupados, dia.slots_livres) == (20, 17, 3)
    assert dia.percentual == 0.85
    assert dia.abaixo_da_meta is False
    assert dia.slots_para_meta == 0


def test_dia_sem_escala_vai_para_dias_sem_agenda_e_domingo_nunca_aparece() -> None:
    origem = FakeScheduleDataSource(
        grade={
            SEGUNDA: grade(SEGUNDA, horas_da_manha(SEGUNDA)),
            QUARTA: grade(QUARTA, horas_da_manha(QUARTA)),
            # Escala de outra profissional não conta como agenda dela.
            SEXTA: grade(SEXTA, horas_da_manha(SEXTA), profissional=OUTRA),
        },
    )

    ocupacao = construir_ocupacao_semanal_profissional(origem, "luciana", SABADO)

    assert [dia.data for dia in ocupacao.dias] == [SEGUNDA, QUARTA]
    assert ocupacao.dias_sem_agenda == [TERCA, QUINTA, SEXTA, SABADO]
    todas_as_datas = [dia.data for dia in ocupacao.dias] + ocupacao.dias_sem_agenda
    assert all(data.weekday() != 6 for data in todas_as_datas)
    assert ocupacao.dias_com_falha == []
    assert ocupacao.parcial is False


def test_sala_com_dois_postos_no_mesmo_dia_e_detalhada_por_posto() -> None:
    manha = horas_da_manha(TERCA)
    origem = FakeScheduleDataSource(
        salas={TERCA: [Sala(id="sala-12", nome="Sala 12", capacidade_simultanea=2)]},
        grade={TERCA: grade(TERCA, manha, indice_posto=0) + grade(TERCA, manha, indice_posto=1)},
        atendimentos={
            TERCA: atendimentos(TERCA, manha[:3], indice_posto=0)
            + atendimentos(TERCA, manha[:7], indice_posto=1)
        },
    )

    dia = construir_ocupacao_semanal_profissional(origem, "luciana", TERCA).dias[0]

    assert [
        (item.sala_nome, item.indice_posto, item.slots_escalados, item.slots_ocupados)
        for item in dia.por_sala_posto
    ] == [("Sala 12", 0, 10, 3), ("Sala 12", 1, 10, 7)]
    assert (dia.slots_escalados, dia.slots_ocupados) == (20, 10)


def test_cada_dia_bate_com_o_relatorio_de_ocupacao_do_dia() -> None:
    manha = horas_da_manha(SEGUNDA)
    tarde = horas_da_tarde(SEGUNDA)
    origem = FakeScheduleDataSource(
        profissionais={dia: [LUCIANA, OUTRA] for dia in SEMANA},
        grade={
            SEGUNDA: grade(SEGUNDA, manha + tarde)
            + grade(SEGUNDA, manha, profissional=OUTRA, sala_id="sala-3"),
            TERCA: grade(TERCA, horas_da_manha(TERCA), indice_posto=0)
            + grade(TERCA, horas_da_manha(TERCA), indice_posto=1),
            QUINTA: grade(QUINTA, horas_da_tarde(QUINTA), sala_id="sala-7"),
        },
        atendimentos={
            SEGUNDA: atendimentos(SEGUNDA, manha[:6] + tarde[2:9])
            + atendimentos(SEGUNDA, manha[:4], profissional=OUTRA, sala_id="sala-3"),
            TERCA: atendimentos(TERCA, horas_da_manha(TERCA)[:5], indice_posto=1)
            + atendimentos(TERCA, horas_da_manha(TERCA)[:2], paciente_ids=["a", "b"]),
            QUINTA: atendimentos(QUINTA, horas_da_tarde(QUINTA)),
        },
    )

    ocupacao = construir_ocupacao_semanal_profissional(origem, "luciana", SEGUNDA)

    assert len(ocupacao.dias) == 3
    for dia in ocupacao.dias:
        do_relatorio = next(
            item
            for item in construir_relatorio_ocupacao_do_dia(origem, dia.data).por_profissional
            if item.profissional_id == "luciana"
        )
        assert dia.slots_escalados == do_relatorio.slots_escalados
        assert dia.slots_ocupados == do_relatorio.slots_ocupados
        assert dia.percentual == do_relatorio.percentual


def test_sessao_em_grupo_conta_o_slot_uma_vez() -> None:
    manha = horas_da_manha(SEGUNDA)
    origem = FakeScheduleDataSource(
        grade={SEGUNDA: grade(SEGUNDA, manha)},
        atendimentos={SEGUNDA: atendimentos(SEGUNDA, manha[:2], paciente_ids=["ana", "bia"])},
    )

    ocupacao = construir_ocupacao_semanal_profissional(origem, "luciana", SEGUNDA)

    assert ocupacao.dias[0].slots_ocupados == 2
    assert ocupacao.tem_inconsistencia is False


def test_atendimento_sem_grade_conta_como_ocupado_e_sinaliza_inconsistencia(
    caplog: pytest.LogCaptureFixture,
) -> None:
    manha = horas_da_manha(SEGUNDA)
    origem = FakeScheduleDataSource(
        grade={SEGUNDA: grade(SEGUNDA, manha)},
        atendimentos={SEGUNDA: atendimentos(SEGUNDA, [manha[0], time(13, 0)])},
    )

    with caplog.at_level(logging.WARNING):
        ocupacao = construir_ocupacao_semanal_profissional(origem, "luciana", SEGUNDA)

    dia = ocupacao.dias[0]
    assert (dia.slots_escalados, dia.slots_ocupados, dia.slots_sem_grade) == (10, 2, 1)
    assert (dia.tarde_escalados, dia.tarde_ocupados) == (0, 1)
    assert ocupacao.tem_inconsistencia is True
    assert "sem entrada correspondente na grade" in caplog.text


def test_falha_em_um_dia_deixa_a_semana_parcial(caplog: pytest.LogCaptureFixture) -> None:
    origem = FakeScheduleDataSource(
        grade={SEGUNDA: grade(SEGUNDA, horas_da_manha(SEGUNDA))},
        dias_com_falha={TERCA},
    )

    with caplog.at_level(logging.WARNING):
        ocupacao = construir_ocupacao_semanal_profissional(origem, "luciana", SEGUNDA)

    assert ocupacao.dias_com_falha == [TERCA]
    assert TERCA not in ocupacao.dias_sem_agenda
    assert ocupacao.parcial is True
    assert ocupacao.slots_escalados == 10
    assert "RuntimeError" in caplog.text
    assert "falha simulada" not in caplog.text


def test_falha_em_todos_os_dias_levanta_o_erro() -> None:
    origem = FakeScheduleDataSource(dias_com_falha=set(SEMANA))

    with pytest.raises(RuntimeError, match="falha simulada"):
        construir_ocupacao_semanal_profissional(origem, "luciana", SEGUNDA)


@pytest.mark.parametrize(
    ("escalados", "ocupados", "esperado"),
    [
        (20, 16, 0),  # exatamente 80%
        (20, 14, 2),  # abaixo
        (20, 19, 0),  # acima
        (10, 0, 8),
        (0, 0, 0),  # sem escala
        (15, 12, 0),
        (15, 11, 1),
    ],
)
def test_slots_para_meta(escalados: int, ocupados: int, esperado: int) -> None:
    assert slots_para_meta(escalados, ocupados) == esperado


def test_slots_para_meta_nao_herda_artefato_de_ponto_flutuante(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Com a meta de 0.8 nenhuma contagem inteira gera artefato, mas a conta não
    pode depender disso: 0.56 * 25 == 14.000000000000002, e um `ceil` direto
    pediria 15 slots."""
    monkeypatch.setattr(ocupacao_profissional, "META_OCUPACAO_POR_SALA", 0.56)
    assert 0.56 * 25 > 14

    assert slots_para_meta(25, 14) == 0
    assert slots_para_meta(25, 13) == 1


def test_abaixo_da_meta_acompanha_slots_para_meta_na_fronteira() -> None:
    dia = OcupacaoDiaProfissional(
        data=SEGUNDA,
        slots_escalados=15,
        slots_ocupados=12,
        manha_escalados=15,
        manha_ocupados=12,
        tarde_escalados=0,
        tarde_ocupados=0,
        por_sala_posto=[],
    )

    assert dia.slots_para_meta == 0
    assert dia.abaixo_da_meta is False
