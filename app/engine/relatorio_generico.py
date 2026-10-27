"""Motor de relatório genérico: dimensões x métricas sobre a fotografia de um período.

Toda conta é feita aqui, em código, sobre `FotografiaPeriodo`; o modelo de IA
só escolhe a pergunta (dimensões, métricas, filtros e ordenação). As regras
são as mesmas da engine que já existia, para o mesmo recorte devolver os
mesmos números:

- slots escalados = entradas de grade; slots ocupados = slots de atendimento
  (inclusive fora da grade, como em `construir_relatorio_ocupacao_do_dia`,
  exceto quando o recorte é por sala ou posto — lá eles não têm entrada de
  grade e ficam fora, como em `RelatorioOcupacaoDoDia.por_sala`);
- ocupação = ocupados / escalados, sempre recalculada a partir dos inteiros do
  grupo — num período, é ponderada pelos slots de cada dia, nunca a média dos
  percentuais;
- meta de 80% e "slots para a meta" vêm de `slots_para_meta`;
- pacientes distintos são contados sobre o grupo inteiro (o total não é a soma
  das linhas): quem aparece em dois dias, postos ou profissionais do mesmo
  grupo conta uma vez.
"""

from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import date, time
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from app.domain import Especialidade, normalizar_id
from app.domain.constants import DURACAO_SLOT_MINUTOS, META_OCUPACAO_POR_SALA
from app.domain.formatacao import chave_natural, rotulo_enum
from app.engine.fotografia import (
    CONVENIO_NAO_INFORMADO,
    EstadoSlot,
    FotografiaPeriodo,
    Periodo,
    SlotFotografado,
)
from app.engine.ocupacao import percentual_de_ocupacao
from app.engine.ocupacao_profissional import slots_para_meta

#: Valor da dimensão convênio para slots sem paciente (livres ou fechados).
CONVENIO_SEM_PACIENTE: Final[str] = "sem_paciente"

#: Máximo de slots numa janela contínua pedida (10 slots = 5 horas).
MAX_SLOTS_POR_JANELA: Final[int] = 10


class Dimensao(StrEnum):
    """Por onde agrupar as linhas do relatório."""

    DIA = "dia"
    PROFISSIONAL = "profissional"
    ESPECIALIDADE = "especialidade"
    SALA = "sala"
    #: Posto dentro da sala (o rótulo já traz a sala).
    POSTO = "posto"
    PERIODO = "periodo"
    CONVENIO = "convenio"


class Metrica(StrEnum):
    """O que calcular em cada linha (e no total)."""

    OCUPACAO = "ocupacao"
    SLOTS_ESCALADOS = "slots_escalados"
    SLOTS_OCUPADOS = "slots_ocupados"
    VAGAS_LIVRES = "vagas_livres"
    SLOTS_PARA_META = "slots_para_meta"
    SLOTS_FECHADOS = "slots_fechados"
    PACIENTES_DISTINTOS = "pacientes_distintos"
    SESSOES = "sessoes"
    HORAS_ESCALADAS = "horas_escaladas"
    HORAS_OCUPADAS = "horas_ocupadas"
    HORAS_LIVRES = "horas_livres"
    AGUARDANDO_AUTORIZACAO = "aguardando_autorizacao"
    JANELAS_LIVRES = "janelas_livres"


#: O que cada métrica significa — também é o texto do catálogo para a IA.
DEFINICOES_METRICAS: Final[dict[Metrica, str]] = {
    Metrica.OCUPACAO: (
        "slots ocupados ÷ slots escalados do grupo (fração; ponderada pelos slots, "
        "nunca média de percentuais); meta 80%"
    ),
    Metrica.SLOTS_ESCALADOS: (
        "janelas de 30 min em que há profissional escalado num posto de sala (a oferta)"
    ),
    Metrica.SLOTS_OCUPADOS: (
        "janelas de 30 min cobertas por atendimento; sessão em grupo conta uma vez"
    ),
    Metrica.VAGAS_LIVRES: "slots escalados sem atendimento (cada um é uma vaga de 30 min)",
    Metrica.SLOTS_PARA_META: "slots que ainda faltam ocupar para chegar a 80% (0 se já chegou)",
    Metrica.SLOTS_FECHADOS: (
        "janelas do expediente em que um posto de sala não tem ninguém escalado "
        "(inclui FECHADO da planilha)"
    ),
    Metrica.PACIENTES_DISTINTOS: (
        "pessoas diferentes atendidas no grupo; quem aparece mais de uma vez conta uma vez"
    ),
    Metrica.SESSOES: "atendimentos (blocos contínuos com o mesmo paciente ou grupo)",
    Metrica.HORAS_ESCALADAS: "slots escalados x 0,5 h",
    Metrica.HORAS_OCUPADAS: "slots ocupados x 0,5 h",
    Metrica.HORAS_LIVRES: "vagas livres x 0,5 h",
    Metrica.AGUARDANDO_AUTORIZACAO: (
        "sessões encaixadas que ainda aguardam autorização (fonte preta na planilha)"
    ),
    Metrica.JANELAS_LIVRES: (
        "quantos atendimentos de N slots seguidos cabem nas vagas, mesmo profissional, "
        "sala e posto, sem atravessar a pausa (N padrão 2 = 1 h)"
    ),
}

#: Métricas sobre a oferta (grade): não existem para um convênio, que só
#: aparece em slot ocupado.
METRICAS_DE_OFERTA: Final[frozenset[Metrica]] = frozenset(
    {
        Metrica.OCUPACAO,
        Metrica.SLOTS_ESCALADOS,
        Metrica.VAGAS_LIVRES,
        Metrica.SLOTS_PARA_META,
        Metrica.SLOTS_FECHADOS,
        Metrica.HORAS_ESCALADAS,
        Metrica.HORAS_LIVRES,
        Metrica.JANELAS_LIVRES,
    }
)

_HORAS_POR_SLOT: Final[float] = DURACAO_SLOT_MINUTOS / 60

#: Valor de uma métrica: contagem inteira, fração (ocupação) ou horas.
ValorMetrica = int | float


class ConsultaInvalidaError(ValueError):
    """Parâmetros que não formam um relatório; a mensagem orienta a correção."""


class FiltrosRelatorio(BaseModel):
    """Recorte aplicado antes de agrupar. Conjunto vazio = sem filtro."""

    model_config = ConfigDict(frozen=True)

    profissional_ids: frozenset[str] = frozenset()
    especialidades: frozenset[Especialidade] = frozenset()
    sala_ids: frozenset[str] = frozenset()
    #: `Convenio.value` ou `CONVENIO_NAO_INFORMADO`.
    convenios: frozenset[str] = frozenset()
    #: `date.weekday()`, de 0 (segunda) a 5 (sábado).
    dias_semana: frozenset[int] = frozenset()
    periodos: frozenset[Periodo] = frozenset()
    #: Janela de horário: o slot começa em `hora_inicio` ou depois e termina em
    #: `hora_fim` ou antes (fechada no início, aberta no fim: 11:30-12:00 cabe
    #: em 10:00-12:00; 09:30-10:00 não). `None` = sem limite daquele lado.
    hora_inicio: time | None = None
    hora_fim: time | None = None

    def aceita(self, slot: SlotFotografado) -> bool:
        """Se o slot entra no recorte."""
        if self.profissional_ids and slot.profissional_id not in self.profissional_ids:
            return False
        if self.especialidades and slot.especialidade not in self.especialidades:
            return False
        if self.sala_ids and slot.sala_id not in self.sala_ids:
            return False
        if self.dias_semana and slot.data.weekday() not in self.dias_semana:
            return False
        if self.periodos and slot.periodo not in self.periodos:
            return False
        if self.hora_inicio is not None and slot.hora_inicio < self.hora_inicio:
            return False
        if self.hora_fim is not None and slot.hora_fim > self.hora_fim:
            return False
        return not self.convenios or (
            slot.convenio is not None and bool(self.convenios & set(slot.convenio.split("+")))
        )


class ConsultaRelatorio(BaseModel):
    """A pergunta ao motor: agrupar por `dimensoes` e calcular `metricas`."""

    model_config = ConfigDict(frozen=True)

    dimensoes: tuple[Dimensao, ...] = ()
    metricas: tuple[Metrica, ...]
    filtros: FiltrosRelatorio = FiltrosRelatorio()
    ordenar_por: Metrica | Dimensao | None = None
    #: `None`: decrescente ao ordenar por métrica, crescente por dimensão.
    decrescente: bool | None = None
    limite: int | None = Field(default=None, ge=1)
    #: Tamanho da janela de `JANELAS_LIVRES`, em slots de 30 minutos.
    slots_por_janela: int = Field(default=2, ge=1)

    def validar(self) -> None:
        """Levanta `ConsultaInvalidaError` com uma mensagem útil quando não faz sentido."""
        if not self.metricas:
            raise ConsultaInvalidaError("Informe ao menos uma métrica.")
        if len(set(self.dimensoes)) != len(self.dimensoes):
            raise ConsultaInvalidaError("Dimensão repetida.")
        if len(set(self.metricas)) != len(self.metricas):
            raise ConsultaInvalidaError("Métrica repetida.")
        if isinstance(self.ordenar_por, Metrica) and self.ordenar_por not in self.metricas:
            raise ConsultaInvalidaError(
                f"ordenar_por='{self.ordenar_por.value}' precisa estar entre as métricas pedidas."
            )
        if isinstance(self.ordenar_por, Dimensao) and self.ordenar_por not in self.dimensoes:
            raise ConsultaInvalidaError(
                f"ordenar_por='{self.ordenar_por.value}' precisa estar entre as dimensões pedidas."
            )
        inicio, fim = self.filtros.hora_inicio, self.filtros.hora_fim
        if inicio is not None and fim is not None and fim <= inicio:
            raise ConsultaInvalidaError("O fim da janela de horário precisa ser depois do início.")
        if self.slots_por_janela > MAX_SLOTS_POR_JANELA:
            raise ConsultaInvalidaError(
                f"Janela de {self.slots_por_janela} slots é longa demais: o máximo é "
                f"{MAX_SLOTS_POR_JANELA} slots ({MAX_SLOTS_POR_JANELA // 2} h)."
            )
        usa_convenio = Dimensao.CONVENIO in self.dimensoes or bool(self.filtros.convenios)
        de_oferta = [m.value for m in self.metricas if m in METRICAS_DE_OFERTA]
        if usa_convenio and de_oferta:
            raise ConsultaInvalidaError(
                "Convênio só existe em slot ocupado, então não combina com métricas de oferta "
                f"({', '.join(de_oferta)}). Com convênio, use slots_ocupados, sessoes, "
                "pacientes_distintos, horas_ocupadas ou aguardando_autorizacao."
            )


class ValorDimensao(BaseModel):
    """Valor de uma dimensão numa linha: id estável e rótulo de exibição."""

    model_config = ConfigDict(frozen=True)

    id: str
    rotulo: str


class LinhaRelatorio(BaseModel):
    """Um grupo do relatório."""

    model_config = ConfigDict(frozen=True)

    chaves: dict[Dimensao, ValorDimensao]
    valores: dict[Metrica, ValorMetrica]
    #: Só quando a meta se aplica (ocupação ou slots para a meta pedidos).
    abaixo_da_meta: bool | None = None
    #: (ocupados, escalados) quando a ocupação foi pedida: a exibição em
    #: percentual sai dos inteiros, nunca do float.
    razao_ocupacao: tuple[int, int] | None = None


class ResultadoRelatorio(BaseModel):
    """Tabela tipada devolvida pelo motor, com o total do recorte inteiro."""

    model_config = ConfigDict(frozen=True)

    inicio: date
    fim: date
    dimensoes: tuple[Dimensao, ...]
    metricas: tuple[Metrica, ...]
    linhas: list[LinhaRelatorio]
    #: Calculado sobre todos os slots do recorte (não é a soma das linhas).
    total: dict[Metrica, ValorMetrica]
    total_abaixo_da_meta: bool | None
    total_razao_ocupacao: tuple[int, int] | None = None
    #: Linhas que ficaram de fora por causa de `limite`.
    linhas_omitidas: int = Field(ge=0)
    meta: float | None
    slots_por_janela: int
    dias_consultados: list[date]
    dias_lidos: list[date]
    dias_nao_lidos: list[date]
    avisos: list[str]

    @property
    def parcial(self) -> bool:
        """Se algum dia do período não pôde ser lido."""
        return bool(self.dias_nao_lidos)


#: Aviso de ocupação acima de 100% (mesmo texto das tools que já existiam).
AVISO_ACIMA_DE_CEM: Final[str] = (
    "Atenção: há ocupação acima de 100% (mais slots ocupados que escalados); "
    "possível inconsistência na planilha."
)


def faixas_livres(slots: Iterable[SlotFotografado]) -> list[list[SlotFotografado]]:
    """Sequências contínuas de vagas no mesmo dia, profissional, sala e posto.

    A pausa quebra a sequência (11:30 e 13:00 não são contíguos). Em ordem de
    dia, horário, sala e posto.
    """
    por_posto: dict[tuple[date, str, str, int], list[SlotFotografado]] = defaultdict(list)
    for slot in slots:
        if slot.livre and slot.profissional_id is not None:
            por_posto[(slot.data, slot.profissional_id, slot.sala_id, slot.indice_posto)].append(
                slot
            )
    faixas: list[list[SlotFotografado]] = []
    for do_posto in por_posto.values():
        ordenados = sorted(do_posto, key=lambda slot: slot.hora_inicio)
        atual = [ordenados[0]]
        for slot in ordenados[1:]:
            if atual[-1].hora_fim == slot.hora_inicio:
                atual.append(slot)
            else:
                faixas.append(atual)
                atual = [slot]
        faixas.append(atual)
    faixas.sort(key=lambda f: (f[0].data, f[0].hora_inicio, f[0].sala_id, f[0].indice_posto))
    return faixas


def calcular_metricas(
    slots: list[SlotFotografado], metricas: Iterable[Metrica], slots_por_janela: int = 2
) -> dict[Metrica, ValorMetrica]:
    """As métricas pedidas sobre um conjunto de slots já recortado."""
    escalados = sum(1 for slot in slots if slot.na_grade)
    ocupados = sum(slot.ocupacoes for slot in slots)
    livres = sum(1 for slot in slots if slot.livre)
    calculos: dict[Metrica, Callable[[], ValorMetrica]] = {
        Metrica.OCUPACAO: lambda: percentual_de_ocupacao(escalados, ocupados),
        Metrica.SLOTS_ESCALADOS: lambda: escalados,
        Metrica.SLOTS_OCUPADOS: lambda: ocupados,
        Metrica.VAGAS_LIVRES: lambda: livres,
        Metrica.SLOTS_PARA_META: lambda: slots_para_meta(escalados, ocupados),
        Metrica.SLOTS_FECHADOS: lambda: sum(
            1 for slot in slots if slot.estado is EstadoSlot.FECHADO
        ),
        Metrica.PACIENTES_DISTINTOS: lambda: len(
            {paciente for slot in slots for paciente in slot.paciente_ids}
        ),
        Metrica.SESSOES: lambda: len({at for slot in slots for at in slot.atendimento_ids}),
        Metrica.HORAS_ESCALADAS: lambda: escalados * _HORAS_POR_SLOT,
        Metrica.HORAS_OCUPADAS: lambda: ocupados * _HORAS_POR_SLOT,
        Metrica.HORAS_LIVRES: lambda: livres * _HORAS_POR_SLOT,
        Metrica.AGUARDANDO_AUTORIZACAO: lambda: len(
            {at for slot in slots if slot.aguardando_autorizacao for at in slot.atendimento_ids}
        ),
        Metrica.JANELAS_LIVRES: lambda: sum(
            len(faixa) // slots_por_janela for faixa in faixas_livres(slots)
        ),
    }
    return {metrica: calculos[metrica]() for metrica in metricas}


_SEM_VALOR = "—"


def _valor_dimensao(
    foto: FotografiaPeriodo, slot: SlotFotografado, dimensao: Dimensao
) -> ValorDimensao:
    match dimensao:
        case Dimensao.DIA:
            return ValorDimensao(id=slot.data.isoformat(), rotulo=slot.data.strftime("%d/%m/%Y"))
        case Dimensao.PROFISSIONAL:
            if slot.profissional_id is None:
                return ValorDimensao(id=_SEM_VALOR, rotulo="Sem profissional")
            return ValorDimensao(
                id=slot.profissional_id, rotulo=foto.nome_profissional(slot.profissional_id)
            )
        case Dimensao.ESPECIALIDADE:
            if slot.especialidade is None:
                return ValorDimensao(id=_SEM_VALOR, rotulo="Sem especialidade")
            return ValorDimensao(
                id=slot.especialidade.value, rotulo=rotulo_enum(slot.especialidade.value)
            )
        case Dimensao.SALA:
            return ValorDimensao(id=slot.sala_id, rotulo=foto.nome_sala(slot.sala_id))
        case Dimensao.POSTO:
            posto = slot.indice_posto + 1
            return ValorDimensao(
                id=f"{slot.sala_id}#{posto}",
                rotulo=f"{foto.nome_sala(slot.sala_id)} · posto {posto}",
            )
        case Dimensao.PERIODO:
            return ValorDimensao(
                id=slot.periodo.value,
                rotulo="Manhã" if slot.periodo is Periodo.MANHA else "Tarde",
            )
        case Dimensao.CONVENIO:
            return ValorDimensao(
                id=slot.convenio or CONVENIO_SEM_PACIENTE,
                rotulo=rotulo_convenio(slot.convenio or CONVENIO_SEM_PACIENTE),
            )


def rotulo_convenio(convenio: str) -> str:
    """`unimed+nao_informado` -> `Não informado + Unimed`; `sem_paciente` -> `Sem paciente`."""
    especiais = {CONVENIO_NAO_INFORMADO: "Não informado", CONVENIO_SEM_PACIENTE: "Sem paciente"}
    return " + ".join(
        especiais.get(parte, rotulo_enum(parte)) for parte in sorted(convenio.split("+"))
    )


def _chave_ordem(
    foto: FotografiaPeriodo, dimensao: Dimensao, valor: ValorDimensao
) -> tuple[object, ...]:
    """Ordem natural de cada dimensão; os "sem valor" vão ao fim."""
    sem_valor = valor.id in (_SEM_VALOR, CONVENIO_SEM_PACIENTE)
    match dimensao:
        case Dimensao.DIA | Dimensao.PERIODO:
            return (sem_valor, valor.id)
        case Dimensao.SALA:
            return (sem_valor, chave_natural(valor.rotulo), valor.id)
        case Dimensao.POSTO:
            sala_id, _, posto = valor.id.rpartition("#")
            return (sem_valor, chave_natural(foto.nome_sala(sala_id)), sala_id, int(posto))
        case _:
            return (sem_valor, normalizar_id(valor.rotulo), valor.id)


def _meta_se_aplica(metricas: Iterable[Metrica]) -> bool:
    return bool({Metrica.OCUPACAO, Metrica.SLOTS_PARA_META} & set(metricas))


def _valores_do_grupo(
    slots: list[SlotFotografado], consulta: ConsultaRelatorio, meta: float | None
) -> tuple[dict[Metrica, ValorMetrica], bool | None, tuple[int, int] | None]:
    """Métricas pedidas, se está abaixo da meta e a razão (ocupados, escalados).

    Com meta, "slots para a meta" é sempre calculado (decide `abaixo_da_meta`);
    com ocupação, escalados e ocupados também. Só ficam nos valores se pedidos.
    """
    auxiliares: list[Metrica] = []
    if meta is not None:
        auxiliares.append(Metrica.SLOTS_PARA_META)
    if Metrica.OCUPACAO in consulta.metricas:
        auxiliares += [Metrica.SLOTS_OCUPADOS, Metrica.SLOTS_ESCALADOS]
    calculadas = calcular_metricas(
        slots, dict.fromkeys([*consulta.metricas, *auxiliares]), consulta.slots_por_janela
    )
    abaixo = int(calculadas[Metrica.SLOTS_PARA_META]) > 0 if meta is not None else None
    razao = (
        (int(calculadas[Metrica.SLOTS_OCUPADOS]), int(calculadas[Metrica.SLOTS_ESCALADOS]))
        if Metrica.OCUPACAO in consulta.metricas
        else None
    )
    valores = {metrica: calculadas[metrica] for metrica in consulta.metricas}
    return valores, abaixo, razao


def gerar_relatorio(foto: FotografiaPeriodo, consulta: ConsultaRelatorio) -> ResultadoRelatorio:
    """Agrupa os slots da fotografia por `consulta.dimensoes` e calcula as métricas.

    Levanta `ConsultaInvalidaError` quando os parâmetros não fazem sentido.
    """
    consulta.validar()
    avisos: list[str] = []
    por_sala = bool({Dimensao.SALA, Dimensao.POSTO} & set(consulta.dimensoes))
    quer_fechados = Metrica.SLOTS_FECHADOS in consulta.metricas
    por_convenio = Dimensao.CONVENIO in consulta.dimensoes

    recorte: list[SlotFotografado] = []
    fora_da_grade = 0
    for slot in foto.slots:
        if not consulta.filtros.aceita(slot):
            continue
        if slot.estado is EstadoSlot.FECHADO and not quer_fechados:
            continue
        # Convênio só existe em slot ocupado: os demais não formam linha.
        if por_convenio and slot.convenio is None:
            continue
        if por_sala and slot.estado is EstadoSlot.OCUPADO and not slot.na_grade:
            fora_da_grade += slot.ocupacoes
            continue
        recorte.append(slot)
    if fora_da_grade:
        avisos.append(
            f"{fora_da_grade} slot(s) ocupado(s) fora da grade não entram na quebra por sala "
            "ou posto (sem escala para atribuir); possível inconsistência na planilha."
        )

    grupos: dict[tuple[ValorDimensao, ...], list[SlotFotografado]] = defaultdict(list)
    for slot in recorte:
        chave = tuple(_valor_dimensao(foto, slot, d) for d in consulta.dimensoes)
        grupos[chave].append(slot)

    meta = META_OCUPACAO_POR_SALA if _meta_se_aplica(consulta.metricas) else None
    linhas: list[LinhaRelatorio] = []
    for chave, slots in grupos.items():
        valores, abaixo, razao = _valores_do_grupo(slots, consulta, meta)
        linhas.append(
            LinhaRelatorio(
                chaves=dict(zip(consulta.dimensoes, chave, strict=True)),
                valores=valores,
                abaixo_da_meta=abaixo,
                razao_ocupacao=razao,
            )
        )

    linhas.sort(
        key=lambda linha: tuple(_chave_ordem(foto, d, linha.chaves[d]) for d in consulta.dimensoes)
    )
    if isinstance(consulta.ordenar_por, Metrica):
        metrica = consulta.ordenar_por
        linhas.sort(
            key=lambda linha: linha.valores[metrica],
            reverse=consulta.decrescente is not False,
        )
    elif isinstance(consulta.ordenar_por, Dimensao):
        dimensao = consulta.ordenar_por
        linhas.sort(
            key=lambda linha: _chave_ordem(foto, dimensao, linha.chaves[dimensao]),
            reverse=consulta.decrescente is True,
        )
    omitidas = 0
    if consulta.limite is not None and len(linhas) > consulta.limite:
        omitidas = len(linhas) - consulta.limite
        linhas = linhas[: consulta.limite]

    total, total_abaixo, total_razao = _valores_do_grupo(recorte, consulta, meta)
    if any(
        escalados and ocupados > escalados
        for ocupados, escalados in [
            linha.razao_ocupacao for linha in linhas if linha.razao_ocupacao is not None
        ]
        + ([total_razao] if total_razao is not None else [])
    ):
        avisos.append(AVISO_ACIMA_DE_CEM)

    return ResultadoRelatorio(
        inicio=foto.inicio,
        fim=foto.fim,
        dimensoes=consulta.dimensoes,
        metricas=consulta.metricas,
        linhas=linhas,
        total=total,
        total_abaixo_da_meta=total_abaixo,
        total_razao_ocupacao=total_razao,
        linhas_omitidas=omitidas,
        meta=meta,
        slots_por_janela=consulta.slots_por_janela,
        dias_consultados=foto.dias_consultados,
        dias_lidos=foto.dias_lidos,
        dias_nao_lidos=foto.dias_nao_lidos,
        avisos=avisos,
    )
