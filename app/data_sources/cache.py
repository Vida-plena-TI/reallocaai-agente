"""Cache em memória de leitura da agenda, com expiração por TTL.

Envolve uma `ScheduleDataSource` real e guarda, por método e por dia, o
resultado da última busca por `ttl_segundos`. Usa `time.monotonic()` — nunca
`datetime.now()` — porque a expiração do cache não pode ser afetada por uma
mudança no relógio do sistema.
"""

import time
from collections.abc import Callable
from datetime import date
from typing import TypeVar, cast

from app.data_sources.base import EntradaGrade, ScheduleDataSource
from app.domain import Atendimento, Paciente, Profissional, Sala

_T = TypeVar("_T")

#: (nome do método, dia consultado, demais argumentos do método).
_ChaveDeCache = tuple[str, date, tuple[object, ...]]


class CacheadoScheduleDataSource:
    """`ScheduleDataSource` que cacheia em memória o resultado de `fonte`.

    Implementa o mesmo `Protocol` por duck typing: cada método, para um dado
    dia, busca em `fonte` só na primeira chamada (ou depois de `ttl_segundos`
    vencido) e devolve o valor guardado nas chamadas seguintes.
    """

    def __init__(self, fonte: ScheduleDataSource, ttl_segundos: int) -> None:
        self._fonte = fonte
        self._ttl_segundos = ttl_segundos
        self._cache: dict[_ChaveDeCache, tuple[float, object]] = {}

    def listar_salas(self, dia: date) -> list[Sala]:
        """Salas em uso no dia, com a capacidade simultânea de cada uma."""
        return self._cacheado("listar_salas", dia, self._fonte.listar_salas)

    def listar_profissionais(self, dia: date) -> list[Profissional]:
        """Profissionais escalados no dia."""
        return self._cacheado("listar_profissionais", dia, self._fonte.listar_profissionais)

    def listar_grade(self, dia: date) -> list[EntradaGrade]:
        """Todas as janelas (sala, profissional, slot) abertas no dia."""
        return self._cacheado("listar_grade", dia, self._fonte.listar_grade)

    def listar_atendimentos(self, dia: date) -> list[Atendimento]:
        """Atendimentos já alocados no dia."""
        return self._cacheado("listar_atendimentos", dia, self._fonte.listar_atendimentos)

    def listar_pacientes(self, dia: date) -> list[Paciente]:
        """Todos os pacientes que aparecem em algum `Atendimento` do dia."""
        return self._cacheado("listar_pacientes", dia, self._fonte.listar_pacientes)

    def _cacheado(self, nome_do_metodo: str, dia: date, buscar: Callable[[date], _T]) -> _T:
        """Devolve o valor em cache para `(nome_do_metodo, dia)`, se ainda válido.

        Passado o TTL (ou na primeira vez), busca de novo em `fonte` e
        reescreve o cache com uma nova expiração a partir de agora.
        """
        chave: _ChaveDeCache = (nome_do_metodo, dia, ())
        agora = time.monotonic()

        em_cache = self._cache.get(chave)
        if em_cache is not None:
            expira_em, valor_em_cache = em_cache
            if agora < expira_em:
                return cast(_T, valor_em_cache)

        valor = buscar(dia)
        self._cache[chave] = (agora + self._ttl_segundos, valor)
        return valor
