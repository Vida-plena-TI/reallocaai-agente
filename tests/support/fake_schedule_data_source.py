"""`ScheduleDataSource` em memória, para testes que não precisam de rede.

Cada método devolve exatamente o que foi passado no construtor para aquele dia
(lista vazia se o dia não tiver entrada) — nenhuma regra de negócio mora aqui,
só a estrutura de dados que a engine espera.
"""

from dataclasses import dataclass, field
from datetime import date

from app.domain import Atendimento, EntradaGrade, Paciente, Profissional, Sala


@dataclass
class FakeScheduleDataSource:
    """Implementação de `ScheduleDataSource` apoiada em dados fixos por dia."""

    salas: dict[date, list[Sala]] = field(default_factory=dict)
    profissionais: dict[date, list[Profissional]] = field(default_factory=dict)
    pacientes: dict[date, list[Paciente]] = field(default_factory=dict)
    grade: dict[date, list[EntradaGrade]] = field(default_factory=dict)
    atendimentos: dict[date, list[Atendimento]] = field(default_factory=dict)

    def listar_salas(self, dia: date) -> list[Sala]:
        """Salas em uso no dia, com a capacidade simultânea de cada uma."""
        return self.salas.get(dia, [])

    def listar_profissionais(self, dia: date) -> list[Profissional]:
        """Profissionais escalados no dia."""
        return self.profissionais.get(dia, [])

    def listar_grade(self, dia: date) -> list[EntradaGrade]:
        """Todas as janelas (sala, profissional, slot) abertas no dia."""
        return self.grade.get(dia, [])

    def listar_atendimentos(self, dia: date) -> list[Atendimento]:
        """Atendimentos já alocados no dia."""
        return self.atendimentos.get(dia, [])

    def listar_pacientes(self, dia: date) -> list[Paciente]:
        """Todos os pacientes que aparecem em algum `Atendimento` do dia."""
        return self.pacientes.get(dia, [])
