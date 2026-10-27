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
    hora: time, sala: str, profissional: str, especialidade: Especialidade
) -> SlotFotografado:
    return SlotFotografado(
        data=QUARTA,
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
    return FotografiaPeriodo(
        inicio=QUARTA,
        fim=QUARTA,
        dias_consultados=[QUARTA],
        dias_lidos=[QUARTA],
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
