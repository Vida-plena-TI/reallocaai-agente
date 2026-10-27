"""Leitura do catálogo da agenda: um dia por leitura, em paralelo, com cache de TTL.

`app.domain.catalogo_agenda` monta o catálogo; aqui ficam o I/O e o cache. A
semana (segunda a sábado) é lida dia a dia em paralelo, só com
`listar_profissionais`, `listar_salas` e `listar_grade` — nunca atendimentos
nem pacientes. O resultado fica guardado por fonte e semana durante
`cache_ttl_segundos` (60 s por padrão, o mesmo TTL do cache da agenda).
"""

import logging
import threading
import time
import weakref
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from app.config import get_settings
from app.domain import PROFISSIONAIS_APELIDOS, Profissional, ScheduleDataSource, dias_da_semana_de
from app.domain.catalogo_agenda import CatalogoAgenda, DiaDoCatalogo, montar_catalogo

logger = logging.getLogger(__name__)


class CatalogoIndisponivelError(RuntimeError):
    """Nenhum dia da semana pôde ser lido."""


def _ler_dia(fonte: ScheduleDataSource, dia: date) -> DiaDoCatalogo:
    return DiaDoCatalogo(
        profissionais=fonte.listar_profissionais(dia),
        salas=fonte.listar_salas(dia),
        grade=fonte.listar_grade(dia),
    )


def ler_catalogo(fonte: ScheduleDataSource, data: date) -> CatalogoAgenda:
    """Catálogo da semana de `data`, lendo os seis dias em paralelo (sem cache)."""
    semana = dias_da_semana_de(data)
    lidos: dict[date, DiaDoCatalogo] = {}
    com_falha: list[date] = []
    with ThreadPoolExecutor(max_workers=len(semana)) as executor:
        futuros = {dia: executor.submit(_ler_dia, fonte, dia) for dia in semana}
        for dia, futuro in futuros.items():
            try:
                lidos[dia] = futuro.result()
            except Exception as erro:
                # Só o tipo: a mensagem pode repetir dados da planilha.
                logger.warning(
                    "Catálogo: falha ao ler %s (%s).", dia.isoformat(), type(erro).__name__
                )
                com_falha.append(dia)
    if not lidos:
        raise CatalogoIndisponivelError("nenhum dia da semana pôde ser lido")
    return montar_catalogo(lidos, dias_com_falha=tuple(com_falha), apelidos=PROFISSIONAIS_APELIDOS)


class CacheCatalogo:
    """Catálogo por fonte e semana, válido por `ttl_segundos` (relógio monotônico).

    A fonte de teste é uma dataclass sem hash, então a chave é `id(fonte)` com
    uma referência fraca que confirma que é o mesmo objeto (um id reaproveitado
    por outro objeto nunca devolve o catálogo antigo). Falha não entra no cache.
    """

    def __init__(
        self,
        ttl_segundos: float | None = None,
        relogio: Callable[[], float] = time.monotonic,
        ler: Callable[[ScheduleDataSource, date], CatalogoAgenda] = ler_catalogo,
    ) -> None:
        self._ttl_segundos = ttl_segundos
        self._relogio = relogio
        self._ler = ler
        self._lock = threading.Lock()
        self._itens: dict[
            tuple[int, date], tuple[weakref.ReferenceType[object], float, CatalogoAgenda]
        ] = {}

    @property
    def ttl_segundos(self) -> float:
        if self._ttl_segundos is None:
            self._ttl_segundos = float(get_settings().cache_ttl_segundos)
        return self._ttl_segundos

    def obter(self, fonte: ScheduleDataSource, data: date) -> CatalogoAgenda:
        chave = (id(fonte), dias_da_semana_de(data)[0])
        agora = self._relogio()
        with self._lock:
            item = self._itens.get(chave)
            if item is not None:
                referencia, expira_em, catalogo = item
                if referencia() is fonte and agora < expira_em:
                    return catalogo
        catalogo = self._ler(fonte, data)
        with self._lock:
            self._itens[chave] = (weakref.ref(fonte), agora + self.ttl_segundos, catalogo)
            # Some com o que expirou ou cuja fonte já foi coletada.
            for outra, (referencia, expira_em, _) in list(self._itens.items()):
                if referencia() is None or expira_em <= agora:
                    del self._itens[outra]
        return catalogo


_CACHE = CacheCatalogo()


def catalogo_da_semana(fonte: ScheduleDataSource, data: date) -> CatalogoAgenda:
    """Catálogo da semana de `data`, do cache compartilhado do processo."""
    return _CACHE.obter(fonte, data)


def catalogo_ou_nada(fonte: ScheduleDataSource, data: date) -> CatalogoAgenda | None:
    """Como `catalogo_da_semana`, mas devolve `None` (e loga só o tipo) se a leitura falhar."""
    try:
        return catalogo_da_semana(fonte, data)
    except Exception as erro:
        logger.warning("Catálogo da agenda indisponível (%s).", type(erro).__name__)
        return None


def profissionais_do_catalogo(catalogo: CatalogoAgenda) -> dict[str, Profissional]:
    """Os profissionais do catálogo no formato que `resolver_profissional` recebe."""
    return {
        p.id: Profissional(id=p.id, nome=p.nome, especialidade=p.especialidade)
        for p in catalogo.profissionais.values()
    }
