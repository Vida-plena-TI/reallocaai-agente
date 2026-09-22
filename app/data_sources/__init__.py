"""Fontes de dados da agenda: a implementação sobre o Google Sheets.

O contrato (`ScheduleDataSource`/`EntradaGrade`) mora em `app.domain` (Fase 8):
é a engine e os relatórios que dependem dele, não o contrário.
"""

from app.data_sources.continuidade import (
    ContinuidadeDataSource,
    SemHistoricoContinuidadeDataSource,
)
from app.data_sources.google_sheets import GoogleSheetsDataSource
from app.data_sources.google_sheets_parser import (
    DadosAgendaDoDia,
    encontrar_titulo_da_aba,
    nome_da_aba,
    parse_worksheet_data,
)

__all__ = [
    "ContinuidadeDataSource",
    "DadosAgendaDoDia",
    "GoogleSheetsDataSource",
    "SemHistoricoContinuidadeDataSource",
    "encontrar_titulo_da_aba",
    "nome_da_aba",
    "parse_worksheet_data",
]
