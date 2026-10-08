"""Quantos pacientes cada profissional atende, por dia e no período.

Complementa `app.engine.ocupacao_profissional`: aquela mede slots (oferta e
ocupação da grade); esta conta pessoas. As regras de contagem:

- pacientes do dia de um profissional = conjunto de `paciente_ids` dos
  atendimentos dele no dia, somando todos os postos — quem aparece em dois
  blocos ou dois postos conta uma vez; numa sessão em grupo, cada paciente
  conta;
- sessões = número de `Atendimento`s;
- slots ocupados = slots cobertos por esses atendimentos, a mesma conta de
  `app.engine.ocupacao` e `app.engine.ocupacao_profissional`;
- dia com agenda = o profissional tem entrada de grade ou atendimento no dia,
  a mesma regra de `construir_ocupacao_semanal_profissional`.

Cada dia é lido uma única vez (`listar_profissionais`, `listar_grade` e
`listar_atendimentos`) e calculado para todos os profissionais de uma vez: o
número de leituras à fonte não cresce com o número de profissionais.
"""

import logging
from collections import defaultdict
from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.domain import (
    Atendimento,
    EntradaGrade,
    Especialidade,
    Profissional,
    ScheduleDataSource,
    normalizar_id,
)

logger = logging.getLogger(__name__)

#: `date.weekday()` do domingo, que nunca tem agenda (ver `app.domain.semana`).
_DOMINGO = 6


class CargaDiaProfissional(BaseModel):
    """Pacientes, sessões e slots ocupados de um profissional num dia com agenda."""

    model_config = ConfigDict(frozen=True)

    data: date
    pacientes: int = Field(ge=0)
    sessoes: int = Field(ge=0)
    slots_ocupados: int = Field(ge=0)


class CargaProfissional(BaseModel):
    """Pacientes de um profissional em cada dia com agenda do período consultado."""

    model_config = ConfigDict(frozen=True)

    profissional_id: str
    nome: str
    especialidade: Especialidade | None
    #: Só os dias com agenda (grade ou atendimento), em ordem.
    dias: list[CargaDiaProfissional]
    dias_sem_agenda: list[date]
    dias_com_falha: list[date]
    #: Soma dos pacientes dos dias com agenda dividida pelo número desses dias
    #: (dia com agenda e nenhum paciente entra como 0); `None` sem dia com agenda.
    media_pacientes_por_dia: float | None
    pacientes_distintos_semana: int = Field(ge=0)

    @property
    def parcial(self) -> bool:
        """Se algum dia do período não pôde ser lido (os números estão incompletos)."""
        return bool(self.dias_com_falha)


class CargaDoDia(BaseModel):
    """Pacientes distintos da clínica inteira num dia lido.

    Quem passa por mais de um profissional no dia conta uma vez só — por isso
    pode ser menor que a soma das linhas por profissional.
    """

    model_config = ConfigDict(frozen=True)

    data: date
    pacientes_distintos_clinica: int = Field(ge=0)


class CargaProfissionais(BaseModel):
    """Resultado de `construir_carga_profissionais`."""

    model_config = ConfigDict(frozen=True)

    #: Dias consultados (sem domingo), em ordem — lidos ou não.
    dias: list[date]
    #: Profissionais com ao menos um dia com agenda, por especialidade e nome.
    profissionais: list[CargaProfissional]
    #: Um item por dia lido com sucesso, em ordem.
    por_dia: list[CargaDoDia]
    dias_com_falha: list[date]

    @property
    def parcial(self) -> bool:
        """Se algum dia do período não pôde ser lido."""
        return bool(self.dias_com_falha)


class _DiaLido(BaseModel):
    """O que foi lido da fonte para um dia."""

    model_config = ConfigDict(frozen=True)

    data: date
    profissionais: list[Profissional]
    grade: list[EntradaGrade]
    atendimentos: list[Atendimento]


def _chave_ordenacao(especialidade: Especialidade | None, nome: str) -> tuple[bool, str, str]:
    """Especialidade e depois nome, sem acento e sem caixa; sem especialidade vai ao fim."""
    rotulo = especialidade.value if especialidade is not None else ""
    return especialidade is None, normalizar_id(rotulo), normalizar_id(nome)


def _nome_do_id(profissional_id: str) -> str:
    """Nome legível a partir do id, para quem não está em `listar_profissionais`."""
    return " ".join(parte.capitalize() for parte in profissional_id.split("-"))


def _ler_dias(fonte: ScheduleDataSource, dias: list[date]) -> tuple[list[_DiaLido], list[date]]:
    """Lê cada dia uma vez; dias cuja leitura falha voltam à parte (com warning).

    Se todos falharem, o último erro sobe.
    """
    lidos: list[_DiaLido] = []
    com_falha: list[date] = []
    ultimo_erro: Exception | None = None
    for dia in dias:
        try:
            lidos.append(
                _DiaLido(
                    data=dia,
                    profissionais=fonte.listar_profissionais(dia),
                    grade=fonte.listar_grade(dia),
                    atendimentos=fonte.listar_atendimentos(dia),
                )
            )
        except Exception as erro:
            ultimo_erro = erro
            com_falha.append(dia)
            # Só o tipo do erro: a mensagem pode repetir dados da planilha.
            logger.warning(
                "Falha ao ler a agenda de %s ao contar pacientes por profissional: %s",
                dia.isoformat(),
                type(erro).__name__,
            )
    if not lidos and ultimo_erro is not None:
        raise ultimo_erro
    return lidos, com_falha


def construir_carga_profissionais(
    fonte: ScheduleDataSource, dias: list[date]
) -> CargaProfissionais:
    """Pacientes por profissional em cada um de `dias` e pacientes distintos da clínica por dia.

    Domingos e datas repetidas são descartados. Um dia cuja leitura falhe é
    pulado com warning e entra em `dias_com_falha` (do resultado e de cada
    profissional): os números ficam parciais. Se todos os dias falharem, o
    último erro sobe. Profissional sem agenda em nenhum dia lido não aparece;
    com agenda e nenhum paciente, aparece com 0.
    """
    janela = sorted({dia for dia in dias if dia.weekday() != _DOMINGO})
    lidos, dias_com_falha = _ler_dias(fonte, janela)

    nomes: dict[str, str] = {}
    especialidades: dict[str, Especialidade] = {}
    carga_por_profissional: dict[str, list[CargaDiaProfissional]] = defaultdict(list)
    pacientes_na_semana: dict[str, set[str]] = defaultdict(set)
    sem_agenda: dict[str, list[date]] = defaultdict(list)
    por_dia: list[CargaDoDia] = []

    for lido in lidos:
        for profissional in lido.profissionais:
            nomes.setdefault(profissional.id, profissional.nome)
            especialidades.setdefault(profissional.id, profissional.especialidade)
        for entrada in lido.grade:
            especialidades.setdefault(entrada.profissional_id, entrada.especialidade)
        for atendimento in lido.atendimentos:
            especialidades.setdefault(atendimento.profissional_id, atendimento.especialidade)

    ids_com_agenda = sorted(
        {entrada.profissional_id for lido in lidos for entrada in lido.grade}
        | {atendimento.profissional_id for lido in lidos for atendimento in lido.atendimentos}
    )

    for lido in lidos:
        escalados = {entrada.profissional_id for entrada in lido.grade}
        atendimentos_por_profissional: dict[str, list[Atendimento]] = defaultdict(list)
        for atendimento in lido.atendimentos:
            atendimentos_por_profissional[atendimento.profissional_id].append(atendimento)

        for profissional_id in ids_com_agenda:
            do_profissional = atendimentos_por_profissional.get(profissional_id, [])
            if profissional_id not in escalados and not do_profissional:
                sem_agenda[profissional_id].append(lido.data)
                continue
            pacientes = {
                paciente_id
                for atendimento in do_profissional
                for paciente_id in atendimento.paciente_ids
            }
            pacientes_na_semana[profissional_id] |= pacientes
            carga_por_profissional[profissional_id].append(
                CargaDiaProfissional(
                    data=lido.data,
                    pacientes=len(pacientes),
                    sessoes=len(do_profissional),
                    slots_ocupados=sum(len(atendimento.slots) for atendimento in do_profissional),
                )
            )

        por_dia.append(
            CargaDoDia(
                data=lido.data,
                pacientes_distintos_clinica=len(
                    {
                        paciente_id
                        for atendimento in lido.atendimentos
                        for paciente_id in atendimento.paciente_ids
                    }
                ),
            )
        )

    profissionais = [
        CargaProfissional(
            profissional_id=profissional_id,
            nome=nomes.get(profissional_id) or _nome_do_id(profissional_id),
            especialidade=especialidades.get(profissional_id),
            dias=carga_por_profissional[profissional_id],
            dias_sem_agenda=sem_agenda.get(profissional_id, []),
            dias_com_falha=list(dias_com_falha),
            media_pacientes_por_dia=_media(carga_por_profissional[profissional_id]),
            pacientes_distintos_semana=len(pacientes_na_semana[profissional_id]),
        )
        for profissional_id in ids_com_agenda
    ]
    profissionais.sort(key=lambda carga: _chave_ordenacao(carga.especialidade, carga.nome))

    return CargaProfissionais(
        dias=janela,
        profissionais=profissionais,
        por_dia=por_dia,
        dias_com_falha=dias_com_falha,
    )


def _media(dias: list[CargaDiaProfissional]) -> float | None:
    """Média de pacientes por dia com agenda; `None` sem nenhum dia."""
    if not dias:
        return None
    return sum(dia.pacientes for dia in dias) / len(dias)
