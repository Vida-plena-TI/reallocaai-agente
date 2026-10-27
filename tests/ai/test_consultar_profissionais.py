"""`consultar_profissionais` e os enums do catálogo, sobre a semana fictícia dos casos reais.

Três profissionais da grade de `tests/casos_reais/planilha.py`, conferidos à mão
em `AGENDA` (semana de 28/09 a 03/10/2026; todas as salas têm um posto só):
- Bia (Sala 1): todos os seis dias, manhã e tarde. Na fixture ela é de
  Fonoaudiologia; na planilha real, a Bia é de Psicomotricidade — a fixture é
  fictícia de propósito e não acompanha a equipe real;
- Assizely (Sala 2): Terapia Ocupacional, segunda a sexta;
- Elo (Sala 5): Fonoaudiologia, segunda, terça, quarta e sexta (sem coluna na
  quinta e no sábado).
"""

import json
from datetime import date, timedelta
from typing import Any, cast

import gspread
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from app.ai.tools import criar_tools
from app.data_sources.continuidade import SemHistoricoContinuidadeDataSource
from app.data_sources.google_sheets import GoogleSheetsDataSource
from tests.casos_reais.planilha import PlanilhaContada
from tests.support.fake_schedule_data_source import FakeScheduleDataSource

QUARTA = date(2026, 9, 30)
SEMANA = {date(2026, 9, 28) + timedelta(days=i) for i in range(6)}

TITULO = "Equipe da semana de 28/09 a 03/10/2026:"
DIAS_UTEIS = ("segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira")


def _tools(fonte: Any = None) -> dict[str, BaseTool]:
    if fonte is None:
        fonte = GoogleSheetsDataSource(cast(gspread.Spreadsheet, PlanilhaContada()))
    return {t.name: t for t in criar_tools(fonte, SemHistoricoContinuidadeDataSource(), QUARTA)}


def _consultar(**args: Any) -> str:
    return str(_tools()["consultar_profissionais"].invoke(args))


def _dias(nomes: tuple[str, ...], sala: str) -> str:
    return ", ".join(f"{dia} (manhã e tarde; {sala})" for dia in nomes)


def test_tres_profissionais_reais_da_grade() -> None:
    texto = _consultar(nomes=["beatriz", "ASSIZELLY", "Elo"])

    assert texto.splitlines() == [
        TITULO,
        "- Bia: Fonoaudiologia (também chamada de beatriz). Atende: "
        + _dias((*DIAS_UTEIS, "sábado"), "Sala 1")
        + ".",
        "- Assizely: Terapia Ocupacional. Atende: " + _dias(DIAS_UTEIS, "Sala 2") + ".",
        "- Elo: Fonoaudiologia. Atende: "
        + _dias(("segunda-feira", "terça-feira", "quarta-feira", "sexta-feira"), "Sala 5")
        + ".",
    ]


def test_filtra_por_especialidade_e_dia() -> None:
    assert _consultar(especialidade="fono", dia="quinta") == (
        f"{TITULO}\n- Bia: Fonoaudiologia (também chamada de beatriz). Atende: "
        "quinta-feira (manhã e tarde; Sala 1)."
    )


def test_ninguem_no_filtro() -> None:
    assert _consultar(especialidade="psicomotricidade", dia="sabado") == (
        "Equipe da semana de 28/09 a 03/10/2026: ninguém de Psicomotricidade atende no "
        "sábado (03/10) na grade."
    )


def test_nao_atende_e_inexistente() -> None:
    assert _consultar(nomes=["Elo"], dia="quinta") == (
        "Elo não atende na quinta-feira (01/10). Nesta semana, atende: segunda-feira, "
        "terça-feira, quarta-feira, sexta-feira."
    )
    assert _consultar(nomes=["Zuleide"]).startswith(
        "'Zuleide' não existe na grade da semana de 28/09 a 03/10/2026."
    )


def test_sem_dado_de_paciente() -> None:
    assert "Paciente" not in _consultar()


def _enum(tool: BaseTool, campo: str) -> list[str]:
    esquema = cast(type[BaseModel], tool.tool_call_schema).model_json_schema()
    texto = json.dumps(esquema)
    definicoes = esquema.get("$defs", {})
    nome = {"especialidade": "Especialidade", "dia": "DiaDaSemana"}[campo]
    assert nome in definicoes, texto
    return list(definicoes[nome]["enum"])


def test_enums_saem_do_catalogo_da_semana() -> None:
    tools = _tools()
    especialidades = [
        "terapia_ocupacional",
        "fonoaudiologia",
        "psicologia",
        "psicomotricidade",
    ]
    for nome in ("buscar_encaixe", "consultar_disponibilidade", "consultar_profissionais"):
        assert _enum(tools[nome], "especialidade") == especialidades
    assert _enum(tools["consultar_profissionais"], "dia") == [
        "segunda",
        "terca",
        "quarta",
        "quinta",
        "sexta",
        "sabado",
    ]


def test_planilha_ilegivel_usa_a_lista_de_reserva_e_nao_derruba() -> None:
    tools = _tools(FakeScheduleDataSource(dias_com_falha=SEMANA))

    assert len(_enum(tools["consultar_disponibilidade"], "especialidade")) == 7
    assert len(_enum(tools["consultar_profissionais"], "dia")) == 6
    texto = str(tools["consultar_profissionais"].invoke({}))
    assert texto == "Erro ao ler a agenda para consultar a equipe (CatalogoIndisponivelError)."
