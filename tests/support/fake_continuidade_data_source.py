"""`ContinuidadeDataSource` em memória, para testes que não precisam de rede."""

from dataclasses import dataclass, field

from app.domain import Especialidade


@dataclass
class FakeContinuidadeDataSource:
    """Implementação de `ContinuidadeDataSource` apoiada em um mapa fixo."""

    habitual: dict[tuple[str, Especialidade], str] = field(default_factory=dict)

    def profissional_habitual(self, paciente_id: str, especialidade: Especialidade) -> str | None:
        return self.habitual.get((paciente_id, especialidade))
