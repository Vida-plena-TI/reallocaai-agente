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
    #: Dias cuja leitura falha (qualquer método levanta `RuntimeError`), para
    #: simular uma aba da planilha ilegível.
    dias_com_falha: set[date] = field(default_factory=set)

    def _falhar_se_preciso(self, dia: date) -> None:
        if dia in self.dias_com_falha:
            raise RuntimeError(f"falha simulada ao ler {dia.isoformat()}")

    def listar_salas(self, dia: date) -> list[Sala]:
        """Salas em uso no dia, com a capacidade simultânea de cada uma."""
        self._falhar_se_preciso(dia)
        return self.salas.get(dia, [])

    def listar_profissionais(self, dia: date) -> list[Profissional]:
        """Profissionais escalados no dia."""
        self._falhar_se_preciso(dia)
        return self.profissionais.get(dia, [])

    def listar_grade(self, dia: date) -> list[EntradaGrade]:
        """Todas as janelas (sala, profissional, slot) abertas no dia."""
        self._falhar_se_preciso(dia)
        return self.grade.get(dia, [])

    def listar_atendimentos(self, dia: date) -> list[Atendimento]:
        """Atendimentos já alocados no dia."""
        self._falhar_se_preciso(dia)
        return self.atendimentos.get(dia, [])

    def listar_pacientes(self, dia: date) -> list[Paciente]:
        """Todos os pacientes que aparecem em algum `Atendimento` do dia."""
        self._falhar_se_preciso(dia)
        return self.pacientes.get(dia, [])
