"""Os construtores antigos, refeitos sobre a fotografia, para os testes de regra.

As tools leem uma fotografia e chamam `app.engine.visoes_fotografia`; estes
atalhos fazem o mesmo a partir de uma fonte, para os testes que descrevem as
regras de ocupação, carga e busca de profissional continuarem valendo.
"""

from collections.abc import Iterable
from datetime import date

from app.ai.servico_agenda import LocalizacaoProfissional, resolver_profissional
from app.domain import ScheduleDataSource, dias_da_semana_de
from app.engine import visoes_fotografia
from app.engine.carga_profissionais import CargaProfissionais
from app.engine.fotografia import FotografiaPeriodo, fotografar_periodo
from app.engine.ocupacao_profissional import OcupacaoSemanalProfissional


def _semana(fonte: ScheduleDataSource, data: date) -> FotografiaPeriodo:
    semana = dias_da_semana_de(data)
    return fotografar_periodo(fonte, semana[0], semana[-1])


def ocupacao_semanal(
    fonte: ScheduleDataSource, profissional_id: str, data: date
) -> OcupacaoSemanalProfissional:
    return visoes_fotografia.ocupacao_semanal_profissional(_semana(fonte, data), profissional_id)


def construir_carga(fonte: ScheduleDataSource, dias: Iterable[date]) -> CargaProfissionais:
    validos = sorted({dia for dia in dias if dia.weekday() != 6})
    if not validos:
        return CargaProfissionais(dias=[], profissionais=[], por_dia=[], dias_com_falha=[])
    return visoes_fotografia.carga_profissionais(
        fotografar_periodo(fonte, validos[0], validos[-1]), validos
    )


def localizar(fonte: ScheduleDataSource, data: date, texto: str) -> LocalizacaoProfissional:
    return resolver_profissional(_semana(fonte, data).profissionais, texto)
