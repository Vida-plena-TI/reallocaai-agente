"""Pergunta de um dia: consultar_disponibilidade vai pelo mesmo caminho do relatório de vagas."""

from datetime import date
from typing import Any, cast

import gspread
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool

from app.ai.tools import criar_tools
from app.data_sources.continuidade import SemHistoricoContinuidadeDataSource
from app.data_sources.google_sheets import GoogleSheetsDataSource
from tests.casos_reais.planilha import PlanilhaContada

SEGUNDA = date(2026, 9, 28)


def _tools() -> dict[str, BaseTool]:
    fonte = GoogleSheetsDataSource(cast(gspread.Spreadsheet, PlanilhaContada()))
    return {t.name: t for t in criar_tools(fonte, SemHistoricoContinuidadeDataSource(), SEGUNDA)}


def _chamar(tool: BaseTool, args: dict[str, Any]) -> ToolMessage:
    mensagem = tool.invoke({"type": "tool_call", "id": "t", "name": tool.name, "args": args})
    assert isinstance(mensagem, ToolMessage)
    return mensagem


def test_dia_unico_devolve_o_mesmo_relatorio_de_gerar_relatorio() -> None:
    tools = _tools()
    dia = _chamar(tools["consultar_disponibilidade"], {"data": SEGUNDA.isoformat()})
    relatorio = _chamar(
        tools["gerar_relatorio"],
        {"relatorio": "vagas_livres", "inicio": SEGUNDA.isoformat(), "fim": SEGUNDA.isoformat()},
    )

    assert dia.artifact is not None and dia.artifact["tipo"] == "disponibilidade"
    assert dia.artifact == relatorio.artifact
    assert dia.content == relatorio.content
    # O texto que o modelo recebe é um resumo, não uma linha por vaga.
    assert len(str(dia.content).splitlines()) < 15


def test_ate_tres_faixas_so_texto() -> None:
    mensagem = _chamar(
        _tools()["consultar_disponibilidade"],
        {"data": SEGUNDA.isoformat(), "especialidade": "psicomotricidade"},
    )
    # Duda (Sala 4), segunda: 09:00-10:00, 14:00-15:00 e 16:00-17:00.
    assert mensagem.artifact is None
    assert mensagem.content == (
        "Horários livres em 28/09/2026:\n"
        "- 09:00 às 10:00: Psicomotricidade com Duda na Sala 4.\n"
        "- 14:00 às 15:00: Psicomotricidade com Duda na Sala 4.\n"
        "- 16:00 às 17:00: Psicomotricidade com Duda na Sala 4."
    )
