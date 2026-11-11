"""Quais pacientes cada profissional atende na grade semanal, com dias e sessões.

Complementa `app.engine.carga_profissionais` (que só conta): aqui sai a lista,
para o relatório `pacientes_por_profissional_nomes`. As regras são as mesmas
da contagem de pacientes distintos:

- paciente = id normalizado do nome (`normalizar_id`, feito pelo parser):
  homônimos se fundem e grafias diferentes viram dois; o nome exibido é o do
  cadastro da fotografia (a primeira grafia lida);
- atendimento = `Atendimento` (sessão); numa sessão em grupo, conta para cada
  paciente;
- paciente com mais de um profissional aparece em cada um;
- ordem: rótulo legível da especialidade, profissional e paciente, em ordem
  alfabética sem acento nem caixa (sem especialidade por último). Os grupos
  saem contíguos: o bloco usa isso na dica `agrupar_por`.

Só usa os dias pedidos que foram lidos; os não lidos ficam em
`dias_com_falha`, para o relatório sair parcial.
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from app.domain import Especialidade
from app.domain.formatacao import chave_texto, rotulo_enum
from app.engine.fotografia import FotografiaPeriodo


@dataclass(frozen=True, slots=True)
class PacienteDoProfissional:
    """Um paciente de um profissional: dias da semana com sessão e quantas sessões."""

    nome: str
    dias: tuple[date, ...]
    atendimentos: int


@dataclass(frozen=True, slots=True)
class GrupoDoProfissional:
    """Os pacientes de um profissional, em ordem alfabética."""

    profissional_id: str
    nome: str
    especialidade: Especialidade | None
    pacientes: tuple[PacienteDoProfissional, ...]
    #: Sessões do profissional no período (a sessão em grupo conta uma vez).
    atendimentos: int


@dataclass(frozen=True, slots=True)
class PacientesPorProfissional:
    """Um grupo por profissional com paciente no período, por especialidade e nome."""

    grupos: tuple[GrupoDoProfissional, ...]
    #: Pacientes distintos no recorte inteiro (quem passa por dois conta uma vez).
    pacientes: int
    pacientes_em_mais_de_um_profissional: int
    dias_com_falha: tuple[date, ...]


def _chave(grupo: GrupoDoProfissional) -> tuple[bool, str, str, str]:
    """Rótulo legível da especialidade (`Terapia Ocupacional`), depois o nome da
    profissional, sem acento e sem caixa; sem especialidade vai ao fim. O id
    desempata dois nomes iguais, para a ordem (e os grupos contíguos) ser estável.
    """
    especialidade = grupo.especialidade
    rotulo = rotulo_enum(especialidade.value) if especialidade is not None else ""
    return (
        especialidade is None,
        chave_texto(rotulo),
        chave_texto(grupo.nome),
        grupo.profissional_id,
    )


def listar_pacientes_por_profissional(
    foto: FotografiaPeriodo,
    dias: Iterable[date],
    profissional_ids: Iterable[str] | None = None,
    especialidade: Especialidade | None = None,
) -> PacientesPorProfissional:
    """Pacientes de cada profissional nos `dias` lidos da fotografia, com os filtros.

    `profissional_ids` (ids da fotografia) e `especialidade` restringem os
    profissionais; `None` deixa todos.
    """
    janela = {dia for dia in dias if dia in foto.dias_consultados}
    lidos = {dia for dia in janela if dia in foto.dias_lidos}
    com_falha = tuple(sorted(dia for dia in janela if dia in foto.dias_nao_lidos))
    escolhidos = set(profissional_ids) if profissional_ids is not None else None

    # profissional -> paciente -> {atendimento_id: dia}
    sessoes: dict[str, dict[str, dict[str, date]]] = defaultdict(lambda: defaultdict(dict))
    sessoes_do_profissional: dict[str, set[str]] = defaultdict(set)
    especialidade_vista: dict[str, Especialidade] = {}
    for atendimento in foto.atendimentos:
        dia = atendimento.slots[0].data
        profissional_id = atendimento.profissional_id
        if dia not in lidos or (escolhidos is not None and profissional_id not in escolhidos):
            continue
        cadastro = foto.profissionais.get(profissional_id)
        do_profissional = cadastro.especialidade if cadastro else atendimento.especialidade
        if especialidade is not None and do_profissional != especialidade:
            continue
        especialidade_vista.setdefault(profissional_id, do_profissional)
        sessoes_do_profissional[profissional_id].add(atendimento.id)
        for paciente_id in dict.fromkeys(atendimento.paciente_ids):
            sessoes[profissional_id][paciente_id][atendimento.id] = dia

    grupos = []
    profissionais_por_paciente: dict[str, int] = defaultdict(int)
    for profissional_id, por_paciente in sessoes.items():
        pacientes = []
        for paciente_id, dia_da_sessao in por_paciente.items():
            profissionais_por_paciente[paciente_id] += 1
            cadastro_paciente = foto.pacientes.get(paciente_id)
            pacientes.append(
                PacienteDoProfissional(
                    nome=cadastro_paciente.nome if cadastro_paciente else paciente_id,
                    dias=tuple(sorted(set(dia_da_sessao.values()))),
                    atendimentos=len(dia_da_sessao),
                )
            )
        pacientes.sort(key=lambda paciente: chave_texto(paciente.nome))
        grupos.append(
            GrupoDoProfissional(
                profissional_id=profissional_id,
                nome=foto.nome_profissional(profissional_id),
                especialidade=especialidade_vista[profissional_id],
                pacientes=tuple(pacientes),
                atendimentos=len(sessoes_do_profissional[profissional_id]),
            )
        )
    grupos.sort(key=_chave)
    return PacientesPorProfissional(
        grupos=tuple(grupos),
        pacientes=len(profissionais_por_paciente),
        pacientes_em_mais_de_um_profissional=sum(
            1 for n in profissionais_por_paciente.values() if n > 1
        ),
        dias_com_falha=com_falha,
    )
