"""Entidades do domínio do RealocAI.

Nenhuma delas sabe alocar: aqui só existem os dados do negócio e as regras que
impedem um estado inválido de ser construído.
"""

from datetime import date, time
from itertools import pairwise
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.constants import (
    DURACAO_SLOT_MINUTOS,
    HORARIO_ABERTURA,
    HORARIO_FECHAMENTO,
    HORARIO_PREFERENCIAL_PADRAO,
)
from app.domain.enums import Convenio, Especialidade
from app.domain.exceptions import (
    AtendimentoComBuracoError,
    AtendimentoEmDiasDiferentesError,
)
from app.domain.slot import Slot


class Profissional(BaseModel):
    """Quem atende. Tem exatamente uma especialidade — nunca mais de uma."""

    model_config = ConfigDict(validate_assignment=True)

    id: str = Field(min_length=1)
    nome: str = Field(min_length=1)
    especialidade: Especialidade


class Sala(BaseModel):
    """Onde se atende.

    `capacidade_simultanea` é um dado da sala, não uma regra fixa: quantos
    atendimentos ela comporta ao mesmo tempo.
    """

    model_config = ConfigDict(validate_assignment=True)

    id: str = Field(min_length=1)
    nome: str = Field(min_length=1)
    capacidade_simultanea: int = Field(default=1, ge=1)


class Paciente(BaseModel):
    """Quem é atendido.

    `convenio` é apenas informativo: hoje não existe restrição de alocação por
    convênio.
    """

    model_config = ConfigDict(validate_assignment=True)

    id: str = Field(min_length=1)
    nome: str = Field(min_length=1)
    convenio: Convenio | None = None


class Atendimento(BaseModel):
    """Um bloco contínuo de slots com um profissional numa sala.

    Os slots são normalizados em ordem crescente e precisam formar um bloco sem
    buraco dentro do mesmo dia.

    `paciente_ids` é uma lista porque a sessão pode ser em grupo: dois pacientes
    dividindo o mesmo profissional, na mesma sala e no mesmo horário.
    """

    model_config = ConfigDict(validate_assignment=True)

    id: str = Field(min_length=1)
    paciente_ids: list[Annotated[str, Field(min_length=1)]] = Field(min_length=1)
    profissional_id: str = Field(min_length=1)
    sala_id: str = Field(min_length=1)
    especialidade: Especialidade
    slots: list[Slot] = Field(min_length=1)
    #: O encaixe já está na agenda, mas ainda depende da confirmação do
    #: responsável (plano de saúde ou família).
    aguardando_autorizacao: bool = False
    #: Posto (coluna) da sala onde o atendimento acontece — ver `EntradaGrade`.
    #: 0 cobre a sala de capacidade 1, que é o caso comum.
    indice_posto: int = Field(default=0, ge=0)

    @field_validator("slots")
    @classmethod
    def _validar_bloco_continuo(cls, slots: list[Slot]) -> list[Slot]:
        dias = {slot.data for slot in slots}
        if len(dias) > 1:
            raise AtendimentoEmDiasDiferentesError(
                f"Um atendimento acontece em um único dia, mas recebeu {len(dias)}: "
                f"{', '.join(dia.isoformat() for dia in sorted(dias))}."
            )

        ordenados = sorted(slots)
        for anterior, seguinte in pairwise(ordenados):
            if not anterior.e_contiguo_a(seguinte):
                raise AtendimentoComBuracoError(
                    f"Há um buraco entre {anterior} e {seguinte}: "
                    "os slots de um atendimento precisam ser contíguos."
                )

        return ordenados

    @property
    def data(self) -> date:
        """Dia do atendimento (todos os slots são do mesmo dia)."""
        return self.slots[0].data

    @property
    def duracao_minutos(self) -> int:
        """Duração total do atendimento em minutos."""
        return len(self.slots) * DURACAO_SLOT_MINUTOS


class ItemSolicitacao(BaseModel):
    """Uma especialidade pedida e quantos slots ela precisa ocupar.

    `profissional_id`, quando informado, restringe a busca a esse profissional
    específico (continuidade terapêutica); quando `None`, qualquer profissional
    disponível daquela especialidade serve.
    """

    model_config = ConfigDict(validate_assignment=True)

    especialidade: Especialidade
    duracao_em_slots: int = Field(ge=1)
    profissional_id: str | None = None

    @property
    def duracao_minutos(self) -> int:
        """Duração pedida em minutos."""
        return self.duracao_em_slots * DURACAO_SLOT_MINUTOS


class SolicitacaoAtendimento(BaseModel):
    """A necessidade de um paciente num dia — o "atendimento casado".

    É só a estrutura da demanda: encontrar o encaixe é trabalho da engine.
    """

    model_config = ConfigDict(validate_assignment=True)

    paciente_id: str = Field(min_length=1)
    data: date
    itens: list[ItemSolicitacao] = Field(min_length=1)
    #: Nunca sugerir horário de início anterior a este, dentro do expediente.
    horario_minimo: time = HORARIO_PREFERENCIAL_PADRAO
    #: Horário exato desejado, quando o paciente/família pediu um horário
    #: específico.
    horario_desejado: time | None = None

    @field_validator("horario_minimo")
    @classmethod
    def _validar_horario_minimo_no_expediente(cls, horario_minimo: time) -> time:
        return _validar_horario_no_expediente(horario_minimo, campo="horario_minimo")

    @field_validator("horario_desejado")
    @classmethod
    def _validar_horario_desejado_no_expediente(cls, horario_desejado: time | None) -> time | None:
        if horario_desejado is None:
            return None
        return _validar_horario_no_expediente(horario_desejado, campo="horario_desejado")

    @property
    def total_de_slots(self) -> int:
        """Quantos slots o conjunto de itens solicitados consome."""
        return sum(item.duracao_em_slots for item in self.itens)


def _validar_horario_no_expediente(horario: time, *, campo: str) -> time:
    if horario < HORARIO_ABERTURA or horario > HORARIO_FECHAMENTO:
        raise ValueError(
            f"{campo} {horario:%H:%M} está fora do expediente "
            f"({HORARIO_ABERTURA:%H:%M} às {HORARIO_FECHAMENTO:%H:%M})."
        )
    return horario
