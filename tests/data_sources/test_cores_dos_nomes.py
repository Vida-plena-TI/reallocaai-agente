"""Cor de cada trecho dos nomes de paciente: parser, casca do Sheets e cache.

Nomes fictícios. O que se confere: os trechos (`textFormatRuns`) são
aplicados ao texto bruto antes de juntar espaços, o índice UTF-16 da API é
respeitado, a leitura continua sendo uma chamada só por semana e nada do que
já existia (convênio, sessões, grade) muda.
"""

import logging
from datetime import date
from typing import Any, cast

import gspread
import pytest

from app.data_sources.cache import CacheadoScheduleDataSource
from app.data_sources.google_sheets import _CAMPOS_DA_GRADE, GoogleSheetsDataSource
from app.data_sources.google_sheets_parser import parse_worksheet_data
from app.domain import NomeColorido, TrechoColorido
from tests.data_sources.fixtures import SEGUNDA_FICTICIA
from tests.data_sources.test_google_sheets import canais, payload_da_aba
from tests.support.fake_schedule_data_source import FakeScheduleDataSource

SEGUNDA = date(2026, 9, 7)
AZUL, VERMELHO, PRETO = "#0000FF", "#FF0000", "#000000"


def _aba(*celulas: str) -> list[list[str]]:
    """Uma sala, um profissional e uma linha de horário por célula (09:00, 09:30...)."""
    horas = ["09:00", "09:30", "10:00", "10:30"]
    return [
        ["", "Sala 1"],
        ["", "Ivo (Fono)"],
        *([h, c] for h, c in zip(horas, celulas, strict=False)),
    ]


def _trechos(nome: NomeColorido) -> list[tuple[str, str]]:
    return [(trecho.texto, trecho.cor) for trecho in nome.trechos]


def test_sem_trechos_o_nome_tem_a_cor_da_celula() -> None:
    dados = parse_worksheet_data(SEGUNDA, _aba("Paciente Ficticio Um"), [], {"B3": AZUL})

    assert [_trechos(n) for n in dados.nomes_coloridos] == [[("Paciente Ficticio Um", AZUL)]]
    assert dados.nomes_coloridos[0].atendimento_id == dados.atendimentos[0].id


def test_trechos_aplicados_no_texto_bruto_antes_de_juntar_espacos() -> None:
    # "Ana  Ficticia\n(falta)": o "(" está no índice 14 do texto bruto, mas no 13
    # do texto achatado. Aplicar depois de achatar pintaria o espaço de preto.
    bruto = "Ana  Ficticia\n(falta)"
    dados = parse_worksheet_data(
        SEGUNDA, _aba(bruto), [], {"B3": AZUL}, {"B3": [(0, AZUL), (14, PRETO)]}
    )

    assert [_trechos(n) for n in dados.nomes_coloridos] == [
        [("Ana Ficticia ", AZUL), ("(falta)", PRETO)]
    ]


def test_indice_dos_trechos_em_unidades_utf16() -> None:
    # O emoji ocupa 2 unidades UTF-16 e 1 caractere: o "B" está na unidade 9.
    bruto = "\N{GRINNING FACE} Ana / Bia"
    dados = parse_worksheet_data(
        SEGUNDA, _aba(bruto), [], {"B3": AZUL}, {"B3": [(0, AZUL), (9, VERMELHO)]}
    )

    primeiro, segundo = ({cor: texto for texto, cor in _trechos(n)} for n in dados.nomes_coloridos)
    assert "Ana" in primeiro[AZUL]
    assert segundo[VERMELHO] == "Bia"


def test_sessao_em_grupo_com_duas_cores_gera_um_nome_de_cada_cor() -> None:
    texto = "Paciente Ficticio Um / Paciente Ficticio Dois"
    dados = parse_worksheet_data(
        SEGUNDA, _aba(texto), [], {"B3": VERMELHO}, {"B3": [(0, VERMELHO), (22, AZUL)]}
    )

    assert [n.atendimento_id for n in dados.nomes_coloridos] == [dados.atendimentos[0].id] * 2
    cores = [{t.cor for t in n.trechos if t.texto.strip(" /")} for n in dados.nomes_coloridos]
    assert cores == [{VERMELHO}, {AZUL}]


def test_sessao_de_uma_hora_repete_o_nome_so_quando_a_cor_muda() -> None:
    mesma = parse_worksheet_data(
        SEGUNDA, _aba("Paciente Ficticio Um", "Paciente Ficticio Um"), [], {"B3": AZUL, "B4": AZUL}
    )
    trocada = parse_worksheet_data(
        SEGUNDA,
        _aba("Paciente Ficticio Um", "Paciente Ficticio Um"),
        [],
        {"B3": AZUL, "B4": VERMELHO},
    )

    assert len(mesma.atendimentos) == len(trocada.atendimentos) == 1
    assert len(mesma.nomes_coloridos) == 1
    assert [n.trechos[0].cor for n in trocada.nomes_coloridos] == [AZUL, VERMELHO]


def test_cor_fora_do_formato_vira_preto_sem_derrubar_o_dia() -> None:
    dados = parse_worksheet_data(SEGUNDA, _aba("Paciente Ficticio Um"), [], {"B3": "azul"})

    assert dados.nomes_coloridos[0].trechos == (
        TrechoColorido(texto="Paciente Ficticio Um", cor=PRETO),
    )


def test_trechos_nao_mudam_nada_do_que_ja_existia() -> None:
    trechos = {"B4": [(0, VERMELHO), (5, AZUL)], "E4": [(0, AZUL), (12, PRETO)]}
    sem = parse_worksheet_data(
        SEGUNDA, SEGUNDA_FICTICIA.valores, SEGUNDA_FICTICIA.merges, SEGUNDA_FICTICIA.cores
    )
    com = parse_worksheet_data(
        SEGUNDA,
        SEGUNDA_FICTICIA.valores,
        SEGUNDA_FICTICIA.merges,
        SEGUNDA_FICTICIA.cores,
        trechos,
    )

    assert com.model_dump(exclude={"nomes_coloridos"}) == sem.model_dump(
        exclude={"nomes_coloridos"}
    )
    assert com.nomes_coloridos != sem.nomes_coloridos


# --- casca do Sheets --------------------------------------------------------


class PlanilhaQueGuardaParametros:
    def __init__(self, abas: list[dict[str, Any]]) -> None:
        self.abas = abas
        self.parametros: list[dict[str, str]] = []

    def fetch_sheet_metadata(self, params: dict[str, str] | None = None) -> dict[str, Any]:
        self.parametros.append(dict(params or {}))
        return {"sheets": self.abas}


def _aba_com_formatos(formato_b3: dict[str, Any], **extras: Any) -> dict[str, Any]:
    from tests.data_sources.fixtures import AbaFicticia

    payload = payload_da_aba("Segunda", AbaFicticia(valores=_aba("Ana / Bia")), [])
    payload["data"][0]["rowData"][2]["values"][1].update(formato_b3)
    payload.update(extras)
    return payload


def _fonte(aba: dict[str, Any]) -> tuple[GoogleSheetsDataSource, PlanilhaQueGuardaParametros]:
    planilha = PlanilhaQueGuardaParametros([aba])
    return GoogleSheetsDataSource(cast(gspread.Spreadsheet, planilha)), planilha


def test_mascara_traz_trechos_e_regras_condicionais_sem_effective_format() -> None:
    assert "textFormatRuns(startIndex,format(foregroundColor,foregroundColorStyle))" in (
        _CAMPOS_DA_GRADE
    )
    assert "conditionalFormats.booleanRule.format.textFormat" in _CAMPOS_DA_GRADE
    assert "userEnteredFormat.textFormat(foregroundColor,foregroundColorStyle)" in (
        _CAMPOS_DA_GRADE
    )
    # Medido na planilha real: effectiveFormat custa ~5 s e ~10x o tamanho.
    assert "effectiveFormat" not in _CAMPOS_DA_GRADE


def test_trecho_sem_cor_herda_a_da_celula() -> None:
    origem, planilha = _fonte(
        _aba_com_formatos(
            {
                "userEnteredFormat": {"textFormat": {"foregroundColor": canais(VERMELHO)}},
                "textFormatRuns": [
                    {"startIndex": 0, "format": {}},
                    {"startIndex": 6, "format": {"foregroundColor": canais(AZUL)}},
                ],
            }
        )
    )

    nomes = origem.listar_nomes_coloridos(SEGUNDA)

    assert [{t.cor for t in n.trechos if t.texto.strip(" /")} for n in nomes] == [
        {VERMELHO},
        {AZUL},
    ]
    assert planilha.parametros[0]["fields"] == _CAMPOS_DA_GRADE


def test_cor_por_foreground_color_style_quando_falta_o_rgb_antigo() -> None:
    origem, _ = _fonte(
        _aba_com_formatos(
            {
                "userEnteredFormat": {
                    "textFormat": {"foregroundColorStyle": {"rgbColor": canais(AZUL)}}
                }
            }
        )
    )

    assert {t.cor for n in origem.listar_nomes_coloridos(SEGUNDA) for t in n.trechos} == {AZUL}


def test_cor_de_tema_sem_rgb_conta_como_preto() -> None:
    origem, _ = _fonte(
        _aba_com_formatos(
            {
                "userEnteredFormat": {
                    "textFormat": {"foregroundColorStyle": {"themeColor": "ACCENT1"}}
                }
            }
        )
    )

    assert {t.cor for n in origem.listar_nomes_coloridos(SEGUNDA) for t in n.trechos} == {PRETO}


def test_regra_condicional_de_cor_vira_aviso_so_com_a_contagem(
    caplog: pytest.LogCaptureFixture,
) -> None:
    regras = [
        {"booleanRule": {"format": {"textFormat": {"foregroundColor": canais(VERMELHO)}}}},
        {"booleanRule": {"format": {"backgroundColor": canais(AZUL)}}},
    ]
    origem, _ = _fonte(_aba_com_formatos({}, conditionalFormats=regras))

    with caplog.at_level(logging.WARNING):
        origem.listar_nomes_coloridos(SEGUNDA)

    avisos = [r.getMessage() for r in caplog.records if "condicional" in r.getMessage()]
    assert len(avisos) == 1 and "1 regra(s)" in avisos[0]
    assert "Ana" not in avisos[0] and "Bia" not in avisos[0]


def test_nomes_coloridos_da_semana_saem_da_mesma_chamada_das_outras_listas() -> None:
    origem, planilha = _fonte(_aba_com_formatos({}))

    origem.listar_atendimentos(SEGUNDA)
    origem.listar_pacientes(SEGUNDA)
    for deslocamento in range(6):
        origem.listar_nomes_coloridos(date.fromordinal(SEGUNDA.toordinal() + deslocamento))

    assert len(planilha.parametros) == 1


def test_cache_repassa_os_nomes_coloridos_e_guarda_por_dia() -> None:
    origem, planilha = _fonte(_aba_com_formatos({}))
    cacheado = CacheadoScheduleDataSource(origem, ttl_segundos=60)

    primeira = cacheado.listar_nomes_coloridos(SEGUNDA)

    assert primeira == origem.listar_nomes_coloridos(SEGUNDA)
    assert cacheado.listar_nomes_coloridos(SEGUNDA) is primeira
    assert len(planilha.parametros) == 1


def test_cache_sobre_fonte_sem_cor_nao_finge_agenda_vazia() -> None:
    cacheado = CacheadoScheduleDataSource(FakeScheduleDataSource(), ttl_segundos=60)

    with pytest.raises(TypeError):
        cacheado.listar_nomes_coloridos(SEGUNDA)
