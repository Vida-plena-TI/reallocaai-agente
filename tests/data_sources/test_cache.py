"""Testes de `CacheadoScheduleDataSource` (Fase 6a)."""

from dataclasses import dataclass
from datetime import date

import pytest

from app.data_sources.base import EntradaGrade
from app.data_sources.cache import CacheadoScheduleDataSource
from app.domain import Atendimento, Paciente, Profissional, Sala

DIA = date(2026, 9, 8)
OUTRO_DIA = date(2026, 9, 9)


@dataclass
class FonteContadora:
    """`ScheduleDataSource` que conta quantas vezes cada método foi chamado de fato."""

    chamadas_listar_salas: int = 0
    chamadas_listar_profissionais: int = 0
    chamadas_listar_grade: int = 0
    chamadas_listar_atendimentos: int = 0
    chamadas_listar_pacientes: int = 0

    def listar_salas(self, dia: date) -> list[Sala]:
        self.chamadas_listar_salas += 1
        return []

    def listar_profissionais(self, dia: date) -> list[Profissional]:
        self.chamadas_listar_profissionais += 1
        return []

    def listar_grade(self, dia: date) -> list[EntradaGrade]:
        self.chamadas_listar_grade += 1
        return []

    def listar_atendimentos(self, dia: date) -> list[Atendimento]:
        self.chamadas_listar_atendimentos += 1
        return []

    def listar_pacientes(self, dia: date) -> list[Paciente]:
        self.chamadas_listar_pacientes += 1
        return []


def test_chamada_repetida_dentro_do_ttl_nao_chama_a_fonte_de_novo() -> None:
    fonte = FonteContadora()
    cache = CacheadoScheduleDataSource(fonte, ttl_segundos=60)

    cache.listar_salas(DIA)
    cache.listar_salas(DIA)
    cache.listar_salas(DIA)

    assert fonte.chamadas_listar_salas == 1


def test_cada_metodo_tem_seu_proprio_cache() -> None:
    fonte = FonteContadora()
    cache = CacheadoScheduleDataSource(fonte, ttl_segundos=60)

    cache.listar_salas(DIA)
    cache.listar_profissionais(DIA)
    cache.listar_grade(DIA)
    cache.listar_atendimentos(DIA)
    cache.listar_pacientes(DIA)

    assert fonte.chamadas_listar_salas == 1
    assert fonte.chamadas_listar_profissionais == 1
    assert fonte.chamadas_listar_grade == 1
    assert fonte.chamadas_listar_atendimentos == 1
    assert fonte.chamadas_listar_pacientes == 1


def test_dias_diferentes_nao_compartilham_cache() -> None:
    fonte = FonteContadora()
    cache = CacheadoScheduleDataSource(fonte, ttl_segundos=60)

    cache.listar_salas(DIA)
    cache.listar_salas(OUTRO_DIA)

    assert fonte.chamadas_listar_salas == 2


def test_chamada_apos_ttl_expirado_busca_de_novo_na_fonte(monkeypatch: pytest.MonkeyPatch) -> None:
    fonte = FonteContadora()
    cache = CacheadoScheduleDataSource(fonte, ttl_segundos=60)

    tempo_atual = 1_000.0
    monkeypatch.setattr("app.data_sources.cache.time.monotonic", lambda: tempo_atual)

    cache.listar_salas(DIA)
    assert fonte.chamadas_listar_salas == 1

    tempo_atual += 30  # ainda dentro do TTL de 60s
    cache.listar_salas(DIA)
    assert fonte.chamadas_listar_salas == 1

    tempo_atual += 31  # 61s desde a primeira busca: TTL vencido
    cache.listar_salas(DIA)
    assert fonte.chamadas_listar_salas == 2
