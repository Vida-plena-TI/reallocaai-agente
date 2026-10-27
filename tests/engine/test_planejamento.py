"""Motor do plano semanal (`app.engine.planejamento`).

Os casos de quantas especialidades cabem usam a semana fictícia de
`tests/casos_reais/planilha.py`; os desempates usam fotografias montadas à mão,
com só os slots que importam.
"""

from datetime import date, time
from itertools import combinations
from typing import cast

import gspread
import pytest

from app.data_sources.google_sheets import GoogleSheetsDataSource
from app.domain import Especialidade
from app.engine.fotografia import (
    EstadoSlot,
    FotografiaPeriodo,
    SlotFotografado,
    fotografar_periodo,
    periodo_do_horario,
)
from app.engine.planejamento import (
    MAX_ALTERNATIVAS,
    ItemPlano,
    PedidoPlano,
    PlanoSemanal,
    SessaoPlano,
    maior_sequencia,
    planejar_semana,
)
from tests.casos_reais.planilha import PlanilhaContada

SEGUNDA = date(2026, 9, 28)
TERCA = date(2026, 9, 29)
QUARTA = date(2026, 9, 30)
QUINTA = date(2026, 10, 1)
SABADO = date(2026, 10, 3)

FONO = Especialidade.FONOAUDIOLOGIA
PSICOMOTRICIDADE = Especialidade.PSICOMOTRICIDADE
PSICOLOGIA = Especialidade.PSICOLOGIA
TO = Especialidade.TERAPIA_OCUPACIONAL
QUATRO = (FONO, PSICOMOTRICIDADE, PSICOLOGIA, TO)


@pytest.fixture(scope="module")
def semana() -> FotografiaPeriodo:
    fonte = GoogleSheetsDataSource(cast(gspread.Spreadsheet, PlanilhaContada()))
    return fotografar_periodo(fonte, SEGUNDA, SABADO)


def _pedido(
    dias: tuple[date, ...],
    inicio: time,
    fim: time,
    especialidades: tuple[Especialidade, ...] = QUATRO,
    *,
    slots: int = 1,
    preferidos: dict[Especialidade, str] | None = None,
    salas_evitadas: frozenset[str] = frozenset(),
    seguidas: bool = False,
) -> PedidoPlano:
    return PedidoPlano(
        dias=dias,
        inicio=inicio,
        fim=fim,
        itens=tuple(
            ItemPlano(
                especialidade=e,
                duracao_em_slots=slots,
                profissional_preferido=(preferidos or {}).get(e),
            )
            for e in especialidades
        ),
        salas_evitadas=salas_evitadas,
        seguidas=seguidas,
    )


def _sem_sobreposicao(plano: PlanoSemanal) -> bool:
    return all(
        a.fim <= b.inicio or b.fim <= a.inicio
        for dia in plano.dias
        for a, b in combinations(dia.sessoes, 2)
    )


# --- semana fictícia ---------------------------------------------------------


def test_caso_de_referencia_quarta_e_quinta_das_10h_as_12h(semana: FotografiaPeriodo) -> None:
    """O pedido real: TO lotada das 10h às 12h nos dois dias -> "não coube" com alternativas."""
    plano = planejar_semana(semana, _pedido((QUARTA, QUINTA), time(10), time(12)))

    assert _sem_sobreposicao(plano)
    for dia in plano.dias:
        assert all(time(10) <= s.inicio and s.fim <= time(12) for s in dia.sessoes)
        nao_couberam = {n.especialidade: n for n in dia.nao_couberam}
        assert TO in nao_couberam
        assert TO not in {s.especialidade for s in dia.sessoes}
        alternativas = nao_couberam[TO].alternativas
        assert 1 <= len(alternativas) <= MAX_ALTERNATIVAS
        assert all(a.especialidade is TO for a in alternativas)
        # Fora da janela, e nunca em cima de uma sessão do plano.
        assert all(
            a.data != dia.data or a.fim <= time(10) or a.inicio >= time(12) for a in alternativas
        )
    quarta = plano.dias[0]
    assert {s.especialidade for s in quarta.sessoes} == {FONO, PSICOMOTRICIDADE, PSICOLOGIA}
    # Sessões não precisam ser seguidas, mas o plano é o mais compacto possível.
    assert [s.inicio for s in quarta.sessoes] == [time(10), time(10, 30), time(11)]
    # Mesmo dia primeiro: a TO da quarta tem alternativas na própria quarta.
    assert quarta.nao_couberam[0].alternativas[0].data == QUARTA
    assert not plano.completo


def test_as_quatro_cabem(semana: FotografiaPeriodo) -> None:
    plano = planejar_semana(semana, _pedido((SEGUNDA,), time(9), time(11)))

    (dia,) = plano.dias
    assert {s.especialidade for s in dia.sessoes} == set(QUATRO)
    assert dia.nao_couberam == []
    assert _sem_sobreposicao(plano)
    assert plano.completo


def test_so_uma_cabe(semana: FotografiaPeriodo) -> None:
    """Terça das 8h às 10h: só a fono tem vaga."""
    plano = planejar_semana(semana, _pedido((TERCA,), time(8), time(10)))

    (dia,) = plano.dias
    assert [s.especialidade for s in dia.sessoes] == [FONO]
    assert [n.especialidade for n in dia.nao_couberam] == [PSICOMOTRICIDADE, PSICOLOGIA, TO]
    assert all(n.alternativas for n in dia.nao_couberam)


def test_nenhuma_cabe(semana: FotografiaPeriodo) -> None:
    """Terça das 9h às 10h: tudo ocupado."""
    plano = planejar_semana(semana, _pedido((TERCA,), time(9), time(10)))

    (dia,) = plano.dias
    assert dia.sessoes == []
    assert [n.especialidade for n in dia.nao_couberam] == list(QUATRO)


def test_sessao_mais_longa_usa_slots_seguidos_do_mesmo_posto(semana: FotografiaPeriodo) -> None:
    plano = planejar_semana(semana, _pedido((QUARTA,), time(7), time(12), (FONO,), slots=2))

    (sessao,) = plano.dias[0].sessoes
    assert (sessao.inicio, sessao.fim) == (time(7), time(8))


def test_salas_evitadas_ficam_de_fora(semana: FotografiaPeriodo) -> None:
    livre = planejar_semana(semana, _pedido((QUARTA,), time(10), time(12), (FONO,)))
    evitando = planejar_semana(
        semana,
        _pedido(
            (QUARTA,),
            time(10),
            time(12),
            (FONO,),
            salas_evitadas=frozenset({livre.dias[0].sessoes[0].sala_id}),
        ),
    )

    assert evitando.dias[0].sessoes
    assert evitando.dias[0].sessoes[0].sala_id != livre.dias[0].sessoes[0].sala_id


# --- desempates (fotografia montada à mão) -----------------------------------


def _livre(
    hora: time,
    sala: str,
    profissional: str,
    especialidade: Especialidade,
    dia: date = QUARTA,
) -> SlotFotografado:
    return SlotFotografado(
        data=dia,
        hora_inicio=hora,
        periodo=periodo_do_horario(hora),
        estado=EstadoSlot.LIVRE,
        sala_id=sala,
        indice_posto=0,
        profissional_id=profissional,
        especialidade=especialidade,
        na_grade=True,
        ocupacoes=0,
    )


def _foto(*slots: SlotFotografado) -> FotografiaPeriodo:
    dias = sorted({slot.data for slot in slots} | {QUARTA})
    return FotografiaPeriodo(
        inicio=dias[0],
        fim=dias[-1],
        dias_consultados=dias,
        dias_lidos=dias,
        dias_nao_lidos=[],
        slots=list(slots),
        profissionais={},
        salas={},
    )


def _plano_da_quarta(
    foto: FotografiaPeriodo,
    especialidades: tuple[Especialidade, ...],
    preferidos: dict[Especialidade, str] | None = None,
) -> list[tuple[Especialidade, time, str, str]]:
    pedido = _pedido((QUARTA,), time(7), time(12), especialidades, preferidos=preferidos)
    (dia,) = planejar_semana(foto, pedido).dias
    return [(s.especialidade, s.inicio, s.sala_id, s.profissional_id) for s in dia.sessoes]


def test_desempate_prefere_o_mais_compacto_ao_mais_cedo() -> None:
    foto = _foto(
        _livre(time(7), "sala-1", "ana", FONO),
        _livre(time(10), "sala-1", "ana", FONO),
        _livre(time(10, 30), "sala-1", "bia", PSICOLOGIA),
    )

    assert _plano_da_quarta(foto, (FONO, PSICOLOGIA)) == [
        (FONO, time(10), "sala-1", "ana"),
        (PSICOLOGIA, time(10, 30), "sala-1", "bia"),
    ]


def test_desempate_prefere_menos_trocas_de_sala_ao_mais_cedo() -> None:
    """Mesmo intervalo (1 h) nas duas opções; a de 11:00 não troca de sala."""
    foto = _foto(
        _livre(time(10), "sala-1", "ana", FONO),
        _livre(time(11), "sala-2", "caio", FONO),
        _livre(time(10, 30), "sala-2", "bia", PSICOLOGIA),
    )

    assert _plano_da_quarta(foto, (FONO, PSICOLOGIA)) == [
        (PSICOLOGIA, time(10, 30), "sala-2", "bia"),
        (FONO, time(11), "sala-2", "caio"),
    ]


def test_desempate_mesma_hora_escolhe_a_sala_que_evita_troca() -> None:
    foto = _foto(
        _livre(time(10), "sala-1", "ana", FONO),
        _livre(time(10), "sala-2", "caio", FONO),
        _livre(time(10, 30), "sala-2", "bia", PSICOLOGIA),
    )

    assert _plano_da_quarta(foto, (FONO, PSICOLOGIA))[0] == (FONO, time(10), "sala-2", "caio")


def test_desempate_final_e_o_horario_mais_cedo() -> None:
    foto = _foto(
        _livre(time(10), "sala-1", "ana", FONO),
        _livre(time(11), "sala-1", "ana", FONO),
    )

    assert _plano_da_quarta(foto, (FONO,)) == [(FONO, time(10), "sala-1", "ana")]


def test_maximiza_especialidades_antes_de_tudo() -> None:
    """Fono às 10:00 deixaria a psicologia de fora; fono às 10:30 atende as duas."""
    foto = _foto(
        _livre(time(10), "sala-1", "ana", FONO),
        _livre(time(10, 30), "sala-1", "ana", FONO),
        _livre(time(10), "sala-2", "bia", PSICOLOGIA),
    )

    assert _plano_da_quarta(foto, (FONO, PSICOLOGIA)) == [
        (PSICOLOGIA, time(10), "sala-2", "bia"),
        (FONO, time(10, 30), "sala-1", "ana"),
    ]


def test_profissional_preferido_tem_prioridade_mas_nao_e_exigencia() -> None:
    foto = _foto(
        _livre(time(10), "sala-1", "ana", FONO),
        _livre(time(10), "sala-2", "caio", FONO),
    )

    assert _plano_da_quarta(foto, (FONO,), preferidos={FONO: "caio"})[0][3] == "caio"
    assert _plano_da_quarta(foto, (FONO,), preferidos={FONO: "ninguem"})[0][3] == "ana"


def test_alternativas_ficam_fora_do_plano_e_sao_no_maximo_tres() -> None:
    foto = _foto(
        _livre(time(10), "sala-1", "ana", FONO),
        _livre(time(10), "sala-2", "bia", TO),
        *(_livre(time(h, m), "sala-2", "bia", TO) for h in (7, 8, 13) for m in (0, 30)),
    )
    pedido = _pedido((QUARTA,), time(10), time(10, 30), (FONO, TO))
    (dia,) = planejar_semana(foto, pedido).dias

    (nao_coube,) = dia.nao_couberam
    assert len(nao_coube.alternativas) == MAX_ALTERNATIVAS
    # A TO das 10:00 cruzaria a fono do plano; as alternativas são as mais perto da janela.
    assert [a.inicio for a in nao_coube.alternativas] == [time(8, 30), time(8), time(7, 30)]


# --- alternativas: só dias pedidos, sem repetição ------------------------------


def _chave(sessao: SessaoPlano) -> tuple[date, time, str]:
    return (sessao.data, sessao.inicio, sessao.sala_id)


def test_alternativas_so_nos_dias_pedidos_e_sem_repeticao() -> None:
    """TO lotada das 10h às 11h na quarta e na quinta; a segunda (não pedida) tem
    vaga às 10:00, mais perto da janela que qualquer outra, e não entra."""
    foto = _foto(
        _livre(time(10), "sala-1", "ana", FONO, QUARTA),
        _livre(time(10), "sala-1", "ana", FONO, QUINTA),
        _livre(time(8), "sala-2", "bia", TO, QUARTA),
        *(_livre(time(h), "sala-2", "bia", TO, QUINTA) for h in (7, 8, 9, 13)),
        *(_livre(time(h), "sala-2", "bia", TO, SEGUNDA) for h in (10, 11)),
    )
    pedido = _pedido((QUARTA, QUINTA), time(10), time(11), (FONO, TO))
    plano = planejar_semana(foto, pedido)

    quarta, quinta = (dia.nao_couberam[0] for dia in plano.dias)
    todas = [*quarta.alternativas, *quinta.alternativas]
    assert {a.data for a in todas} <= {QUARTA, QUINTA}
    assert len({_chave(a) for a in todas}) == len(todas)
    # Quinta: as 3 do próprio dia, mais perto da janela primeiro.
    assert [(a.data, a.inicio) for a in quinta.alternativas] == [
        (QUINTA, time(9)),
        (QUINTA, time(8)),
        (QUINTA, time(13)),
    ]
    # Quarta: a do próprio dia primeiro; depois a sobra da quinta (7:00).
    assert [(a.data, a.inicio) for a in quarta.alternativas] == [
        (QUARTA, time(8)),
        (QUINTA, time(7)),
    ]
    # Faltou uma na quarta e a segunda tem TO: avisa, sem pôr na tabela.
    assert quarta.ha_em_outros_dias
    assert not quinta.ha_em_outros_dias


def test_sem_outros_dias_nao_avisa() -> None:
    foto = _foto(_livre(time(10), "sala-1", "ana", FONO), _livre(time(8), "sala-2", "bia", TO))
    pedido = _pedido((QUARTA,), time(10), time(11), (FONO, TO))
    ((nao_coube,),) = (dia.nao_couberam for dia in planejar_semana(foto, pedido).dias)

    assert [a.inicio for a in nao_coube.alternativas] == [time(8)]
    assert not nao_coube.ha_em_outros_dias


def test_dia_em_que_a_especialidade_coube_nao_da_alternativa() -> None:
    """TO coube na quarta: uma segunda TO na quarta não serve de alternativa à da quinta."""
    foto = _foto(
        _livre(time(10), "sala-2", "bia", TO, QUARTA),
        _livre(time(8), "sala-2", "bia", TO, QUARTA),
    )
    pedido = _pedido((QUARTA, QUINTA), time(10), time(11), (TO,))
    quarta, quinta = planejar_semana(foto, pedido).dias

    assert [s.inicio for s in quarta.sessoes] == [time(10)]
    (nao_coube,) = quinta.nao_couberam
    assert nao_coube.alternativas == []
    assert not nao_coube.ha_em_outros_dias


def test_semana_ficticia_alternativas_nunca_repetem(semana: FotografiaPeriodo) -> None:
    plano = planejar_semana(semana, _pedido((QUARTA, QUINTA), time(10), time(12)))

    todas = [a for dia in plano.dias for n in dia.nao_couberam for a in n.alternativas]
    assert todas
    assert {a.data for a in todas} <= {QUARTA, QUINTA}
    assert len({_chave(a) for a in todas}) == len(todas)


# --- seguidas -------------------------------------------------------------------


def test_seguidas_escolhe_a_maior_sequencia_consecutiva() -> None:
    """Sem `seguidas` cabem as três (com buraco); seguidas, só fono e psicologia."""
    foto = _foto(
        _livre(time(10), "sala-1", "ana", FONO),
        _livre(time(10, 30), "sala-2", "bia", PSICOLOGIA),
        _livre(time(11, 30), "sala-3", "caio", TO),
    )
    comum = _pedido((QUARTA,), time(10), time(12), (FONO, PSICOLOGIA, TO))
    seguidas = _pedido((QUARTA,), time(10), time(12), (FONO, PSICOLOGIA, TO), seguidas=True)

    (dia_comum,) = planejar_semana(foto, comum).dias
    (dia,) = planejar_semana(foto, seguidas).dias

    assert len(dia_comum.sessoes) == 3
    assert [(s.especialidade, s.inicio) for s in dia.sessoes] == [
        (FONO, time(10)),
        (PSICOLOGIA, time(10, 30)),
    ]
    assert maior_sequencia(dia.sessoes) == dia.sessoes
    # Fora da sequência: sem alternativas (não respondem ao pedido).
    (nao_coube,) = dia.nao_couberam
    assert nao_coube.especialidade is TO and nao_coube.alternativas == []


def test_seguidas_na_semana_ficticia(semana: FotografiaPeriodo) -> None:
    plano = planejar_semana(semana, _pedido((QUARTA, QUINTA), time(10), time(12), seguidas=True))

    for dia in plano.dias:
        assert maior_sequencia(dia.sessoes) == dia.sessoes
        assert all(n.alternativas == [] for n in dia.nao_couberam)
    assert [len(dia.sessoes) for dia in plano.dias] == [3, 2]


def test_maior_sequencia() -> None:
    def sessao(inicio: time, fim: time) -> SessaoPlano:
        return SessaoPlano(
            data=QUARTA,
            especialidade=FONO,
            inicio=inicio,
            fim=fim,
            profissional_id="ana",
            sala_id="sala-1",
            indice_posto=0,
        )

    a, b = sessao(time(8), time(8, 30)), sessao(time(9), time(9, 30))
    c, d = sessao(time(9, 30), time(10)), sessao(time(10), time(10, 30))

    assert maior_sequencia([]) == []
    assert maior_sequencia([a]) == [a]
    assert maior_sequencia([a, b, c, d]) == [b, c, d]
