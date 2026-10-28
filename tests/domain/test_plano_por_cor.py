"""Plano pela matiz da cor do texto: calibração, preto e a regra do trecho do nome."""

import pytest

from app.domain import (
    PLANO_POR_COR,
    NomeColorido,
    TrechoColorido,
    e_preto,
    ler_nome_colorido,
    plano_da_cor,
)
from app.domain import plano_por_cor as modulo
from app.domain.constants import FaixaDePlano


def _nome(*trechos: tuple[str, str]) -> NomeColorido:
    return NomeColorido(
        atendimento_id="2026-09-28-B3",
        trechos=tuple(TrechoColorido(texto=texto, cor=cor) for texto, cor in trechos),
    )


@pytest.mark.parametrize(
    ("cor", "plano"),
    [
        # As cores da planilha real (out/2026).
        ("#0000FF", "SulAmérica"),
        ("#FF0000", "Klini"),
        ("#9900FF", "Particulares"),
        ("#FF9900", "Care Plus"),
        ("#38761D", "Unimed"),
        ("#274E13", "Unimed"),
        # Tons vizinhos da paleta do Sheets.
        ("#CC0000", "Klini"),
        ("#4A86E8", "SulAmérica"),
        ("#E69138", "Care Plus"),
        ("#6AA84F", "Unimed"),
    ],
)
def test_cores_da_planilha_e_tons_vizinhos(cor: str, plano: str) -> None:
    assert not e_preto(cor)
    assert plano_da_cor(cor) == plano


@pytest.mark.parametrize("cor", ["#FF00FF", "#00FFFF", "#FFFF00", "#666666", "#999999"])
def test_fora_das_faixas_e_nao_reconhecida(cor: str) -> None:
    assert not e_preto(cor)
    assert plano_da_cor(cor) is None


@pytest.mark.parametrize("cor", ["#000000", "#202020", "#434343", "#1A0000", "#00001F"])
def test_preto_e_tons_muito_escuros(cor: str) -> None:
    assert e_preto(cor)


def test_verde_escuro_da_unimed_nao_e_preto() -> None:
    # V 0,31 e S 0,76: escuro, mas saturado.
    assert not e_preto("#274E13")


def test_ordem_dos_planos_e_a_do_relatorio() -> None:
    assert [faixa.rotulo for faixa in PLANO_POR_COR] == [
        "SulAmérica",
        "Klini",
        "Particulares",
        "Care Plus",
        "Unimed",
    ]


def test_remover_um_plano_e_tirar_uma_linha(monkeypatch: pytest.MonkeyPatch) -> None:
    sem_unimed = tuple(f for f in PLANO_POR_COR if f.rotulo != "Unimed")
    monkeypatch.setattr(modulo, "PLANO_POR_COR", sem_unimed)

    assert plano_da_cor("#38761D") is None


def test_incluir_um_plano_e_acrescentar_uma_linha(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        modulo, "PLANO_POR_COR", (*PLANO_POR_COR, FaixaDePlano("Ficticio", 170, 190))
    )

    assert plano_da_cor("#00FFFF") == "Ficticio"


def test_nome_colorido_com_observacao_preta_vale_o_trecho_do_nome() -> None:
    leitura = ler_nome_colorido(_nome(("Ana Souza ", "#0000FF"), ("(falta)", "#000000")))

    assert leitura.nome == "Ana Souza"
    assert leitura.plano == "SulAmérica"
    assert not leitura.preto


def test_observacao_preta_no_meio_nao_cola_as_palavras() -> None:
    leitura = ler_nome_colorido(
        _nome(("Ana", "#FF0000"), (" repos ", "#000000"), ("Souza", "#FF0000"))
    )

    assert leitura.nome == "Ana Souza"
    assert leitura.plano == "Klini"


def test_todo_preto_e_ignorado() -> None:
    leitura = ler_nome_colorido(_nome(("Ana Souza", "#000000")))

    assert leitura.preto and leitura.plano is None


def test_cor_nao_reconhecida_nao_e_preto_nem_plano() -> None:
    leitura = ler_nome_colorido(_nome(("Ana Souza", "#FF00FF")))

    assert not leitura.preto and leitura.plano is None


def test_duas_cores_no_mesmo_nome_vale_a_de_mais_letras() -> None:
    # Caso real: as letras em verde e um parêntese solto em lilás.
    leitura = ler_nome_colorido(_nome(("Ana Souza", "#38761D"), (")", "#9900FF")))

    assert leitura.plano == "Unimed"
    assert leitura.nome == "Ana Souza"


def test_empate_de_letras_fica_com_a_primeira_cor() -> None:
    leitura = ler_nome_colorido(_nome(("Ana", "#FF9900"), ("Bia", "#0000FF")))

    assert leitura.plano == "Care Plus"
