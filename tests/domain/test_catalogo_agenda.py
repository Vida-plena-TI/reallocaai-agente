"""Catálogo da agenda: derivação pura (domínio), leitura paralela e cache (`app.ai.catalogo`).

A semana é a fictícia de `tests/casos_reais/planilha.py`, lida pelo caminho de
produção (`GoogleSheetsDataSource` e parser). O esperado foi conferido à mão
em `AGENDA` e `PROFISSIONAIS` daquele arquivo.
"""

from datetime import date, time
from typing import cast

import gspread
import pytest

from app.ai.catalogo import CacheCatalogo, CatalogoIndisponivelError, ler_catalogo
from app.data_sources.google_sheets import GoogleSheetsDataSource
from app.domain import EntradaGrade, Especialidade, Profissional, Sala, Slot
from app.domain.catalogo_agenda import CatalogoAgenda, DiaDoCatalogo, montar_catalogo
from tests.casos_reais.planilha import PlanilhaContada
from tests.support.fake_schedule_data_source import FakeScheduleDataSource

SEGUNDA = date(2026, 9, 28)
TERCA = date(2026, 9, 29)
QUARTA = date(2026, 9, 30)
QUINTA = date(2026, 10, 1)
SEXTA = date(2026, 10, 2)
SABADO = date(2026, 10, 3)


def _fonte() -> tuple[GoogleSheetsDataSource, PlanilhaContada]:
    planilha = PlanilhaContada()
    return GoogleSheetsDataSource(cast(gspread.Spreadsheet, planilha)), planilha


@pytest.fixture(scope="module")
def catalogo() -> CatalogoAgenda:
    fonte, _ = _fonte()
    return ler_catalogo(fonte, QUARTA)


def test_uma_leitura_da_planilha_para_a_semana_inteira() -> None:
    fonte, planilha = _fonte()
    ler_catalogo(fonte, QUARTA)
    assert planilha.chamadas == 1


def test_especialidades_e_quem_atende_cada_uma(catalogo: CatalogoAgenda) -> None:
    assert catalogo.especialidades == {
        Especialidade.TERAPIA_OCUPACIONAL: ("assizely",),
        Especialidade.FONOAUDIOLOGIA: ("bia", "elo"),
        Especialidade.PSICOLOGIA: ("carol",),
        Especialidade.PSICOMOTRICIDADE: ("duda",),
    }


def test_dias_turnos_salas_e_postos(catalogo: CatalogoAgenda) -> None:
    elo = catalogo.profissionais["elo"]
    assert elo.nome == "Elo"
    assert elo.primeiro_nome == "elo"
    # Sala 5 não existe na quinta nem no sábado.
    assert elo.dias == (SEGUNDA, TERCA, QUARTA, SEXTA)
    assert not elo.atende_no_dia(QUINTA)
    assert {(p.sala_nome, p.posto) for p in elo.postos} == {("Sala 5", 1)}

    bia = catalogo.profissionais["bia"]
    assert bia.dias == (SEGUNDA, TERCA, QUARTA, QUINTA, SEXTA, SABADO)
    # No sábado, 07:00 e 07:30 estão FECHADO, mas o resto da manhã e a tarde não.
    assert bia.turnos_no_dia(SABADO) == ("manha", "tarde")

    duda = catalogo.profissionais["duda"]
    assert duda.dias == (SEGUNDA, TERCA, QUARTA, QUINTA, SEXTA)


def test_quem_ocupa_cada_sala_por_dia(catalogo: CatalogoAgenda) -> None:
    assert catalogo.salas["sala-1"].ocupantes_por_dia[SABADO] == ("bia",)
    assert QUINTA not in catalogo.salas["sala-5"].ocupantes_por_dia
    assert catalogo.dias_com_agenda == (SEGUNDA, TERCA, QUARTA, QUINTA, SEXTA, SABADO)


def test_catalogo_nao_tem_dado_de_paciente(catalogo: CatalogoAgenda) -> None:
    assert "Paciente" not in catalogo.model_dump_json()


def _entrada(profissional: str, hora: time, posto: int = 0) -> EntradaGrade:
    return EntradaGrade(
        sala_id="sala-9",
        profissional_id=profissional,
        especialidade=Especialidade.MUSICOTERAPIA,
        slot=Slot(data=SEGUNDA, hora_inicio=hora),
        indice_posto=posto,
    )


def test_montar_catalogo_com_grade_sem_cadastro_e_cadastro_sem_grade() -> None:
    catalogo = montar_catalogo(
        {
            SEGUNDA: DiaDoCatalogo(
                profissionais=[
                    Profissional(id="sem-grade", nome="Sem Grade", especialidade="psicologia")
                ],
                salas=[Sala(id="sala-9", nome="Sala 9", capacidade_simultanea=2)],
                grade=[_entrada("so-na-grade", time(13)), _entrada("so-na-grade", time(8), 1)],
            )
        },
        apelidos={"apelido-x": "so-na-grade"},
    )
    so_na_grade = catalogo.profissionais["so-na-grade"]
    assert so_na_grade.nome == "So Na Grade"
    assert so_na_grade.apelidos == ("apelido-x",)
    assert [(p.posto, p.turnos) for p in so_na_grade.postos] == [(1, ("tarde",)), (2, ("manha",))]
    assert catalogo.profissionais["sem-grade"].postos == ()
    assert catalogo.salas["sala-9"].capacidade == 2


def test_dia_ilegivel_deixa_o_catalogo_parcial() -> None:
    fonte = FakeScheduleDataSource(dias_com_falha={TERCA})
    catalogo = ler_catalogo(fonte, TERCA)
    assert catalogo.parcial and catalogo.dias_com_falha == (TERCA,)


def test_semana_inteira_ilegivel_levanta() -> None:
    fonte = FakeScheduleDataSource(dias_com_falha={SEGUNDA, TERCA, QUARTA, QUINTA, SEXTA, SABADO})
    with pytest.raises(CatalogoIndisponivelError):
        ler_catalogo(fonte, TERCA)


def test_cache_por_fonte_e_semana_com_ttl() -> None:
    agora = [0.0]
    leituras: list[date] = []

    def ler(fonte: object, data: date) -> CatalogoAgenda:
        leituras.append(data)
        return CatalogoAgenda(dias=(), profissionais={}, salas={})

    cache = CacheCatalogo(ttl_segundos=60, relogio=lambda: agora[0], ler=ler)
    fonte, outra = FakeScheduleDataSource(), FakeScheduleDataSource()
    cache.obter(fonte, SEGUNDA)
    cache.obter(fonte, SABADO)  # mesma semana: do cache
    cache.obter(outra, SEGUNDA)  # outra fonte: lê
    agora[0] = 59.9
    cache.obter(fonte, QUARTA)  # ainda vale
    agora[0] = 60.0
    cache.obter(fonte, QUARTA)  # venceu
    assert leituras == [SEGUNDA, SEGUNDA, QUARTA]


def test_falha_nao_fica_no_cache() -> None:
    tentativas = [0]

    def ler(fonte: object, data: date) -> CatalogoAgenda:
        tentativas[0] += 1
        if tentativas[0] == 1:
            raise CatalogoIndisponivelError("falhou")
        return CatalogoAgenda(dias=(), profissionais={}, salas={})

    cache = CacheCatalogo(ttl_segundos=60, relogio=lambda: 0.0, ler=ler)
    fonte = FakeScheduleDataSource()
    with pytest.raises(CatalogoIndisponivelError):
        cache.obter(fonte, SEGUNDA)
    cache.obter(fonte, SEGUNDA)
    assert tentativas[0] == 2
