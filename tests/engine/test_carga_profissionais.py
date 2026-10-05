"""Testes de `construir_carga_profissionais`: pacientes por profissional, por dia e na semana."""

from dataclasses import dataclass, field
from datetime import date

import pytest

from app.domain import (
    Atendimento,
    EntradaGrade,
    Especialidade,
    Paciente,
    Profissional,
    Sala,
    Slot,
    dias_da_semana_de,
)
from app.engine.carga_profissionais import CargaProfissional, construir_carga_profissionais
from app.engine.ocupacao_profissional import construir_ocupacao_semanal_profissional
from tests.support.agenda_semanal import (
    DOMINGO,
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

AMANDA = Profissional(id="amanda", nome="Amanda", especialidade=Especialidade.PSICOLOGIA)
ELIS = Profissional(id="elis", nome="Élis", especialidade=Especialidade.PSICOLOGIA)
BRUNO = Profissional(id="bruno", nome="Bruno", especialidade=Especialidade.FONOAUDIOLOGIA)


def _carga(fonte: FakeScheduleDataSource, profissional_id: str = "luciana") -> CargaProfissional:
    resultado = construir_carga_profissionais(fonte, SEMANA)
    return next(item for item in resultado.profissionais if item.profissional_id == profissional_id)


def test_mesmo_paciente_em_dois_blocos_conta_um_paciente_e_duas_sessoes() -> None:
    manha, tarde = horas_da_manha(SEGUNDA), horas_da_tarde(SEGUNDA)
    fonte = FakeScheduleDataSource(
        profissionais={SEGUNDA: [LUCIANA]},
        grade={SEGUNDA: grade(SEGUNDA, manha + tarde)},
        atendimentos={
            SEGUNDA: atendimentos(SEGUNDA, manha[:1], paciente_ids=["ana"])
            + atendimentos(SEGUNDA, tarde[:1], paciente_ids=["ana"])
        },
    )

    (dia,) = _carga(fonte).dias

    assert (dia.pacientes, dia.sessoes, dia.slots_ocupados) == (1, 2, 2)


def test_sessao_em_grupo_conta_cada_paciente_e_uma_sessao() -> None:
    manha = horas_da_manha(SEGUNDA)
    fonte = FakeScheduleDataSource(
        grade={SEGUNDA: grade(SEGUNDA, manha)},
        atendimentos={SEGUNDA: atendimentos(SEGUNDA, manha[:1], paciente_ids=["ana", "beto"])},
    )

    (dia,) = _carga(fonte).dias

    assert (dia.pacientes, dia.sessoes, dia.slots_ocupados) == (2, 1, 1)


def test_dois_postos_no_dia_somam_sem_duplicar_quem_aparece_nos_dois() -> None:
    manha = horas_da_manha(SEGUNDA)
    fonte = FakeScheduleDataSource(
        grade={
            SEGUNDA: grade(SEGUNDA, manha, indice_posto=0) + grade(SEGUNDA, manha, indice_posto=1)
        },
        atendimentos={
            SEGUNDA: atendimentos(SEGUNDA, manha[:1], indice_posto=0, paciente_ids=["ana"])
            + atendimentos(SEGUNDA, manha[1:2], indice_posto=0, paciente_ids=["beto"])
            + atendimentos(SEGUNDA, manha[:1], indice_posto=1, paciente_ids=["beto"])
            + atendimentos(SEGUNDA, manha[1:2], indice_posto=1, paciente_ids=["caio"])
        },
    )

    (dia,) = _carga(fonte).dias

    assert (dia.pacientes, dia.sessoes, dia.slots_ocupados) == (3, 4, 4)


def test_media_usa_so_dias_com_agenda_e_dia_sem_paciente_entra_como_zero() -> None:
    fonte = FakeScheduleDataSource(
        grade={dia: grade(dia, horas_da_manha(dia)) for dia in (SEGUNDA, TERCA, QUARTA)},
        atendimentos={
            SEGUNDA: atendimentos(SEGUNDA, horas_da_manha(SEGUNDA)[:3]),
            TERCA: atendimentos(TERCA, horas_da_manha(TERCA)[:2]),
        },
    )

    carga = _carga(fonte)

    assert [(dia.data, dia.pacientes) for dia in carga.dias] == [
        (SEGUNDA, 3),
        (TERCA, 2),
        (QUARTA, 0),
    ]
    assert carga.media_pacientes_por_dia == pytest.approx(5 / 3)
    assert carga.dias_sem_agenda == [QUINTA, SEXTA, SABADO]
    assert carga.dias_com_falha == []
    assert not carga.parcial


def test_pacientes_distintos_na_semana_nao_repetem_quem_volta_em_outro_dia() -> None:
    fonte = FakeScheduleDataSource(
        grade={dia: grade(dia, horas_da_manha(dia)) for dia in (SEGUNDA, TERCA)},
        atendimentos={
            SEGUNDA: atendimentos(SEGUNDA, horas_da_manha(SEGUNDA)[:1], paciente_ids=["ana"])
            + atendimentos(SEGUNDA, horas_da_manha(SEGUNDA)[1:2], paciente_ids=["beto"]),
            TERCA: atendimentos(TERCA, horas_da_manha(TERCA)[:1], paciente_ids=["ana"]),
        },
    )

    assert _carga(fonte).pacientes_distintos_semana == 2


def test_sem_agenda_no_periodo_nao_aparece_e_com_agenda_sem_paciente_aparece_com_zero() -> None:
    fonte = FakeScheduleDataSource(
        # `OUTRA` está entre os profissionais do dia, mas sem grade nem atendimento.
        profissionais={SEGUNDA: [LUCIANA, OUTRA, BRUNO]},
        grade={SEGUNDA: grade(SEGUNDA, horas_da_manha(SEGUNDA), profissional=BRUNO)},
    )

    resultado = construir_carga_profissionais(fonte, SEMANA)

    assert [item.profissional_id for item in resultado.profissionais] == ["bruno"]
    (bruno,) = resultado.profissionais
    assert [dia.pacientes for dia in bruno.dias] == [0]
    assert bruno.media_pacientes_por_dia == 0.0
    assert bruno.pacientes_distintos_semana == 0


def test_atendimento_sem_grade_tambem_conta_como_dia_com_agenda() -> None:
    fonte = FakeScheduleDataSource(
        profissionais={SEGUNDA: [LUCIANA]},
        atendimentos={SEGUNDA: atendimentos(SEGUNDA, horas_da_manha(SEGUNDA)[:2])},
    )

    carga = _carga(fonte)

    assert [(dia.data, dia.pacientes) for dia in carga.dias] == [(SEGUNDA, 2)]
    assert carga.nome == "Luciana"
    assert carga.especialidade is Especialidade.PSICOLOGIA


def test_pacientes_distintos_da_clinica_nao_contam_duas_vezes_quem_passa_por_duas() -> None:
    manha = horas_da_manha(SEGUNDA)
    fonte = FakeScheduleDataSource(
        profissionais={SEGUNDA: [LUCIANA, BRUNO]},
        grade={SEGUNDA: grade(SEGUNDA, manha) + grade(SEGUNDA, manha, profissional=BRUNO)},
        atendimentos={
            SEGUNDA: atendimentos(SEGUNDA, manha[:1], paciente_ids=["ana"])
            + atendimentos(SEGUNDA, manha[1:2], paciente_ids=["beto"])
            + atendimentos(SEGUNDA, manha[1:2], profissional=BRUNO, paciente_ids=["ana"])
        },
    )

    resultado = construir_carga_profissionais(fonte, SEMANA)

    soma_das_linhas = sum(item.dias[0].pacientes for item in resultado.profissionais)
    clinica = {item.data: item.pacientes_distintos_clinica for item in resultado.por_dia}
    assert clinica[SEGUNDA] == 2
    assert soma_das_linhas == 3
    assert soma_das_linhas > clinica[SEGUNDA]
    assert clinica[TERCA] == 0


def test_ordem_por_especialidade_e_nome_sem_acento_nem_caixa() -> None:
    manha = horas_da_manha(SEGUNDA)
    profissionais = [
        Profissional(id="zelia", nome="zélia", especialidade=Especialidade.PSICOLOGIA),
        ELIS,
        AMANDA,
        BRUNO,
    ]
    fonte = FakeScheduleDataSource(
        profissionais={SEGUNDA: profissionais},
        grade={
            SEGUNDA: [
                entrada
                for profissional in profissionais
                for entrada in grade(SEGUNDA, manha, profissional=profissional)
            ]
        },
    )

    resultado = construir_carga_profissionais(fonte, SEMANA)

    assert [item.nome for item in resultado.profissionais] == ["Bruno", "Amanda", "Élis", "zélia"]


def test_domingo_e_datas_repetidas_sao_descartados() -> None:
    fonte = FakeScheduleDataSource(grade={SEGUNDA: grade(SEGUNDA, horas_da_manha(SEGUNDA))})

    resultado = construir_carga_profissionais(fonte, [DOMINGO, SEGUNDA, SEGUNDA])

    assert resultado.dias == [SEGUNDA]
    assert [item.data for item in resultado.por_dia] == [SEGUNDA]


def _atendimento_longo(
    profissional: Profissional, dia: date, inicio: int, duracao: int, pacientes: list[str]
) -> Atendimento:
    slots = Slot.slots_do_dia(dia)[inicio : inicio + duracao]
    return Atendimento(
        id=f"longo-{profissional.id}-{dia.isoformat()}-{inicio}",
        paciente_ids=pacientes,
        profissional_id=profissional.id,
        sala_id="sala-1",
        especialidade=profissional.especialidade,
        slots=slots,
        indice_posto=0,
    )


def test_slots_ocupados_iguais_aos_da_ocupacao_por_profissional() -> None:
    def grade_completa(dia: date, profissional: Profissional) -> list[EntradaGrade]:
        return grade(dia, horas_da_manha(dia) + horas_da_tarde(dia), profissional=profissional)

    fonte = FakeScheduleDataSource(
        profissionais={dia: [LUCIANA, BRUNO] for dia in SEMANA},
        grade={
            SEGUNDA: grade_completa(SEGUNDA, LUCIANA) + grade_completa(SEGUNDA, BRUNO),
            TERCA: grade_completa(TERCA, LUCIANA),
            QUINTA: grade_completa(QUINTA, BRUNO),
        },
        atendimentos={
            SEGUNDA: [
                _atendimento_longo(LUCIANA, SEGUNDA, 0, 2, ["ana"]),
                _atendimento_longo(LUCIANA, SEGUNDA, 4, 3, ["beto", "caio"]),
                _atendimento_longo(BRUNO, SEGUNDA, 0, 1, ["ana"]),
            ],
            TERCA: atendimentos(TERCA, horas_da_tarde(TERCA)[:4]),
            # Atendimento fora da grade do Bruno: a ocupação conta, esta também.
            SEXTA: [_atendimento_longo(BRUNO, SEXTA, 2, 2, ["dora"])],
        },
    )

    resultado = construir_carga_profissionais(fonte, SEMANA)

    assert {item.profissional_id for item in resultado.profissionais} == {"luciana", "bruno"}
    for carga in resultado.profissionais:
        ocupacao = construir_ocupacao_semanal_profissional(fonte, carga.profissional_id, SEGUNDA)
        assert {dia.data: dia.slots_ocupados for dia in carga.dias} == {
            dia.data: dia.slots_ocupados for dia in ocupacao.dias
        }
        assert carga.dias_sem_agenda == ocupacao.dias_sem_agenda


@dataclass
class _FonteQueContaChamadas(FakeScheduleDataSource):
    """`FakeScheduleDataSource` que conta as chamadas a cada `listar_*`."""

    chamadas: dict[str, int] = field(default_factory=dict)

    def _contar(self, metodo: str) -> None:
        self.chamadas[metodo] = self.chamadas.get(metodo, 0) + 1

    def listar_salas(self, dia: date) -> list[Sala]:
        self._contar("listar_salas")
        return super().listar_salas(dia)

    def listar_profissionais(self, dia: date) -> list[Profissional]:
        self._contar("listar_profissionais")
        return super().listar_profissionais(dia)

    def listar_grade(self, dia: date) -> list[EntradaGrade]:
        self._contar("listar_grade")
        return super().listar_grade(dia)

    def listar_atendimentos(self, dia: date) -> list[Atendimento]:
        self._contar("listar_atendimentos")
        return super().listar_atendimentos(dia)

    def listar_pacientes(self, dia: date) -> list[Paciente]:
        self._contar("listar_pacientes")
        return super().listar_pacientes(dia)


def _fonte_com_profissionais(quantidade: int) -> _FonteQueContaChamadas:
    profissionais = [
        Profissional(id=f"prof-{indice}", nome=f"Prof {indice}", especialidade=especialidade)
        for indice, especialidade in zip(
            range(quantidade), list(Especialidade) * quantidade, strict=False
        )
    ]
    return _FonteQueContaChamadas(
        profissionais=dict.fromkeys(SEMANA, profissionais),
        grade={
            dia: [
                entrada
                for profissional in profissionais
                for entrada in grade(dia, horas_da_manha(dia)[:2], profissional=profissional)
            ]
            for dia in SEMANA
        },
        atendimentos={
            dia: [
                atendimento
                for profissional in profissionais
                for atendimento in atendimentos(dia, horas_da_manha(dia)[:1], profissional)
            ]
            for dia in SEMANA
        },
    )


def test_leituras_a_fonte_nao_crescem_com_o_numero_de_profissionais() -> None:
    poucas, muitas = _fonte_com_profissionais(3), _fonte_com_profissionais(30)

    resultado_poucas = construir_carga_profissionais(poucas, SEMANA)
    resultado_muitas = construir_carga_profissionais(muitas, SEMANA)

    assert len(resultado_poucas.profissionais) == 3
    assert len(resultado_muitas.profissionais) == 30
    assert poucas.chamadas == muitas.chamadas
    assert sum(muitas.chamadas.values()) == 3 * len(SEMANA)


def test_falha_em_um_dia_deixa_os_numeros_parciais_e_avisa(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fonte = FakeScheduleDataSource(
        grade={dia: grade(dia, horas_da_manha(dia)) for dia in (SEGUNDA, TERCA)},
        atendimentos={SEGUNDA: atendimentos(SEGUNDA, horas_da_manha(SEGUNDA)[:2])},
        dias_com_falha={TERCA},
    )

    resultado = construir_carga_profissionais(fonte, SEMANA)

    carga = resultado.profissionais[0]
    assert resultado.parcial
    assert resultado.dias_com_falha == [TERCA]
    assert carga.parcial
    assert carga.dias_com_falha == [TERCA]
    assert [dia.data for dia in carga.dias] == [SEGUNDA]
    assert TERCA not in carga.dias_sem_agenda
    assert TERCA not in [item.data for item in resultado.por_dia]
    assert "Falha ao ler a agenda de 2026-09-29" in caplog.text


def test_falha_em_todos_os_dias_levanta_o_erro() -> None:
    fonte = FakeScheduleDataSource(dias_com_falha=set(dias_da_semana_de(SEGUNDA)))

    with pytest.raises(RuntimeError, match="falha simulada"):
        construir_carga_profissionais(fonte, SEMANA)
