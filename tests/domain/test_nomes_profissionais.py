"""Nome de profissional digitado: acento, caixa, grafia, prefixo, apelido e ambiguidade."""

import pytest

from app.ai.servico_agenda import (
    ProfissionalAmbiguo,
    ProfissionalEncontrado,
    ProfissionalNaoEncontrado,
    resolver_profissional,
)
from app.domain import PROFISSIONAIS_APELIDOS, Especialidade, Profissional
from app.domain.nomes_profissionais import chave_de_comparacao, resolver_nome

#: Primeiros nomes repetidos de propósito, como acontece na equipe.
EQUIPE = {
    "ana-carolina": "Ana Carolina",
    "ana-paula": "Ana Paula",
    "maria-clara": "Maria Clara",
    "maria-fernanda": "Maria Fernanda",
    "maria-luiza": "Maria Luiza",
    "debora": "Débora",
    "assizelly": "ASSIZELLY",
    "rossana": "Rossana",
    "bia": "Bia",
}


@pytest.mark.parametrize(
    ("digitado", "esperado"),
    [
        ("DÉBORA", ("debora",)),
        ("debora", ("debora",)),
        ("Rossana Belfort", ("rossana",)),
        ("Ana Paula", ("ana-paula",)),
        ("maria clara", ("maria-clara",)),
        # Grafia: letras dobradas e y = i só na comparação.
        ("Assizely", ("assizelly",)),
        ("ASSIZELI", ("assizelly",)),
        ("Rosana", ("rossana",)),
        # Apelido confirmado em PROFISSIONAIS_APELIDOS.
        ("beatriz", ("bia",)),
        ("Beatriz", ("bia",)),
    ],
)
def test_resolve_um_unico_profissional(digitado: str, esperado: tuple[str, ...]) -> None:
    assert resolver_nome(EQUIPE, digitado) == esperado


@pytest.mark.parametrize(
    ("digitado", "candidatos"),
    [
        ("ana", ("ana-carolina", "ana-paula")),
        ("ANA", ("ana-carolina", "ana-paula")),
        ("maria", ("maria-clara", "maria-fernanda", "maria-luiza")),
    ],
)
def test_ambiguo_devolve_todos_os_candidatos(digitado: str, candidatos: tuple[str, ...]) -> None:
    assert resolver_nome(EQUIPE, digitado) == candidatos


@pytest.mark.parametrize("digitado", ["", "Zuleide", "Ross", "Ana Pa"])
def test_ninguem(digitado: str) -> None:
    # Palavra pela metade não casa: "Ross" não é "Rossana".
    assert resolver_nome(EQUIPE, digitado) == ()


def test_quem_se_chama_beatriz_vence_o_apelido() -> None:
    assert PROFISSIONAIS_APELIDOS["beatriz"] == "bia"
    assert resolver_nome({**EQUIPE, "beatriz": "Beatriz"}, "beatriz") == ("beatriz",)


def test_apelido_sem_a_profissional_na_semana_nao_casa() -> None:
    sem_bia = {k: v for k, v in EQUIPE.items() if k != "bia"}
    assert resolver_nome(sem_bia, "beatriz") == ()


def test_chave_so_para_comparar() -> None:
    assert chave_de_comparacao("ASSIZELLY") == chave_de_comparacao("Assizely") == "asizeli"
    profissionais = {
        "assizelly": Profissional(
            id="assizelly", nome="ASSIZELLY", especialidade=Especialidade.PSICOLOGIA
        )
    }
    # O nome exibido é sempre o da planilha.
    assert resolver_profissional(profissionais, "Assizely") == ProfissionalEncontrado(
        profissional_id="assizelly", nome="ASSIZELLY"
    )


def test_resolver_profissional_ambiguo_e_nao_encontrado() -> None:
    profissionais = {
        i: Profissional(id=i, nome=nome, especialidade=Especialidade.FONOAUDIOLOGIA)
        for i, nome in EQUIPE.items()
    }
    ambiguo = resolver_profissional(profissionais, "maria")
    assert isinstance(ambiguo, ProfissionalAmbiguo)
    assert [c.nome for c in ambiguo.candidatos] == ["Maria Clara", "Maria Fernanda", "Maria Luiza"]
    assert isinstance(resolver_profissional(profissionais, "Zuleide"), ProfissionalNaoEncontrado)
