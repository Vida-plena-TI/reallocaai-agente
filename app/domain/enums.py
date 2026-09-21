"""Enumerações do domínio do RealocAI."""

from enum import StrEnum, auto


class Especialidade(StrEnum):
    """Especialidades atendidas pela clínica.

    Um profissional atende exatamente uma especialidade (regra do negócio),
    por isso a especialidade é um valor único e não uma coleção.
    """

    TERAPIA_OCUPACIONAL = auto()
    FONOAUDIOLOGIA = auto()
    PSICOLOGIA = auto()
    TERAPIA_ALIMENTAR = auto()
    PSICOMOTRICIDADE = auto()
    PSICOPEDAGOGIA = auto()
    MUSICOTERAPIA = auto()


class Convenio(StrEnum):
    """Quem paga o atendimento.

    Na planilha o convênio não é uma coluna: ele é codificado pela **cor da
    fonte** da célula do paciente (veja `CORES_CONVENIO`).
    """

    KLINI_SAUDE = auto()
    PARTICULAR = auto()
    UNIMED = auto()
    SULAMERICA = auto()
