"""Janela de horário (hora_inicio/hora_fim, horario_maximo) sobre a semana fictícia.

A semana é a de `tests/casos_reais/planilha.py`, lida pelo caminho de produção.
Borda: fechada no início e aberta no fim; com 10:00 a 12:00, 11:30-12:00 entra
e 09:30-10:00 não.
"""

from datetime import date, time
from typing import Any, cast

import gspread
import pytest
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool

from app.ai.relatorios import BlocoRelatorio
from app.ai.tools import criar_tools
from app.data_sources.continuidade import SemHistoricoContinuidadeDataSource
from app.data_sources.google_sheets import GoogleSheetsDataSource
from app.engine import FiltrosRelatorio, fotografar_periodo
from tests.casos_reais.planilha import PlanilhaContada

SEGUNDA = date(2026, 9, 28)
TERCA = date(2026, 9, 29)
QUARTA = date(2026, 9, 30)
QUINTA = date(2026, 10, 1)
SABADO = date(2026, 10, 3)


def _fonte() -> GoogleSheetsDataSource:
    return GoogleSheetsDataSource(cast(gspread.Spreadsheet, PlanilhaContada()))


def _tools() -> dict[str, BaseTool]:
    return {t.name: t for t in criar_tools(_fonte(), SemHistoricoContinuidadeDataSource(), SEGUNDA)}


def _chamar(nome: str, **args: Any) -> tuple[str, BlocoRelatorio | None]:
    mensagem = _tools()[nome].invoke({"name": nome, "args": args, "id": "c1", "type": "tool_call"})
    assert isinstance(mensagem, ToolMessage)
    bloco = BlocoRelatorio.model_validate(mensagem.artifact) if mensagem.artifact else None
    return str(mensagem.content), bloco


# --- motor -------------------------------------------------------------------


def test_filtro_de_janela_e_fechado_no_inicio_e_aberto_no_fim() -> None:
    foto = fotografar_periodo(_fonte(), QUARTA, QUARTA)
    filtro = FiltrosRelatorio(hora_inicio=time(10), hora_fim=time(12))
    aceitos = {slot.hora_inicio for slot in foto.slots if filtro.aceita(slot)}

    assert time(9, 30) not in aceitos  # 09:30-10:00 termina na borda de início
    assert time(10) in aceitos
    assert time(11, 30) in aceitos  # 11:30-12:00 termina exatamente no fim
    assert max(aceitos) == time(11, 30)


def test_filtro_de_janela_so_de_um_lado() -> None:
    foto = fotografar_periodo(_fonte(), QUARTA, QUARTA)
    so_fim = FiltrosRelatorio(hora_fim=time(8))
    so_inicio = FiltrosRelatorio(hora_inicio=time(17))

    assert {s.hora_inicio for s in foto.slots if so_fim.aceita(s)} == {time(7), time(7, 30)}
    assert {s.hora_inicio for s in foto.slots if so_inicio.aceita(s)} == {time(17), time(17, 30)}


# --- gerar_relatorio ---------------------------------------------------------


def test_gerar_relatorio_com_janela_so_traz_vagas_dentro_dela() -> None:
    texto, bloco = _chamar(
        "gerar_relatorio",
        relatorio="vagas_livres",
        inicio=QUARTA.isoformat(),
        fim=QUINTA.isoformat(),
        especialidades=["fono", "psicomotricidade"],
        hora_inicio="10:00",
        hora_fim="12:00",
    )

    assert bloco is not None
    faixas = bloco.model_dump(mode="json")["dados"]["faixas"]
    assert faixas
    assert all(f["inicio"] >= "10:00" and f["fim"] <= "12:00" for f in faixas)
    assert "10:00-12:00" in bloco.titulo
    assert "10:00-12:00" in texto


def test_gerar_relatorio_recusa_janela_invertida() -> None:
    texto, bloco = _chamar(
        "gerar_relatorio", relatorio="vagas_livres", hora_inicio="12:00", hora_fim="10:00"
    )

    assert bloco is None
    assert "hora_fim precisa ser depois de hora_inicio" in texto


# --- buscar_encaixe ----------------------------------------------------------


def test_buscar_encaixe_sem_especialidade_acha_qualquer_uma_na_janela() -> None:
    """Terça: as fonos estão lotadas das 10h às 12h; outras especialidades têm 10:00 livre."""
    texto, _ = _chamar(
        "buscar_encaixe",
        data=TERCA.isoformat(),
        itens=[{"duracao_minutos": 30}],
        horario_minimo="10:00",
        horario_maximo="12:00",
        horario_desejado="10:00",
    )

    assert texto.startswith("Horário encontrado:")
    assert ": 10:00 às 10:30" in texto
    assert "Fonoaudiologia" not in texto


def test_buscar_encaixe_com_horario_maximo_nao_devolve_a_tarde() -> None:
    sem_limite, _ = _chamar(
        "buscar_encaixe",
        data=TERCA.isoformat(),
        itens=[{"especialidade": "fonoaudiologia", "duracao_minutos": 30}],
        horario_minimo="10:00",
    )
    com_limite, _ = _chamar(
        "buscar_encaixe",
        data=TERCA.isoformat(),
        itens=[{"especialidade": "fonoaudiologia", "duracao_minutos": 30}],
        horario_minimo="10:00",
        horario_maximo="12:00",
    )

    assert "13:" in sem_limite or "14:" in sem_limite
    assert com_limite.startswith("Nenhum horário disponível")
    assert "de 10:00 até 12:00" in com_limite


@pytest.mark.parametrize(
    ("duracao", "cabe"),
    [(30, True), (60, False)],
)
def test_buscar_encaixe_horario_maximo_e_aberto_no_fim(duracao: int, cabe: bool) -> None:
    """Terça, Bia: 08:00 e 08:30 livres; com limite 08:30, só 08:00-08:30 serve."""
    texto, _ = _chamar(
        "buscar_encaixe",
        data=TERCA.isoformat(),
        itens=[{"especialidade": "fonoaudiologia", "duracao_minutos": duracao}],
        horario_minimo="08:00",
        horario_maximo="08:30",
    )

    assert texto.startswith("Horário encontrado:") is cabe
    if cabe:
        assert "08:00 às 08:30" in texto


def test_buscar_encaixe_recusa_maximo_antes_do_minimo() -> None:
    texto, _ = _chamar(
        "buscar_encaixe",
        data=TERCA.isoformat(),
        itens=[{"especialidade": "fonoaudiologia", "duracao_minutos": 30}],
        horario_minimo="10:00",
        horario_maximo="10:00",
    )

    assert "precisa ser depois do horário mínimo" in texto


def test_buscar_encaixe_varios_itens_sem_sequencia_aponta_planejar_semana() -> None:
    """Quarta, 10h às 12h: TO lotada; as 4 especialidades seguidas não cabem."""
    texto, _ = _chamar(
        "buscar_encaixe",
        data=QUARTA.isoformat(),
        itens=[
            {"especialidade": e, "duracao_minutos": 30}
            for e in ("fonoaudiologia", "psicomotricidade", "psicologia", "terapia_ocupacional")
        ],
        horario_minimo="10:00",
        horario_maximo="12:00",
    )

    assert "não há horários SEGUIDOS para as 4 especialidades" in texto
    assert "planejar_semana" in texto
    assert "bloco contínuo" not in texto
