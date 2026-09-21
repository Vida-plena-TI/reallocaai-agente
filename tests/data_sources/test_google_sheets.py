"""Testes da casca do `GoogleSheetsDataSource`.

O que se testa aqui é só a tradução do payload da API e o cache por dia: a
planilha é um duplo em memória, nada sai para a rede e nenhuma credencial é
lida. O parsing propriamente dito tem os seus próprios testes.
"""

from datetime import date
from typing import Any, cast

import gspread
import pytest

from app.data_sources.google_sheets import (
    GoogleSheetsDataSource,
    _intervalo_para_a1,
    _valores_e_cores,
)
from app.data_sources.google_sheets_parser import parse_worksheet_data, rotulo_da_coluna
from tests.data_sources.fixtures import SEGUNDA_FICTICIA, AbaFicticia

SEGUNDA = date(2026, 9, 7)
DOMINGO = date(2026, 9, 13)


class PlanilhaFalsa:
    """Devolve um metadata fixo e conta quantas vezes foi consultada."""

    def __init__(self, abas: list[dict[str, Any]]) -> None:
        self.abas = abas
        self.chamadas = 0

    def fetch_sheet_metadata(self, params: dict[str, str] | None = None) -> dict[str, Any]:
        self.chamadas += 1
        return {"sheets": self.abas}


def canais(hexadecimal: str) -> dict[str, float]:
    """Converte `#RRGGBB` no formato de cor da API (canais de 0 a 1)."""
    valores = {
        nome: int(hexadecimal[posicao : posicao + 2], 16) / 255
        for nome, posicao in (("red", 1), ("green", 3), ("blue", 5))
    }
    # A API omite os canais iguais a zero.
    return {nome: valor for nome, valor in valores.items() if valor}


def payload_da_aba(titulo: str, aba: AbaFicticia, merges: list[dict[str, int]]) -> dict[str, Any]:
    """Monta o payload que a API devolveria para uma aba fictícia."""
    linhas: list[dict[str, Any]] = []
    for indice_da_linha, linha in enumerate(aba.valores):
        celulas: list[dict[str, Any]] = []
        for indice_da_coluna, texto in enumerate(linha):
            celula: dict[str, Any] = {"formattedValue": texto} if texto else {}
            endereco = f"{rotulo_da_coluna(indice_da_coluna)}{indice_da_linha + 1}"
            cor = aba.cores.get(endereco)
            if cor is not None:
                celula["userEnteredFormat"] = {"textFormat": {"foregroundColor": canais(cor)}}
            celulas.append(celula)
        linhas.append({"values": celulas})
    return {
        "properties": {"title": titulo},
        "merges": merges,
        "data": [{"rowData": linhas}],
    }


#: Os mesmos merges de `SEGUNDA_FICTICIA` (`A1:F1`, `D2:E2`, `D9:E9`) no formato
#: `GridRange` da API.
MERGES_DA_SEGUNDA = [
    {"startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 6},
    {"startRowIndex": 1, "endRowIndex": 2, "startColumnIndex": 3, "endColumnIndex": 5},
    {"startRowIndex": 8, "endRowIndex": 9, "startColumnIndex": 3, "endColumnIndex": 5},
]


def fonte(abas: list[dict[str, Any]]) -> tuple[GoogleSheetsDataSource, PlanilhaFalsa]:
    planilha = PlanilhaFalsa(abas)
    return GoogleSheetsDataSource(cast(gspread.Spreadsheet, planilha)), planilha


def test_le_a_aba_do_dia_e_entrega_o_mesmo_resultado_do_parser() -> None:
    aba = payload_da_aba("Segunda", SEGUNDA_FICTICIA, MERGES_DA_SEGUNDA)
    origem, _ = fonte([payload_da_aba("Terça", SEGUNDA_FICTICIA, []), aba])

    esperado = parse_worksheet_data(
        SEGUNDA, SEGUNDA_FICTICIA.valores, SEGUNDA_FICTICIA.merges, SEGUNDA_FICTICIA.cores
    )

    assert origem.listar_salas(SEGUNDA) == esperado.salas
    assert origem.listar_profissionais(SEGUNDA) == esperado.profissionais
    assert origem.listar_grade(SEGUNDA) == esperado.grade
    assert origem.listar_atendimentos(SEGUNDA) == esperado.atendimentos
    assert origem.listar_pacientes(SEGUNDA) == esperado.pacientes


def test_busca_a_planilha_uma_vez_por_dia() -> None:
    origem, planilha = fonte([payload_da_aba("Segunda", SEGUNDA_FICTICIA, MERGES_DA_SEGUNDA)])

    origem.listar_salas(SEGUNDA)
    origem.listar_profissionais(SEGUNDA)
    origem.listar_grade(SEGUNDA)
    origem.listar_atendimentos(SEGUNDA)

    assert planilha.chamadas == 1


def test_domingo_nem_chega_a_consultar_a_api() -> None:
    origem, planilha = fonte([payload_da_aba("Segunda", SEGUNDA_FICTICIA, MERGES_DA_SEGUNDA)])

    assert origem.listar_grade(DOMINGO) == []
    assert origem.listar_atendimentos(DOMINGO) == []
    assert planilha.chamadas == 0


def test_aba_do_dia_ausente_devolve_vazio_com_aviso(caplog: pytest.LogCaptureFixture) -> None:
    origem, _ = fonte([payload_da_aba("Terça", SEGUNDA_FICTICIA, [])])

    with caplog.at_level("WARNING"):
        salas = origem.listar_salas(SEGUNDA)

    assert salas == []
    assert "não tem aba 'Segunda'" in caplog.text


def test_celula_sem_cor_no_payload_conta_como_fonte_preta() -> None:
    aba = AbaFicticia(valores=[["", "Sala 1"], ["", "Ivo (Fono)"], ["09:00", "Paciente Um"]])
    origem, _ = fonte([payload_da_aba("Segunda", aba, [])])

    atendimentos = origem.listar_atendimentos(SEGUNDA)

    assert atendimentos[0].aguardando_autorizacao is True


def test_valores_e_cores_so_registra_cor_de_celula_com_texto() -> None:
    aba = AbaFicticia(
        valores=[["", "Sala 1"], ["", "Ivo (Fono)"], ["09:00", "Paciente Um"]],
        cores={"B3": "#0000FF"},
    )

    valores, cores = _valores_e_cores(payload_da_aba("Segunda", aba, []))

    assert valores[2] == ["09:00", "Paciente Um"]
    assert cores["B3"] == "#0000FF"
    assert "A1" not in cores


@pytest.mark.parametrize(
    ("intervalo", "esperado"),
    [
        (
            {"startRowIndex": 1, "endRowIndex": 2, "startColumnIndex": 7, "endColumnIndex": 9},
            "H2:I2",
        ),
        (
            {"startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 15},
            "A1:O1",
        ),
        (
            {"startRowIndex": 13, "endRowIndex": 15, "startColumnIndex": 0, "endColumnIndex": 15},
            "A14:O15",
        ),
        (
            {"startRowIndex": 1, "endRowIndex": 2, "startColumnIndex": 26, "endColumnIndex": 27},
            "AA2:AA2",
        ),
    ],
)
def test_grid_range_vira_notacao_a1(intervalo: dict[str, int], esperado: str) -> None:
    assert _intervalo_para_a1(intervalo) == esperado
