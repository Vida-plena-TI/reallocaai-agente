"""Catálogo da agenda: quem atende o quê, em que dias, turnos, salas e postos.

Lógica pura, sem I/O: recebe o que a fonte já devolveu para cada dia da semana
(profissionais, salas e grade) e devolve um retrato da equipe. Nada é escrito
à mão: especialidades, dias, turnos, salas e postos saem da grade, que é a
oferta escalada (livre ou ocupada). Pacientes e atendimentos não entram, então
o catálogo nunca carrega dado de paciente.

A leitura dos dias (em paralelo) e o cache ficam em `app.ai.catalogo`.
"""

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, time
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.domain.constants import INICIO_PAUSA
from app.domain.entities import Profissional, Sala
from app.domain.enums import Especialidade
from app.domain.fonte_agenda import EntradaGrade

Turno = Literal["manha", "tarde"]

#: Nome de cada `date.weekday()` com agenda, sem acento (é o valor dos enums de dia).
NOMES_DOS_DIAS: tuple[str, ...] = ("segunda", "terca", "quarta", "quinta", "sexta", "sabado")


def turno_do_horario(hora: time) -> Turno:
    """Manhã antes do início da pausa; tarde a partir dela."""
    return "manha" if hora < INICIO_PAUSA else "tarde"


class PostoNoDia(BaseModel):
    """Onde um profissional atende num dia: sala, posto (a partir de 1) e turnos."""

    model_config = ConfigDict(frozen=True)

    dia: date
    sala_id: str
    sala_nome: str
    #: Número de exibição do posto (coluna da sala), contado a partir de 1.
    posto: int
    turnos: tuple[Turno, ...]


class ProfissionalCatalogo(BaseModel):
    """Um profissional da semana, como a grade o mostra."""

    model_config = ConfigDict(frozen=True)

    id: str
    #: Nome canônico, como está na planilha.
    nome: str
    especialidade: Especialidade
    #: Apelidos confirmados (`PROFISSIONAIS_APELIDOS`), já normalizados.
    apelidos: tuple[str, ...] = ()
    #: Onde atende, por dia, sala e posto, em ordem de dia, sala e posto.
    postos: tuple[PostoNoDia, ...] = ()

    @property
    def primeiro_nome(self) -> str:
        """Primeiro nome normalizado (o id é o nome normalizado, palavra a palavra)."""
        return self.id.split("-")[0]

    @property
    def dias(self) -> tuple[date, ...]:
        return tuple(sorted({posto.dia for posto in self.postos}))

    def turnos_no_dia(self, dia: date) -> tuple[Turno, ...]:
        turnos = {turno for posto in self.postos if posto.dia == dia for turno in posto.turnos}
        return tuple(t for t in ("manha", "tarde") if t in turnos)

    def atende_no_dia(self, dia: date) -> bool:
        return any(posto.dia == dia for posto in self.postos)


class SalaCatalogo(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    nome: str
    capacidade: int
    #: Dia -> ids dos profissionais escalados na sala, em ordem de posto.
    ocupantes_por_dia: dict[date, tuple[str, ...]]


class CatalogoAgenda(BaseModel):
    """Retrato da equipe numa semana (segunda a sábado)."""

    model_config = ConfigDict(frozen=True)

    dias: tuple[date, ...]
    #: Dias cuja leitura falhou: o catálogo está incompleto neles.
    dias_com_falha: tuple[date, ...] = ()
    profissionais: dict[str, ProfissionalCatalogo]
    salas: dict[str, SalaCatalogo]

    @property
    def parcial(self) -> bool:
        return bool(self.dias_com_falha)

    @property
    def especialidades(self) -> dict[Especialidade, tuple[str, ...]]:
        """Especialidade -> ids dos profissionais, na ordem do enum e por nome."""
        por_especialidade: dict[Especialidade, list[ProfissionalCatalogo]] = defaultdict(list)
        for profissional in self.profissionais.values():
            por_especialidade[profissional.especialidade].append(profissional)
        return {
            especialidade: tuple(
                p.id for p in sorted(por_especialidade[especialidade], key=lambda p: p.id)
            )
            for especialidade in Especialidade
            if especialidade in por_especialidade
        }

    @property
    def dias_com_agenda(self) -> tuple[date, ...]:
        """Dias com alguém escalado."""
        return tuple(sorted({posto.dia for p in self.profissionais.values() for posto in p.postos}))


@dataclass(frozen=True)
class DiaDoCatalogo:
    """O que a fonte devolveu para um dia: só o que o catálogo usa."""

    profissionais: list[Profissional] = field(default_factory=list)
    salas: list[Sala] = field(default_factory=list)
    grade: list[EntradaGrade] = field(default_factory=list)


def _nome_do_id(profissional_id: str) -> str:
    """Nome de quem está na grade, mas não em `listar_profissionais` (`ana-lima` -> `Ana Lima`)."""
    return " ".join(parte.capitalize() for parte in profissional_id.split("-"))


def montar_catalogo(
    dias: Mapping[date, DiaDoCatalogo],
    *,
    dias_com_falha: tuple[date, ...] = (),
    apelidos: Mapping[str, str] | None = None,
) -> CatalogoAgenda:
    """Catálogo a partir dos dias lidos. `apelidos`: apelido normalizado -> id canônico."""
    nomes: dict[str, str] = {}
    especialidades: dict[str, Especialidade] = {}
    salas: dict[str, Sala] = {}
    # (profissional, dia, sala, posto) -> turnos
    turnos: dict[tuple[str, date, str, int], set[Turno]] = defaultdict(set)
    ocupantes: dict[tuple[str, date], dict[int, str]] = defaultdict(dict)

    for dia, dados in sorted(dias.items()):
        for profissional in dados.profissionais:
            nomes.setdefault(profissional.id, profissional.nome)
            especialidades.setdefault(profissional.id, profissional.especialidade)
        for sala in dados.salas:
            salas.setdefault(sala.id, sala)
        for entrada in dados.grade:
            especialidades.setdefault(entrada.profissional_id, entrada.especialidade)
            turnos[(entrada.profissional_id, dia, entrada.sala_id, entrada.indice_posto)].add(
                turno_do_horario(entrada.slot.hora_inicio)
            )
            ocupantes[(entrada.sala_id, dia)].setdefault(
                entrada.indice_posto, entrada.profissional_id
            )

    def nome_da_sala(sala_id: str) -> str:
        sala = salas.get(sala_id)
        return sala.nome if sala is not None else sala_id

    postos: dict[str, list[PostoNoDia]] = defaultdict(list)
    for (profissional_id, dia, sala_id, indice), conjunto in sorted(turnos.items()):
        postos[profissional_id].append(
            PostoNoDia(
                dia=dia,
                sala_id=sala_id,
                sala_nome=nome_da_sala(sala_id),
                posto=indice + 1,
                turnos=tuple(t for t in ("manha", "tarde") if t in conjunto),
            )
        )

    apelidos_por_id: dict[str, list[str]] = defaultdict(list)
    for apelido, canonico in (apelidos or {}).items():
        apelidos_por_id[canonico].append(apelido)

    profissionais = {
        profissional_id: ProfissionalCatalogo(
            id=profissional_id,
            nome=nomes.get(profissional_id, _nome_do_id(profissional_id)),
            especialidade=especialidade,
            apelidos=tuple(sorted(apelidos_por_id.get(profissional_id, []))),
            postos=tuple(postos.get(profissional_id, [])),
        )
        for profissional_id, especialidade in sorted(especialidades.items())
    }
    salas_do_catalogo = {
        sala_id: SalaCatalogo(
            id=sala_id,
            nome=nome_da_sala(sala_id),
            capacidade=salas[sala_id].capacidade_simultanea if sala_id in salas else 1,
            ocupantes_por_dia={
                dia: tuple(por_posto[i] for i in sorted(por_posto))
                for (sala, dia), por_posto in sorted(ocupantes.items())
                if sala == sala_id
            },
        )
        for sala_id in sorted({*salas, *(sala for sala, _ in ocupantes)})
    }
    return CatalogoAgenda(
        dias=tuple(sorted(dias)),
        dias_com_falha=tuple(sorted(dias_com_falha)),
        profissionais=profissionais,
        salas=salas_do_catalogo,
    )
