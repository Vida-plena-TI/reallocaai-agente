"""O oráculo e a semana fictícia: independência do motor e contagens conferidas à mão."""

import ast
from datetime import date, time
from pathlib import Path

import pytest

from tests.casos_reais import oraculo
from tests.casos_reais.planilha import PlanilhaContada, payload_da_semana

SEGUNDA = date(2026, 9, 28)
TERCA = date(2026, 9, 29)
QUARTA = date(2026, 9, 30)
QUINTA = date(2026, 10, 1)
SABADO = date(2026, 10, 3)
DOMINGO = date(2026, 10, 4)
SEMANA = [SEGUNDA, TERCA, QUARTA, QUINTA, date(2026, 10, 2), SABADO]

PASTA = Path(__file__).resolve().parent


@pytest.mark.parametrize("arquivo", ["oraculo.py", "planilha.py"])
def test_nao_importa_nada_do_motor(arquivo: str) -> None:
    """O esperado não pode sair do código que está sendo testado."""
    arvore = ast.parse((PASTA / arquivo).read_text(encoding="utf-8"))
    modulos = [
        nome
        for no in ast.walk(arvore)
        for nome in (
            [alias.name for alias in no.names]
            if isinstance(no, ast.Import)
            else [no.module or ""]
            if isinstance(no, ast.ImportFrom)
            else []
        )
    ]
    assert not [m for m in modulos if m == "app" or m.startswith(("app.", "scripts"))]


def _horas(vagas: list[oraculo.Vaga], sala: int) -> list[str]:
    return [vaga.hora.strftime("%H:%M") for vaga in vagas if vaga.sala == sala]


def test_vagas_conferidas_a_mao_na_terca() -> None:
    vagas = oraculo.vagas(payload_da_semana(), TERCA)
    # Sala 1 (Bia): "AA..BBCCDD ..EEFF..GG".
    assert _horas(vagas, 1) == ["08:00", "08:30", "13:00", "13:30", "16:00", "16:30"]
    assert {vaga.profissional for vaga in vagas if vaga.sala == 1} == {"Bia"}


def test_fechado_nao_e_vaga_e_meio_dia_nao_e_expediente() -> None:
    vagas = oraculo.vagas(payload_da_semana(), SABADO)
    assert "07:00" not in _horas(vagas, 1)
    assert "07:30" not in _horas(vagas, 1)
    assert all(vaga.hora != time(12) for vaga in vagas)


def test_total_de_vagas_da_segunda() -> None:
    # Pontos de cada sala em `AGENDA["Segunda"]`, contados à mão.
    vagas = oraculo.vagas(payload_da_semana(), SEGUNDA)
    assert [len(_horas(vagas, sala)) for sala in range(1, 6)] == [8, 8, 11, 6, 12]


def test_sala_sem_coluna_e_domingo_nao_tem_vaga() -> None:
    assert _horas(oraculo.vagas(payload_da_semana(), QUINTA), 5) == []
    assert oraculo.vagas(payload_da_semana(), DOMINGO) == []


@pytest.mark.parametrize(
    ("nome", "dia", "esperado"),
    [
        (None, QUINTA, "nao_existe"),
        ("Zuleide", QUINTA, "nao_existe"),
        ("Elo", QUINTA, "nao_atende_no_dia"),
        ("Carol", QUINTA, "sem_vaga_na_janela"),
        ("Bia", QUINTA, "com_vaga"),
        ("ASSIZELY", QUARTA, "sem_vaga_na_janela"),
    ],
)
def test_estado_do_profissional_na_janela_das_10_as_12(
    nome: str | None, dia: date, esperado: str
) -> None:
    estado = oraculo.estado_profissional(payload_da_semana(), nome, dia, SEMANA, time(10), time(12))
    assert estado == esperado


def test_planilha_contada_busca_uma_vez_e_conta_todas() -> None:
    buscas: list[object] = []

    def buscar(params: object) -> dict[str, object]:
        buscas.append(params)
        return {"sheets": []}

    planilha = PlanilhaContada(buscar)
    planilha.fetch_sheet_metadata({"a": "1"})
    planilha.fetch_sheet_metadata()
    assert planilha.chamadas == 2
    assert buscas == [{"a": "1"}]


def test_rotulo_da_especialidade_vem_do_cabecalho() -> None:
    assert oraculo.rotulo_do_profissional("Bia (Fono)") == "Fono"
    assert oraculo.rotulo_do_profissional("Bia") == ""
    vagas = oraculo.vagas(payload_da_semana(), TERCA)
    assert {vaga.especialidade for vaga in vagas if vaga.sala == 2} == {"TO"}


def test_maior_sequencia_conferida_a_mao() -> None:
    """10h às 12h (posições 6 a 9 da manhã). Quarta: Elo (Fono) 10:00, Duda
    (Psicomotricidade) 10:30 e 11:00, Carol (Psicologia) 11:00; TO lotada -> 3.
    Quinta: Bia (Fono) 10:00 e 10:30, Duda 10:30, 11:00 e 11:30; Carol e TO
    lotadas -> 2 (fono e psicomotricidade)."""
    quatro = ["Fono", "Psicomotricidade", "Psicologia", "TO"]
    payload = payload_da_semana()

    assert oraculo.maior_sequencia(payload, QUARTA, quatro, time(10), time(12)) == 3
    assert oraculo.maior_sequencia(payload, QUINTA, quatro, time(10), time(12)) == 2
    assert oraculo.maior_sequencia(payload, QUARTA, ["TO"], time(10), time(12)) == 0
    assert oraculo.maior_sequencia(payload, DOMINGO, quatro, time(10), time(12)) == 0


def test_cenario_poucas_vagas_conferido_a_mao() -> None:
    """c09: TO com 1 vaga na quarta (14:00) e 4 na quinta, todas fora das 10h às 12h."""
    payload = payload_da_semana("poucas_vagas")

    assert _horas(oraculo.vagas(payload, QUARTA), 2) == ["14:00"]
    assert _horas(oraculo.vagas(payload, QUINTA), 2) == ["07:00", "07:30", "08:30", "09:00"]
    assert _horas(oraculo.vagas(payload, QUARTA), 3) == []
    assert "10:00" in _horas(oraculo.vagas(payload, TERCA), 2)
    # A semana padrão não mudou.
    assert payload_da_semana() == payload_da_semana(None)
