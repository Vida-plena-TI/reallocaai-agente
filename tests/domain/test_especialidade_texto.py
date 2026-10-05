"""Testes de `reconhecer_especialidade` (texto livre -> `Especialidade`)."""

import pytest

from app.domain import Especialidade, reconhecer_especialidade


@pytest.mark.parametrize(
    ("texto", "esperada"),
    [
        ("Psicologia", Especialidade.PSICOLOGIA),
        ("psicóloga", Especialidade.PSICOLOGIA),
        ("FONO", Especialidade.FONOAUDIOLOGIA),
        ("TO", Especialidade.TERAPIA_OCUPACIONAL),
        ("terapia ocupacional", Especialidade.TERAPIA_OCUPACIONAL),
        ("terapia_alimentar", Especialidade.TERAPIA_ALIMENTAR),
        ("Terapia Alimentar", Especialidade.TERAPIA_ALIMENTAR),
        ("nutrição", Especialidade.TERAPIA_ALIMENTAR),
        ("fisioterapia", Especialidade.PSICOMOTRICIDADE),
        ("psicomotricidade", Especialidade.PSICOMOTRICIDADE),
        ("psicopedagoga", Especialidade.PSICOPEDAGOGIA),
        ("musicoterapia", Especialidade.MUSICOTERAPIA),
    ],
)
def test_reconhece_nome_e_grafias_da_planilha(texto: str, esperada: Especialidade) -> None:
    assert reconhecer_especialidade(texto) is esperada


@pytest.mark.parametrize("texto", ["astrologia", "", "  ", "terapia"])
def test_texto_sem_especialidade_devolve_none(texto: str) -> None:
    assert reconhecer_especialidade(texto) is None
