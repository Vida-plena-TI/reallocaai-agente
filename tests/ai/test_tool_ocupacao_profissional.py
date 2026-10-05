"""Testes da tool `consultar_ocupacao_profissional`, sem nenhuma LLM envolvida."""

from datetime import date
from typing import Any

from langchain_core.tools import BaseTool

from app.ai.tools import NOTA_GRADE_SEMANAL, criar_tools
from app.data_sources.continuidade import SemHistoricoContinuidadeDataSource
from app.domain import Especialidade, Profissional, Sala, ScheduleDataSource
from tests.support.agenda_semanal import (
    OUTRA,
    QUARTA,
    SEGUNDA,
    SEMANA,
    TERCA,
    atendimentos,
    grade,
    horas_da_manha,
    horas_da_tarde,
)
from tests.support.fake_schedule_data_source import FakeScheduleDataSource

LUCIANA = Profissional(
    id="luciana-xavier", nome="Luciana Xavier", especialidade=Especialidade.PSICOLOGIA
)
LUCIA = Profissional(id="lucia-maria", nome="Lúcia Maria", especialidade=Especialidade.PSICOLOGIA)
LUCAS = Profissional(
    id="lucia-lucas", nome="Lúcia Lucas", especialidade=Especialidade.MUSICOTERAPIA
)
SALA_12 = Sala(id="sala-12", nome="Sala 12", capacidade_simultanea=2)
SALA_3 = Sala(id="sala-3", nome="Sala 3")


def _enviar_relatorio_nao_usado(fonte: Any, data: date, destinatarios: list[str] | None) -> None:
    raise AssertionError("enviar_relatorio não deveria ter sido chamado neste teste")


def _tool(fonte: ScheduleDataSource, data_referencia: date = QUARTA) -> BaseTool:
    return next(
        item
        for item in criar_tools(
            fonte,
            SemHistoricoContinuidadeDataSource(),
            data_referencia,
            _enviar_relatorio_nao_usado,
        )
        if item.name == "consultar_ocupacao_profissional"
    )


def _semana_exemplo(**extra: Any) -> FakeScheduleDataSource:
    """Segunda 17/20 (uma sala só); terça 14/20 em dois postos da Sala 12; resto sem agenda."""
    manha_seg, tarde_seg = horas_da_manha(SEGUNDA), horas_da_tarde(SEGUNDA)
    manha_ter, tarde_ter = horas_da_manha(TERCA), horas_da_tarde(TERCA)
    return FakeScheduleDataSource(
        salas={dia: [SALA_12, SALA_3] for dia in SEMANA},
        profissionais={SEGUNDA: [LUCIANA], TERCA: [LUCIANA, OUTRA]},
        grade={
            SEGUNDA: grade(SEGUNDA, manha_seg + tarde_seg, profissional=LUCIANA),
            TERCA: grade(TERCA, manha_ter, profissional=LUCIANA, indice_posto=1)
            + grade(TERCA, tarde_ter, profissional=LUCIANA, indice_posto=0)
            + grade(TERCA, manha_ter, profissional=OUTRA, sala_id="sala-3"),
        },
        atendimentos={
            SEGUNDA: atendimentos(SEGUNDA, manha_seg[:9] + tarde_seg[:8], profissional=LUCIANA),
            TERCA: atendimentos(TERCA, manha_ter[:6], profissional=LUCIANA, indice_posto=1)
            + atendimentos(TERCA, tarde_ter[:8], profissional=LUCIANA, indice_posto=0),
        },
        **extra,
    )


def test_texto_completo_por_dia_semana_e_meta() -> None:
    resultado = _tool(_semana_exemplo()).invoke({"profissional": "Luciana"})

    assert resultado == "\n".join(
        [
            "Ocupação de Luciana Xavier (Psicologia) — semana de 28/09 a 03/10/2026",
            "Meta: 80%",
            "",
            "Por dia:",
            "- Segunda (28/09): 85,0% — 17 de 20 slots ocupados, 3 livres "
            "(manhã 9/10, tarde 8/10) — meta atingida",
            "- Terça (29/09): 70,0% — 14 de 20 slots ocupados, 6 livres "
            "(manhã 6/10, tarde 8/10) — abaixo da meta; faltam 2 slots para 80%",
            "  · Sala 12 (posto 1): 8 de 10",
            "  · Sala 12 (posto 2): 6 de 10",
            "Sem agenda: Quarta, Quinta, Sexta, Sábado.",
            "",
            "Semana: 77,5% — 31 de 40 slots ocupados, 9 livres — abaixo da meta; "
            "falta 1 slot para 80%",
            "",
            NOTA_GRADE_SEMANAL,
        ]
    )
    assert "luciana-xavier" not in resultado
    assert "a grade semanal da planilha" in resultado


def test_sem_data_usa_a_semana_da_data_de_referencia() -> None:
    resultado = _tool(_semana_exemplo(), data_referencia=date(2026, 10, 7)).invoke(
        {"profissional": "Luciana"}
    )

    assert "na agenda da semana de 05/10 a 10/10/2026" in resultado


def test_data_informada_escolhe_a_semana() -> None:
    resultado = _tool(_semana_exemplo(), data_referencia=date(2026, 10, 7)).invoke(
        {"profissional": "Luciana", "data": "2026-09-30"}
    )

    assert resultado.startswith("Ocupação de Luciana Xavier")


def test_salas_diferentes_sem_posto_e_singular() -> None:
    origem = FakeScheduleDataSource(
        salas={SEGUNDA: [SALA_12, SALA_3]},
        profissionais={SEGUNDA: [LUCIANA]},
        grade={
            SEGUNDA: grade(SEGUNDA, horas_da_manha(SEGUNDA)[:1], profissional=LUCIANA)
            + grade(SEGUNDA, horas_da_tarde(SEGUNDA)[:1], profissional=LUCIANA, sala_id="sala-3")
        },
        atendimentos={
            SEGUNDA: atendimentos(SEGUNDA, horas_da_manha(SEGUNDA)[:1], profissional=LUCIANA)
        },
    )

    resultado = _tool(origem).invoke({"profissional": "Luciana"})

    assert (
        "- Segunda (28/09): 50,0% — 1 de 2 slots ocupados, 1 livre (manhã 1/1, tarde 0/1) — "
        "abaixo da meta; falta 1 slot para 80%"
    ) in resultado
    assert "  · Sala 12: 1 de 1" in resultado
    assert "  · Sala 3: 0 de 1" in resultado
    assert "posto" not in resultado
    assert "Semana: 50,0% — 1 de 2 slots ocupados, 1 livre" in resultado


def test_um_slot_escalado_usa_singular() -> None:
    origem = FakeScheduleDataSource(
        profissionais={SEGUNDA: [LUCIANA]},
        grade={SEGUNDA: grade(SEGUNDA, horas_da_manha(SEGUNDA)[:1], profissional=LUCIANA)},
        atendimentos={
            SEGUNDA: atendimentos(SEGUNDA, horas_da_manha(SEGUNDA)[:1], profissional=LUCIANA)
        },
    )

    resultado = _tool(origem).invoke({"profissional": "Luciana"})

    assert "100,0% — 1 de 1 slot ocupado, 0 livres (manhã 1/1) — meta atingida" in resultado


def test_ambiguo_lista_os_candidatos_e_pede_escolha() -> None:
    origem = FakeScheduleDataSource(profissionais={SEGUNDA: [LUCIA, LUCAS]})

    resultado = _tool(origem).invoke({"profissional": "lucia"})

    assert "Mais de uma profissional" in resultado
    assert "Lúcia Lucas (Musicoterapia)" in resultado
    assert "Lúcia Maria (Psicologia)" in resultado
    assert "Pergunte qual delas" in resultado


def test_nao_encontrado_lista_os_nomes_disponiveis() -> None:
    resultado = _tool(_semana_exemplo()).invoke({"profissional": "Fernanda"})

    assert "Não há profissional com o nome 'Fernanda'" in resultado
    assert "Profissionais disponíveis: Luciana Xavier, Outra." in resultado


def test_profissional_sem_agenda_na_semana() -> None:
    origem = FakeScheduleDataSource(profissionais={SEGUNDA: [LUCIANA]})

    resultado = _tool(origem).invoke({"profissional": "Luciana"})

    assert resultado.startswith(
        "Luciana Xavier (Psicologia) não tem agenda na semana de 28/09 a 03/10/2026."
    )


def test_falha_em_um_dia_avisa_que_os_totais_sao_parciais() -> None:
    resultado = _tool(_semana_exemplo(dias_com_falha={QUARTA})).invoke({"profissional": "Luciana"})

    assert "Não foi possível ler: Quarta (30/09); os totais da semana são parciais." in resultado
    assert "Sem agenda: Quinta, Sexta, Sábado." in resultado


def test_falha_em_todos_os_dias_vira_mensagem_de_erro() -> None:
    resultado = _tool(FakeScheduleDataSource(dias_com_falha=set(SEMANA))).invoke(
        {"profissional": "Luciana"}
    )

    assert resultado.startswith("Erro ao consultar ocupação da profissional:")
    assert "falha simulada" in resultado


def test_atendimento_fora_da_grade_gera_linha_de_atencao() -> None:
    origem = FakeScheduleDataSource(
        profissionais={SEGUNDA: [LUCIANA]},
        grade={SEGUNDA: grade(SEGUNDA, horas_da_manha(SEGUNDA), profissional=LUCIANA)},
        atendimentos={
            SEGUNDA: atendimentos(SEGUNDA, horas_da_tarde(SEGUNDA)[:1], profissional=LUCIANA)
        },
    )

    resultado = _tool(origem).invoke({"profissional": "Luciana"})

    assert (
        "Atenção: há atendimentos fora dos horários escalados da profissional "
        "(possível inconsistência na planilha)."
    ) in resultado


def test_descricoes_separam_ocupacao_da_profissional_da_ocupacao_geral() -> None:
    tools = {
        item.name: item
        for item in criar_tools(
            FakeScheduleDataSource(),
            SemHistoricoContinuidadeDataSource(),
            QUARTA,
            _enviar_relatorio_nao_usado,
        )
    }

    assert "UMA profissional" in tools["consultar_ocupacao_profissional"].description
    assert "NÃO use para" in tools["consultar_ocupacao_profissional"].description
    assert "consultar_ocupacao_profissional" in tools["consultar_ocupacao"].description
