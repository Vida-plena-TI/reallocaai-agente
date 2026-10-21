"""Script manual: gera localmente o e-mail de relatório, sem enviar.

Ferramenta pontual — **não** faz parte do pacote `app`, não roda em CI e não
usa o Resend: só monta o e-mail com a planilha real (`GoogleSheetsDataSource`)
e grava o HTML e o texto que iriam no corpo. Útil para revisar o layout
enquanto o Resend não está configurado.

Uso:
    uv run python scripts/preview_email.py [AAAA-MM-DD]
    uv run python scripts/preview_email.py --relatorios vagas_livres,ranking_ocupacao \\
        [--inicio AAAA-MM-DD --fim AAAA-MM-DD]

Sem `--relatorios`, é o relatório de ocupação do dia (o e-mail de sempre; sem
data, hoje). Com `--relatorios`, é o e-mail de `POST /relatorio/enviar` com o
campo `relatorios`: os tipos, na ordem, do dia informado ou do período
`--inicio`/`--fim` (até 14 dias), com as mesmas validações da rota.

Grava `scripts/output/preview_email.html` e `scripts/output/preview_email.txt`
(pasta git-ignorada) e imprime os caminhos. O e-mail traz só salas,
especialidades, profissionais e contagens, sem nomes de pacientes, mas a saída
vem da agenda real e fica fora do Git mesmo assim.
"""

import argparse
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

# Mesmo ajuste de `scripts/chat_manual.py`: a raiz do projeto entra no path.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.data_sources.google_sheets import GoogleSheetsDataSource  # noqa: E402
from app.domain import ScheduleDataSource  # noqa: E402
from app.engine.fotografia import fotografar_periodo  # noqa: E402
from app.engine.ocupacao import construir_relatorio_ocupacao_do_dia  # noqa: E402
from app.reports.relatorios_gerais import (  # noqa: E402
    TIPOS_RELATORIO_EMAIL,
    renderizar_relatorios_gerais,
    resolver_periodo,
    validar_tipos,
)
from app.reports.template import renderizar_relatorio  # noqa: E402

PASTA_SAIDA = _PROJECT_ROOT / "scripts" / "output"


def gerar_preview(
    fonte: ScheduleDataSource,
    data: date,
    pasta: Path,
    relatorios: Sequence[str] | None = None,
    inicio: date | None = None,
    fim: date | None = None,
) -> tuple[Path, Path]:
    """Grava `preview_email.html` e `preview_email.txt` em `pasta` e devolve os caminhos.

    Sem `relatorios`, o relatório de ocupação de `data`. Com `relatorios`, os
    relatórios gerais de `data` ou de `inicio`/`fim` (`ValueError` com a
    mesma mensagem que a rota daria num 422).
    """
    if relatorios is None:
        assunto, corpo_html, corpo_texto = renderizar_relatorio(
            construir_relatorio_ocupacao_do_dia(fonte, data)
        )
    else:
        tipos = validar_tipos(relatorios)
        sem_periodo = inicio is None and fim is None
        primeiro, ultimo = resolver_periodo(
            tipos, data if sem_periodo else None, inicio, fim, date.today()
        )
        assunto, corpo_html, corpo_texto = renderizar_relatorios_gerais(
            fotografar_periodo(fonte, primeiro, ultimo), tipos
        )
    pasta.mkdir(parents=True, exist_ok=True)
    caminho_html = pasta / "preview_email.html"
    caminho_texto = pasta / "preview_email.txt"
    caminho_html.write_text(corpo_html, encoding="utf-8")
    caminho_texto.write_text(f"Assunto: {assunto}\n\n{corpo_texto}", encoding="utf-8")
    return caminho_html, caminho_texto


def _lista(texto: str) -> list[str]:
    return [parte.strip() for parte in texto.split(",") if parte.strip()]


def _argumentos(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Gera localmente o e-mail de relatório, sem enviar."
    )
    parser.add_argument(
        "data",
        nargs="?",
        type=date.fromisoformat,
        default=None,
        help="Data do relatório (AAAA-MM-DD). Padrão: hoje.",
    )
    parser.add_argument(
        "--relatorios",
        type=_lista,
        default=None,
        help=f"Tipos separados por vírgula, na ordem: {', '.join(TIPOS_RELATORIO_EMAIL)}.",
    )
    parser.add_argument(
        "--inicio", type=date.fromisoformat, default=None, help="Com --relatorios: primeiro dia."
    )
    parser.add_argument(
        "--fim", type=date.fromisoformat, default=None, help="Com --relatorios: último dia."
    )
    return parser.parse_args(argv)


def main() -> None:
    argumentos = _argumentos()
    data: date = argumentos.data or date.today()
    if argumentos.relatorios is None and (argumentos.inicio or argumentos.fim):
        sys.exit("--inicio e --fim só valem com --relatorios.")
    try:
        caminho_html, caminho_texto = gerar_preview(
            GoogleSheetsDataSource(),
            data,
            PASTA_SAIDA,
            argumentos.relatorios,
            argumentos.inicio,
            argumentos.fim,
        )
    except ValueError as erro:
        sys.exit(str(erro))
    print("Prévia do e-mail gerada (nada foi enviado):")
    print(f"  HTML:  {caminho_html}")
    print(f"  Texto: {caminho_texto}")


if __name__ == "__main__":
    main()
