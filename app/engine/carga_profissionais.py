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
  a mesma regra da ocupação semanal por profissional.

Quem monta estes modelos a partir da fotografia do período (cada dia lido uma
vez, para todos os profissionais) é
`app.engine.visoes_fotografia.carga_profissionais`.
"""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.domain import Especialidade


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
    """Pacientes por profissional num período (`visoes_fotografia.carga_profissionais`)."""

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
