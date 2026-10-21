"""Os resultados das tools antigas, montados a partir da fotografia do período.

As tools `consultar_disponibilidade`, `consultar_ocupacao`,
`consultar_ocupacao_profissional` e `consultar_pacientes_por_profissional`
devolvem estruturas que o modelo e o front já conhecem
(`RelatorioOcupacaoDoDia`, `OcupacaoSemanalProfissional`, `CargaProfissionais`,
`SlotDisponivel`). Este módulo produz exatamente essas estruturas a partir de
uma `FotografiaPeriodo` — a agenda lida uma vez e já classificada por slot — e
de `calcular_metricas` do motor genérico, sem reler a fonte nem refazer a
atribuição de cada atendimento ao seu posto.

Onde o cálculo antigo tinha uma regra própria, ela é reproduzida aqui de
propósito (o golden de `tests/ai/golden/` prova a equivalência):

- ocupação do dia **por sala** só conta atendimento que caiu numa janela da
  grade; o atendimento fora da grade entra na ocupação do profissional e da
  especialidade, mas não na da sala;
- profissional listado no dia sem grade nenhuma aparece com 0 de 0, e a
  especialidade dele também;
- disponibilidade casa o atendimento com a vaga pelo posto exato, como
  `listar_disponibilidade` (e o encaixe): um atendimento lançado noutro posto
  ocupa a vaga na ocupação, mas não some da lista de vagas.
"""

from collections import defaultdict
from collections.abc import Iterable
from datetime import date

from app.domain import Especialidade, Slot, normalizar_id
from app.domain.constants import FIM_PAUSA, INICIO_PAUSA
from app.engine.carga_profissionais import (
    CargaDiaProfissional,
    CargaDoDia,
    CargaProfissionais,
    CargaProfissional,
)
from app.engine.disponibilidade import SlotDisponivel
from app.engine.fotografia import FotografiaPeriodo, SlotFotografado
from app.engine.ocupacao import OcupacaoAgregada, OcupacaoProfissional, RelatorioOcupacaoDoDia
from app.engine.ocupacao_profissional import (
    OcupacaoDiaProfissional,
    OcupacaoSalaPosto,
    OcupacaoSemanalProfissional,
)
from app.engine.relatorio_generico import Metrica, calcular_metricas

_ESCALADOS_OCUPADOS = (Metrica.SLOTS_ESCALADOS, Metrica.SLOTS_OCUPADOS)
_CARGA = (Metrica.PACIENTES_DISTINTOS, Metrica.SESSOES, Metrica.SLOTS_OCUPADOS)


def _escalados_ocupados(slots: list[SlotFotografado]) -> tuple[int, int]:
    valores = calcular_metricas(slots, _ESCALADOS_OCUPADOS)
    return int(valores[Metrica.SLOTS_ESCALADOS]), int(valores[Metrica.SLOTS_OCUPADOS])


def _da_profissional(slots: Iterable[SlotFotografado]) -> dict[str, list[SlotFotografado]]:
    """Slots com profissional (escalado ou atendendo), agrupados por profissional."""
    por_profissional: dict[str, list[SlotFotografado]] = defaultdict(list)
    for slot in slots:
        if slot.profissional_id is not None:
            por_profissional[slot.profissional_id].append(slot)
    return por_profissional


def _nome_do_id(profissional_id: str) -> str:
    """Nome legível a partir do id, para quem não está em `listar_profissionais`."""
    return " ".join(parte.capitalize() for parte in profissional_id.split("-"))


def ocupacao_do_dia(foto: FotografiaPeriodo, dia: date) -> RelatorioOcupacaoDoDia:
    """`RelatorioOcupacaoDoDia` de `dia`, a partir de uma fotografia que o contém.

    Os profissionais listados sem grade vêm de `foto.profissionais`: numa
    fotografia de um dia só, são exatamente os de `listar_profissionais` dele.
    """
    do_dia = [slot for slot in foto.slots if slot.data == dia]
    por_profissional = _da_profissional(do_dia)
    escalados_no_dia = {
        slot.profissional_id for slot in do_dia if slot.na_grade and slot.profissional_id
    }
    # Atendimento de quem não tem grade nem está listado não entrava no
    # relatório antigo (nem na especialidade); continua de fora.
    ids = sorted(escalados_no_dia | set(foto.profissionais))

    ocupacoes: list[OcupacaoProfissional] = []
    for profissional_id in ids:
        slots = por_profissional.get(profissional_id, [])
        escalados, ocupados = _escalados_ocupados(slots)
        profissional = foto.profissionais.get(profissional_id)
        ocupacoes.append(
            OcupacaoProfissional(
                profissional_id=profissional_id,
                nome=profissional.nome if profissional is not None else profissional_id,
                especialidade=_especialidade(profissional_id, foto, slots),
                slots_escalados=escalados,
                slots_ocupados=ocupados,
            )
        )

    na_grade_por_sala: dict[str, list[SlotFotografado]] = defaultdict(list)
    for slot in do_dia:
        if slot.na_grade:
            na_grade_por_sala[slot.sala_id].append(slot)
    detalhamento_por_sala = {
        sala_id: OcupacaoAgregada(
            slots_escalados=_escalados_ocupados(slots)[0],
            slots_ocupados=_escalados_ocupados(slots)[1],
        )
        for sala_id, slots in na_grade_por_sala.items()
    }
    return RelatorioOcupacaoDoDia(
        data=dia, por_profissional=ocupacoes, detalhamento_por_sala=detalhamento_por_sala
    )


def _especialidade(
    profissional_id: str, foto: FotografiaPeriodo, slots: list[SlotFotografado]
) -> Especialidade:
    profissional = foto.profissionais.get(profissional_id)
    if profissional is not None:
        return profissional.especialidade
    return next(slot.especialidade for slot in slots if slot.especialidade is not None)


def ocupacao_semanal_profissional(
    foto: FotografiaPeriodo, profissional_id: str
) -> OcupacaoSemanalProfissional:
    """`OcupacaoSemanalProfissional` de uma fotografia da semana (segunda a sábado)."""
    da_profissional = _da_profissional(foto.slots).get(profissional_id, [])
    por_dia: dict[date, list[SlotFotografado]] = defaultdict(list)
    for slot in da_profissional:
        por_dia[slot.data].append(slot)

    dias: list[OcupacaoDiaProfissional] = []
    dias_sem_agenda: list[date] = []
    for dia in foto.dias_lidos:
        slots = por_dia.get(dia)
        if not slots:
            dias_sem_agenda.append(dia)
            continue
        dias.append(_ocupacao_do_dia_da_profissional(foto, dia, slots))

    profissional = foto.profissionais.get(profissional_id)
    especialidade = (
        profissional.especialidade
        if profissional is not None
        else next((s.especialidade for s in da_profissional if s.na_grade), None)
    )
    return OcupacaoSemanalProfissional(
        profissional_id=profissional_id,
        nome=profissional.nome if profissional is not None else profissional_id,
        especialidade=especialidade,
        semana_inicio=foto.inicio,
        semana_fim=foto.fim,
        slots_escalados=sum(dia.slots_escalados for dia in dias),
        slots_ocupados=sum(dia.slots_ocupados for dia in dias),
        dias=dias,
        dias_sem_agenda=dias_sem_agenda,
        dias_com_falha=list(foto.dias_nao_lidos),
    )


def _ocupacao_do_dia_da_profissional(
    foto: FotografiaPeriodo, dia: date, slots: list[SlotFotografado]
) -> OcupacaoDiaProfissional:
    escalados, ocupados = _escalados_ocupados(slots)
    manha = [slot for slot in slots if slot.hora_inicio < INICIO_PAUSA]
    tarde = [slot for slot in slots if slot.hora_inicio >= FIM_PAUSA]
    manha_escalados, manha_ocupados = _escalados_ocupados(manha)
    tarde_escalados, tarde_ocupados = _escalados_ocupados(tarde)

    por_posto: dict[tuple[str, int], list[SlotFotografado]] = defaultdict(list)
    for slot in slots:
        por_posto[(slot.sala_id, slot.indice_posto)].append(slot)
    por_sala_posto = [
        OcupacaoSalaPosto(
            sala_id=sala_id,
            sala_nome=(
                foto.nome_sala(sala_id) if sala_id in foto.salas_por_dia.get(dia, ()) else sala_id
            ),
            indice_posto=indice_posto,
            slots_escalados=_escalados_ocupados(do_posto)[0],
            slots_ocupados=_escalados_ocupados(do_posto)[1],
        )
        for (sala_id, indice_posto), do_posto in sorted(por_posto.items())
    ]
    return OcupacaoDiaProfissional(
        data=dia,
        slots_escalados=escalados,
        slots_ocupados=ocupados,
        manha_escalados=manha_escalados,
        manha_ocupados=manha_ocupados,
        tarde_escalados=tarde_escalados,
        tarde_ocupados=tarde_ocupados,
        por_sala_posto=por_sala_posto,
        slots_sem_grade=_escalados_ocupados([s for s in slots if not s.na_grade])[1],
    )


def carga_profissionais(foto: FotografiaPeriodo, dias: Iterable[date]) -> CargaProfissionais:
    """`CargaProfissionais` dos `dias` (domingo e repetidos descartados) de uma fotografia.

    Se nenhum dos dias pôde ser lido, levanta `RuntimeError` com a mensagem
    do último erro de leitura, como a leitura antiga fazia.
    """
    janela = sorted({dia for dia in dias if dia in foto.dias_consultados})
    lidos = [dia for dia in janela if dia in foto.dias_lidos]
    com_falha = [dia for dia in janela if dia in foto.dias_nao_lidos]
    if not lidos and com_falha:
        raise RuntimeError(foto.erros_de_leitura[com_falha[-1]])

    do_periodo = [slot for slot in foto.slots if slot.data in lidos]
    por_profissional = _da_profissional(do_periodo)

    profissionais: list[CargaProfissional] = []
    for profissional_id, slots in por_profissional.items():
        por_dia: dict[date, list[SlotFotografado]] = defaultdict(list)
        for slot in slots:
            por_dia[slot.data].append(slot)
        cargas = []
        for dia in lidos:
            if dia not in por_dia:
                continue
            valores = calcular_metricas(por_dia[dia], _CARGA)
            cargas.append(
                CargaDiaProfissional(
                    data=dia,
                    pacientes=int(valores[Metrica.PACIENTES_DISTINTOS]),
                    sessoes=int(valores[Metrica.SESSOES]),
                    slots_ocupados=int(valores[Metrica.SLOTS_OCUPADOS]),
                )
            )
        # Nome e especialidade vêm só das listas dos dias consultados.
        listado = any(profissional_id in foto.profissionais_por_dia.get(dia, ()) for dia in lidos)
        profissional = foto.profissionais.get(profissional_id) if listado else None
        especialidade = (
            profissional.especialidade
            if profissional is not None
            else next((s.especialidade for s in slots if s.especialidade is not None), None)
        )
        nome = profissional.nome if profissional is not None else _nome_do_id(profissional_id)
        profissionais.append(
            CargaProfissional(
                profissional_id=profissional_id,
                nome=nome,
                especialidade=especialidade,
                dias=cargas,
                dias_sem_agenda=[dia for dia in lidos if dia not in por_dia],
                dias_com_falha=com_falha,
                media_pacientes_por_dia=(
                    sum(carga.pacientes for carga in cargas) / len(cargas) if cargas else None
                ),
                pacientes_distintos_semana=int(
                    calcular_metricas(slots, (Metrica.PACIENTES_DISTINTOS,))[
                        Metrica.PACIENTES_DISTINTOS
                    ]
                ),
            )
        )
    profissionais.sort(key=lambda carga: _chave_ordenacao(carga.especialidade, carga.nome))

    por_dia_clinica = [
        CargaDoDia(
            data=dia,
            pacientes_distintos_clinica=int(
                calcular_metricas(
                    [slot for slot in do_periodo if slot.data == dia],
                    (Metrica.PACIENTES_DISTINTOS,),
                )[Metrica.PACIENTES_DISTINTOS]
            ),
        )
        for dia in lidos
    ]
    return CargaProfissionais(
        dias=janela, profissionais=profissionais, por_dia=por_dia_clinica, dias_com_falha=com_falha
    )


def _chave_ordenacao(especialidade: Especialidade | None, nome: str) -> tuple[bool, str, str]:
    """Especialidade e depois nome, sem acento e sem caixa; sem especialidade vai ao fim."""
    rotulo = especialidade.value if especialidade is not None else ""
    return especialidade is None, normalizar_id(rotulo), normalizar_id(nome)


def disponibilidade(
    foto: FotografiaPeriodo,
    dia: date,
    *,
    especialidade: Especialidade | None = None,
    profissional_id: str | None = None,
) -> list[SlotDisponivel]:
    """Vagas de `dia`, com a regra de `listar_disponibilidade` (posto exato).

    A ordem é a de `listar_disponibilidade`: horário, sala e posto.
    """
    ocupados = {
        (atendimento.profissional_id, atendimento.sala_id, atendimento.indice_posto, slot)
        for atendimento in foto.atendimentos
        for slot in atendimento.slots
        if slot.data == dia
    }
    vagas = [
        SlotDisponivel(
            slot=Slot(data=dia, hora_inicio=slot.hora_inicio),
            sala_id=slot.sala_id,
            profissional_id=slot.profissional_id,
            especialidade=slot.especialidade,
            indice_posto=slot.indice_posto,
        )
        for slot in foto.slots
        if slot.data == dia
        and slot.na_grade
        and slot.profissional_id is not None
        and slot.especialidade is not None
        and (especialidade is None or slot.especialidade == especialidade)
        and (profissional_id is None or slot.profissional_id == profissional_id)
        and (
            slot.profissional_id,
            slot.sala_id,
            slot.indice_posto,
            Slot(data=dia, hora_inicio=slot.hora_inicio),
        )
        not in ocupados
    ]
    return sorted(vagas, key=lambda vaga: (vaga.slot, vaga.sala_id, vaga.indice_posto))
