"""Testes de `fotografar_periodo`: leitura do período e normalização por slot."""

import threading
from datetime import date, time

import pytest

from app.data_sources.google_sheets_parser import parse_worksheet_data
from app.domain import Atendimento, Especialidade, Slot
from app.engine import EstadoSlot, Periodo, fotografar_periodo
from app.engine.fotografia import MAX_DIAS_POR_PERIODO, dias_do_periodo
from tests.data_sources.fixtures import SEGUNDA_FICTICIA
from tests.support.agenda_clinica import ANA, CAU, SALA_2, fonte_clinica
from tests.support.agenda_semanal import (
    DOMINGO,
    LUCIANA,
    QUARTA,
    SABADO,
    SEGUNDA,
    SEMANA,
    TERCA,
    atendimentos,
    grade,
)
from tests.support.fake_schedule_data_source import FakeScheduleDataSource


class FonteContadora(FakeScheduleDataSource):
    """Conta as leituras por (método, dia), de forma segura entre threads."""

    def __init__(self, base: FakeScheduleDataSource) -> None:
        super().__init__(
            salas=base.salas,
            profissionais=base.profissionais,
            pacientes=base.pacientes,
            grade=base.grade,
            atendimentos=base.atendimentos,
            dias_com_falha=base.dias_com_falha,
        )
        self.leituras: dict[tuple[str, date], int] = {}
        self._lock = threading.Lock()

    def _contar(self, metodo: str, dia: date) -> None:
        with self._lock:
            self.leituras[(metodo, dia)] = self.leituras.get((metodo, dia), 0) + 1

    def listar_grade(self, dia: date) -> list:  # type: ignore[type-arg]
        self._contar("grade", dia)
        return super().listar_grade(dia)

    def listar_atendimentos(self, dia: date) -> list:  # type: ignore[type-arg]
        self._contar("atendimentos", dia)
        return super().listar_atendimentos(dia)


def test_sala_com_merge_vira_postos_independentes_e_fechado_vira_estado() -> None:
    """Pela aba fictícia: `Sala 3` mesclada em D:E (Carla no posto 1, Dora no 2)."""
    dados = parse_worksheet_data(
        SEGUNDA, SEGUNDA_FICTICIA.valores, SEGUNDA_FICTICIA.merges, SEGUNDA_FICTICIA.cores
    )
    fonte = FakeScheduleDataSource(
        salas={SEGUNDA: dados.salas},
        profissionais={SEGUNDA: dados.profissionais},
        pacientes={SEGUNDA: dados.pacientes},
        grade={SEGUNDA: dados.grade},
        atendimentos={SEGUNDA: dados.atendimentos},
    )

    foto = fotografar_periodo(fonte, SEGUNDA, SEGUNDA)
    sala_3 = {(s.indice_posto, s.hora_inicio): s for s in foto.slots if s.sala_id == "sala-3"}

    # FECHADO na coluna da Carla às 08:00 e 08:30.
    assert sala_3[(0, time(8, 0))].estado is EstadoSlot.FECHADO
    assert sala_3[(0, time(8, 0))].profissional_id is None
    assert sala_3[(0, time(9, 0))].estado is EstadoSlot.LIVRE
    # Sessão em grupo da Dora: um atendimento, dois pacientes, o slot conta uma vez.
    grupo = sala_3[(1, time(8, 0))]
    assert grupo.profissional_id == "dora"
    assert grupo.estado is EstadoSlot.OCUPADO
    assert grupo.ocupacoes == 1
    assert len(grupo.paciente_ids) == 2
    assert grupo.convenio == "particular"
    assert sala_3[(1, time(9, 0))].estado is EstadoSlot.LIVRE
    # À tarde o posto 2 da sala 3 não tem ninguém escalado.
    assert sala_3[(1, time(13, 0))].estado is EstadoSlot.FECHADO
    # Toda janela do expediente aparece uma vez por posto da sala.
    assert len(sala_3) == 2 * len(Slot.slots_do_dia(SEGUNDA))


def test_postos_da_mesma_sala_sao_vagas_independentes() -> None:
    foto = fotografar_periodo(fonte_clinica(), SEGUNDA, SEGUNDA)
    sala_2_as_13 = {
        s.indice_posto: s
        for s in foto.slots
        if s.sala_id == SALA_2.id and s.hora_inicio == time(13, 30)
    }

    assert sala_2_as_13[0].profissional_id != sala_2_as_13[1].profissional_id
    assert sala_2_as_13[1].profissional_id == CAU.id
    assert all(s.estado is EstadoSlot.OCUPADO for s in sala_2_as_13.values())
    # 14:30: o posto do Beto está livre e o do Cau, ocupado.
    as_1430 = {
        s.indice_posto: s.estado
        for s in foto.slots
        if s.sala_id == SALA_2.id and s.hora_inicio == time(14, 30)
    }
    assert as_1430 == {0: EstadoSlot.LIVRE, 1: EstadoSlot.OCUPADO}


def test_periodo_do_slot_separa_manha_e_tarde() -> None:
    foto = fotografar_periodo(fonte_clinica(), SEGUNDA, SEGUNDA)

    assert {s.periodo for s in foto.slots if s.hora_inicio < time(12)} == {Periodo.MANHA}
    assert {s.periodo for s in foto.slots if s.hora_inicio >= time(13)} == {Periodo.TARDE}


def test_le_cada_dia_uma_vez_e_domingo_nunca() -> None:
    fonte = FonteContadora(fonte_clinica())

    foto = fotografar_periodo(fonte, SEGUNDA, DOMINGO)

    assert foto.dias_consultados == SEMANA
    assert DOMINGO not in foto.dias_lidos
    assert all(dia != DOMINGO for _, dia in fonte.leituras)
    assert set(fonte.leituras.values()) == {1}
    assert {dia for _, dia in fonte.leituras} == set(SEMANA)


def test_dia_com_falha_deixa_o_resto_e_marca_parcial() -> None:
    foto = fotografar_periodo(fonte_clinica({TERCA, QUARTA}), SEGUNDA, SABADO)

    assert foto.parcial
    assert foto.dias_nao_lidos == [TERCA, QUARTA]
    assert TERCA not in {s.data for s in foto.slots}
    assert foto.dias_lidos == [d for d in SEMANA if d not in (TERCA, QUARTA)]


def test_todos_os_dias_com_falha_sobe_o_erro() -> None:
    with pytest.raises(RuntimeError):
        fotografar_periodo(fonte_clinica({SEGUNDA, TERCA}), SEGUNDA, TERCA)


def test_so_domingo_devolve_fotografia_vazia_sem_ler() -> None:
    fonte = FonteContadora(fonte_clinica())

    foto = fotografar_periodo(fonte, DOMINGO, DOMINGO)

    assert foto.slots == [] and foto.dias_consultados == [] and not foto.parcial
    assert fonte.leituras == {}


def test_atendimento_fora_da_grade_vira_linha_ocupada_fora_da_grade() -> None:
    fonte = FakeScheduleDataSource(
        profissionais={SEGUNDA: [LUCIANA]},
        grade={SEGUNDA: grade(SEGUNDA, [time(8, 0)])},
        atendimentos={SEGUNDA: atendimentos(SEGUNDA, [time(8, 0), time(9, 0)])},
    )

    foto = fotografar_periodo(fonte, SEGUNDA, SEGUNDA)
    por_hora = {s.hora_inicio: s for s in foto.slots if s.profissional_id == LUCIANA.id}

    assert por_hora[time(8, 0)].na_grade and por_hora[time(8, 0)].ocupacoes == 1
    assert not por_hora[time(9, 0)].na_grade
    assert por_hora[time(9, 0)].estado is EstadoSlot.OCUPADO


def test_atendimento_em_outro_posto_vai_para_o_posto_escalado() -> None:
    """Mesma regra de `construir_ocupacao_semanal_profissional`."""
    fonte = FakeScheduleDataSource(
        profissionais={SEGUNDA: [LUCIANA]},
        grade={SEGUNDA: grade(SEGUNDA, [time(8, 0)], indice_posto=0)},
        atendimentos={SEGUNDA: atendimentos(SEGUNDA, [time(8, 0)], indice_posto=1)},
    )

    foto = fotografar_periodo(fonte, SEGUNDA, SEGUNDA)
    da_luciana = [s for s in foto.slots if s.profissional_id == LUCIANA.id]

    assert len(da_luciana) == 1
    assert da_luciana[0].indice_posto == 0 and da_luciana[0].ocupacoes == 1


def test_especialidade_vem_de_listar_profissionais() -> None:
    foto = fotografar_periodo(fonte_clinica(), SEGUNDA, SEGUNDA)

    assert {s.especialidade for s in foto.slots if s.profissional_id == ANA.id} == {
        Especialidade.PSICOLOGIA
    }


def test_aguardando_autorizacao_marca_o_slot() -> None:
    foto = fotografar_periodo(fonte_clinica(), SEGUNDA, SABADO)

    aguardando = {(s.data, s.profissional_id) for s in foto.slots if s.aguardando_autorizacao}

    assert aguardando == {(QUARTA, "beto-reis"), (date(2026, 10, 1), ANA.id)}


def test_paciente_nunca_aparece_no_dump_da_fotografia() -> None:
    from tests.support.agenda_clinica import NOMES_PACIENTES

    foto = fotografar_periodo(fonte_clinica(), SEGUNDA, SABADO)
    dump = foto.model_dump_json()

    assert all(nome not in dump for nome in NOMES_PACIENTES)
    assert "Paciente" not in repr(foto.slots[0])


def test_limites_do_periodo() -> None:
    with pytest.raises(ValueError, match="anterior"):
        dias_do_periodo(TERCA, SEGUNDA)
    with pytest.raises(ValueError, match="longo demais"):
        dias_do_periodo(SEGUNDA, date.fromordinal(SEGUNDA.toordinal() + MAX_DIAS_POR_PERIODO))


def test_atendimento_sem_paciente_conhecido_tem_convenio_nao_informado() -> None:
    sem_cadastro = Atendimento(
        id="x",
        paciente_ids=["sem-registro"],
        profissional_id=LUCIANA.id,
        sala_id="sala-12",
        especialidade=LUCIANA.especialidade,
        slots=[Slot(data=SEGUNDA, hora_inicio=time(8, 0))],
    )
    fonte = FakeScheduleDataSource(
        profissionais={SEGUNDA: [LUCIANA]},
        grade={SEGUNDA: grade(SEGUNDA, [time(8, 0)])},
        atendimentos={SEGUNDA: [sem_cadastro]},
    )

    foto = fotografar_periodo(fonte, SEGUNDA, SEGUNDA)

    assert next(s for s in foto.slots if s.ocupacoes).convenio == "nao_informado"
