"""Ocupação de um único profissional, dia a dia, na semana de uma data: os modelos.

Escalados são as entradas de grade do profissional; ocupados são os slots
cobertos por atendimentos dele — uma sessão em grupo é um único `Atendimento`
com vários `paciente_ids`, então o slot conta uma vez; um atendimento sem
entrada correspondente na grade conta como ocupado mesmo assim. O recorte é um
profissional, a semana inteira, com o detalhamento por bloco (manhã/tarde) e
por sala/posto. Quem monta estes modelos a partir da fotografia do período é
`app.engine.visoes_fotografia.ocupacao_semanal_profissional`.
"""

import math
from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.domain import Especialidade
from app.domain.constants import META_OCUPACAO_POR_SALA
from app.engine.ocupacao import percentual_de_ocupacao

#: Casas decimais usadas para arredondar `META * escalados` antes do `ceil`:
#: sem isso, `0.8 * 15` vira `12.000000000000002` e a meta pediria 13 slots.
_CASAS_ARREDONDAMENTO_META = 9


def slots_para_meta(slots_escalados: int, slots_ocupados: int) -> int:
    """Quantos slots ainda faltam ocupar para atingir `META_OCUPACAO_POR_SALA`.

    Zero quando a meta já foi atingida (ou superada) e quando não há escala.
    """
    alvo = math.ceil(round(META_OCUPACAO_POR_SALA * slots_escalados, _CASAS_ARREDONDAMENTO_META))
    return max(0, alvo - slots_ocupados)


class _MetricasDeOcupacao(BaseModel):
    """Escalados/ocupados e as métricas derivadas, comuns ao dia e à semana."""

    model_config = ConfigDict(frozen=True)

    slots_escalados: int = Field(ge=0)
    slots_ocupados: int = Field(ge=0)

    @property
    def slots_livres(self) -> int:
        """Slots escalados ainda sem atendimento (nunca negativo)."""
        return max(0, self.slots_escalados - self.slots_ocupados)

    @property
    def percentual(self) -> float:
        """Fração de `slots_escalados` já ocupada (0.0 quando não há escala)."""
        return percentual_de_ocupacao(self.slots_escalados, self.slots_ocupados)

    @property
    def slots_para_meta(self) -> int:
        """Slots que faltam ocupar para atingir a meta (0 quando já atingida)."""
        return slots_para_meta(self.slots_escalados, self.slots_ocupados)

    @property
    def abaixo_da_meta(self) -> bool:
        """Se ainda faltam slots para a meta.

        Derivado de `slots_para_meta` (contas inteiras) e não da comparação de
        `percentual` com a meta em ponto flutuante, para os dois nunca
        discordarem num caso de fronteira.
        """
        return self.slots_para_meta > 0


class OcupacaoSalaPosto(BaseModel):
    """Escalados/ocupados de um profissional numa sala+posto, num dia."""

    model_config = ConfigDict(frozen=True)

    sala_id: str
    sala_nome: str
    indice_posto: int = Field(ge=0)
    slots_escalados: int = Field(ge=0)
    slots_ocupados: int = Field(ge=0)


class OcupacaoDiaProfissional(_MetricasDeOcupacao):
    """Ocupação de um profissional num dia, com os blocos manhã/tarde e por sala/posto."""

    data: date
    manha_escalados: int = Field(ge=0)
    manha_ocupados: int = Field(ge=0)
    tarde_escalados: int = Field(ge=0)
    tarde_ocupados: int = Field(ge=0)
    por_sala_posto: list[OcupacaoSalaPosto]
    #: Slots ocupados por atendimentos sem entrada correspondente na grade do
    #: profissional — já incluídos em `slots_ocupados`.
    slots_sem_grade: int = Field(default=0, ge=0)


class OcupacaoSemanalProfissional(_MetricasDeOcupacao):
    """Ocupação de um profissional na semana (segunda a sábado) de uma data.

    `slots_escalados`/`slots_ocupados` são a soma de `dias`, então o
    percentual da semana é ponderado pelos slots de cada dia — não a média
    dos percentuais diários.
    """

    profissional_id: str
    nome: str
    especialidade: Especialidade | None
    semana_inicio: date
    semana_fim: date
    dias: list[OcupacaoDiaProfissional]
    dias_sem_agenda: list[date]
    dias_com_falha: list[date]

    @property
    def parcial(self) -> bool:
        """Se algum dia da semana não pôde ser lido (os totais estão incompletos)."""
        return bool(self.dias_com_falha)

    @property
    def tem_inconsistencia(self) -> bool:
        """Se há atendimento fora dos horários escalados do profissional."""
        return any(dia.slots_sem_grade > 0 for dia in self.dias)
