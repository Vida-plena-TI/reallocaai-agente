"""Testes de `localizar_profissional`: resolução do nome citado entre os profissionais da semana."""

import logging

import pytest

from app.ai.servico_agenda import (
    ProfissionalAmbiguo,
    ProfissionalEncontrado,
    ProfissionalNaoEncontrado,
    localizar_profissional,
)
from app.domain import Especialidade, Profissional
from tests.support.agenda_semanal import QUARTA, SEGUNDA, SEMANA, SEXTA, TERCA
from tests.support.fake_schedule_data_source import FakeScheduleDataSource

JOANA = Profissional(id="joana", nome="Joana", especialidade=Especialidade.PSICOLOGIA)
JOAO_PEDRO = Profissional(
    id="joao-pedro", nome="João Pedro", especialidade=Especialidade.FONOAUDIOLOGIA
)
JOAO_PAULO = Profissional(
    id="joao-paulo", nome="João Paulo", especialidade=Especialidade.PSICOLOGIA
)
BEATRIZ = Profissional(id="beatriz", nome="Beatriz", especialidade=Especialidade.MUSICOTERAPIA)


def _origem() -> FakeScheduleDataSource:
    # Profissionais espalhados pela semana: a busca precisa olhar todos os dias.
    return FakeScheduleDataSource(
        profissionais={SEGUNDA: [JOANA], TERCA: [JOAO_PEDRO, JOANA], SEXTA: [JOAO_PAULO]}
    )


def test_id_exato() -> None:
    resultado = localizar_profissional(_origem(), QUARTA, "joana")

    assert resultado == ProfissionalEncontrado(profissional_id="joana", nome="Joana")


def test_nome_completo_casa_por_prefixo_de_palavras() -> None:
    resultado = localizar_profissional(_origem(), QUARTA, "Joana Silveira")

    assert resultado == ProfissionalEncontrado(profissional_id="joana", nome="Joana")


def test_parte_do_nome_casa_quando_so_um_profissional_comeca_assim() -> None:
    resultado = localizar_profissional(_origem(), QUARTA, "João Pe")

    # "joao-pe" não é prefixo de palavras de "joao-pedro" ("pe" != "pedro").
    assert isinstance(resultado, ProfissionalNaoEncontrado)


def test_ambiguo_devolve_os_candidatos_sem_escolher() -> None:
    resultado = localizar_profissional(_origem(), QUARTA, "João")

    assert isinstance(resultado, ProfissionalAmbiguo)
    assert [candidato.id for candidato in resultado.candidatos] == ["joao-paulo", "joao-pedro"]


def test_insensivel_a_acento_e_caixa() -> None:
    resultado = localizar_profissional(_origem(), QUARTA, "JOÃO pedro")

    assert resultado == ProfissionalEncontrado(profissional_id="joao-pedro", nome="João Pedro")


def test_nao_encontrado_lista_os_nomes_da_semana() -> None:
    resultado = localizar_profissional(_origem(), QUARTA, "Beatriz")

    assert resultado == ProfissionalNaoEncontrado(
        nomes_disponiveis=["Joana", "João Paulo", "João Pedro"]
    )


def test_texto_vazio_nao_casa_com_ninguem() -> None:
    resultado = localizar_profissional(_origem(), QUARTA, "  ")

    assert isinstance(resultado, ProfissionalNaoEncontrado)


def test_dia_com_falha_e_pulado(caplog: pytest.LogCaptureFixture) -> None:
    origem = FakeScheduleDataSource(
        profissionais={SEGUNDA: [BEATRIZ], TERCA: [JOANA]}, dias_com_falha={SEGUNDA}
    )

    with caplog.at_level(logging.WARNING):
        resultado = localizar_profissional(origem, QUARTA, "Joana")

    assert resultado == ProfissionalEncontrado(profissional_id="joana", nome="Joana")
    assert "falha simulada" in caplog.text


def test_falha_em_todos_os_dias_levanta_o_erro() -> None:
    origem = FakeScheduleDataSource(dias_com_falha=set(SEMANA))

    with pytest.raises(RuntimeError, match="falha simulada"):
        localizar_profissional(origem, QUARTA, "Joana")
