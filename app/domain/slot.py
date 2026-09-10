"""`Slot`: a janela de 30 minutos que é a unidade atômica da agenda."""

from datetime import date, time
from functools import total_ordering
from typing import Self

from pydantic import BaseModel, ConfigDict, field_validator

from app.domain.constants import (
    DURACAO_SLOT_MINUTOS,
    FIM_PAUSA,
    HORARIO_ABERTURA,
    HORARIO_FECHAMENTO,
    INICIO_PAUSA,
)
from app.domain.exceptions import (
    SlotForaDoExpedienteError,
    SlotForaDoGridError,
    SlotNaPausaError,
)

_MINUTOS_POR_HORA = 60


def _para_minutos(hora: time) -> int:
    """Converte um horário em minutos desde a meia-noite."""
    return hora.hour * _MINUTOS_POR_HORA + hora.minute


def _para_hora(minutos: int) -> time:
    """Converte minutos desde a meia-noite em horário."""
    return time(minutos // _MINUTOS_POR_HORA, minutos % _MINUTOS_POR_HORA)


@total_ordering
class Slot(BaseModel):
    """Janela imutável de 30 minutos em um dia específico.

    Só existe slot válido: o construtor recusa horários fora do expediente,
    fora do grid de 30 em 30 minutos ou dentro da pausa geral.
    """

    model_config = ConfigDict(frozen=True)

    data: date
    hora_inicio: time

    @field_validator("hora_inicio")
    @classmethod
    def _validar_hora_inicio(cls, hora_inicio: time) -> time:
        inicio = _para_minutos(hora_inicio)
        fim = inicio + DURACAO_SLOT_MINUTOS

        if inicio < _para_minutos(HORARIO_ABERTURA) or fim > _para_minutos(HORARIO_FECHAMENTO):
            raise SlotForaDoExpedienteError(
                f"{hora_inicio:%H:%M} está fora do expediente "
                f"({HORARIO_ABERTURA:%H:%M} às {HORARIO_FECHAMENTO:%H:%M})."
            )

        if inicio < _para_minutos(FIM_PAUSA) and fim > _para_minutos(INICIO_PAUSA):
            raise SlotNaPausaError(
                f"{hora_inicio:%H:%M} cai na pausa geral "
                f"({INICIO_PAUSA:%H:%M} às {FIM_PAUSA:%H:%M})."
            )

        if inicio % DURACAO_SLOT_MINUTOS != 0 or hora_inicio.second or hora_inicio.microsecond:
            raise SlotForaDoGridError(
                f"{hora_inicio:%H:%M:%S} não está no grid de {DURACAO_SLOT_MINUTOS} minutos."
            )

        return hora_inicio

    @property
    def hora_fim(self) -> time:
        """Horário em que a janela termina (exclusivo)."""
        return _para_hora(_para_minutos(self.hora_inicio) + DURACAO_SLOT_MINUTOS)

    @classmethod
    def slots_do_dia(cls, data: date) -> list[Self]:
        """Todos os slots atendíveis de um dia, em ordem e já sem a pausa."""
        slots: list[Self] = []
        inicio = _para_minutos(HORARIO_ABERTURA)
        fechamento = _para_minutos(HORARIO_FECHAMENTO)
        pausa_inicio = _para_minutos(INICIO_PAUSA)
        pausa_fim = _para_minutos(FIM_PAUSA)

        while inicio + DURACAO_SLOT_MINUTOS <= fechamento:
            na_pausa = inicio < pausa_fim and inicio + DURACAO_SLOT_MINUTOS > pausa_inicio
            if not na_pausa:
                slots.append(cls(data=data, hora_inicio=_para_hora(inicio)))
            inicio += DURACAO_SLOT_MINUTOS

        return slots

    def e_contiguo_a(self, outro: Slot) -> bool:
        """Diz se os dois slots são imediatamente sequenciais, em qualquer ordem.

        Contíguo significa mesmo dia e fim de um igual ao início do outro. Como
        a pausa geral não é um slot, 11:30 e 13:00 **não** são contíguos: o
        atendimento seria interrompido pelo almoço.
        """
        if self.data != outro.data:
            return False
        return self.hora_fim == outro.hora_inicio or outro.hora_fim == self.hora_inicio

    def __lt__(self, outro: object) -> bool:
        if not isinstance(outro, Slot):
            return NotImplemented
        return (self.data, self.hora_inicio) < (outro.data, outro.hora_inicio)

    def __str__(self) -> str:
        return f"{self.data:%d/%m/%Y} {self.hora_inicio:%H:%M}-{self.hora_fim:%H:%M}"
