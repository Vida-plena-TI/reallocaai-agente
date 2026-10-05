"""Aliases de profissional de ponta a ponta: parser -> ocupação -> busca.

O alias é aplicado pelo parser (`MAPA_ALIAS_PROFISSIONAL`), então estes
testes partem de abas fictícias da planilha, e não de entidades já prontas:
só assim provam que a ocupação e a busca enxergam uma única profissional.
Os nomes são fictícios e o mapa é estendido com `monkeypatch.setitem`.
"""

from datetime import date

import pytest

from app.ai.servico_agenda import (
    ProfissionalEncontrado,
    localizar_profissional,
)
from app.data_sources.google_sheets_parser import parse_worksheet_data
from app.domain import MAPA_ALIAS_PROFISSIONAL
from app.engine.ocupacao import construir_relatorio_ocupacao_do_dia
from app.engine.ocupacao_profissional import construir_ocupacao_semanal_profissional
from tests.support.fake_schedule_data_source import FakeScheduleDataSource

SEGUNDA = date(2026, 9, 28)
TERCA = date(2026, 9, 29)

#: Grafia antiga -> canônica, só para estes testes.
ALIAS_FICTICIO = ("odete-lima", "odette-lima")

#: Segunda: só a grafia antiga, um paciente em dois slots escalados.
SEGUNDA_GRAFIA_ANTIGA = [
    ["", "Sala 1"],
    ["", "Odete Lima (Fono)"],
    ["09:00", "Paciente Um"],
    ["09:30", ""],
]

#: Terça: só a grafia canônica, dois pacientes em três slots escalados.
TERCA_GRAFIA_CANONICA = [
    ["", "Sala 1"],
    ["", "Odette Lima (Fono)"],
    ["09:00", ""],
    ["09:30", "Paciente Dois"],
    ["10:00", "Paciente Três"],
]

#: As duas grafias no mesmo dia: a antiga de manhã, a canônica à tarde.
DIA_COM_AS_DUAS_GRAFIAS = [
    ["", "Sala 1"],
    ["", "Odete Lima (Fono)"],
    ["08:00", "Paciente Um"],
    ["08:30", ""],
    ["12:00", ""],
    ["", "Sala 1"],
    ["", "Odette Lima (Fono)"],
    ["13:00", "Paciente Dois"],
    ["13:30", "Paciente Dois"],
]


def _fonte(abas: dict[date, list[list[str]]]) -> FakeScheduleDataSource:
    """`ScheduleDataSource` com o que o parser extraiu de cada aba fictícia."""
    fonte = FakeScheduleDataSource()
    for dia, valores in abas.items():
        dados = parse_worksheet_data(dia, valores, [], {})
        fonte.salas[dia] = dados.salas
        fonte.profissionais[dia] = dados.profissionais
        fonte.grade[dia] = dados.grade
        fonte.atendimentos[dia] = dados.atendimentos
        fonte.pacientes[dia] = dados.pacientes
    return fonte


def _ids_da_semana(fonte: FakeScheduleDataSource) -> set[str]:
    return {item.id for itens in fonte.profissionais.values() for item in itens}


def test_duas_grafias_em_dias_diferentes_viram_uma_profissional_na_semana(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(MAPA_ALIAS_PROFISSIONAL, *ALIAS_FICTICIO)
    fonte = _fonte({SEGUNDA: SEGUNDA_GRAFIA_ANTIGA, TERCA: TERCA_GRAFIA_CANONICA})

    ocupacao = construir_ocupacao_semanal_profissional(fonte, "odette-lima", SEGUNDA)

    assert _ids_da_semana(fonte) == {"odette-lima"}
    assert ocupacao.nome == "Odette Lima"
    assert [dia.data for dia in ocupacao.dias] == [SEGUNDA, TERCA]
    assert (ocupacao.slots_escalados, ocupacao.slots_ocupados) == (5, 3)


def test_sem_o_alias_as_duas_grafias_dividem_a_ocupacao() -> None:
    """Contraprova do teste acima: é o alias que junta as duas metades."""
    fonte = _fonte({SEGUNDA: SEGUNDA_GRAFIA_ANTIGA, TERCA: TERCA_GRAFIA_CANONICA})

    antiga = construir_ocupacao_semanal_profissional(fonte, "odete-lima", SEGUNDA)
    canonica = construir_ocupacao_semanal_profissional(fonte, "odette-lima", SEGUNDA)

    assert _ids_da_semana(fonte) == {"odete-lima", "odette-lima"}
    assert (antiga.slots_escalados, antiga.slots_ocupados) == (2, 1)
    assert (canonica.slots_escalados, canonica.slots_ocupados) == (3, 2)


def test_duas_grafias_no_mesmo_dia_viram_um_registro_no_relatorio_do_dia(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(MAPA_ALIAS_PROFISSIONAL, *ALIAS_FICTICIO)
    fonte = _fonte({SEGUNDA: DIA_COM_AS_DUAS_GRAFIAS})

    relatorio = construir_relatorio_ocupacao_do_dia(fonte, SEGUNDA)
    dia = construir_ocupacao_semanal_profissional(fonte, "odette-lima", SEGUNDA).dias[0]

    assert [item.profissional_id for item in relatorio.por_profissional] == ["odette-lima"]
    do_relatorio = relatorio.por_profissional[0]
    assert (do_relatorio.slots_escalados, do_relatorio.slots_ocupados) == (4, 3)
    assert (dia.slots_escalados, dia.slots_ocupados) == (4, 3)
    assert dia.percentual == do_relatorio.percentual
    assert (dia.manha_ocupados, dia.tarde_ocupados) == (1, 2)


def test_busca_pela_grafia_antiga_encontra_a_canonica(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(MAPA_ALIAS_PROFISSIONAL, *ALIAS_FICTICIO)
    fonte = _fonte({SEGUNDA: SEGUNDA_GRAFIA_ANTIGA, TERCA: TERCA_GRAFIA_CANONICA})

    resultado = localizar_profissional(fonte, SEGUNDA, "Odete Lima")

    assert resultado == ProfissionalEncontrado(profissional_id="odette-lima", nome="Odette Lima")


def test_sem_o_alias_a_busca_ve_duas_profissionais_distintas() -> None:
    fonte = _fonte({SEGUNDA: SEGUNDA_GRAFIA_ANTIGA, TERCA: TERCA_GRAFIA_CANONICA})

    antiga = localizar_profissional(fonte, SEGUNDA, "Odete Lima")
    canonica = localizar_profissional(fonte, SEGUNDA, "Odette Lima")

    assert antiga == ProfissionalEncontrado(profissional_id="odete-lima", nome="Odete Lima")
    assert canonica == ProfissionalEncontrado(profissional_id="odette-lima", nome="Odette Lima")


def test_mapa_de_alias_real_nao_tem_auto_referencia_nem_cadeia() -> None:
    """O parser aplica o alias uma única vez: um destino que também fosse chave
    deixaria a pessoa dividida entre dois ids."""
    for variante, canonico in MAPA_ALIAS_PROFISSIONAL.items():
        assert variante != canonico, f"{variante!r} aponta para si mesmo"
        assert canonico not in MAPA_ALIAS_PROFISSIONAL, (
            f"{variante!r} -> {canonico!r}, mas {canonico!r} também é variante"
        )
