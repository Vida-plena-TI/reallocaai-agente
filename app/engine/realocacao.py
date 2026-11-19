"""Plano de realocação: abrir espaço para um pedido deslocando outros pacientes.

Motor determinístico, sem I/O: trabalha só sobre a `FotografiaPeriodo` da
semana (uma leitura da planilha). O pedido é de um paciente "A" (novo ou já na
grade) numa especialidade, duração, dias e janela; a busca procura onde A cabe
deslocando, no máximo, `MAX_DESLOCADOS` pacientes, e cada deslocado mantém a
MESMA especialidade, o MESMO profissional e a MESMA duração. Nunca se propõe
mudança de terapia nem de profissional (a não ser com
`PERMITIR_OUTRO_PROFISSIONAL_MESMA_ESPECIALIDADE`, desligada por padrão).

Busca, em ordem de custo: 0, depois 1, depois 2 deslocados. Um ocupante do
destino de A sai inteiro (mesmo a parte fora da janela) e vai para uma vaga
livre ou para o lugar de um único outro paciente C, que então precisa de uma
vaga livre. A busca para quando junta opções suficientes ou ao avaliar
`MAX_CADEIAS_AVALIADAS` cadeias (o resultado avisa "busca limitada").

Toda opção passa por uma validação independente da grade simulada final:
(a) um atendimento por (profissional, sala, posto, horário) e simultâneos do
profissional até o número de postos escalados; (b) nenhum paciente em dois
lugares; (c) tudo dentro da grade escalada, do expediente e fora da pausa, e
cada deslocado com especialidade, profissional e duração de origem; (d) a
contagem de (paciente, especialidade, dia) nunca aumenta além do que já era
(e só passa de 1 onde já passava); (e) o conjunto de especialidades de cada
paciente na semana não muda (A novo ganha só a pedida). Opção que falha é
descartada e contada no log: é rede de segurança, falhar ali é bug. Os
índices, a simulação e essa validação moram em `app.engine.grade_simulada`
(compartilhados com `app.engine.enxugamento`).

Privacidade: os ids de paciente são nomes normalizados e os nomes exibidos
ficam só no resultado, para o bloco da tela. O log leva só contagens e tempo.
"""

import logging
import time as relogio
from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date, time
from enum import StrEnum
from typing import Final

from app.domain import Especialidade
from app.domain.constants import (
    HORARIO_MINIMO_DESLOCADO,
    MANTER_TURNO_DO_DESLOCADO,
    MAX_CADEIAS_AVALIADAS,
    MAX_DESLOCADOS,
    MAX_OPCOES_REALOCACAO,
    META_OCUPACAO_POR_SALA,
    MOVER_AGUARDANDO_AUTORIZACAO,
    MOVER_ATENDIMENTO_FORA_DA_GRADE,
    MOVER_SESSAO_EM_GRUPO,
    PACIENTES_FIXOS,
    PENALIDADE_OUTRO_DIA,
    PERMITIR_OUTRO_PROFISSIONAL_MESMA_ESPECIALIDADE,
    PROFISSIONAIS_FIXOS,
    REALOCACAO_PODE_MUDAR_DE_DIA,
)
from app.domain.nomes_profissionais import casar_nome
from app.engine.fotografia import FotografiaPeriodo
from app.engine.grade_simulada import (
    PASSO,
    AtendimentoExtra,
    AtendimentoGrade,
    ModeloSemana,
    Movimento,
    Simulacao,
    validar_grade_final,
)

# Reexportado: `Lugar` faz parte do resultado público deste módulo.
from app.engine.grade_simulada import Lugar as Lugar
from app.engine.grade_simulada import minutos as _minutos

logger = logging.getLogger(__name__)

#: Rótulo do paciente novo sem nome nas linhas do plano (não é nome de paciente).
ROTULO_PACIENTE_NOVO: Final[str] = "Paciente novo"

_PASSO: Final[int] = PASSO


class Situacao(StrEnum):
    NOVO = "novo"
    JA_NA_GRADE = "ja_na_grade"


@dataclass(frozen=True)
class Regras:
    """As regras configuráveis (padrões de `app.domain.constants`)."""

    pacientes_fixos: frozenset[str] = PACIENTES_FIXOS
    profissionais_fixos: frozenset[str] = PROFISSIONAIS_FIXOS
    pode_mudar_de_dia: bool = REALOCACAO_PODE_MUDAR_DE_DIA
    penalidade_outro_dia: int = PENALIDADE_OUTRO_DIA
    permitir_outro_profissional: bool = PERMITIR_OUTRO_PROFISSIONAL_MESMA_ESPECIALIDADE
    max_deslocados: int = MAX_DESLOCADOS
    max_opcoes: int = MAX_OPCOES_REALOCACAO
    max_cadeias: int = MAX_CADEIAS_AVALIADAS
    mover_grupo: bool = MOVER_SESSAO_EM_GRUPO
    mover_aguardando: bool = MOVER_AGUARDANDO_AUTORIZACAO
    manter_turno: bool = MANTER_TURNO_DO_DESLOCADO
    mover_fora_da_grade: bool = MOVER_ATENDIMENTO_FORA_DA_GRADE
    horario_minimo_deslocado: time = HORARIO_MINIMO_DESLOCADO


@dataclass(frozen=True)
class PedidoRealocacao:
    """O que o modelo extraiu da conversa (o profissional já resolvido para id)."""

    dias: tuple[date, ...] = ()
    paciente: str | None = None
    situacao: Situacao | None = None
    especialidade: Especialidade | None = None
    profissional_id: str | None = None
    duracao_minutos: int | None = None
    horario_desejado: time | None = None
    horario_minimo: time | None = None
    horario_maximo: time | None = None
    dia_origem: date | None = None
    horario_origem: time | None = None
    #: Número da candidata escolhida depois de uma pergunta (a partir de 1).
    candidata: int | None = None
    #: Resposta à pergunta "é a mesma pessoa que já está na grade?".
    mesmo_paciente_da_grade: bool | None = None


class MotivoPergunta(StrEnum):
    NOME_AMBIGUO = "nome_ambiguo"
    HOMONIMO_POSSIVEL = "homonimo_possivel"
    QUAL_ATENDIMENTO = "qual_atendimento"
    QUAL_HORARIO_DE_ORIGEM = "qual_horario_de_origem"
    CANDIDATA_INVALIDA = "candidata_invalida"
    MESMO_PACIENTE_DA_GRADE = "mesmo_paciente_da_grade"
    NAO_ENCONTRADO = "nao_encontrado"
    DURACAO_DIFERENTE = "duracao_diferente"
    FALTA_ESPECIALIDADE = "falta_especialidade"
    FALTA_DURACAO = "falta_duracao"
    FALTA_DIA = "falta_dia"
    FALTA_HORARIO = "falta_horario"


@dataclass(frozen=True)
class Candidata:
    """Um paciente com uma terapia e um profissional na semana."""

    numero: int
    #: Nome exibido do paciente: só para o bloco da tela, nunca para o texto.
    paciente: str = field(repr=False)
    especialidade: Especialidade
    profissional_id: str
    dias: tuple[date, ...]


@dataclass(frozen=True)
class Pergunta:
    """A busca não começa: falta algo que só quem pediu sabe."""

    motivo: MotivoPergunta
    candidatas: tuple[Candidata, ...] = ()
    #: Atendimentos de origem possíveis (`QUAL_HORARIO_DE_ORIGEM`).
    origens: tuple[Lugar, ...] = ()
    #: Duração atual do atendimento de A (`DURACAO_DIFERENTE`).
    duracao_atual: int | None = None


@dataclass(frozen=True)
class Passo:
    """Uma mudança, na ordem de execução manual na planilha."""

    #: Nome exibido (ou `ROTULO_PACIENTE_NOVO`): só para o bloco da tela.
    paciente: str = field(repr=False)
    #: É o atendimento pedido (A)?
    pedido: bool
    especialidade: Especialidade
    #: `None`: paciente novo, sem lugar de origem.
    de: Lugar | None
    para: Lugar

    @property
    def outro_dia(self) -> bool:
        return self.de is not None and self.de.data != self.para.data

    @property
    def outra_sala(self) -> bool:
        return self.de is not None and self.de.sala_id != self.para.sala_id

    @property
    def outro_profissional(self) -> bool:
        return self.de is not None and self.de.profissional_id != self.para.profissional_id


@dataclass(frozen=True)
class OpcaoRealocacao:
    passos: tuple[Passo, ...]

    @property
    def pedido(self) -> Passo:
        return next(passo for passo in self.passos if passo.pedido)

    @property
    def deslocados(self) -> tuple[Passo, ...]:
        return tuple(passo for passo in self.passos if not passo.pedido)

    @property
    def mudam_de_dia(self) -> int:
        return sum(1 for passo in self.deslocados if passo.outro_dia)


@dataclass(frozen=True)
class EstatisticaBusca:
    cadeias_avaliadas: int = 0
    descartadas_na_validacao: int = 0
    busca_limitada: bool = False
    segundos: float = 0.0


@dataclass(frozen=True)
class PlanoRealocacao:
    opcoes: tuple[OpcaoRealocacao, ...]
    especialidade: Especialidade
    duracao_minutos: int
    estatistica: EstatisticaBusca


class MotivoSemSolucao(StrEnum):
    NENHUMA_COMBINACAO = "nenhuma_combinacao"
    SEM_OFERTA = "sem_oferta"
    SESSAO_EM_GRUPO = "sessao_em_grupo"


@dataclass(frozen=True)
class SemSolucao:
    motivo: MotivoSemSolucao
    especialidade: Especialidade
    duracao_minutos: int
    estatistica: EstatisticaBusca
    #: Encaixe direto (sem deslocar ninguém) mais perto da janela, fora dela.
    alternativa_fora_da_janela: Lugar | None = None


ResultadoRealocacao = Pergunta | PlanoRealocacao | SemSolucao


_Atendimento = AtendimentoGrade
_Modelo = ModeloSemana
_Movimento = Movimento


@dataclass(frozen=True)
class _A:
    """O pedido já resolvido."""

    paciente_id: str | None
    rotulo: str
    especialidade: Especialidade
    blocos: int
    profissionais: tuple[str, ...]
    origem: _Atendimento | None


# ------------------------------------------------------------------ busca


@dataclass(frozen=True, slots=True)
class _Folha:
    """Uma cadeia completa: A no destino e cada deslocado no seu."""

    movimentos: tuple[_Movimento, ...]
    ordem: tuple[object, ...]

    @property
    def destino_a(self) -> Lugar:
        return self.movimentos[-1].para

    @property
    def deslocados(self) -> frozenset[int]:
        return frozenset(m.k for m in self.movimentos if m.k is not None)


class _TetoAtingidoError(Exception):
    pass


class _Busca:
    def __init__(
        self,
        modelo: _Modelo,
        a: _A,
        pedido: PedidoRealocacao,
        regras: Regras,
        vetar_destino: Callable[[Lugar], bool] | None = None,
    ) -> None:
        """`vetar_destino`: lugares que nenhum movimento pode usar (o enxugamento veta
        os turnos sendo liberados e os que abririam turno novo). `None`: nenhum."""
        self.modelo = modelo
        self.a = a
        self.pedido = pedido
        self.regras = regras
        self.vetar_destino = vetar_destino
        self.sim = Simulacao(modelo, a.paciente_id, a.especialidade)
        self.base: frozenset[int] = frozenset({a.origem.k}) if a.origem else frozenset()
        self.cadeias = 0
        self.folhas: list[_Folha] = []
        self._destinos_cache: dict[int, list[Lugar]] = {}

    # -- regras de quem pode sair do lugar
    def movivel(self, at: _Atendimento) -> bool:
        regras = self.regras
        if at.grupo and not regras.mover_grupo:
            return False
        if at.aguardando and not regras.mover_aguardando:
            return False
        if not at.na_grade and not regras.mover_fora_da_grade:
            return False
        if at.lugar.profissional_id in regras.profissionais_fixos:
            return False
        return not any(p in regras.pacientes_fixos for p in at.pacientes)

    # -- destinos de A, na janela
    def destinos_de_a(self) -> list[Lugar]:
        pedido = self.pedido
        desejado = _minutos(pedido.horario_desejado) if pedido.horario_desejado else None
        minimo = _minutos(pedido.horario_minimo) if pedido.horario_minimo else None
        maximo = _minutos(pedido.horario_maximo) if pedido.horario_maximo else None
        dias = set(pedido.dias)
        origem = self.a.origem.lugar if self.a.origem else None
        destinos = []
        for profissional in self.a.profissionais:
            for lugar in self.modelo.lugares(profissional, self.a.blocos):
                if lugar.data not in dias or lugar == origem:
                    continue
                if minimo is None and maximo is None:
                    if lugar.inicio != desejado:
                        continue
                elif (minimo is not None and lugar.inicio < minimo) or (
                    maximo is not None and lugar.fim > maximo
                ):
                    continue
                if self.vetar_destino is not None and self.vetar_destino(lugar):
                    continue
                destinos.append(lugar)
        referencia = desejado if desejado is not None else minimo
        destinos.sort(
            key=lambda lugar: (
                abs(lugar.inicio - referencia) if referencia is not None else 0,
                lugar,
            )
        )
        return destinos

    def a_cabe(self, lugar: Lugar, removidos: frozenset[int]) -> bool:
        """A (quando é um paciente conhecido) não fica em dois lugares nem quebra a (d)."""
        paciente = self.a.paciente_id
        if paciente is None:
            return True
        return self.sim.paciente_livre((paciente,), lugar, removidos, ()) and self.sim.contagem_ok(
            (paciente,), self.a.especialidade, lugar.data, removidos, ()
        )

    # -- destinos de um deslocado
    def destinos_do_deslocado(self, at: _Atendimento) -> list[Lugar]:
        if at.k in self._destinos_cache:
            return self._destinos_cache[at.k]
        regras = self.regras
        piso = min(_minutos(regras.horario_minimo_deslocado), at.lugar.inicio)
        profissionais = (
            self.modelo.profissionais_da(at.especialidade)
            if regras.permitir_outro_profissional
            else [at.lugar.profissional_id]
        )
        destinos = [
            lugar
            for profissional in profissionais
            for lugar in self.modelo.lugares(profissional, at.lugar.blocos)
            if lugar != at.lugar
            and lugar.inicio >= piso
            and (regras.pode_mudar_de_dia or lugar.data == at.lugar.data)
            and (self.vetar_destino is None or not self.vetar_destino(lugar))
        ]
        destinos.sort(key=lambda lugar: (self._custo(at, lugar), lugar))
        self._destinos_cache[at.k] = destinos
        return destinos

    def _custo(self, at: _Atendimento, lugar: Lugar) -> tuple[int, int, int]:
        outro_dia = lugar.data != at.lugar.data
        return (
            abs(lugar.inicio - at.lugar.inicio) + (self.regras.penalidade_outro_dia * outro_dia),
            int(self.regras.manter_turno and lugar.manha != at.lugar.manha),
            int(lugar.profissional_id != at.lugar.profissional_id),
        )

    def deslocado_cabe(
        self,
        at: _Atendimento,
        lugar: Lugar,
        removidos: frozenset[int],
        movimentos: Sequence[_Movimento],
    ) -> bool:
        sim = self.sim
        return sim.paciente_livre(at.pacientes, lugar, removidos, movimentos) and sim.contagem_ok(
            at.pacientes, at.especialidade, lugar.data, removidos, movimentos
        )

    # -- folhas
    def _contar(self) -> None:
        self.cadeias += 1
        if self.cadeias > self.regras.max_cadeias:
            raise _TetoAtingidoError

    def _folha(self, movimentos: tuple[_Movimento, ...]) -> None:
        self.folhas.append(_Folha(movimentos, self._ordem(movimentos)))

    def _ordem(self, movimentos: tuple[_Movimento, ...]) -> tuple[object, ...]:
        destino = movimentos[-1].para
        pedido = self.pedido
        referencia = pedido.horario_desejado or pedido.horario_minimo
        custo = abs(destino.inicio - _minutos(referencia)) if referencia else 0
        turno = 0
        for movimento in movimentos[:-1]:
            assert movimento.k is not None
            at = self.modelo.atendimentos[movimento.k]
            de_custo, troca_turno, _ = self._custo(at, movimento.para)
            custo += de_custo
            turno += troca_turno
        removidos = self.base | frozenset(m.k for m in movimentos if m.k is not None)
        return (
            len(movimentos) - 1,
            custo,
            turno,
            -self._vizinhos_ocupados(destino, removidos, movimentos),
            self._distancia_da_meta(destino, removidos, movimentos),
            destino,
            tuple(m.para for m in movimentos[:-1]),
        )

    def _vizinhos_ocupados(
        self, lugar: Lugar, removidos: frozenset[int], movimentos: Sequence[_Movimento]
    ) -> int:
        """Desfragmentação: quantos dos dois vizinhos do destino de A já estão ocupados."""
        total = 0
        for minuto in (lugar.inicio - _PASSO, lugar.fim):
            vizinho = Lugar(
                lugar.data, minuto, lugar.sala_id, lugar.indice_posto, lugar.profissional_id, 1
            )
            ocupantes = self.sim.ocupantes(vizinho, removidos, movimentos[:-1])
            total += ocupantes is None or bool(ocupantes)
        return total

    def _distancia_da_meta(
        self, lugar: Lugar, removidos: frozenset[int], movimentos: Sequence[_Movimento]
    ) -> float:
        """|ocupação do profissional de A no dia, depois do plano, - 80%|."""
        ofertadas = self.modelo.oferta_do_dia.get((lugar.profissional_id, lugar.data), set())
        if not ofertadas:
            return 1.0
        ocupadas = {
            chave
            for chave in ofertadas
            if any(k not in removidos for k in self.modelo.ocupacao.get(chave, ()))
        }
        for movimento in movimentos:
            ocupadas.update(c for c in movimento.para.chaves() if c in ofertadas)
        return round(abs(len(ocupadas) / len(ofertadas) - META_OCUPACAO_POR_SALA), 6)

    # -- níveis
    def nivel(self, nivel: int, destinos: Sequence[tuple[Lugar, tuple[int, ...]]]) -> None:
        for destino, ocupantes in destinos:
            if nivel == 0 and not ocupantes:
                self._contar()
                self._folha((_Movimento(None, destino),))
            elif nivel == 1 and len(ocupantes) == 1:
                self._um_deslocado(destino, ocupantes[0])
            elif nivel == 2 and len(ocupantes) == 1:
                self._cadeia(destino, ocupantes[0])
            elif nivel == 2 and len(ocupantes) == 2:
                self._dois_diretos(destino, ocupantes)

    def _livres(
        self,
        at: _Atendimento,
        removidos: frozenset[int],
        movimentos: tuple[_Movimento, ...],
    ) -> Iterator[Lugar]:
        for lugar in self.destinos_do_deslocado(at):
            self._contar()
            if self.sim.ocupantes(lugar, removidos, movimentos) == set() and self.deslocado_cabe(
                at, lugar, removidos, movimentos
            ):
                yield lugar

    def _um_deslocado(self, destino: Lugar, k: int) -> None:
        removidos = self.base | {k}
        a = (_Movimento(None, destino),)
        for lugar in self._livres(self.modelo.atendimentos[k], removidos, a):
            self._folha((_Movimento(k, lugar), *a))

    def _dois_diretos(self, destino: Lugar, ks: tuple[int, ...]) -> None:
        removidos = self.base | set(ks)
        primeiro, segundo = (self.modelo.atendimentos[k] for k in ks)
        a = (_Movimento(None, destino),)
        for lugar_1 in self._livres(primeiro, removidos, a):
            m1 = (_Movimento(primeiro.k, lugar_1), *a)
            for lugar_2 in self._livres(segundo, removidos, m1):
                self._folha((_Movimento(segundo.k, lugar_2), *m1))

    def _cadeia(self, destino: Lugar, k: int) -> None:
        """B sai do destino de A para o lugar de um único C; C vai para uma vaga."""
        b = self.modelo.atendimentos[k]
        a = (_Movimento(None, destino),)
        removidos_b = self.base | {k}
        for lugar_b in self.destinos_do_deslocado(b):
            ocupantes = self.sim.ocupantes(lugar_b, removidos_b, a)
            if not ocupantes or len(ocupantes) != 1:
                continue
            (k_c,) = ocupantes
            c = self.modelo.atendimentos[k_c]
            if not self.movivel(c):
                continue
            removidos = removidos_b | {k_c}
            if not self.deslocado_cabe(b, lugar_b, removidos, a):
                continue
            m_b = (_Movimento(k, lugar_b), *a)
            for lugar_c in self._livres(c, removidos, m_b):
                self._folha((_Movimento(k_c, lugar_c), *m_b))

    def destinos_classificados(self) -> list[tuple[Lugar, tuple[int, ...]]]:
        """Destinos de A com os ocupantes que sairiam (descarta os impossíveis)."""
        classificados = []
        for destino in self.destinos_de_a():
            ocupantes = self.sim.ocupantes(destino, self.base, ())
            assert ocupantes is not None
            if len(ocupantes) > self.regras.max_deslocados:
                continue
            ats = [self.modelo.atendimentos[k] for k in sorted(ocupantes)]
            if not all(self.movivel(at) for at in ats):
                continue
            if not self.a_cabe(destino, self.base | ocupantes):
                continue
            classificados.append((destino, tuple(sorted(ocupantes))))
        return classificados


# -------------------------------------------------------------- validação


class _Validador:
    """Confere a grade final de uma opção do zero, sem os atalhos da busca."""

    def __init__(self, modelo: _Modelo, a: _A, regras: Regras) -> None:
        self.modelo = modelo
        self.a = a
        self.regras = regras

    def valida(self, movimentos: Sequence[_Movimento]) -> bool:
        a = self.a
        movidos = {m.k: m.para for m in movimentos if m.k is not None}
        destino_a = next(m.para for m in movimentos if m.k is None)
        if a.origem and destino_a.blocos != a.origem.lugar.blocos:
            return False
        return validar_grade_final(
            self.modelo,
            movidos,
            permitir_outro_profissional=self.regras.permitir_outro_profissional,
            removidos=(a.origem.k,) if a.origem else (),
            extra=AtendimentoExtra(
                pacientes=(a.paciente_id,) if a.paciente_id else (),
                especialidade=a.especialidade,
                lugar=destino_a,
                novo=a.origem is None,
            ),
        )


# ---------------------------------------------------------------- pedido


def _janela_ok(pedido: PedidoRealocacao) -> bool:
    return any((pedido.horario_desejado, pedido.horario_minimo, pedido.horario_maximo))


def _blocos(duracao_minutos: int) -> int:
    return max(1, -(-duracao_minutos // _PASSO))


GrupoDoPaciente = tuple[str, Especialidade, str]


def _candidatas(
    modelo: _Modelo, ids: Sequence[str], filtro: PedidoRealocacao
) -> list[tuple[GrupoDoPaciente, Candidata]]:
    """(paciente, especialidade, profissional) de cada atendimento, numerados em ordem
    estável (nome, especialidade, profissional): o número volta em `candidata`."""
    grupos: dict[GrupoDoPaciente, set[date]] = defaultdict(set)
    for paciente in ids:
        for k in modelo.do_paciente.get(paciente, ()):
            at = modelo.atendimentos[k]
            if filtro.especialidade is not None and at.especialidade is not filtro.especialidade:
                continue
            if (
                filtro.profissional_id is not None
                and at.lugar.profissional_id != filtro.profissional_id
            ):
                continue
            grupos[(paciente, at.especialidade, at.lugar.profissional_id)].add(at.lugar.data)
    ordenados = sorted(
        grupos.items(), key=lambda item: (modelo.nome_do_paciente(item[0][0]), *item[0][1:])
    )
    return [
        (
            grupo,
            Candidata(
                numero=numero,
                paciente=modelo.nome_do_paciente(grupo[0]),
                especialidade=grupo[1],
                profissional_id=grupo[2],
                dias=tuple(sorted(dias)),
            ),
        )
        for numero, (grupo, dias) in enumerate(ordenados, 1)
    ]


def _em_dois_lugares(modelo: _Modelo, paciente: str) -> bool:
    ats = [modelo.atendimentos[k] for k in modelo.do_paciente.get(paciente, ())]
    return any(a.lugar.cruza(b.lugar) for i, a in enumerate(ats) for b in ats[i + 1 :])


def _resolver_a(modelo: _Modelo, pedido: PedidoRealocacao) -> _A | Pergunta | SemSolucao:
    """Quem é A, qual atendimento (se já está na grade) e onde pode ir."""
    nomes = list(modelo.do_paciente)
    ids = list(casar_nome(nomes, pedido.paciente)) if pedido.paciente else []
    situacao = (
        Situacao(pedido.situacao)
        if pedido.situacao
        else (Situacao.JA_NA_GRADE if ids else Situacao.NOVO)
    )
    if situacao is Situacao.JA_NA_GRADE and not ids:
        return Pergunta(MotivoPergunta.NAO_ENCONTRADO)

    paciente_id: str | None = None
    grupo: GrupoDoPaciente | None = None
    usar_paciente = situacao is Situacao.JA_NA_GRADE or (
        bool(ids) and pedido.mesmo_paciente_da_grade is not False
    )
    if usar_paciente:
        filtro = pedido if situacao is Situacao.JA_NA_GRADE else PedidoRealocacao()
        numeradas = _candidatas(modelo, ids, filtro)
        chaves = [grupo for grupo, _ in numeradas]
        candidatas = tuple(candidata for _, candidata in numeradas)
        if not chaves:
            return Pergunta(MotivoPergunta.NAO_ENCONTRADO)
        if pedido.candidata is not None:
            if not 1 <= pedido.candidata <= len(chaves):
                return Pergunta(MotivoPergunta.CANDIDATA_INVALIDA, candidatas)
            grupo = chaves[pedido.candidata - 1]
            paciente_id = grupo[0]
        else:
            pacientes = {c[0] for c in chaves}
            if len(pacientes) > 1:
                return Pergunta(MotivoPergunta.NOME_AMBIGUO, candidatas)
            paciente_id = next(iter(pacientes))
            if situacao is Situacao.NOVO and pedido.mesmo_paciente_da_grade is None:
                return Pergunta(MotivoPergunta.MESMO_PACIENTE_DA_GRADE, candidatas)
            if _em_dois_lugares(modelo, paciente_id):
                return Pergunta(MotivoPergunta.HOMONIMO_POSSIVEL, candidatas)
            if situacao is Situacao.JA_NA_GRADE:
                if len(chaves) > 1:
                    return Pergunta(MotivoPergunta.QUAL_ATENDIMENTO, candidatas)
                grupo = chaves[0]

    rotulo = (
        modelo.nome_do_paciente(paciente_id)
        if paciente_id
        else (pedido.paciente.strip() if pedido.paciente else ROTULO_PACIENTE_NOVO)
    )
    if situacao is Situacao.JA_NA_GRADE:
        assert grupo is not None and paciente_id is not None
        _, especialidade, profissional = grupo
        origens = [
            modelo.atendimentos[k]
            for k in modelo.do_paciente[paciente_id]
            if modelo.atendimentos[k].especialidade is especialidade
            and modelo.atendimentos[k].lugar.profissional_id == profissional
        ]
        if pedido.dia_origem is not None:
            origens = [at for at in origens if at.lugar.data == pedido.dia_origem]
        if pedido.horario_origem is not None:
            origens = [at for at in origens if at.lugar.inicio == _minutos(pedido.horario_origem)]
        if len(origens) != 1:
            todas = sorted(
                at.lugar
                for at in (modelo.atendimentos[k] for k in modelo.do_paciente[paciente_id])
                if at.especialidade is especialidade and at.lugar.profissional_id == profissional
            )
            return Pergunta(MotivoPergunta.QUAL_HORARIO_DE_ORIGEM, origens=tuple(todas))
        origem = origens[0]
        if pedido.duracao_minutos is not None and _blocos(pedido.duracao_minutos) != (
            origem.lugar.blocos
        ):
            return Pergunta(
                MotivoPergunta.DURACAO_DIFERENTE, duracao_atual=origem.lugar.blocos * _PASSO
            )
        if not pedido.dias:
            return Pergunta(MotivoPergunta.FALTA_DIA)
        if not _janela_ok(pedido):
            return Pergunta(MotivoPergunta.FALTA_HORARIO)
        if origem.grupo:
            return SemSolucao(
                MotivoSemSolucao.SESSAO_EM_GRUPO,
                especialidade,
                origem.lugar.blocos * _PASSO,
                EstatisticaBusca(),
            )
        pedido_outro = pedido.profissional_id
        profissionais = (
            (pedido_outro,)
            if pedido_outro
            and modelo.especialidade_por_profissional.get(pedido_outro) is especialidade
            else (profissional,)
        )
        return _A(paciente_id, rotulo, especialidade, origem.lugar.blocos, profissionais, origem)

    especialidade_pedida = pedido.especialidade or (
        modelo.especialidade_por_profissional.get(pedido.profissional_id)
        if pedido.profissional_id
        else None
    )
    if especialidade_pedida is None:
        return Pergunta(MotivoPergunta.FALTA_ESPECIALIDADE)
    if pedido.duracao_minutos is None:
        return Pergunta(MotivoPergunta.FALTA_DURACAO)
    if not pedido.dias:
        return Pergunta(MotivoPergunta.FALTA_DIA)
    if not _janela_ok(pedido):
        return Pergunta(MotivoPergunta.FALTA_HORARIO)
    profissionais_novo: tuple[str, ...] = tuple(
        p
        for p in (
            [pedido.profissional_id]
            if pedido.profissional_id
            else modelo.profissionais_da(especialidade_pedida)
        )
        if modelo.especialidade_por_profissional.get(p) is especialidade_pedida
    )
    return _A(
        paciente_id if usar_paciente else None,
        rotulo,
        especialidade_pedida,
        _blocos(pedido.duracao_minutos),
        profissionais_novo,
        None,
    )


# ------------------------------------------------------------------ saída


def _opcao(modelo: _Modelo, a: _A, folha: _Folha) -> OpcaoRealocacao:
    passos = []
    for movimento in folha.movimentos:
        if movimento.k is None:
            passos.append(
                Passo(
                    paciente=a.rotulo,
                    pedido=True,
                    especialidade=a.especialidade,
                    de=a.origem.lugar if a.origem else None,
                    para=movimento.para,
                )
            )
        else:
            at = modelo.atendimentos[movimento.k]
            passos.append(
                Passo(
                    paciente=" / ".join(modelo.nome_do_paciente(p) for p in at.pacientes),
                    pedido=False,
                    especialidade=at.especialidade,
                    de=at.lugar,
                    para=movimento.para,
                )
            )
    return OpcaoRealocacao(tuple(passos))


def _escolher(
    folhas: Sequence[_Folha], validador: _Validador, maximo: int
) -> tuple[list[_Folha], int]:
    """Até `maximo` folhas válidas: primeiro (destino de A, deslocados) diferentes."""
    ordenadas = sorted(folhas, key=lambda folha: folha.ordem)
    validas: dict[int, bool] = {}
    descartadas = 0

    def valida(indice: int) -> bool:
        nonlocal descartadas
        if indice not in validas:
            validas[indice] = validador.valida(ordenadas[indice].movimentos)
            descartadas += not validas[indice]
        return validas[indice]

    escolhidas: list[int] = []
    vistos: set[tuple[Lugar, frozenset[int]]] = set()
    for indice, folha in enumerate(ordenadas):
        if len(escolhidas) >= maximo:
            break
        assinatura = (folha.destino_a, folha.deslocados)
        if assinatura in vistos:
            continue
        if valida(indice):
            escolhidas.append(indice)
            vistos.add(assinatura)
    for indice in range(len(ordenadas)):
        if len(escolhidas) >= maximo:
            break
        if indice not in escolhidas and valida(indice):
            escolhidas.append(indice)
    escolhidas.sort(key=lambda i: ordenadas[i].ordem)
    return [ordenadas[i] for i in escolhidas], descartadas


def _alternativa(busca: _Busca) -> Lugar | None:
    """O encaixe direto mais perto da janela, nos dias pedidos, fora dela."""
    pedido = busca.pedido
    desejado = pedido.horario_desejado or pedido.horario_minimo or pedido.horario_maximo
    referencia = _minutos(desejado) if desejado else 0
    dias = set(pedido.dias)
    origem = busca.a.origem.lugar if busca.a.origem else None
    melhores = sorted(
        (
            (abs(lugar.inicio - referencia), lugar)
            for profissional in busca.a.profissionais
            for lugar in busca.modelo.lugares(profissional, busca.a.blocos)
            if lugar.data in dias
            and lugar != origem
            and busca.sim.ocupantes(lugar, busca.base, ()) == set()
            and busca.a_cabe(lugar, busca.base)
        ),
    )
    return melhores[0][1] if melhores else None


def planejar_realocacao(
    foto: FotografiaPeriodo, pedido: PedidoRealocacao, regras: Regras | None = None
) -> ResultadoRealocacao:
    """Até `regras.max_opcoes` opções para o pedido, uma pergunta ou "sem solução"."""
    regras = regras or Regras()
    comeco = relogio.perf_counter()
    modelo = _Modelo(foto)
    a = _resolver_a(modelo, pedido)
    if not isinstance(a, _A):
        logger.info("planejar_realocacao: %s.", getattr(a, "motivo", "pergunta"))
        return a

    duracao = a.blocos * _PASSO
    if not a.profissionais:
        return SemSolucao(MotivoSemSolucao.SEM_OFERTA, a.especialidade, duracao, EstatisticaBusca())

    busca = _Busca(modelo, a, pedido, regras)
    validador = _Validador(modelo, a, regras)
    destinos = busca.destinos_classificados()
    limitada = False
    escolhidas: list[_Folha] = []
    descartadas = 0
    try:
        for nivel in range(regras.max_deslocados + 1):
            busca.nivel(nivel, destinos)
            escolhidas, descartadas = _escolher(busca.folhas, validador, regras.max_opcoes)
            if len(escolhidas) >= regras.max_opcoes:
                break
    except _TetoAtingidoError:
        limitada = True
        escolhidas, descartadas = _escolher(busca.folhas, validador, regras.max_opcoes)

    estatistica = EstatisticaBusca(
        cadeias_avaliadas=min(busca.cadeias, regras.max_cadeias),
        descartadas_na_validacao=descartadas,
        busca_limitada=limitada,
        segundos=relogio.perf_counter() - comeco,
    )
    logger.info(
        "planejar_realocacao: %d opção(ões), mínimo de %d deslocado(s), %d cadeia(s) "
        "avaliada(s), %d descartada(s) na validação, busca limitada: %s, em %.3fs",
        len(escolhidas),
        min((len(f.movimentos) - 1 for f in escolhidas), default=0),
        estatistica.cadeias_avaliadas,
        descartadas,
        "sim" if limitada else "não",
        estatistica.segundos,
    )
    if descartadas:
        logger.warning(
            "planejar_realocacao: %d opção(ões) reprovada(s) na validação independente.",
            descartadas,
        )
    if not escolhidas:
        sem_oferta = not destinos and not busca.destinos_de_a()
        return SemSolucao(
            MotivoSemSolucao.SEM_OFERTA if sem_oferta else MotivoSemSolucao.NENHUMA_COMBINACAO,
            a.especialidade,
            duracao,
            estatistica,
            _alternativa(busca),
        )
    return PlanoRealocacao(
        tuple(_opcao(modelo, a, folha) for folha in escolhidas),
        a.especialidade,
        duracao,
        estatistica,
    )
