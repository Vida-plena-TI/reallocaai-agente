"""Script manual: gera localmente o e-mail do relatório de ocupação, sem enviar.

Ferramenta pontual — **não** faz parte do pacote `app`, não roda em CI e não
usa o Resend: só monta o relatório de ocupação de uma data com a planilha
real (`GoogleSheetsDataSource`) e grava o HTML e o texto que iriam no corpo do
e-mail. Útil para revisar o layout enquanto o Resend não está configurado.

Uso:
    uv run python scripts/preview_email.py [AAAA-MM-DD]

Sem data, usa hoje. Grava `scripts/output/preview_email.html` e
`scripts/output/preview_email.txt` (pasta git-ignorada) e imprime os caminhos.
O relatório traz só salas, especialidades e contagens, sem nomes de
pacientes, mas a saída vem da agenda real e fica fora do Git mesmo assim.
"""

import argparse
import sys
from datetime import date
from pathlib import Path

# Mesmo ajuste de `scripts/chat_manual.py`: a raiz do projeto entra no path.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.data_sources.google_sheets import GoogleSheetsDataSource  # noqa: E402
from app.domain import ScheduleDataSource  # noqa: E402
from app.engine.ocupacao import construir_relatorio_ocupacao_do_dia  # noqa: E402
from app.reports.template import renderizar_relatorio  # noqa: E402

PASTA_SAIDA = _PROJECT_ROOT / "scripts" / "output"


def gerar_preview(fonte: ScheduleDataSource, data: date, pasta: Path) -> tuple[Path, Path]:
    """Grava `preview_email.html` e `preview_email.txt` em `pasta` e devolve os caminhos."""
    relatorio = construir_relatorio_ocupacao_do_dia(fonte, data)
    assunto, corpo_html, corpo_texto = renderizar_relatorio(relatorio)
    pasta.mkdir(parents=True, exist_ok=True)
    caminho_html = pasta / "preview_email.html"
    caminho_texto = pasta / "preview_email.txt"
    caminho_html.write_text(corpo_html, encoding="utf-8")
    caminho_texto.write_text(f"Assunto: {assunto}\n\n{corpo_texto}", encoding="utf-8")
    return caminho_html, caminho_texto


def _argumentos(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Gera localmente o e-mail do relatório de ocupação, sem enviar."
    )
    parser.add_argument(
        "data",
        nargs="?",
        type=date.fromisoformat,
        default=None,
        help="Data do relatório (AAAA-MM-DD). Padrão: hoje.",
    )
    return parser.parse_args(argv)


def main() -> None:
    argumentos = _argumentos()
    data: date = argumentos.data or date.today()
    caminho_html, caminho_texto = gerar_preview(GoogleSheetsDataSource(), data, PASTA_SAIDA)
    print(f"Prévia do relatório de {data.strftime('%d/%m/%Y')} (nada foi enviado):")
    print(f"  HTML:  {caminho_html}")
    print(f"  Texto: {caminho_texto}")


if __name__ == "__main__":
    main()
