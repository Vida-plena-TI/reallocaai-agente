"""Leitura da agenda direto da planilha do Google Sheets.

Esta é a casca fina do parsing: aqui só acontece autenticação, uma chamada à API
e a tradução do payload do Sheets para as estruturas simples que
`parse_worksheet_data` espera. Toda a interpretação da planilha — blocos, salas,
profissionais, sessões, convênio por cor, cor de cada nome — vive no parser, que é puro e testado
sem rede.
"""

import logging
import threading
import time
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any, Final

import gspread
from google.oauth2.service_account import Credentials

from app.config import carregar_credencial_google_json, get_settings
from app.data_sources.google_sheets_parser import (
    DadosAgendaDoDia,
    encontrar_titulo_da_aba,
    nome_da_aba,
    parse_worksheet_data,
    rotulo_da_coluna,
)
from app.domain import (
    Atendimento,
    EntradaGrade,
    NomeColorido,
    Paciente,
    Profissional,
    Sala,
    dias_da_semana_de,
)

logger = logging.getLogger(__name__)

#: A planilha é compartilhada só para leitura com a service account.
SCOPES: Final[list[str]] = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

#: Raiz do projeto, para resolver o caminho relativo da credencial do `.env`.
_RAIZ_DO_PROJETO: Final[Path] = Path(__file__).resolve().parent.parent.parent

#: Sem recortar os campos, `includeGridData` traz a planilha inteira com toda a
#: formatação. O parser só precisa do texto e da cor da fonte de cada célula,
#: da cor de cada trecho do texto (`textFormatRuns`: um nome azul e outro
#: vermelho na mesma célula) e dos intervalos mesclados de cada aba.
#:
#: A cor é a digitada (`userEnteredFormat`), não a exibida (`effectiveFormat`):
#: medido na planilha real (out/2026), pedir `effectiveFormat` levava a leitura
#: de ~1,4 s para ~7 s e a resposta de ~2 MB para ~20 MB, porque a API o
#: calcula para toda célula da grade, inclusive as vazias. As duas só divergem
#: com formatação condicional, que a planilha não usa; `conditionalFormats`
#: (pequeno, no nível da aba) vem junto só para avisar se isso mudar.
_CAMPOS_DA_GRADE: Final[str] = (
    "sheets(properties.title,merges,conditionalFormats.booleanRule.format.textFormat,"
    "data.rowData.values(formattedValue,"
    "userEnteredFormat.textFormat(foregroundColor,foregroundColorStyle),"
    "textFormatRuns(startIndex,format(foregroundColor,foregroundColorStyle))))"
)

#: Cor de fonte padrão do Sheets: célula sem `foregroundColor` no payload não
#: está sem cor, está com o texto preto.
_COR_DE_FONTE_PADRAO: Final[str] = "#000000"


def abrir_planilha() -> gspread.Spreadsheet:
    """Autentica com a service account e abre a planilha configurada.

    A credencial vem de `GOOGLE_SERVICE_ACCOUNT_JSON` (produção, só em memória)
    quando preenchida; senão, do arquivo em `GOOGLE_SHEETS_CREDENTIALS_PATH`
    (uso local).
    """
    settings = get_settings()

    if not settings.google_sheets_spreadsheet_id:
        raise ValueError("GOOGLE_SHEETS_SPREADSHEET_ID não está definido no .env")

    # `google-auth` publica py.typed, mas os construtores de Credentials não têm
    # anotações; daí o ignore pontual.
    if settings.google_service_account_json:
        info = carregar_credencial_google_json(settings.google_service_account_json)
        credenciais = Credentials.from_service_account_info(  # type: ignore[no-untyped-call]
            info, scopes=SCOPES
        )
    else:
        credenciais = Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
            str(_caminho_da_credencial(settings.google_sheets_credentials_path)), scopes=SCOPES
        )
    cliente = gspread.authorize(credenciais)
    return cliente.open_by_key(settings.google_sheets_spreadsheet_id)


def _caminho_da_credencial(caminho_configurado: str | None) -> Path:
    """Resolve `GOOGLE_SHEETS_CREDENTIALS_PATH` relativo à raiz do projeto."""
    if not caminho_configurado:
        raise ValueError(
            "Nem GOOGLE_SERVICE_ACCOUNT_JSON nem GOOGLE_SHEETS_CREDENTIALS_PATH estão definidos"
        )
    caminho = Path(caminho_configurado).expanduser()
    if not caminho.is_absolute():
        caminho = (_RAIZ_DO_PROJETO / caminho).resolve()
    if not caminho.is_file():
        raise FileNotFoundError(f"Credencial da service account não encontrada em {caminho}")
    return caminho


class GoogleSheetsDataSource:
    """`ScheduleDataSource` apoiado na planilha da clínica.

    A API devolve todas as abas numa chamada só, então montar um dia já traz
    a semana inteira: a busca de um dia preenche os seis dias (segunda a
    sábado) da semana dele. Os cinco métodos leem do mesmo resultado em cache.

    `ttl_segundos` limita a validade desse cache; `None` (padrão, usado pelos
    scripts) guarda para sempre. Um lock serializa as buscas: leituras em
    paralelo de dias da mesma semana custam uma chamada à API, não seis. O
    cache vive na instância — nada é compartilhado entre processos.
    """

    def __init__(
        self, planilha: gspread.Spreadsheet | None = None, ttl_segundos: float | None = None
    ) -> None:
        #: Injetável para testes e scripts; quando ausente, é aberta sob demanda.
        self._planilha = planilha
        self._ttl_segundos = ttl_segundos
        #: Dia -> (instante monotônico da busca, resultado do parsing).
        self._cache: dict[date, tuple[float, DadosAgendaDoDia]] = {}
        self._lock = threading.Lock()

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

    def listar_nomes_coloridos(self, dia: date) -> list[NomeColorido]:
        """Nomes de paciente do dia com a cor de cada trecho (ver `FonteNomesColoridos`)."""
        return self._dados_do_dia(dia).nomes_coloridos

    def _em_cache(self, dia: date) -> DadosAgendaDoDia | None:
        em_cache = self._cache.get(dia)
        if em_cache is None:
            return None
        buscado_em, dados = em_cache
        if self._ttl_segundos is not None and time.monotonic() - buscado_em >= self._ttl_segundos:
            return None
        return dados

    def _dados_do_dia(self, dia: date) -> DadosAgendaDoDia:
        """Resultado do parsing do dia, buscando na planilha só quando o cache não vale."""
        if nome_da_aba(dia) is None:
            logger.debug("%s é domingo: a clínica não abre, nada a buscar.", dia.isoformat())
            return DadosAgendaDoDia()
        dados = self._em_cache(dia)
        if dados is not None:
            return dados
        with self._lock:
            # Outra thread pode ter buscado a semana enquanto esta esperava.
            dados = self._em_cache(dia)
            if dados is None:
                dados = self._carregar_semana(dia)
        return dados

    def _carregar_semana(self, dia: date) -> DadosAgendaDoDia:
        """Busca todas as abas uma vez e guarda os dias da semana de `dia`.

        Só o dia pedido propaga erro de parsing: os outros dias da semana que
        falharem ficam fora do cache e são tentados de novo quando pedidos.
        """
        buscado_em = time.monotonic()
        abas: Sequence[Mapping[str, Any]] = self.planilha.fetch_sheet_metadata(
            params={"includeGridData": "true", "fields": _CAMPOS_DA_GRADE}
        ).get("sheets", [])
        titulos = [str(aba.get("properties", {}).get("title", "")) for aba in abas]
        _avisar_formatacao_condicional(abas, titulos)

        pedido = DadosAgendaDoDia()
        for outro_dia in dias_da_semana_de(dia):
            try:
                dados = self._parsear_dia(outro_dia, abas, titulos, avisar=outro_dia == dia)
            except Exception as erro:
                if outro_dia == dia:
                    raise
                # Só o tipo do erro: a mensagem pode repetir dados da planilha.
                logger.warning(
                    "Falha ao interpretar a aba de %s (%s); será lida de novo quando pedida.",
                    outro_dia.isoformat(),
                    type(erro).__name__,
                )
                continue
            self._cache[outro_dia] = (buscado_em, dados)
            if outro_dia == dia:
                pedido = dados
        return pedido

    def _parsear_dia(
        self,
        dia: date,
        abas: Sequence[Mapping[str, Any]],
        titulos: list[str],
        *,
        avisar: bool,
    ) -> DadosAgendaDoDia:
        """Entrega ao parser os dados crus da aba de `dia`."""
        titulo = encontrar_titulo_da_aba(dia, titulos)
        if titulo is None:
            if avisar:
                logger.warning(
                    "A planilha não tem aba %r para %s: agenda do dia vazia.",
                    nome_da_aba(dia),
                    dia.isoformat(),
                )
            return DadosAgendaDoDia()

        aba = abas[titulos.index(titulo)]
        valores, cores, trechos = _valores_e_cores(aba)
        merges = [_intervalo_para_a1(merge) for merge in aba.get("merges", [])]
        return parse_worksheet_data(dia, valores, merges, cores, trechos)


def _avisar_formatacao_condicional(
    abas: Sequence[Mapping[str, Any]], titulos: Sequence[str]
) -> None:
    """Avisa (só a contagem) quando alguma aba tem regra condicional de cor de texto.

    Com ela, a cor exibida pode não ser a digitada que o parser lê.
    """
    for aba, titulo in zip(abas, titulos, strict=True):
        regras = [
            regra
            for regra in aba.get("conditionalFormats", [])
            if regra.get("booleanRule", {}).get("format", {}).get("textFormat")
        ]
        if regras:
            logger.warning(
                "Aba %r tem %d regra(s) de formatação condicional na cor do texto: "
                "a cor lida é a digitada e pode não ser a exibida.",
                titulo,
                len(regras),
            )


def _valores_e_cores(
    aba: Mapping[str, Any],
) -> tuple[list[list[str]], dict[str, str], dict[str, list[tuple[int, str]]]]:
    """Separa o payload da aba em texto por célula, cor de fonte e cor por trecho.

    Só as células com texto entram nos mapas de cores: as vazias não têm
    convênio a informar. Os trechos (`textFormatRuns`) só aparecem nas células
    com mais de uma formatação; trecho sem cor herda a da célula.
    """
    valores: list[list[str]] = []
    cores: dict[str, str] = {}
    trechos: dict[str, list[tuple[int, str]]] = {}
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
                    runs: Sequence[Mapping[str, Any]] = celula.get("textFormatRuns", [])
                    if runs:
                        trechos[endereco] = [
                            (
                                int(run.get("startIndex", 0)),
                                _cor_do_formato(run.get("format", {})) or cores[endereco],
                            )
                            for run in runs
                        ]
            valores.append(textos)
    return valores, cores, trechos


def _cor_da_fonte(celula: Mapping[str, Any]) -> str:
    """Cor de fonte da célula inteira em `#RRGGBB`; sem cor informada, preto."""
    formato: Mapping[str, Any] = celula.get("userEnteredFormat", {}).get("textFormat", {})
    return _cor_do_formato(formato) or _COR_DE_FONTE_PADRAO


def _cor_do_formato(formato: Mapping[str, Any]) -> str | None:
    """Converte a cor de um `TextFormat` (canais de 0 a 1) em `#RRGGBB`.

    Vale o `foregroundColor`; sem ele, o `rgbColor` de `foregroundColorStyle`.
    Cor de tema (sem RGB no payload) conta como não informada. A API omite os
    canais iguais a zero, então um dicionário vazio já é preto.
    """
    cor: Mapping[str, Any] | None = formato.get("foregroundColor")
    if cor is None:
        cor = formato.get("foregroundColorStyle", {}).get("rgbColor")
    if cor is None:
        return None
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
