"""Fotografia da agenda de um período: cada dia lido uma vez, normalizado por slot.

É a base do motor de relatório genérico (`app.engine.relatorio_generico`).
Em vez de cada relatório reler a fonte com a sua própria conta, o período é
lido uma vez (dias em paralelo, com limite) e vira uma lista plana de
`SlotFotografado`: uma linha por janela da grade (livre ou ocupada), mais as
linhas que a grade não cobre — atendimento fora da grade e posto sem escala
(fechado).

As regras de atribuição são as mesmas da engine que já existia:

- ocupado = coberto por um `Atendimento` do mesmo profissional, sala, posto e
  slot (`listar_disponibilidade`); escalado no horário, mas noutro posto, vai
  para o menor posto escalado dele naquele slot (a mesma regra da ocupação
  semanal por profissional);
- atendimento sem entrada de grade do profissional no slot vira uma linha
  `na_grade=False` ocupada, com warning — conta como ocupado, como em
  `construir_relatorio_ocupacao_do_dia`;
- sessão em grupo é um único atendimento: o slot conta uma vez;
- domingo nunca tem agenda e nem é consultado.

`paciente_ids` é interno: o id é o nome normalizado do paciente, então nunca
sai em relatório, bloco ou log — só é usado para contar pessoas distintas e
para a agenda de um paciente pedido explicitamente.
"""

import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, time, timedelta
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from app.domain import (
    Atendimento,
    EntradaGrade,
    Especialidade,
    Paciente,
    Profissional,
    Sala,
    ScheduleDataSource,
    Slot,
)
from app.domain.constants import DURACAO_SLOT_MINUTOS, INICIO_PAUSA

logger = logging.getLogger(__name__)

#: `date.weekday()` do domingo, que nunca tem agenda (ver `app.domain.semana`).
_DOMINGO: Final[int] = 6

#: Máximo de dias lidos ao mesmo tempo. Seis cobre uma semana inteira.
MAX_LEITURAS_PARALELAS: Final[int] = 6

#: Maior período aceito numa fotografia (dias corridos, domingo incluído).
MAX_DIAS_POR_PERIODO: Final[int] = 62

#: Convênio de um slot ocupado cujos pacientes não têm convênio identificado.
CONVENIO_NAO_INFORMADO: Final[str] = "nao_informado"


class Periodo(StrEnum):
    """Bloco do dia: manhã (antes da pausa) ou tarde (depois dela)."""

    MANHA = "manha"
    TARDE = "tarde"


class EstadoSlot(StrEnum):
    """Situação de uma janela de 30 minutos num posto de sala."""

    LIVRE = "livre"
    OCUPADO = "ocupado"
    #: Posto da sala sem profissional escalado no horário (inclui `FECHADO` da planilha).
    FECHADO = "fechado"


def periodo_do_horario(hora: time) -> Periodo:
    """Manhã antes do início da pausa; tarde a partir dela."""
    return Periodo.MANHA if hora < INICIO_PAUSA else Periodo.TARDE


class SlotFotografado(BaseModel):
    """Uma janela de 30 minutos num posto de sala, num dia, já classificada."""

    model_config = ConfigDict(frozen=True)

    data: date
    hora_inicio: time
    periodo: Periodo
    estado: EstadoSlot
    sala_id: str
    indice_posto: int = Field(ge=0)
    #: `None` só no estado `FECHADO`.
    profissional_id: str | None
    especialidade: Especialidade | None
    #: Se a janela é uma entrada de `listar_grade` (oferta escalada).
    na_grade: bool
    #: Slots de atendimento que caíram aqui (1 no caso normal; 0 se livre).
    ocupacoes: int = Field(ge=0)
    atendimento_ids: tuple[str, ...] = ()
    #: Interno: ids normalizados (nomes) dos pacientes. Nunca exposto.
    paciente_ids: tuple[str, ...] = Field(default=(), repr=False)
    #: Convênio(s) dos pacientes do slot ocupado (`Convenio.value`, vários unidos
    #: por `+`, ou `CONVENIO_NAO_INFORMADO`); `None` quando não há paciente.
    convenio: str | None = None
    aguardando_autorizacao: bool = False

    @property
    def livre(self) -> bool:
        """Escalado e sem atendimento: é uma vaga."""
        return self.estado is EstadoSlot.LIVRE

    @property
    def hora_fim(self) -> time:
        """Fim da janela (exclusivo)."""
        return _somar_minutos(self.hora_inicio, DURACAO_SLOT_MINUTOS)


class FotografiaPeriodo(BaseModel):
    """A agenda de um período inteiro, lida uma vez e normalizada por slot."""

    model_config = ConfigDict(frozen=True)

    inicio: date
    fim: date
    #: Dias do período com expediente (sem domingo), lidos ou não.
    dias_consultados: list[date]
    dias_lidos: list[date]
    #: Dias cuja leitura falhou; os números do período ficam parciais.
    dias_nao_lidos: list[date]
    slots: list[SlotFotografado]
    profissionais: dict[str, Profissional]
    salas: dict[str, Sala]
    #: Internos: só a agenda de um paciente pedido explicitamente os usa.
    atendimentos: list[Atendimento] = Field(default_factory=list, repr=False, exclude=True)
    pacientes: dict[str, Paciente] = Field(default_factory=dict, repr=False, exclude=True)
    #: Interno: mensagem do erro de cada dia de `dias_nao_lidos`. Pode repetir
    #: dados da planilha, então nunca vai para log nem para bloco; só as tools
    #: antigas a devolvem, como já faziam, quando o dia pedido não pôde ser lido.
    erros_de_leitura: dict[date, str] = Field(default_factory=dict, repr=False, exclude=True)
    #: Internos: ids listados por `listar_salas` e `listar_profissionais` em cada
    #: dia lido. As tools antigas nomeiam sala e profissional só com a lista do
    #: próprio dia (fora dela, mostram o id), e isso é preservado.
    salas_por_dia: dict[date, frozenset[str]] = Field(
        default_factory=dict, repr=False, exclude=True
    )
    profissionais_por_dia: dict[date, frozenset[str]] = Field(
        default_factory=dict, repr=False, exclude=True
    )

    @property
    def parcial(self) -> bool:
        """Se algum dia do período não pôde ser lido."""
        return bool(self.dias_nao_lidos)

    def nome_profissional(self, profissional_id: str) -> str:
        """Nome do profissional, ou um nome legível derivado do id."""
        profissional = self.profissionais.get(profissional_id)
        if profissional is not None:
            return profissional.nome
        return " ".join(parte.capitalize() for parte in profissional_id.split("-"))

    def nome_sala(self, sala_id: str) -> str:
        """Nome da sala, ou o próprio id quando ela não está em `listar_salas`."""
        sala = self.salas.get(sala_id)
        return sala.nome if sala is not None else sala_id


class _DiaLido(BaseModel):
    """O que a fonte devolveu para um dia."""

    model_config = ConfigDict(frozen=True)

    data: date
    salas: list[Sala]
    profissionais: list[Profissional]
    grade: list[EntradaGrade]
    atendimentos: list[Atendimento]
    pacientes: list[Paciente]


@dataclass(slots=True)
class _Linha:
    """Acumulador mutável de uma linha antes de virar `SlotFotografado`."""

    slot: Slot
    sala_id: str
    indice_posto: int
    profissional_id: str | None
    especialidade: Especialidade | None
    na_grade: bool
    ocupacoes: int = 0
    atendimento_ids: list[str] = field(default_factory=list)
    paciente_ids: list[str] = field(default_factory=list)
    aguardando_autorizacao: bool = False


def _somar_minutos(hora: time, minutos: int) -> time:
    total = hora.hour * 60 + hora.minute + minutos
    return time(total // 60, total % 60)


def dias_do_periodo(inicio: date, fim: date) -> list[date]:
    """Dias de `inicio` a `fim` (inclusive), sem domingo."""
    if fim < inicio:
        raise ValueError("O fim do período não pode ser anterior ao início.")
    quantidade = (fim - inicio).days + 1
    if quantidade > MAX_DIAS_POR_PERIODO:
        raise ValueError(
            f"Período longo demais ({quantidade} dias): o máximo é {MAX_DIAS_POR_PERIODO} dias."
        )
    dias = (inicio + timedelta(days=deslocamento) for deslocamento in range(quantidade))
    return [dia for dia in dias if dia.weekday() != _DOMINGO]


def _ler_dia(fonte: ScheduleDataSource, dia: date) -> _DiaLido:
    return _DiaLido(
        data=dia,
        salas=fonte.listar_salas(dia),
        profissionais=fonte.listar_profissionais(dia),
        grade=fonte.listar_grade(dia),
        atendimentos=fonte.listar_atendimentos(dia),
        pacientes=fonte.listar_pacientes(dia),
    )


def _ler_dias(
    fonte: ScheduleDataSource, dias: list[date]
) -> tuple[list[_DiaLido], dict[date, Exception]]:
    """Lê os dias em paralelo; os que falham voltam à parte (com o erro), com warning.

    Se todos falharem, o último erro sobe (o mesmo contrato das leituras de
    vários dias que já existiam na engine).
    """
    if not dias:
        return [], {}
    lidos: list[_DiaLido] = []
    com_falha: dict[date, Exception] = {}
    ultimo_erro: Exception | None = None
    trabalhadores = min(MAX_LEITURAS_PARALELAS, len(dias))
    with ThreadPoolExecutor(max_workers=trabalhadores) as executor:
        futuros = [(dia, executor.submit(_ler_dia, fonte, dia)) for dia in dias]
        for dia, futuro in futuros:
            try:
                lidos.append(futuro.result())
            except Exception as erro:
                ultimo_erro = erro
                com_falha[dia] = erro
                # Só o tipo do erro: a mensagem pode repetir dados da planilha.
                logger.warning(
                    "Falha ao ler a agenda de %s ao montar a fotografia do período: %s",
                    dia.isoformat(),
                    type(erro).__name__,
                )
    if not lidos and ultimo_erro is not None:
        raise ultimo_erro
    return lidos, com_falha


def _convenio_do_slot(paciente_ids: list[str], pacientes: dict[str, Paciente]) -> str | None:
    if not paciente_ids:
        return None
    rotulos = {
        paciente.convenio.value
        if (paciente := pacientes.get(paciente_id)) is not None and paciente.convenio is not None
        else CONVENIO_NAO_INFORMADO
        for paciente_id in paciente_ids
    }
    return "+".join(sorted(rotulos))


def _fotografar_dia(lido: _DiaLido) -> list[SlotFotografado]:
    especialidade_por_profissional = {p.id: p.especialidade for p in lido.profissionais}
    linhas: dict[tuple[str | None, str, int, Slot], _Linha] = {}
    postos_por_profissional_e_slot: dict[tuple[str, Slot], list[tuple[str, int]]] = defaultdict(
        list
    )
    for entrada in lido.grade:
        chave = (entrada.profissional_id, entrada.sala_id, entrada.indice_posto, entrada.slot)
        linhas[chave] = _Linha(
            slot=entrada.slot,
            sala_id=entrada.sala_id,
            indice_posto=entrada.indice_posto,
            profissional_id=entrada.profissional_id,
            especialidade=especialidade_por_profissional.get(
                entrada.profissional_id, entrada.especialidade
            ),
            na_grade=True,
        )
        postos_por_profissional_e_slot[(entrada.profissional_id, entrada.slot)].append(
            (entrada.sala_id, entrada.indice_posto)
        )

    for atendimento in lido.atendimentos:
        for slot in atendimento.slots:
            chave = (
                atendimento.profissional_id,
                atendimento.sala_id,
                atendimento.indice_posto,
                slot,
            )
            linha = linhas.get(chave)
            if linha is None:
                postos = postos_por_profissional_e_slot.get((atendimento.profissional_id, slot))
                if postos:
                    # Escalado no horário, mas noutro posto: vai para o posto da grade.
                    sala_id, indice_posto = min(postos)
                    linha = linhas[(atendimento.profissional_id, sala_id, indice_posto, slot)]
                else:
                    logger.warning(
                        "Atendimento %r cobre %s sem entrada correspondente na grade de %s: "
                        "contando como ocupado mesmo assim.",
                        atendimento.id,
                        slot,
                        lido.data.isoformat(),
                    )
                    linha = linhas.setdefault(
                        chave,
                        _Linha(
                            slot=slot,
                            sala_id=atendimento.sala_id,
                            indice_posto=atendimento.indice_posto,
                            profissional_id=atendimento.profissional_id,
                            especialidade=especialidade_por_profissional.get(
                                atendimento.profissional_id, atendimento.especialidade
                            ),
                            na_grade=False,
                        ),
                    )
            linha.ocupacoes += 1
            linha.atendimento_ids.append(atendimento.id)
            linha.paciente_ids.extend(atendimento.paciente_ids)
            linha.aguardando_autorizacao |= atendimento.aguardando_autorizacao

    # Postos da sala sem ninguém escalado num horário do expediente: fechado.
    horarios_por_posto: dict[tuple[str, int], set[Slot]] = defaultdict(set)
    for linha in linhas.values():
        horarios_por_posto[(linha.sala_id, linha.indice_posto)].add(linha.slot)
    for (sala_id, indice_posto), cobertos in horarios_por_posto.items():
        for slot in Slot.slots_do_dia(lido.data):
            if slot not in cobertos:
                linhas[(None, sala_id, indice_posto, slot)] = _Linha(
                    slot=slot,
                    sala_id=sala_id,
                    indice_posto=indice_posto,
                    profissional_id=None,
                    especialidade=None,
                    na_grade=False,
                )

    pacientes = {paciente.id: paciente for paciente in lido.pacientes}
    fotografados = [
        SlotFotografado(
            data=lido.data,
            hora_inicio=linha.slot.hora_inicio,
            periodo=periodo_do_horario(linha.slot.hora_inicio),
            estado=(
                EstadoSlot.OCUPADO
                if linha.ocupacoes
                else EstadoSlot.LIVRE
                if linha.na_grade
                else EstadoSlot.FECHADO
            ),
            sala_id=linha.sala_id,
            indice_posto=linha.indice_posto,
            profissional_id=linha.profissional_id,
            especialidade=linha.especialidade,
            na_grade=linha.na_grade,
            ocupacoes=linha.ocupacoes,
            atendimento_ids=tuple(dict.fromkeys(linha.atendimento_ids)),
            paciente_ids=tuple(dict.fromkeys(linha.paciente_ids)),
            convenio=_convenio_do_slot(linha.paciente_ids, pacientes),
            aguardando_autorizacao=linha.aguardando_autorizacao,
        )
        for linha in linhas.values()
    ]
    fotografados.sort(
        key=lambda s: (s.hora_inicio, s.sala_id, s.indice_posto, s.profissional_id or "")
    )
    return fotografados


def fotografar_periodo(fonte: ScheduleDataSource, inicio: date, fim: date) -> FotografiaPeriodo:
    """Lê cada dia de `inicio` a `fim` uma única vez e normaliza tudo por slot.

    Domingo fica de fora sem consultar a fonte. Um dia cuja leitura falhe
    entra em `dias_nao_lidos` e o resto segue (o resultado fica parcial); se
    todos falharem, o último erro sobe.
    """
    dias = dias_do_periodo(inicio, fim)
    lidos, com_falha = _ler_dias(fonte, dias)

    profissionais: dict[str, Profissional] = {}
    salas: dict[str, Sala] = {}
    pacientes: dict[str, Paciente] = {}
    slots: list[SlotFotografado] = []
    atendimentos: list[Atendimento] = []
    for lido in sorted(lidos, key=lambda item: item.data):
        for profissional in lido.profissionais:
            profissionais.setdefault(profissional.id, profissional)
        for sala in lido.salas:
            salas.setdefault(sala.id, sala)
        for paciente in lido.pacientes:
            atual = pacientes.get(paciente.id)
            if atual is None or (atual.convenio is None and paciente.convenio is not None):
                pacientes[paciente.id] = paciente
        slots.extend(_fotografar_dia(lido))
        atendimentos.extend(lido.atendimentos)

    return FotografiaPeriodo(
        inicio=inicio,
        fim=fim,
        dias_consultados=dias,
        dias_lidos=sorted(lido.data for lido in lidos),
        dias_nao_lidos=sorted(com_falha),
        erros_de_leitura={dia: str(erro) for dia, erro in com_falha.items()},
        salas_por_dia={lido.data: frozenset(s.id for s in lido.salas) for lido in lidos},
        profissionais_por_dia={
            lido.data: frozenset(p.id for p in lido.profissionais) for lido in lidos
        },
        slots=slots,
        profissionais=profissionais,
        salas=salas,
        atendimentos=atendimentos,
        pacientes=pacientes,
    )
