"""Testes de `localizar_paciente`: busca do paciente na semana da data consultada."""

from datetime import date

import pytest

from app.ai.servico_agenda import localizar_paciente
from app.domain import Convenio, Paciente
from tests.support.fake_schedule_data_source import FakeScheduleDataSource

SEGUNDA = date(2026, 9, 7)
TERCA = date(2026, 9, 8)
QUARTA = date(2026, 9, 9)
SEXTA = date(2026, 9, 11)
SABADO = date(2026, 9, 12)
DOMINGO_ANTERIOR = date(2026, 9, 6)
SEGUNDA_SEGUINTE = date(2026, 9, 14)

THEO = Paciente(id="theo-souza", nome="Theo Souza", convenio=Convenio.UNIMED)


def test_encontrado_no_dia() -> None:
    origem = FakeScheduleDataSource(pacientes={TERCA: [THEO], SEXTA: [THEO]})

    localizacao = localizar_paciente(origem, TERCA, "Théo Souza")

    assert localizacao.paciente_no_dia == THEO
    assert localizacao.paciente_referencia == THEO
    assert localizacao.outros_dias == [SEXTA]
    assert localizacao.sugestoes == []


def test_so_em_outros_dias_lista_os_dias_sem_o_consultado() -> None:
    origem = FakeScheduleDataSource(
        pacientes={
            SEGUNDA: [THEO],
            QUARTA: [THEO],
            SABADO: [THEO],
            DOMINGO_ANTERIOR: [THEO],
            SEGUNDA_SEGUINTE: [THEO],
        }
    )

    localizacao = localizar_paciente(origem, SEXTA, "Theo Souza")

    assert localizacao.paciente_no_dia is None
    assert localizacao.outros_dias == [SEGUNDA, QUARTA, SABADO]
    assert SEXTA not in localizacao.outros_dias
    assert localizacao.paciente_referencia == THEO
    assert localizacao.sugestoes == []


def test_ausente_na_semana_traz_nome_parecido_nas_sugestoes() -> None:
    origem = FakeScheduleDataSource(
        pacientes={SEGUNDA: [THEO], QUARTA: [Paciente(id="ana-lima", nome="Ana Lima")]}
    )

    localizacao = localizar_paciente(origem, SEXTA, "Teo Souza")

    assert localizacao.paciente_no_dia is None
    assert localizacao.paciente_referencia is None
    assert localizacao.outros_dias == []
    assert localizacao.sugestoes == ["Theo Souza"]


def test_ausente_sem_nomes_parecidos_tem_sugestoes_vazias() -> None:
    origem = FakeScheduleDataSource(pacientes={SEGUNDA: [THEO]})

    localizacao = localizar_paciente(origem, SEXTA, "Maria Fernanda Albuquerque")

    assert localizacao.paciente_no_dia is None
    assert localizacao.paciente_referencia is None
    assert localizacao.outros_dias == []
    assert localizacao.sugestoes == []


def test_pacientes_de_nomes_parecidos_permanecem_distintos() -> None:
    theo = Paciente(id="theo-souza", nome="Theo Souza")
    teo = Paciente(id="teo-souza", nome="Teo Souza")
    origem = FakeScheduleDataSource(pacientes={SEGUNDA: [theo], QUARTA: [teo]})

    localizacao = localizar_paciente(origem, SEGUNDA, "Theo Souza")

    assert localizacao.paciente_no_dia == theo
    assert localizacao.outros_dias == []
    assert localizacao.sugestoes == []


def test_dia_com_falha_de_leitura_e_pulado(caplog: pytest.LogCaptureFixture) -> None:
    class FonteComFalha(FakeScheduleDataSource):
        def listar_pacientes(self, dia: date) -> list[Paciente]:
            if dia == QUARTA:
                raise RuntimeError("aba indisponível")
            return super().listar_pacientes(dia)

    origem = FonteComFalha(pacientes={SEGUNDA: [THEO]})

    localizacao = localizar_paciente(origem, SEXTA, "Theo Souza")

    assert localizacao.outros_dias == [SEGUNDA]
    assert "aba indisponível" in caplog.text
