"""Testes do value object `Slot`."""

from datetime import date, time

import pytest
from pydantic import ValidationError

from app.domain import (
    DURACAO_SLOT_MINUTOS,
    FIM_PAUSA,
    INICIO_PAUSA,
    Slot,
    SlotForaDoExpedienteError,
    SlotForaDoGridError,
    SlotNaPausaError,
)

DIA = date(2026, 9, 10)


def test_slots_do_dia_gera_vinte_slots_uteis() -> None:
    slots = Slot.slots_do_dia(DIA)

    assert len(slots) == 20
    assert slots[0].hora_inicio == time(7, 0)
    assert slots[-1].hora_inicio == time(17, 30)
    assert slots[-1].hora_fim == time(18, 0)


def test_slots_do_dia_nao_inclui_a_pausa() -> None:
    slots = Slot.slots_do_dia(DIA)

    assert all(not (INICIO_PAUSA <= slot.hora_inicio < FIM_PAUSA) for slot in slots)
    assert all(slot.hora_fim <= INICIO_PAUSA or slot.hora_inicio >= FIM_PAUSA for slot in slots)


def test_slots_do_dia_vem_ordenado_e_todos_no_mesmo_dia() -> None:
    slots = Slot.slots_do_dia(DIA)

    assert slots == sorted(slots)
    assert {slot.data for slot in slots} == {DIA}


@pytest.mark.parametrize("hora", [time(6, 30), time(6, 0), time(18, 0), time(19, 0)])
def test_slot_fora_do_expediente_levanta_erro(hora: time) -> None:
    with pytest.raises(SlotForaDoExpedienteError):
        Slot(data=DIA, hora_inicio=hora)


@pytest.mark.parametrize("hora", [time(12, 0), time(12, 30)])
def test_slot_na_pausa_levanta_erro(hora: time) -> None:
    with pytest.raises(SlotNaPausaError):
        Slot(data=DIA, hora_inicio=hora)


@pytest.mark.parametrize("hora", [time(9, 15), time(10, 10), time(8, 0, 30)])
def test_slot_fora_do_grid_levanta_erro(hora: time) -> None:
    with pytest.raises(SlotForaDoGridError):
        Slot(data=DIA, hora_inicio=hora)


def test_slot_e_imutavel() -> None:
    slot = Slot(data=DIA, hora_inicio=time(9, 0))

    with pytest.raises(ValidationError):
        slot.hora_inicio = time(10, 0)  # type: ignore[misc]


def test_e_contiguo_a_verdadeiro_para_slots_sequenciais() -> None:
    primeiro = Slot(data=DIA, hora_inicio=time(9, 0))
    segundo = Slot(data=DIA, hora_inicio=time(9, 30))

    assert primeiro.e_contiguo_a(segundo)
    assert segundo.e_contiguo_a(primeiro)


def test_e_contiguo_a_falso_quando_ha_buraco() -> None:
    primeiro = Slot(data=DIA, hora_inicio=time(9, 0))
    distante = Slot(data=DIA, hora_inicio=time(10, 0))

    assert not primeiro.e_contiguo_a(distante)


def test_e_contiguo_a_falso_em_dias_diferentes() -> None:
    hoje = Slot(data=DIA, hora_inicio=time(17, 30))
    amanha = Slot(data=date(2026, 9, 11), hora_inicio=time(7, 0))

    assert not hoje.e_contiguo_a(amanha)


def test_e_contiguo_a_falso_atravessando_a_pausa() -> None:
    antes = Slot(data=DIA, hora_inicio=time(11, 30))
    depois = Slot(data=DIA, hora_inicio=time(13, 0))

    assert not antes.e_contiguo_a(depois)


def test_hora_fim_respeita_a_duracao_do_slot() -> None:
    slot = Slot(data=DIA, hora_inicio=time(9, 0))

    assert DURACAO_SLOT_MINUTOS == 30
    assert slot.hora_fim == time(9, 30)


def test_slots_sao_ordenaveis_e_comparaveis() -> None:
    cedo = Slot(data=DIA, hora_inicio=time(9, 0))
    tarde = Slot(data=DIA, hora_inicio=time(15, 0))
    outro_dia = Slot(data=date(2026, 9, 11), hora_inicio=time(7, 0))

    assert cedo < tarde < outro_dia
    assert sorted([outro_dia, tarde, cedo]) == [cedo, tarde, outro_dia]
    assert cedo == Slot(data=DIA, hora_inicio=time(9, 0))
