"""Teste de `scripts/preview_email.py` com fonte falsa (sem planilha nem Resend)."""

from pathlib import Path

import pytest

from scripts.preview_email import _argumentos, gerar_preview
from tests.support.agenda_clinica import NOMES_PACIENTES, fonte_clinica
from tests.support.agenda_semanal import QUARTA, SEGUNDA, SEXTA
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


def test_preview_dos_relatorios_gerais_de_um_periodo(tmp_path: Path) -> None:
    _, caminho_texto = gerar_preview(
        fonte_clinica(),
        QUARTA,
        tmp_path,
        ["ranking_ocupacao", "salas_ociosas"],
        inicio=SEGUNDA,
        fim=SEXTA,
    )

    texto = caminho_texto.read_text(encoding="utf-8")
    assert texto.startswith("Assunto: RealocAI — Relatórios de 28/09/2026 a 02/10/2026")
    assert texto.index("RANKING DE OCUPAÇÃO") < texto.index("SALAS OCIOSAS")
    assert not [nome for nome in NOMES_PACIENTES if nome in texto]


def test_preview_recusa_agenda_de_paciente(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="nunca contém nome de paciente"):
        gerar_preview(fonte_clinica(), QUARTA, tmp_path, ["agenda_paciente"])


def test_argumentos_de_relatorios_e_periodo() -> None:
    argumentos = _argumentos(
        ["--relatorios", "vagas_livres, convenios", "--inicio", "2026-09-28", "--fim", "2026-10-02"]
    )

    assert argumentos.relatorios == ["vagas_livres", "convenios"]
    assert (argumentos.inicio.isoformat(), argumentos.fim.isoformat()) == (
        "2026-09-28",
        "2026-10-02",
    )
