"""Teste de `scripts/preview_email.py` com fonte falsa (sem planilha nem Resend)."""

from pathlib import Path

from scripts.preview_email import _argumentos, gerar_preview
from tests.support.agenda_semanal import SEGUNDA
from tests.support.relatorios import fonte_relatorios


def test_gerar_preview_grava_html_e_texto(tmp_path: Path) -> None:
    caminho_html, caminho_texto = gerar_preview(fonte_relatorios(), SEGUNDA, tmp_path / "saida")

    assert caminho_html == tmp_path / "saida" / "preview_email.html"
    assert caminho_texto == tmp_path / "saida" / "preview_email.txt"
    assert "<html" in caminho_html.read_text(encoding="utf-8").lower()
    texto = caminho_texto.read_text(encoding="utf-8")
    assert texto.startswith("Assunto: ")
    assert "%" in texto


def test_data_padrao_e_hoje() -> None:
    assert _argumentos([]).data is None
    assert _argumentos(["2026-10-05"]).data.isoformat() == "2026-10-05"
