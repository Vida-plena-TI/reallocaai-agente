"""Oráculo dos casos reais: o resultado esperado, contado direto na grade crua.

Independente do motor que está sendo testado: não importa nada de `app` (nem
parser, nem engine, nem domínio — `test_oraculo.py` garante) e não reaproveita
regra nenhuma de lá. Lê o mesmo payload que a API do Sheets devolve e conta
do jeito mais simples possível:

- cabeçalho de bloco: linha com alguma célula `Sala N`; a linha seguinte traz
  o profissional de cada coluna;
- linha de horário: `HH:MM` na coluna A, dentro do expediente (07:00 a 11:30
  e 13:00 a 17:30);
- célula vazia sob um profissional = vaga; `FECHADO` = não existe; qualquer
  outro texto = ocupada.

Só entende o layout simples (uma sala por coluna, sem merge). As
inconsistências da planilha real são assunto dos testes do parser, não daqui.
"""

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, time
from typing import Any, Literal

#: Aba de cada `date.weekday()`; domingo não tem aba.
_ABAS = ("segunda", "terca", "quarta", "quinta", "sexta", "sabado")

_SALA = re.compile(r"^sala\s*0*(\d+)")
_HORA = re.compile(r"^(\d{1,2}):(\d{2})$")

EstadoProfissional = Literal["nao_existe", "nao_atende_no_dia", "sem_vaga_na_janela", "com_vaga"]


@dataclass(frozen=True, order=True)
class Vaga:
    dia: date
    sala: int
    hora: time
    profissional: str


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return " ".join(sem_acento.casefold().split())


def _no_expediente(hora: time) -> bool:
    return time(7) <= hora <= time(11, 30) or time(13) <= hora <= time(17, 30)


def _grade(payload: Mapping[str, Any], dia: date) -> list[list[str]]:
    """Texto das células da aba de `dia`; vazio se a aba não existe (ou é domingo)."""
    if dia.weekday() >= len(_ABAS):
        return []
    for aba in payload.get("sheets", []):
        if _normalizar(str(aba.get("properties", {}).get("title", ""))) != _ABAS[dia.weekday()]:
            continue
        return [
            [str(celula.get("formattedValue", "")) for celula in linha.get("values", [])]
            for dados in aba.get("data", [])
            for linha in dados.get("rowData", [])
        ]
    return []


def _celula(linhas: Sequence[Sequence[str]], linha: int, coluna: int) -> str:
    if linha >= len(linhas) or coluna >= len(linhas[linha]):
        return ""
    return linhas[linha][coluna].strip()


def nome_do_profissional(texto: str) -> str:
    """`Bia (Fono)` -> `Bia`: o que vem antes do parêntese ou da quebra de linha."""
    return re.split(r"[(\n]", texto, maxsplit=1)[0].strip()


@dataclass(frozen=True)
class _Celula:
    sala: int
    profissional: str
    hora: time
    texto: str


def _celulas(payload: Mapping[str, Any], dia: date) -> list[_Celula]:
    linhas = _grade(payload, dia)
    celulas: list[_Celula] = []
    salas: dict[int, int] = {}
    profissionais: dict[int, str] = {}
    for indice, linha in enumerate(linhas):
        cabecalho = {
            coluna: int(achado.group(1))
            for coluna, texto in enumerate(linha)
            if coluna > 0 and (achado := _SALA.match(_normalizar(texto)))
        }
        if cabecalho:
            salas = cabecalho
            profissionais = {
                coluna: nome_do_profissional(_celula(linhas, indice + 1, coluna))
                for coluna in salas
            }
            continue
        achado = _HORA.match(_celula(linhas, indice, 0))
        if achado is None or not salas:
            continue
        hora = time(int(achado.group(1)), int(achado.group(2)))
        if not _no_expediente(hora):
            continue
        for coluna, sala in salas.items():
            if not profissionais.get(coluna):
                continue
            texto = _celula(linhas, indice, coluna)
            if _normalizar(texto) == "fechado":
                continue
            celulas.append(_Celula(sala, profissionais[coluna], hora, texto))
    return celulas


def vagas(payload: Mapping[str, Any], dia: date) -> list[Vaga]:
    """Toda célula vazia sob um profissional, no expediente de `dia`."""
    return sorted(
        Vaga(dia, celula.sala, celula.hora, celula.profissional)
        for celula in _celulas(payload, dia)
        if not celula.texto
    )


def profissionais_do_dia(payload: Mapping[str, Any], dia: date) -> set[str]:
    return {celula.profissional for celula in _celulas(payload, dia)}


def estado_profissional(
    payload: Mapping[str, Any],
    nome: str | None,
    dia: date,
    semana: Sequence[date],
    inicio: time,
    fim: time,
) -> EstadoProfissional:
    """O estado de `nome` (como escrito na planilha) em `dia`, na janela [inicio, fim).

    `nome=None` é o profissional que não existe na planilha.
    """
    alvo = _normalizar(nome or "")
    if not alvo or not any(
        alvo == _normalizar(outro) for d in semana for outro in profissionais_do_dia(payload, d)
    ):
        return "nao_existe"
    if not any(alvo == _normalizar(outro) for outro in profissionais_do_dia(payload, dia)):
        return "nao_atende_no_dia"
    na_janela = [
        vaga
        for vaga in vagas(payload, dia)
        if _normalizar(vaga.profissional) == alvo and inicio <= vaga.hora < fim
    ]
    return "com_vaga" if na_janela else "sem_vaga_na_janela"
