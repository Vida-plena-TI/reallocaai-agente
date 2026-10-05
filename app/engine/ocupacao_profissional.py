"""Ocupação de um único profissional, dia a dia, na semana de uma data.

Usa as mesmas regras de `app.engine.ocupacao` (relatório do dia): escalados
são as entradas de grade do profissional; ocupados são os slots cobertos por
atendimentos dele — uma sessão em grupo é um único `Atendimento` com vários
`paciente_ids`, então o slot conta uma vez; um atendimento sem entrada
correspondente na grade gera warning e conta como ocupado mesmo assim. Aqui
o recorte é outro: um profissional, a semana inteira, com o detalhamento por
bloco (manhã/tarde) e por sala/posto que o relatório do dia não guarda.
"""

import logging
import math
from collections import defaultdict
from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.domain import (
    Atendimento,
    EntradaGrade,
    Especialidade,
    ScheduleDataSource,
    dias_da_semana_de,
)
from app.domain.constants import FIM_PAUSA, INICIO_PAUSA, META_OCUPACAO_POR_SALA
from app.domain.slot import Slot
from app.engine.ocupacao import percentual_de_ocupacao

logger = logging.getLogger(__name__)

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


def _e_manha(slot: Slot) -> bool:
    return slot.hora_inicio < INICIO_PAUSA


def _e_tarde(slot: Slot) -> bool:
    return slot.hora_inicio >= FIM_PAUSA


def _ocupacao_do_dia(
    profissional_id: str,
    dia: date,
    grade: list[EntradaGrade],
    atendimentos: list[Atendimento],
    nome_da_sala: dict[str, str],
) -> OcupacaoDiaProfissional:
    """Contagens do profissional num dia já lido (só os dados dele)."""
    escalados_por_posto: dict[tuple[str, int], int] = defaultdict(int)
    ocupados_por_posto: dict[tuple[str, int], int] = defaultdict(int)
    #: Postos escalados do profissional em cada slot, para atribuir cada slot
    #: ocupado à sala+posto certos.
    postos_por_slot: dict[Slot, set[tuple[str, int]]] = defaultdict(set)
    manha_escalados = tarde_escalados = 0
    for entrada in grade:
        posto = (entrada.sala_id, entrada.indice_posto)
        escalados_por_posto[posto] += 1
        postos_por_slot[entrada.slot].add(posto)
        manha_escalados += _e_manha(entrada.slot)
        tarde_escalados += _e_tarde(entrada.slot)

    manha_ocupados = tarde_ocupados = slots_sem_grade = 0
    for atendimento in atendimentos:
        posto_do_atendimento = (atendimento.sala_id, atendimento.indice_posto)
        for slot in atendimento.slots:
            manha_ocupados += _e_manha(slot)
            tarde_ocupados += _e_tarde(slot)
            postos_escalados = postos_por_slot.get(slot, set())
            if posto_do_atendimento in postos_escalados:
                ocupados_por_posto[posto_do_atendimento] += 1
            elif postos_escalados:
                # Escalado no horário, mas noutro posto: atribui ao posto da grade.
                ocupados_por_posto[min(postos_escalados)] += 1
            else:
                logger.warning(
                    "Atendimento %r do profissional %r cobre %s sem entrada "
                    "correspondente na grade de %s: contando como ocupado mesmo assim.",
                    atendimento.id,
                    profissional_id,
                    slot,
                    dia.isoformat(),
                )
                slots_sem_grade += 1
                ocupados_por_posto[posto_do_atendimento] += 1

    por_sala_posto = [
        OcupacaoSalaPosto(
            sala_id=sala_id,
            sala_nome=nome_da_sala.get(sala_id, sala_id),
            indice_posto=indice_posto,
            slots_escalados=escalados_por_posto.get((sala_id, indice_posto), 0),
            slots_ocupados=ocupados_por_posto.get((sala_id, indice_posto), 0),
        )
        for sala_id, indice_posto in sorted(set(escalados_por_posto) | set(ocupados_por_posto))
    ]

    return OcupacaoDiaProfissional(
        data=dia,
        slots_escalados=len(grade),
        slots_ocupados=sum(len(atendimento.slots) for atendimento in atendimentos),
        manha_escalados=manha_escalados,
        manha_ocupados=manha_ocupados,
        tarde_escalados=tarde_escalados,
        tarde_ocupados=tarde_ocupados,
        por_sala_posto=por_sala_posto,
        slots_sem_grade=slots_sem_grade,
    )


def construir_ocupacao_semanal_profissional(
    fonte: ScheduleDataSource, profissional_id: str, data_referencia: date
) -> OcupacaoSemanalProfissional:
    """Ocupação de `profissional_id` em cada dia (segunda a sábado) da semana de
    `data_referencia`, e o total ponderado da semana.

    Um dia cuja leitura falhe é pulado com warning e entra em `dias_com_falha`
    (os totais da semana ficam parciais); se todos falharem, o último erro
    sobe. Dias lidos sem nenhuma escala nem atendimento do profissional vão
    para `dias_sem_agenda`.
    """
    janela = dias_da_semana_de(data_referencia)

    dias: list[OcupacaoDiaProfissional] = []
    dias_sem_agenda: list[date] = []
    dias_com_falha: list[date] = []
    nome: str | None = None
    especialidade: Especialidade | None = None
    ultimo_erro: Exception | None = None
    for dia in janela:
        try:
            grade = [
                entrada
                for entrada in fonte.listar_grade(dia)
                if entrada.profissional_id == profissional_id
            ]
            atendimentos = [
                atendimento
                for atendimento in fonte.listar_atendimentos(dia)
                if atendimento.profissional_id == profissional_id
            ]
            nome_da_sala = {sala.id: sala.nome for sala in fonte.listar_salas(dia)}
            profissional = next(
                (item for item in fonte.listar_profissionais(dia) if item.id == profissional_id),
                None,
            )
        except Exception as erro:
            ultimo_erro = erro
            dias_com_falha.append(dia)
            logger.warning(
                "Falha ao ler a agenda de %s ao calcular a ocupação de %r: %s",
                dia.isoformat(),
                profissional_id,
                erro,
            )
            continue

        if profissional is not None:
            nome = nome or profissional.nome
            especialidade = especialidade or profissional.especialidade
        elif grade and especialidade is None:
            especialidade = grade[0].especialidade

        if not grade and not atendimentos:
            dias_sem_agenda.append(dia)
            continue
        dias.append(_ocupacao_do_dia(profissional_id, dia, grade, atendimentos, nome_da_sala))

    if len(dias_com_falha) == len(janela) and ultimo_erro is not None:
        raise ultimo_erro

    return OcupacaoSemanalProfissional(
        profissional_id=profissional_id,
        nome=nome or profissional_id,
        especialidade=especialidade,
        semana_inicio=janela[0],
        semana_fim=janela[-1],
        slots_escalados=sum(dia.slots_escalados for dia in dias),
        slots_ocupados=sum(dia.slots_ocupados for dia in dias),
        dias=dias,
        dias_sem_agenda=dias_sem_agenda,
        dias_com_falha=dias_com_falha,
    )
