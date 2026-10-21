"""Relatórios nomeados prontos, montados sobre a fotografia e o motor genérico.

Cada função aqui é só uma pergunta já formulada ao motor
(`app.engine.relatorio_generico`), ou uma leitura direta das faixas livres e
dos atendimentos da fotografia. Nenhuma devolve id nem nome de paciente,
exceto `agenda_do_paciente`, que só existe para o paciente pedido
explicitamente.
"""

from collections import defaultdict
from datetime import date, time
from enum import StrEnum
from itertools import pairwise
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from app.domain import Especialidade, normalizar_id
from app.domain.constants import FIM_PAUSA, INICIO_PAUSA
from app.engine.fotografia import FotografiaPeriodo, Periodo, SlotFotografado
from app.engine.relatorio_generico import (
    ConsultaRelatorio,
    Dimensao,
    FiltrosRelatorio,
    Metrica,
    ResultadoRelatorio,
    faixas_livres,
    gerar_relatorio,
)

#: Ocupação a partir da qual uma especialidade está "no limite" (quase sem vaga).
LIMIAR_NO_LIMITE: Final[float] = 0.95


class FaixaLivre(BaseModel):
    """Vagas seguidas no mesmo dia, profissional, sala e posto."""

    model_config = ConfigDict(frozen=True)

    data: date
    periodo: Periodo
    profissional_id: str
    profissional_nome: str
    especialidade: Especialidade | None
    sala_id: str
    sala_nome: str
    indice_posto: int = Field(ge=0)
    inicio: time
    fim: time
    slots: int = Field(ge=1)
    #: Quantos atendimentos de `slots_por_janela` slots cabem na faixa.
    cabem: int = Field(ge=0)


class RelatorioVagas(BaseModel):
    """Vagas livres do período: as faixas e o resumo por dia e período do dia."""

    model_config = ConfigDict(frozen=True)

    #: Só as faixas com pelo menos `slots_por_janela` slots (1 = todas).
    faixas: list[FaixaLivre]
    slots_por_janela: int
    resumo: ResultadoRelatorio


def _faixa(foto: FotografiaPeriodo, slots: list[SlotFotografado], por_janela: int) -> FaixaLivre:
    primeiro, ultimo = slots[0], slots[-1]
    assert primeiro.profissional_id is not None  # faixas_livres só junta slots escalados
    return FaixaLivre(
        data=primeiro.data,
        periodo=primeiro.periodo,
        profissional_id=primeiro.profissional_id,
        profissional_nome=foto.nome_profissional(primeiro.profissional_id),
        especialidade=primeiro.especialidade,
        sala_id=primeiro.sala_id,
        sala_nome=foto.nome_sala(primeiro.sala_id),
        indice_posto=primeiro.indice_posto,
        inicio=primeiro.hora_inicio,
        fim=ultimo.hora_fim,
        slots=len(slots),
        cabem=len(slots) // por_janela,
    )


def vagas_livres(
    foto: FotografiaPeriodo, filtros: FiltrosRelatorio, slots_por_janela: int = 1
) -> RelatorioVagas:
    """Vagas do período em faixas contínuas, e o resumo por dia e manhã/tarde.

    Com `slots_por_janela` > 1, só entram as faixas onde cabe um atendimento
    desse tamanho (janelas contínuas de 1 h = 2 slots, 1 h 30 = 3 slots).
    """
    recorte = [slot for slot in foto.slots if filtros.aceita(slot)]
    faixas = [
        _faixa(foto, faixa, slots_por_janela)
        for faixa in faixas_livres(recorte)
        if len(faixa) >= slots_por_janela
    ]
    metricas: tuple[Metrica, ...] = (Metrica.VAGAS_LIVRES, Metrica.HORAS_LIVRES)
    if slots_por_janela > 1:
        metricas = (*metricas, Metrica.JANELAS_LIVRES)
    resumo = gerar_relatorio(
        foto,
        ConsultaRelatorio(
            dimensoes=(Dimensao.DIA, Dimensao.PERIODO),
            metricas=metricas,
            filtros=filtros,
            slots_por_janela=slots_por_janela,
        ),
    )
    return RelatorioVagas(faixas=faixas, slots_por_janela=slots_por_janela, resumo=resumo)


def ranking_ocupacao(
    foto: FotografiaPeriodo,
    filtros: FiltrosRelatorio,
    dimensao: Dimensao = Dimensao.PROFISSIONAL,
    *,
    decrescente: bool = True,
    limite: int | None = None,
) -> ResultadoRelatorio:
    """Ocupação por `dimensao`, da maior para a menor (ou o contrário)."""
    return gerar_relatorio(
        foto,
        ConsultaRelatorio(
            dimensoes=(dimensao,),
            metricas=(
                Metrica.OCUPACAO,
                Metrica.SLOTS_OCUPADOS,
                Metrica.SLOTS_ESCALADOS,
                Metrica.VAGAS_LIVRES,
                Metrica.SLOTS_PARA_META,
            ),
            filtros=filtros,
            ordenar_por=Metrica.OCUPACAO,
            decrescente=decrescente,
            limite=limite,
        ),
    )


def comparativo_dias(foto: FotografiaPeriodo, filtros: FiltrosRelatorio) -> ResultadoRelatorio:
    """Um dia contra o outro: ocupação, oferta, vagas, pessoas e sessões."""
    return gerar_relatorio(
        foto,
        ConsultaRelatorio(
            dimensoes=(Dimensao.DIA,),
            metricas=(
                Metrica.OCUPACAO,
                Metrica.SLOTS_ESCALADOS,
                Metrica.SLOTS_OCUPADOS,
                Metrica.VAGAS_LIVRES,
                Metrica.PACIENTES_DISTINTOS,
                Metrica.SESSOES,
            ),
            filtros=filtros,
        ),
    )


class RelatorioConvenios(BaseModel):
    """Atendimentos por convênio e os que aguardam autorização, por dia e profissional."""

    model_config = ConfigDict(frozen=True)

    por_convenio: ResultadoRelatorio
    aguardando: ResultadoRelatorio


def convenios(foto: FotografiaPeriodo, filtros: FiltrosRelatorio) -> RelatorioConvenios:
    """Sessões, pacientes e slots por convênio; aguardando autorização por dia e profissional."""
    por_convenio = gerar_relatorio(
        foto,
        ConsultaRelatorio(
            dimensoes=(Dimensao.CONVENIO,),
            metricas=(
                Metrica.SESSOES,
                Metrica.PACIENTES_DISTINTOS,
                Metrica.SLOTS_OCUPADOS,
                Metrica.AGUARDANDO_AUTORIZACAO,
            ),
            filtros=filtros,
        ),
    )
    aguardando = gerar_relatorio(
        foto,
        ConsultaRelatorio(
            dimensoes=(Dimensao.DIA, Dimensao.PROFISSIONAL),
            metricas=(Metrica.AGUARDANDO_AUTORIZACAO,),
            filtros=filtros,
        ),
    )
    linhas = [linha for linha in aguardando.linhas if linha.valores[Metrica.AGUARDANDO_AUTORIZACAO]]
    # Só interessam as linhas com pendência.
    aguardando = aguardando.model_copy(update={"linhas": linhas})
    return RelatorioConvenios(por_convenio=por_convenio, aguardando=aguardando)


class QualidadeProfissionalDia(BaseModel):
    """Buracos e fragmentação da agenda de um profissional num dia."""

    model_config = ConfigDict(frozen=True)

    profissional_id: str
    profissional_nome: str
    especialidade: Especialidade | None
    data: date
    slots_escalados: int
    slots_ocupados: int
    vagas_livres: int
    #: Faixas livres com atendimento imediatamente antes e depois, no mesmo posto.
    buracos: int
    slots_em_buracos: int
    #: Faixas livres de um único slot (não cabe sessão de 1 h).
    slots_isolados: int
    #: Quantas faixas livres separadas a agenda tem no dia.
    faixas_livres: int
    #: Maior faixa livre contínua, em slots.
    maior_faixa_livre: int


class RelatorioQualidade(BaseModel):
    """Qualidade da agenda: por profissional e dia, e consolidado por profissional."""

    model_config = ConfigDict(frozen=True)

    por_dia: list[QualidadeProfissionalDia]
    #: Mesmos campos somados no período (`maior_faixa_livre` é o máximo; `data` é o início).
    por_profissional: list[QualidadeProfissionalDia]


def _buracos(slots_do_posto: list[SlotFotografado]) -> list[int]:
    """Tamanhos das faixas livres cercadas de ocupado, numa sequência de um posto.

    A sequência é quebrada onde não há contiguidade (pausa, horário fechado).
    """
    ordenados = sorted(slots_do_posto, key=lambda slot: slot.hora_inicio)
    buracos: list[int] = []
    anterior: SlotFotografado | None = None
    livres = 0
    antes_ocupado = False
    for slot in ordenados:
        contiguo = anterior is not None and anterior.hora_fim == slot.hora_inicio
        if not contiguo:
            livres, antes_ocupado = 0, False
        if slot.livre:
            livres += 1
        else:
            if livres and antes_ocupado:
                buracos.append(livres)
            livres, antes_ocupado = 0, slot.ocupacoes > 0
        anterior = slot
    return buracos


def qualidade_agenda(foto: FotografiaPeriodo, filtros: FiltrosRelatorio) -> RelatorioQualidade:
    """Buracos e fragmentação por profissional e dia (só slots da grade do profissional)."""
    por_profissional_dia: dict[tuple[str, date], list[SlotFotografado]] = defaultdict(list)
    for slot in foto.slots:
        if slot.na_grade and slot.profissional_id is not None and filtros.aceita(slot):
            por_profissional_dia[(slot.profissional_id, slot.data)].append(slot)

    por_dia: list[QualidadeProfissionalDia] = []
    for (profissional_id, dia), slots in por_profissional_dia.items():
        por_posto: dict[tuple[str, int], list[SlotFotografado]] = defaultdict(list)
        for slot in slots:
            por_posto[(slot.sala_id, slot.indice_posto)].append(slot)
        buracos = [tamanho for do_posto in por_posto.values() for tamanho in _buracos(do_posto)]
        faixas = [len(faixa) for faixa in faixas_livres(slots)]
        por_dia.append(
            QualidadeProfissionalDia(
                profissional_id=profissional_id,
                profissional_nome=foto.nome_profissional(profissional_id),
                especialidade=slots[0].especialidade,
                data=dia,
                slots_escalados=len(slots),
                slots_ocupados=sum(slot.ocupacoes for slot in slots),
                vagas_livres=sum(1 for slot in slots if slot.livre),
                buracos=len(buracos),
                slots_em_buracos=sum(buracos),
                slots_isolados=sum(1 for tamanho in faixas if tamanho == 1),
                faixas_livres=len(faixas),
                maior_faixa_livre=max(faixas, default=0),
            )
        )
    por_dia.sort(key=lambda item: (_ordem_nome(item.profissional_nome), item.data))

    acumulado: dict[str, list[QualidadeProfissionalDia]] = defaultdict(list)
    for item in por_dia:
        acumulado[item.profissional_id].append(item)
    por_profissional = [
        QualidadeProfissionalDia(
            profissional_id=profissional_id,
            profissional_nome=dias[0].profissional_nome,
            especialidade=dias[0].especialidade,
            data=foto.inicio,
            slots_escalados=sum(d.slots_escalados for d in dias),
            slots_ocupados=sum(d.slots_ocupados for d in dias),
            vagas_livres=sum(d.vagas_livres for d in dias),
            buracos=sum(d.buracos for d in dias),
            slots_em_buracos=sum(d.slots_em_buracos for d in dias),
            slots_isolados=sum(d.slots_isolados for d in dias),
            faixas_livres=sum(d.faixas_livres for d in dias),
            maior_faixa_livre=max(d.maior_faixa_livre for d in dias),
        )
        for profissional_id, dias in acumulado.items()
    ]
    # Quem tem mais slots presos em buraco primeiro.
    por_profissional.sort(key=lambda item: (-item.slots_em_buracos, -item.buracos))
    return RelatorioQualidade(por_dia=por_dia, por_profissional=por_profissional)


def _ordem_nome(nome: str) -> str:
    return normalizar_id(nome)


def salas_ociosas(foto: FotografiaPeriodo, filtros: FiltrosRelatorio) -> ResultadoRelatorio:
    """Salas da menos para a mais ocupada, com vagas e postos sem escala."""
    return gerar_relatorio(
        foto,
        ConsultaRelatorio(
            dimensoes=(Dimensao.SALA,),
            metricas=(
                Metrica.OCUPACAO,
                Metrica.SLOTS_ESCALADOS,
                Metrica.SLOTS_OCUPADOS,
                Metrica.VAGAS_LIVRES,
                Metrica.HORAS_LIVRES,
                Metrica.SLOTS_FECHADOS,
            ),
            filtros=filtros,
            ordenar_por=Metrica.OCUPACAO,
            decrescente=False,
        ),
    )


class SituacaoCapacidade(StrEnum):
    """Leitura da capacidade de uma especialidade frente à meta."""

    #: Abaixo da meta: há capacidade ociosa.
    SOBRANDO = "sobrando"
    #: Ocupação a partir de `LIMIAR_NO_LIMITE`: quase sem vaga, pode faltar.
    NO_LIMITE = "no_limite"
    EQUILIBRADA = "equilibrada"


class RelatorioCapacidade(BaseModel):
    """Capacidade por especialidade e a situação de cada uma."""

    model_config = ConfigDict(frozen=True)

    resultado: ResultadoRelatorio
    #: `id` da especialidade (mesma ordem de `resultado.linhas`) -> situação.
    situacoes: dict[str, SituacaoCapacidade]


def capacidade_por_especialidade(
    foto: FotografiaPeriodo, filtros: FiltrosRelatorio
) -> RelatorioCapacidade:
    """Oferta, uso, vagas e situação (sobrando, equilibrada, no limite) por especialidade."""
    resultado = gerar_relatorio(
        foto,
        ConsultaRelatorio(
            dimensoes=(Dimensao.ESPECIALIDADE,),
            metricas=(
                Metrica.OCUPACAO,
                Metrica.SLOTS_ESCALADOS,
                Metrica.SLOTS_OCUPADOS,
                Metrica.VAGAS_LIVRES,
                Metrica.SLOTS_PARA_META,
                Metrica.PACIENTES_DISTINTOS,
            ),
            filtros=filtros,
        ),
    )
    situacoes: dict[str, SituacaoCapacidade] = {}
    for linha in resultado.linhas:
        ocupacao = float(linha.valores[Metrica.OCUPACAO])
        if linha.abaixo_da_meta:
            situacao = SituacaoCapacidade.SOBRANDO
        elif ocupacao >= LIMIAR_NO_LIMITE:
            situacao = SituacaoCapacidade.NO_LIMITE
        else:
            situacao = SituacaoCapacidade.EQUILIBRADA
        situacoes[linha.chaves[Dimensao.ESPECIALIDADE].id] = situacao
    return RelatorioCapacidade(resultado=resultado, situacoes=situacoes)


class SessaoPaciente(BaseModel):
    """Um atendimento do paciente."""

    model_config = ConfigDict(frozen=True)

    inicio: time
    fim: time
    profissional_nome: str
    especialidade: Especialidade
    sala_nome: str
    aguardando_autorizacao: bool


class IntervaloPaciente(BaseModel):
    """Tempo de espera entre duas terapias seguidas do paciente no mesmo dia."""

    model_config = ConfigDict(frozen=True)

    inicio: time
    fim: time
    minutos: int = Field(ge=0)
    #: Quanto do intervalo é a pausa geral do almoço.
    minutos_de_pausa: int = Field(ge=0)


class DiaAgendaPaciente(BaseModel):
    """O dia do paciente: sessões em ordem e os buracos entre elas."""

    model_config = ConfigDict(frozen=True)

    data: date
    sessoes: list[SessaoPaciente]
    intervalos: list[IntervaloPaciente]


class AgendaPaciente(BaseModel):
    """A agenda de UM paciente, pedida explicitamente. É o único relatório com nome."""

    model_config = ConfigDict(frozen=True)

    nome: str
    convenio: str | None
    dias: list[DiaAgendaPaciente]
    dias_nao_lidos: list[date]

    @property
    def total_sessoes(self) -> int:
        """Sessões no período."""
        return sum(len(dia.sessoes) for dia in self.dias)

    @property
    def minutos_em_intervalos(self) -> int:
        """Espera entre terapias no período, sem contar a pausa do almoço."""
        return sum(i.minutos - i.minutos_de_pausa for dia in self.dias for i in dia.intervalos)


def _minutos(hora: time) -> int:
    return hora.hour * 60 + hora.minute


def _sobreposicao(inicio: time, fim: time, outro_inicio: time, outro_fim: time) -> int:
    return max(
        0, min(_minutos(fim), _minutos(outro_fim)) - max(_minutos(inicio), _minutos(outro_inicio))
    )


def agenda_do_paciente(foto: FotografiaPeriodo, paciente_id: str) -> AgendaPaciente | None:
    """Sessões do paciente no período e os intervalos entre elas; `None` se não aparece."""
    paciente = foto.pacientes.get(paciente_id)
    atendimentos = [a for a in foto.atendimentos if paciente_id in a.paciente_ids]
    if paciente is None or not atendimentos:
        return None

    por_dia: dict[date, list[SessaoPaciente]] = defaultdict(list)
    for atendimento in sorted(atendimentos, key=lambda a: a.slots[0]):
        por_dia[atendimento.data].append(
            SessaoPaciente(
                inicio=atendimento.slots[0].hora_inicio,
                fim=atendimento.slots[-1].hora_fim,
                profissional_nome=foto.nome_profissional(atendimento.profissional_id),
                especialidade=atendimento.especialidade,
                sala_nome=foto.nome_sala(atendimento.sala_id),
                aguardando_autorizacao=atendimento.aguardando_autorizacao,
            )
        )

    dias: list[DiaAgendaPaciente] = []
    for dia in sorted(por_dia):
        sessoes = por_dia[dia]
        intervalos = [
            IntervaloPaciente(
                inicio=anterior.fim,
                fim=seguinte.inicio,
                minutos=_minutos(seguinte.inicio) - _minutos(anterior.fim),
                minutos_de_pausa=_sobreposicao(
                    anterior.fim, seguinte.inicio, INICIO_PAUSA, FIM_PAUSA
                ),
            )
            for anterior, seguinte in pairwise(sessoes)
            if _minutos(seguinte.inicio) > _minutos(anterior.fim)
        ]
        dias.append(DiaAgendaPaciente(data=dia, sessoes=sessoes, intervalos=intervalos))

    return AgendaPaciente(
        nome=paciente.nome,
        convenio=paciente.convenio.value if paciente.convenio is not None else None,
        dias=dias,
        dias_nao_lidos=foto.dias_nao_lidos,
    )
