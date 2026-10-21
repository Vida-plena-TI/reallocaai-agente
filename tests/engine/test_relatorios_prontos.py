"""Testes dos relatórios nomeados sobre a semana fictícia de `agenda_clinica`."""

from datetime import time

import pytest

from app.domain import normalizar_id
from app.engine import Dimensao, FiltrosRelatorio, FotografiaPeriodo, Metrica, fotografar_periodo
from app.engine.relatorios_prontos import (
    SituacaoCapacidade,
    agenda_do_paciente,
    capacidade_por_especialidade,
    comparativo_dias,
    convenios,
    qualidade_agenda,
    ranking_ocupacao,
    salas_ociosas,
    vagas_livres,
)
from tests.support.agenda_clinica import ANA, BETO, PACIENTES, fonte_clinica
from tests.support.agenda_semanal import QUARTA, SABADO, SEGUNDA, SEMANA, SEXTA, TERCA

SEM_FILTRO = FiltrosRelatorio()


@pytest.fixture(scope="module")
def foto() -> FotografiaPeriodo:
    return fotografar_periodo(fonte_clinica(), SEGUNDA, SABADO)


def test_vagas_livres_em_faixas_e_resumo_por_dia_e_periodo(foto: FotografiaPeriodo) -> None:
    relatorio = vagas_livres(foto, FiltrosRelatorio(profissional_ids=frozenset({ANA.id})))

    segunda = [(f.inicio, f.fim, f.slots) for f in relatorio.faixas if f.data == SEGUNDA]
    assert segunda == [
        (time(7, 0), time(8, 0), 2),
        (time(9, 0), time(9, 30), 1),
        (time(10, 0), time(10, 30), 1),
        (time(11, 30), time(12, 0), 1),
    ]
    # Ana só tem manhã: uma linha por dia, de segunda a sexta.
    assert len(relatorio.resumo.linhas) == 5
    assert relatorio.resumo.total[Metrica.VAGAS_LIVRES] == sum(f.slots for f in relatorio.faixas)


@pytest.mark.parametrize(("slots", "cabem_na_quarta"), [(2, 5), (3, 3)])
def test_janelas_continuas_de_uma_hora_e_uma_hora_e_meia(
    foto: FotografiaPeriodo, slots: int, cabem_na_quarta: int
) -> None:
    relatorio = vagas_livres(
        foto, FiltrosRelatorio(profissional_ids=frozenset({ANA.id})), slots_por_janela=slots
    )

    assert all(faixa.slots >= slots for faixa in relatorio.faixas)
    quarta = [f for f in relatorio.faixas if f.data == QUARTA]
    assert [f.cabem for f in quarta] == [cabem_na_quarta]
    assert relatorio.resumo.total[Metrica.JANELAS_LIVRES] == sum(f.cabem for f in relatorio.faixas)


def test_ranking_de_ocupacao(foto: FotografiaPeriodo) -> None:
    ranking = ranking_ocupacao(foto, SEM_FILTRO)
    valores = [float(linha.valores[Metrica.OCUPACAO]) for linha in ranking.linhas]

    assert valores == sorted(valores, reverse=True)
    assert len(ranking.linhas) == 4

    piores = ranking_ocupacao(foto, SEM_FILTRO, decrescente=False, limite=1)
    assert piores.linhas[0].valores[Metrica.OCUPACAO] == min(valores)
    assert piores.linhas_omitidas == 3


def test_comparativo_dia_contra_dia(foto: FotografiaPeriodo) -> None:
    resultado = comparativo_dias(foto, SEM_FILTRO)

    assert [linha.chaves[Dimensao.DIA].id for linha in resultado.linhas] == [
        dia.isoformat() for dia in SEMANA
    ]
    # Na terça, Alfa passa por três profissionais e conta uma vez.
    terca = resultado.linhas[1].valores
    assert terca[Metrica.SESSOES] == 6 and terca[Metrica.PACIENTES_DISTINTOS] == 5


def test_convenios_e_aguardando_autorizacao(foto: FotografiaPeriodo) -> None:
    relatorio = convenios(foto, SEM_FILTRO)

    unimed = next(
        linha
        for linha in relatorio.por_convenio.linhas
        if linha.chaves[Dimensao.CONVENIO].id == "unimed"
    )
    # Alfa (5 sessões), Hotel (2) e Echo no sábado (1); Echo de terça está no grupo.
    assert unimed.valores[Metrica.SESSOES] == 8
    assert [
        (linha.chaves[Dimensao.DIA].id, linha.chaves[Dimensao.PROFISSIONAL].id)
        for linha in relatorio.aguardando.linhas
    ] == [(QUARTA.isoformat(), BETO.id), ("2026-10-01", ANA.id)]


def test_qualidade_da_agenda_buracos_e_fragmentacao(foto: FotografiaPeriodo) -> None:
    relatorio = qualidade_agenda(foto, SEM_FILTRO)
    ana_segunda = next(
        item
        for item in relatorio.por_dia
        if item.profissional_id == ANA.id and item.data == SEGUNDA
    )

    # Livres: 07:00-08:00 e 11:30 (pontas, não são buraco), 09:00 e 10:00 (buracos).
    assert ana_segunda.buracos == 2
    assert ana_segunda.slots_em_buracos == 2
    assert ana_segunda.slots_isolados == 3
    assert ana_segunda.faixas_livres == 4
    assert ana_segunda.maior_faixa_livre == 2

    ana = next(item for item in relatorio.por_profissional if item.profissional_id == ANA.id)
    assert ana.buracos == sum(
        item.buracos for item in relatorio.por_dia if item.profissional_id == ANA.id
    )
    # Ordenado por slots presos em buraco.
    presos = [item.slots_em_buracos for item in relatorio.por_profissional]
    assert presos == sorted(presos, reverse=True)


def test_salas_ociosas_da_menos_para_a_mais_ocupada(foto: FotografiaPeriodo) -> None:
    resultado = salas_ociosas(foto, SEM_FILTRO)
    valores = [float(linha.valores[Metrica.OCUPACAO]) for linha in resultado.linhas]

    assert valores == sorted(valores)
    assert Metrica.SLOTS_FECHADOS in resultado.total


def test_capacidade_por_especialidade(foto: FotografiaPeriodo) -> None:
    relatorio = capacidade_por_especialidade(foto, SEM_FILTRO)

    assert set(relatorio.situacoes) == {
        "psicologia",
        "fonoaudiologia",
        "terapia_ocupacional",
    }
    # Semana fictícia é ociosa: tudo abaixo da meta.
    assert set(relatorio.situacoes.values()) == {SituacaoCapacidade.SOBRANDO}


def test_capacidade_no_limite_quando_quase_sem_vaga() -> None:
    from app.domain import Especialidade, Profissional
    from tests.support.agenda_semanal import atendimentos, grade, horas_da_manha
    from tests.support.fake_schedule_data_source import FakeScheduleDataSource

    musico = Profissional(id="m", nome="M", especialidade=Especialidade.MUSICOTERAPIA)
    horas = horas_da_manha(SEGUNDA)
    fonte = FakeScheduleDataSource(
        profissionais={SEGUNDA: [musico]},
        grade={SEGUNDA: grade(SEGUNDA, horas, musico)},
        atendimentos={SEGUNDA: atendimentos(SEGUNDA, horas, musico)},
    )

    relatorio = capacidade_por_especialidade(
        fotografar_periodo(fonte, SEGUNDA, SEGUNDA), SEM_FILTRO
    )

    assert relatorio.situacoes == {"musicoterapia": SituacaoCapacidade.NO_LIMITE}


def test_agenda_do_paciente_com_intervalos_entre_terapias(foto: FotografiaPeriodo) -> None:
    alfa = normalizar_id(PACIENTES["A"][0])

    agenda = agenda_do_paciente(foto, alfa)

    assert agenda is not None
    assert agenda.nome == PACIENTES["A"][0]
    assert [dia.data for dia in agenda.dias] == [SEGUNDA, TERCA, SEXTA]
    terca = agenda.dias[1]
    assert [(s.inicio, s.fim, s.profissional_nome) for s in terca.sessoes] == [
        (time(8, 0), time(9, 0), "Ana Lima"),
        (time(13, 0), time(13, 30), "Beto Reis"),
        (time(14, 0), time(14, 30), "Cau Melo"),
    ]
    assert [(i.minutos, i.minutos_de_pausa) for i in terca.intervalos] == [(240, 60), (30, 0)]
    assert agenda.total_sessoes == 5
    assert agenda.minutos_em_intervalos == 180 + 30


def test_agenda_de_paciente_que_nao_aparece_e_none(foto: FotografiaPeriodo) -> None:
    assert agenda_do_paciente(foto, "ninguem-com-esse-nome") is None
