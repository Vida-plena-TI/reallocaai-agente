"""Script exploratório: inspeciona ao vivo a estrutura da planilha da agenda.

Ferramenta pontual de investigação — **não** faz parte do pacote `app`. Serve para
descobrir como a planilha real está organizada (abas, cabeçalhos, células mescladas)
antes de escrever o parser definitivo em `app/data_sources`.

Uso:
    uv run python scripts/inspect_sheet.py

Gera três relatórios, impressos no terminal e salvos em `scripts/output/`:

* `sheet_inspection.txt` — dimensões, prévia de valores e células mescladas de cada aba;
* `sheet_colors.txt` — as cores de fundo usadas nas células de paciente das abas de dia,
  agrupadas por cor (a cor provavelmente codifica algo: status, tipo de sessão);
* `sheet_font_colors.txt` — as mesmas células agrupadas pela cor da **fonte**, que é onde a
  planilha codifica o convênio do paciente.
"""

import re
import sys
from collections.abc import Mapping, Sequence
from io import TextIOWrapper
from pathlib import Path
from typing import Any, NamedTuple

# Este script vive fora do pacote `app` e é executado direto (`python scripts/inspect_sheet.py`),
# então o `sys.path[0]` é `scripts/`. A raiz do projeto precisa entrar no path para `import app`.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import gspread  # noqa: E402
from google.oauth2.service_account import Credentials  # noqa: E402

from app.config import get_settings  # noqa: E402

#: Escopo mínimo: a planilha foi compartilhada apenas para leitura.
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

#: Quantas linhas de cada aba entram na prévia.
MAX_PREVIEW_ROWS = 40

#: Largura máxima de uma célula na prévia, para a tabela não estourar o terminal.
MAX_CELL_WIDTH = 24

#: Abas cujo título começa assim são das nutricionistas: layout próprio, fora desta análise.
NUTRI_TITLE_PREFIX = "nutri"

#: Numa aba de dia, uma linha de dados é a que traz o horário do slot na coluna A (`08:30`).
TIME_PATTERN = re.compile(r"^\d{1,2}:\d{2}$")

#: Só o texto, a cor de fundo e a cor da fonte de cada célula — sem isso o `includeGridData`
#: traz a planilha inteira com toda a formatação, um payload enorme e desnecessário.
_CELL_FIELDS = (
    "formattedValue,userEnteredFormat.backgroundColor,userEnteredFormat.textFormat.foregroundColor"
)
GRID_DATA_FIELDS = f"sheets(properties.title,data.rowData.values({_CELL_FIELDS}))"

#: Quantos exemplos de célula são listados por cor.
MAX_COLOR_EXAMPLES = 5

#: Rótulo do grupo das células sem preenchimento (branco, transparente ou sem formato).
NO_COLOR_LABEL = "sem cor"

#: Cor de fonte padrão do Sheets. Célula sem `textFormat.foregroundColor` no payload não é
#: "sem cor": é texto preto, e cai no mesmo grupo de quem tem o preto explícito.
DEFAULT_FONT_HEX = "#000000"

OUTPUT_PATH = _PROJECT_ROOT / "scripts" / "output" / "sheet_inspection.txt"
COLORS_OUTPUT_PATH = _PROJECT_ROOT / "scripts" / "output" / "sheet_colors.txt"
FONT_COLORS_OUTPUT_PATH = _PROJECT_ROOT / "scripts" / "output" / "sheet_font_colors.txt"


class ColoredCell(NamedTuple):
    """Uma célula localizada na planilha, com o texto que ela exibe."""

    sheet: str
    cell: str
    text: str


class CellColors(NamedTuple):
    """Células com texto das abas de dia, agrupadas por cor."""

    #: Células de atendimento por cor de fundo: as das linhas de horário, fora da coluna A.
    patients: dict[str, list[ColoredCell]]
    #: O resto (cabeçalhos e faixa entre os blocos) por cor de fundo, só o que tem cor.
    headers: dict[str, list[ColoredCell]]
    #: As mesmas células de atendimento, agora agrupadas pela cor da fonte.
    patient_fonts: dict[str, list[ColoredCell]]


def _column_label(index: int) -> str:
    """Converte um índice de coluna 0-based na letra da notação A1 (0 -> A, 26 -> AA)."""
    label = ""
    current = index + 1
    while current > 0:
        current, remainder = divmod(current - 1, 26)
        label = chr(ord("A") + remainder) + label
    return label


def _content_bounds(values: Sequence[Sequence[str]]) -> tuple[int, int]:
    """Mede a área realmente preenchida, ignorando linhas e colunas vazias no final.

    Retorna `(linhas, colunas)` — ambos zero quando a aba está totalmente vazia.
    """
    last_row = 0
    last_column = 0
    for row_index, row in enumerate(values, start=1):
        filled = [i for i, cell in enumerate(row, start=1) if cell.strip()]
        if filled:
            last_row = row_index
            last_column = max(last_column, filled[-1])
    return last_row, last_column


def _single_line(cell: str) -> str:
    """Achata uma célula multilinha (`Sala 1\\nTO`) para não desalinhar a tabela."""
    return " ⏎ ".join(part.strip() for part in cell.splitlines() if part.strip())


def _format_preview(values: Sequence[Sequence[str]], columns: int) -> list[str]:
    """Monta a prévia das primeiras linhas como uma tabela alinhada e legível."""
    preview = [
        [_single_line(cell) for cell in row[:columns]] + [""] * (columns - len(row[:columns]))
        for row in values
    ]

    # Largura de cada coluna: o maior conteúdo exibido, limitado por MAX_CELL_WIDTH.
    widths = [len(_column_label(index)) for index in range(columns)]
    for row in preview:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], min(len(cell), MAX_CELL_WIDTH))

    def render(cells: Sequence[str], gutter: str) -> str:
        rendered = [
            (cell if len(cell) <= MAX_CELL_WIDTH else cell[: MAX_CELL_WIDTH - 1] + "…").ljust(
                widths[index]
            )
            for index, cell in enumerate(cells)
        ]
        return f"{gutter:>4} | " + " | ".join(rendered)

    header = render([_column_label(index) for index in range(columns)], "#")
    lines = [header, "-" * len(header)]
    lines += [render(row, str(number)) for number, row in enumerate(preview, start=1)]
    return lines


def _grid_range_to_a1(grid_range: Mapping[str, Any]) -> str:
    """Traduz um `GridRange` da API do Sheets para notação A1 (ex.: `B2:D2`)."""
    start_row = int(grid_range.get("startRowIndex", 0))
    end_row = int(grid_range.get("endRowIndex", start_row + 1))
    start_column = int(grid_range.get("startColumnIndex", 0))
    end_column = int(grid_range.get("endColumnIndex", start_column + 1))
    start = f"{_column_label(start_column)}{start_row + 1}"
    end = f"{_column_label(end_column - 1)}{end_row}"
    return f"{start}:{end}"


def _merges_by_sheet_id(metadata: Mapping[str, Any]) -> dict[int, list[str]]:
    """Extrai os intervalos mesclados de cada aba a partir do metadata da planilha."""
    merges: dict[int, list[str]] = {}
    sheets: Any = metadata.get("sheets", [])
    for sheet in sheets:
        properties: Mapping[str, Any] = sheet.get("properties", {})
        sheet_id = int(properties.get("sheetId", -1))
        merges[sheet_id] = [_grid_range_to_a1(merge) for merge in sheet.get("merges", [])]
    return merges


def _inspect_worksheet(
    worksheet: gspread.Worksheet,
    position: int,
    total: int,
    merges: Sequence[str],
) -> list[str]:
    """Gera o relatório de uma aba: dimensões, prévia de valores e células mescladas."""
    values = worksheet.get_all_values()
    rows, columns = _content_bounds(values)

    lines = [
        "",
        "=" * 100,
        f"ABA {position}/{total}: {worksheet.title!r}  (sheetId={worksheet.id})",
        "=" * 100,
        f"Grade alocada  : {worksheet.row_count} linhas x {worksheet.col_count} colunas",
        f"Conteúdo real  : {rows} linhas x {columns} colunas"
        f" (intervalo A1:{_column_label(max(columns - 1, 0))}{max(rows, 1)})",
    ]

    if merges:
        lines.append(f"Células mescladas ({len(merges)}):")
        lines += [f"  - {merge}" for merge in merges]
    else:
        lines.append("Células mescladas: nenhuma")

    if rows == 0:
        lines += ["", "(aba vazia — nada a exibir)"]
        return lines

    shown = min(rows, MAX_PREVIEW_ROWS)
    lines += ["", f"Primeiras {shown} linhas (de {rows} preenchidas):", ""]
    lines += _format_preview(values[:shown], columns)
    if rows > shown:
        lines.append(f"... (+{rows - shown} linhas não exibidas)")
    return lines


def _is_day_worksheet(title: str) -> bool:
    """Diz se a aba é de um dia da semana (as abas `Nutri *` têm layout próprio)."""
    return not title.strip().casefold().startswith(NUTRI_TITLE_PREFIX)


def _background_hex(cell: Mapping[str, Any]) -> str | None:
    """Converte o `backgroundColor` da célula (canais de 0 a 1) em `#RRGGBB`.

    Devolve `None` quando a célula não tem preenchimento de verdade: sem `userEnteredFormat`,
    transparente, ou branco — o fundo padrão da planilha.

    A API omite os canais iguais a zero, então `{}` é preto, não "sem cor": por isso a
    checagem é pela presença da chave, e não pelo dicionário estar vazio.
    """
    user_entered_format: Mapping[str, Any] = cell.get("userEnteredFormat", {})
    if "backgroundColor" not in user_entered_format:
        return None
    color: Mapping[str, Any] = user_entered_format["backgroundColor"]
    if float(color.get("alpha", 1.0)) == 0.0:
        return None
    channels = tuple(round(float(color.get(name, 0.0)) * 255) for name in ("red", "green", "blue"))
    if channels == (255, 255, 255):
        return None
    return "#{:02X}{:02X}{:02X}".format(*channels)


def _font_hex(cell: Mapping[str, Any]) -> str:
    """Converte o `textFormat.foregroundColor` da célula (canais de 0 a 1) em `#RRGGBB`.

    Ao contrário do fundo, aqui não existe "sem cor": quando a chave não vem no payload, a
    célula está com a formatação padrão do Sheets, ou seja, fonte preta. Os canais iguais a
    zero também são omitidos pela API, então `{}` já significa preto por si só.
    """
    text_format: Mapping[str, Any] = cell.get("userEnteredFormat", {}).get("textFormat", {})
    if "foregroundColor" not in text_format:
        return DEFAULT_FONT_HEX
    color: Mapping[str, Any] = text_format["foregroundColor"]
    channels = tuple(round(float(color.get(name, 0.0)) * 255) for name in ("red", "green", "blue"))
    return "#{:02X}{:02X}{:02X}".format(*channels)


def _collect_cell_colors(metadata: Mapping[str, Any]) -> CellColors:
    """Agrupa por cor de fundo as células com texto das abas de dia.

    As células de paciente são as das linhas de dados — as que trazem o horário do slot na
    coluna A. Esse critério descarta de uma vez o título, as duas linhas de cabeçalho de cada
    bloco (salas e profissionais) e a faixa vazia entre manhã e tarde. A própria coluna A
    também fica de fora: ela é o eixo de horários, não um atendimento.

    O que sobra desse recorte entra num segundo agrupamento, só com as células que têm cor:
    é onde mora quase toda a codificação por cor da planilha.
    """
    patients: dict[str, list[ColoredCell]] = {}
    headers: dict[str, list[ColoredCell]] = {}
    patient_fonts: dict[str, list[ColoredCell]] = {}
    sheets: Any = metadata.get("sheets", [])
    for sheet in sheets:
        title = str(sheet.get("properties", {}).get("title", ""))
        if not _is_day_worksheet(title):
            continue
        for grid in sheet.get("data", []):
            for row_index, row in enumerate(grid.get("rowData", [])):
                cells: Sequence[Mapping[str, Any]] = row.get("values", [])
                if not cells:
                    continue
                hour = str(cells[0].get("formattedValue", "")).strip()
                is_data_row = bool(TIME_PATTERN.match(hour))
                for column_index, cell in enumerate(cells):
                    text = str(cell.get("formattedValue", "")).strip()
                    if not text:
                        continue
                    color = _background_hex(cell)
                    is_patient_cell = is_data_row and column_index > 0
                    if not is_patient_cell and color is None:
                        continue  # cabeçalho sem cor não acrescenta nada ao diagnóstico.
                    address = f"{_column_label(column_index)}{row_index + 1}"
                    located = ColoredCell(title, address, _single_line(text))
                    groups = patients if is_patient_cell else headers
                    groups.setdefault(color or NO_COLOR_LABEL, []).append(located)
                    if is_patient_cell:
                        patient_fonts.setdefault(_font_hex(cell), []).append(located)
    return CellColors(patients, headers, patient_fonts)


def _format_color_groups(groups: Mapping[str, Sequence[ColoredCell]]) -> list[str]:
    """Lista um bloco por cor, da mais frequente para a menos, com exemplos de células."""
    total = sum(len(cells) for cells in groups.values())

    # `sem cor` sempre por último; o resto por frequência decrescente e, no empate, pelo hex.
    ordered = sorted(
        groups.items(), key=lambda item: (item[0] == NO_COLOR_LABEL, -len(item[1]), item[0])
    )
    lines: list[str] = []
    for key, cells in ordered:
        share = len(cells) / total * 100 if total else 0.0
        lines += ["", f"{key}  —  {len(cells)} célula(s) ({share:.1f}%)"]
        lines += [
            f"    - {sample.sheet!r} {sample.cell}: {sample.text}"
            for sample in cells[:MAX_COLOR_EXAMPLES]
        ]
        if len(cells) > MAX_COLOR_EXAMPLES:
            lines.append(f"    ... (+{len(cells) - MAX_COLOR_EXAMPLES} células com esta cor)")
    return lines


def _format_color_report(colors: CellColors) -> list[str]:
    """Monta o relatório de cores: primeiro as células de paciente, depois os cabeçalhos."""
    total = sum(len(cells) for cells in colors.patients.values())
    lines = [
        "#" * 100,
        "# Cores de fundo das abas de dia (abas `Nutri *` ignoradas)",
        f"# Células de paciente : {total}",
        f"# Cores distintas     : {sum(1 for key in colors.patients if key != NO_COLOR_LABEL)}",
        "#" * 100,
        "",
        "CÉLULAS DE PACIENTE (linhas com horário na coluna A, fora da própria coluna A)",
    ]
    lines += _format_color_groups(colors.patients)
    lines += [
        "",
        "",
        "#" * 100,
        "# Fora do recorte acima: as células coloridas dos cabeçalhos (salas, profissionais) e",
        "# da faixa entre os blocos. É onde está quase toda a cor da planilha — pelo que os",
        "# exemplos mostram, a cor acompanha a especialidade do profissional.",
        "#" * 100,
    ]
    lines += _format_color_groups(colors.headers)
    return lines


def _format_font_report(colors: CellColors) -> list[str]:
    """Monta o relatório de cores de fonte das células de atendimento."""
    total = sum(len(cells) for cells in colors.patient_fonts.values())
    lines = [
        "#" * 100,
        "# Cores de FONTE das abas de dia (abas `Nutri *` ignoradas)",
        "# É a cor do texto — e não a de fundo — que indica o convênio do paciente.",
        f"# Células de atendimento: {total}",
        f"# Cores distintas       : {len(colors.patient_fonts)}",
        f"# Célula sem formatação de fonte no payload conta como preto ({DEFAULT_FONT_HEX}).",
        "#" * 100,
        "",
        "CÉLULAS DE ATENDIMENTO (linhas com horário na coluna A, fora da própria coluna A)",
    ]
    lines += _format_color_groups(colors.patient_fonts)
    return lines


def _resolve_credentials_path(raw_path: str) -> Path:
    """Resolve o caminho do JSON da service account em relação à raiz do projeto."""
    path = Path(raw_path).expanduser()
    return path if path.is_absolute() else (_PROJECT_ROOT / path).resolve()


def open_spreadsheet() -> tuple[gspread.Spreadsheet, Credentials]:
    """Autentica com a service account e abre a planilha configurada no `.env`."""
    settings = get_settings()

    if not settings.google_sheets_spreadsheet_id:
        raise SystemExit("GOOGLE_SHEETS_SPREADSHEET_ID não está definido no .env")
    if not settings.google_sheets_credentials_path:
        raise SystemExit("GOOGLE_SHEETS_CREDENTIALS_PATH não está definido no .env")

    credentials_path = _resolve_credentials_path(settings.google_sheets_credentials_path)
    if not credentials_path.is_file():
        raise SystemExit(f"Credencial da service account não encontrada em {credentials_path}")

    # `google-auth` publica py.typed, mas os construtores de Credentials não têm anotações;
    # daí o ignore pontual — não há alternativa tipada na biblioteca.
    credentials = Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
        str(credentials_path), scopes=SCOPES
    )
    client = gspread.authorize(credentials)
    return client.open_by_key(settings.google_sheets_spreadsheet_id), credentials


def inspect_spreadsheet(spreadsheet: gspread.Spreadsheet, credentials: Credentials) -> list[str]:
    """Percorre todas as abas e devolve o relatório de estrutura como linhas de texto."""
    merges = _merges_by_sheet_id(spreadsheet.fetch_sheet_metadata())
    worksheets = spreadsheet.worksheets()

    lines = [
        "#" * 100,
        f"# Planilha       : {spreadsheet.title!r}",
        f"# ID             : {spreadsheet.id}",
        f"# Service account: {credentials.service_account_email}",
        f"# Abas           : {len(worksheets)} -> {[ws.title for ws in worksheets]}",
        "#" * 100,
    ]
    for position, worksheet in enumerate(worksheets, start=1):
        lines += _inspect_worksheet(
            worksheet, position, len(worksheets), merges.get(worksheet.id, [])
        )
    return lines


def fetch_cell_colors(spreadsheet: gspread.Spreadsheet) -> CellColors:
    """Baixa a grade já formatada da planilha e agrupa as células por cor.

    Uma única chamada alimenta os dois relatórios de cor: o de fundo e o de fonte.
    """
    metadata = spreadsheet.fetch_sheet_metadata(
        params={"includeGridData": "true", "fields": GRID_DATA_FIELDS}
    )
    return _collect_cell_colors(metadata)


def main() -> None:
    """Executa as inspeções, imprime no terminal e salva em `scripts/output/`."""
    spreadsheet, credentials = open_spreadsheet()
    colors = fetch_cell_colors(spreadsheet)
    reports = {
        OUTPUT_PATH: "\n".join(inspect_spreadsheet(spreadsheet, credentials)) + "\n",
        COLORS_OUTPUT_PATH: "\n".join(_format_color_report(colors)) + "\n",
        FONT_COLORS_OUTPUT_PATH: "\n".join(_format_font_report(colors)) + "\n",
    }

    # O console do Windows costuma vir em cp1252 e engasgaria com os acentos da planilha.
    if isinstance(sys.stdout, TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    for output_path, report in reports.items():
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report, encoding="utf-8")
        print(report)
        print(f"[ok] Relatório salvo em {output_path.relative_to(_PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
