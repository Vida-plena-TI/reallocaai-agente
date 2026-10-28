"""Pacientes por plano de saúde, pela cor do texto do nome na grade semanal.

Conta só o que a grade vigente mostra: paciente sem sessão na semana não
aparece, e o paciente é identificado só pelo nome (homônimos se fundem,
grafias diferentes viram dois). Nome preto é ignorado (decisão da clínica) e
nome de cor não reconhecida também, mas entra na contagem de avisos.
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from app.domain import PLANO_POR_COR, NomeColorido, ler_nome_colorido, normalizar_id
from app.domain.formatacao import chave_texto


@dataclass(frozen=True, slots=True)
class PacienteDoPlano:
    """Um paciente num plano e quantos atendimentos da semana têm a cor desse plano."""

    nome: str
    atendimentos: int


@dataclass(frozen=True, slots=True)
class GrupoDoPlano:
    """Os pacientes de um plano, em ordem alfabética."""

    plano: str
    pacientes: tuple[PacienteDoPlano, ...]

    @property
    def atendimentos(self) -> int:
        return sum(paciente.atendimentos for paciente in self.pacientes)


@dataclass(frozen=True, slots=True)
class PacientesPorPlano:
    """Um grupo por plano de `PLANO_POR_COR`, na mesma ordem, inclusive os vazios.

    `nomes_nao_reconhecidos` conta nomes distintos com alguma ocorrência de cor
    que não é preta nem de plano (ignorada); `pacientes_em_mais_de_um_plano`
    conta quem aparece em dois planos ou mais (e por isso está em cada um).
    """

    grupos: tuple[GrupoDoPlano, ...]
    #: Pacientes distintos com algum plano (quem está em dois conta uma vez).
    pacientes: int
    nomes_nao_reconhecidos: int
    pacientes_em_mais_de_um_plano: int


def agrupar_pacientes_por_plano(nomes: Iterable[NomeColorido]) -> PacientesPorPlano:
    """Agrupa os nomes coloridos da semana por plano.

    Paciente distinto = nome normalizado (`normalizar_id`); o nome exibido é a
    primeira grafia vista. Atendimento = sessão (horários seguidos da mesma
    coluna contam uma vez), contado em cada plano em que o nome aparece colorido
    nela; o mesmo paciente em preto noutra sessão não entra.
    """
    atendimentos: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    exibicao: dict[str, str] = {}
    nao_reconhecidos: set[str] = set()
    for nome in nomes:
        leitura = ler_nome_colorido(nome)
        identificador = normalizar_id(leitura.nome)
        if leitura.preto or not identificador:
            continue
        if leitura.plano is None:
            nao_reconhecidos.add(identificador)
            continue
        exibicao.setdefault(identificador, leitura.nome)
        atendimentos[leitura.plano][identificador].add(nome.atendimento_id)

    grupos = tuple(
        GrupoDoPlano(
            plano=faixa.rotulo,
            pacientes=tuple(
                sorted(
                    (
                        PacienteDoPlano(nome=exibicao[identificador], atendimentos=len(sessoes))
                        for identificador, sessoes in atendimentos[faixa.rotulo].items()
                    ),
                    key=lambda paciente: chave_texto(paciente.nome),
                )
            ),
        )
        for faixa in PLANO_POR_COR
    )
    planos_por_paciente: dict[str, int] = defaultdict(int)
    for por_paciente in atendimentos.values():
        for identificador in por_paciente:
            planos_por_paciente[identificador] += 1
    return PacientesPorPlano(
        grupos=grupos,
        pacientes=len(planos_por_paciente),
        nomes_nao_reconhecidos=len(nao_reconhecidos),
        pacientes_em_mais_de_um_plano=sum(1 for n in planos_por_paciente.values() if n > 1),
    )
