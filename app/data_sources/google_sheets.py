"""Leitura da agenda direto da planilha do Google Sheets.

Esta é a casca fina do parsing: aqui só acontece autenticação, uma chamada à API
e a tradução do payload do Sheets para as estruturas simples que
`parse_worksheet_data` espera. Toda a interpretação da planilha — blocos, salas,
profissionais, sessões, convênio por cor — vive no parser, que é puro e testado
sem rede.
"""

import logging
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any, Final

import gspread
from google.oauth2.service_account import Credentials

from app.config import get_settings
from app.data_sources.google_sheets_parser import (
    DadosAgendaDoDia,
    encontrar_titulo_da_aba,
    nome_da_aba,
    parse_worksheet_data,
    rotulo_da_coluna,
)
from app.domain import Atendimento, EntradaGrade, Paciente, Profissional, Sala

logger = logging.getLogger(__name__)

#: A planilha é compartilhada só para leitura com a service account.
SCOPES: Final[list[str]] = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

#: Raiz do projeto, para resolver o caminho relativo da credencial do `.env`.
_RAIZ_DO_PROJETO: Final[Path] = Path(__file__).resolve().parent.parent.parent

#: Sem recortar os campos, `includeGridData` traz a planilha inteira com toda a
#: formatação. O parser só precisa do texto e da cor da fonte de cada célula,
#: mais os intervalos mesclados de cada aba.
_CAMPOS_DA_GRADE: Final[str] = (
    "sheets(properties.title,merges,"
    "data.rowData.values(formattedValue,userEnteredFormat.textFormat.foregroundColor))"
)

#: Cor de fonte padrão do Sheets: célula sem `foregroundColor` no payload não
#: está sem cor, está com o texto preto.
_COR_DE_FONTE_PADRAO: Final[str] = "#000000"


def abrir_planilha() -> gspread.Spreadsheet:
    """Autentica com a service account e abre a planilha configurada no `.env`."""
    settings = get_settings()

    if not settings.google_sheets_spreadsheet_id:
        raise ValueError("GOOGLE_SHEETS_SPREADSHEET_ID não está definido no .env")
    if not settings.google_sheets_credentials_path:
        raise ValueError("GOOGLE_SHEETS_CREDENTIALS_PATH não está definido no .env")

    caminho = Path(settings.google_sheets_credentials_path).expanduser()
    if not caminho.is_absolute():
        caminho = (_RAIZ_DO_PROJETO / caminho).resolve()
    if not caminho.is_file():
        raise FileNotFoundError(f"Credencial da service account não encontrada em {caminho}")

    # `google-auth` publica py.typed, mas os construtores de Credentials não têm
    # anotações; daí o ignore pontual.
    credenciais = Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
        str(caminho), scopes=SCOPES
    )
    cliente = gspread.authorize(credenciais)
    return cliente.open_by_key(settings.google_sheets_spreadsheet_id)


class GoogleSheetsDataSource:
    """`ScheduleDataSource` apoiado na planilha da clínica.

    Cada dia é buscado uma vez por instância: os quatro métodos leem do mesmo
    resultado em cache, então montar a agenda de um dia custa uma chamada à API,
    não quatro. O cache vive na instância — nada é compartilhado entre processos.
    """

    def __init__(self, planilha: gspread.Spreadsheet | None = None) -> None:
        #: Injetável para testes e scripts; quando ausente, é aberta sob demanda.
        self._planilha = planilha
        self._cache: dict[date, DadosAgendaDoDia] = {}

    @property
    def planilha(self) -> gspread.Spreadsheet:
        """A planilha aberta, autenticando na primeira vez que for preciso."""
        if self._planilha is None:
            self._planilha = abrir_planilha()
        return self._planilha

    def listar_salas(self, dia: date) -> list[Sala]:
        """Salas em uso no dia, com a capacidade simultânea de cada uma."""
        return self._dados_do_dia(dia).salas

    def listar_profissionais(self, dia: date) -> list[Profissional]:
        """Profissionais escalados no dia."""
        return self._dados_do_dia(dia).profissionais

    def listar_grade(self, dia: date) -> list[EntradaGrade]:
        """Todas as janelas (sala, profissional, slot) abertas no dia."""
        return self._dados_do_dia(dia).grade

    def listar_atendimentos(self, dia: date) -> list[Atendimento]:
        """Atendimentos já alocados no dia."""
        return self._dados_do_dia(dia).atendimentos

    def listar_pacientes(self, dia: date) -> list[Paciente]:
        """Pacientes que aparecem na agenda do dia, com o convênio que a cor indicou."""
        return self._dados_do_dia(dia).pacientes

    def _dados_do_dia(self, dia: date) -> DadosAgendaDoDia:
        """Resultado do parsing do dia, buscando na planilha só na primeira vez."""
        if dia not in self._cache:
            self._cache[dia] = self._carregar(dia)
        return self._cache[dia]

    def _carregar(self, dia: date) -> DadosAgendaDoDia:
        """Busca a aba do dia e entrega os dados crus ao parser."""
        if nome_da_aba(dia) is None:
            logger.debug("%s é domingo: a clínica não abre, nada a buscar.", dia.isoformat())
            return DadosAgendaDoDia()

        abas: Sequence[Mapping[str, Any]] = self.planilha.fetch_sheet_metadata(
            params={"includeGridData": "true", "fields": _CAMPOS_DA_GRADE}
        ).get("sheets", [])
        titulos = [str(aba.get("properties", {}).get("title", "")) for aba in abas]

        titulo = encontrar_titulo_da_aba(dia, titulos)
        if titulo is None:
            logger.warning(
                "A planilha não tem aba %r para %s: agenda do dia vazia.",
                nome_da_aba(dia),
                dia.isoformat(),
            )
            return DadosAgendaDoDia()

        aba = abas[titulos.index(titulo)]
        valores, cores = _valores_e_cores(aba)
        merges = [_intervalo_para_a1(merge) for merge in aba.get("merges", [])]
        return parse_worksheet_data(dia, valores, merges, cores)


def _valores_e_cores(aba: Mapping[str, Any]) -> tuple[list[list[str]], dict[str, str]]:
    """Separa o payload da aba em texto por célula e cor de fonte por endereço A1.

    Só as células com texto entram no mapa de cores: as vazias não têm convênio
    a informar.
    """
    valores: list[list[str]] = []
    cores: dict[str, str] = {}
    grades: Sequence[Mapping[str, Any]] = aba.get("data", [])
    for grade in grades:
        linhas: Sequence[Mapping[str, Any]] = grade.get("rowData", [])
        for indice_da_linha, linha in enumerate(linhas):
            celulas: Sequence[Mapping[str, Any]] = linha.get("values", [])
            textos: list[str] = []
            for indice_da_coluna, celula in enumerate(celulas):
                texto = str(celula.get("formattedValue", ""))
                textos.append(texto)
                if texto.strip():
                    endereco = f"{rotulo_da_coluna(indice_da_coluna)}{indice_da_linha + 1}"
                    cores[endereco] = _cor_da_fonte(celula)
            valores.append(textos)
    return valores, cores


def _cor_da_fonte(celula: Mapping[str, Any]) -> str:
    """Converte o `foregroundColor` da célula (canais de 0 a 1) em `#RRGGBB`.

    A API omite os canais iguais a zero, então um dicionário vazio já é preto.
    """
    formato: Mapping[str, Any] = celula.get("userEnteredFormat", {}).get("textFormat", {})
    if "foregroundColor" not in formato:
        return _COR_DE_FONTE_PADRAO
    cor: Mapping[str, Any] = formato["foregroundColor"]
    canais = tuple(round(float(cor.get(nome, 0.0)) * 255) for nome in ("red", "green", "blue"))
    return "#{:02X}{:02X}{:02X}".format(*canais)


def _intervalo_para_a1(intervalo: Mapping[str, Any]) -> str:
    """Traduz um `GridRange` da API do Sheets para notação A1 (`B2:D2`)."""
    primeira_linha = int(intervalo.get("startRowIndex", 0))
    ultima_linha = int(intervalo.get("endRowIndex", primeira_linha + 1))
    primeira_coluna = int(intervalo.get("startColumnIndex", 0))
    ultima_coluna = int(intervalo.get("endColumnIndex", primeira_coluna + 1))
    inicio = f"{rotulo_da_coluna(primeira_coluna)}{primeira_linha + 1}"
    fim = f"{rotulo_da_coluna(ultima_coluna - 1)}{ultima_linha}"
    return f"{inicio}:{fim}"
