"""Testes da tool `consultar_pacientes_por_profissional`, sem nenhuma LLM envolvida."""

from datetime import date
from typing import Any

from langchain_core.tools import BaseTool

from app.ai.tools import DEFINICAO_PACIENTE, NOTA_GRADE_SEMANAL, criar_tools
from app.data_sources.continuidade import SemHistoricoContinuidadeDataSource
from app.domain import Especialidade, Profissional, ScheduleDataSource
from tests.support.agenda_semanal import (
    DOMINGO,
    QUARTA,
    SEGUNDA,
    SEMANA,
    SEXTA,
    TERCA,
    atendimentos,
    grade,
    horas_da_manha,
)
from tests.support.fake_schedule_data_source import FakeScheduleDataSource

AMANDA = Profissional(id="amanda", nome="Amanda", especialidade=Especialidade.PSICOLOGIA)
ELIS = Profissional(id="elis", nome="Élis", especialidade=Especialidade.PSICOLOGIA)
BRUNO = Profissional(id="bruno", nome="Bruno", especialidade=Especialidade.FONOAUDIOLOGIA)
LIA_SOUZA = Profissional(
    id="lia-souza", nome="Lia Souza", especialidade=Especialidade.MUSICOTERAPIA
)
LIA_MENDES = Profissional(
    id="lia-mendes", nome="Lia Mendes", especialidade=Especialidade.TERAPIA_OCUPACIONAL
)


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
        if item.name == "consultar_pacientes_por_profissional"
    )


def _semana_exemplo(**extra: Any) -> FakeScheduleDataSource:
    """Amanda: seg 3, ter 2, qua 0 (agenda sem paciente). Élis: seg 1. Bruno: seg 1,
    o mesmo paciente das 07:00 da Amanda. Pacientes distintos da clínica: seg 4, ter 2.
    """
    manha_seg, manha_ter, manha_qua = (horas_da_manha(dia) for dia in (SEGUNDA, TERCA, QUARTA))
    return FakeScheduleDataSource(
        profissionais={
            SEGUNDA: [AMANDA, ELIS, BRUNO],
            TERCA: [AMANDA],
            QUARTA: [AMANDA],
        },
        grade={
            SEGUNDA: grade(SEGUNDA, manha_seg, profissional=AMANDA)
            + grade(SEGUNDA, manha_seg, profissional=ELIS, sala_id="sala-3")
            + grade(SEGUNDA, manha_seg, profissional=BRUNO, sala_id="sala-5"),
            TERCA: grade(TERCA, manha_ter, profissional=AMANDA),
            QUARTA: grade(QUARTA, manha_qua, profissional=AMANDA),
        },
        atendimentos={
            SEGUNDA: atendimentos(SEGUNDA, manha_seg[:3], profissional=AMANDA)
            + atendimentos(
                SEGUNDA, manha_seg[:1], profissional=ELIS, sala_id="sala-3", paciente_ids=["joana"]
            )
            + atendimentos(
                SEGUNDA,
                manha_seg[3:4],
                profissional=BRUNO,
                sala_id="sala-5",
                paciente_ids=["pac-0700"],
            ),
            TERCA: atendimentos(TERCA, manha_ter[:2], profissional=AMANDA),
        },
        **extra,
    )


def _sem_ids_nem_pacientes(texto: str) -> None:
    for proibido in ("pac-", "joana", "amanda", "elis", "bruno", "sala-"):
        assert proibido not in texto


def test_semana_todas_as_profissionais_agrupadas_por_especialidade() -> None:
    resultado = _tool(_semana_exemplo()).invoke({})

    assert resultado == "\n".join(
        [
            "Pacientes atendidos por profissional — semana de 28/09 a 03/10/2026",
            "(pacientes distintos por dia; a média considera só os dias com agenda)",
            DEFINICAO_PACIENTE,
            "",
            "Fonoaudiologia",
            "- Bruno: Seg 1 — média 1,0/dia em 1 dia · 1 paciente distinto na semana",
            "",
            "Psicologia",
            "- Amanda: Seg 3 · Ter 2 · Qua 0 — média 1,7/dia em 3 dias · "
            "3 pacientes distintos na semana",
            "- Élis: Seg 1 — média 1,0/dia em 1 dia · 1 paciente distinto na semana",
            "",
            "Pacientes distintos na clínica por dia: Seg 4 · Ter 2",
            "",
            NOTA_GRADE_SEMANAL,
        ]
    )
    _sem_ids_nem_pacientes(resultado)


def test_dia_todas_as_profissionais() -> None:
    resultado = _tool(_semana_exemplo()).invoke({"escopo": "dia", "data": "2026-09-28"})

    assert resultado == "\n".join(
        [
            "Pacientes por profissional — segunda-feira, 28/09/2026",
            DEFINICAO_PACIENTE,
            "",
            "Fonoaudiologia",
            "- Bruno: 1 paciente",
            "",
            "Psicologia",
            "- Amanda: 3 pacientes",
            "- Élis: 1 paciente",
            "",
            "Pacientes distintos na clínica no dia: 4",
        ]
    )
    _sem_ids_nem_pacientes(resultado)


def test_uma_profissional_na_semana() -> None:
    resultado = _tool(_semana_exemplo()).invoke({"profissional": "amanda"})

    assert resultado == "\n".join(
        [
            "Amanda (Psicologia) — semana de 28/09 a 03/10/2026",
            DEFINICAO_PACIENTE,
            "- Segunda (28/09): 3 pacientes, 3 sessões, 3 slots ocupados",
            "- Terça (29/09): 2 pacientes, 2 sessões, 2 slots ocupados",
            "- Quarta (30/09): 0 pacientes, 0 sessões, 0 slots ocupados",
            "Média: 1,7 pacientes por dia em 3 dias · 3 pacientes distintos na semana",
            "Sem agenda: Quinta, Sexta, Sábado.",
            "",
            NOTA_GRADE_SEMANAL,
        ]
    )
    _sem_ids_nem_pacientes(resultado)


def test_uma_profissional_usa_singular() -> None:
    resultado = _tool(_semana_exemplo()).invoke({"profissional": "Elis"})

    assert "- Segunda (28/09): 1 paciente, 1 sessão, 1 slot ocupado" in resultado
    assert "Média: 1,0 pacientes por dia em 1 dia · 1 paciente distinto na semana" in resultado


def test_uma_profissional_num_dia() -> None:
    resultado = _tool(_semana_exemplo()).invoke(
        {"profissional": "Amanda", "escopo": "dia", "data": "2026-09-29"}
    )

    assert resultado == "\n".join(
        [
            "Amanda (Psicologia) — terça-feira, 29/09/2026",
            DEFINICAO_PACIENTE,
            "- 2 pacientes, 2 sessões, 2 slots ocupados",
        ]
    )


def test_sem_data_usa_a_semana_da_data_de_referencia() -> None:
    resultado = _tool(_semana_exemplo(), data_referencia=date(2026, 10, 7)).invoke({})

    assert resultado == "Nenhuma profissional tem agenda na semana de 05/10 a 10/10/2026."


def test_profissional_ambigua_lista_as_candidatas() -> None:
    fonte = FakeScheduleDataSource(profissionais={SEGUNDA: [LIA_SOUZA, LIA_MENDES]})

    resultado = _tool(fonte).invoke({"profissional": "Lia"})

    assert resultado.startswith(
        "Mais de uma profissional corresponde a 'Lia': Lia Mendes (Terapia Ocupacional), "
        "Lia Souza (Musicoterapia)."
    )
    assert "Pergunte qual delas" in resultado


def test_profissional_inexistente_lista_os_nomes() -> None:
    resultado = _tool(_semana_exemplo()).invoke({"profissional": "Zuleica"})

    assert resultado == (
        "Não há profissional com o nome 'Zuleica' na agenda da semana de 28/09 a 03/10/2026. "
        "Profissionais disponíveis: Amanda, Bruno, Élis."
    )


def test_profissional_sem_agenda_no_periodo() -> None:
    resultado = _tool(_semana_exemplo()).invoke(
        {"profissional": "Élis", "escopo": "dia", "data": "2026-09-29"}
    )

    assert resultado == "Élis não tem agenda em terça-feira, 29/09/2026."


def test_filtro_por_especialidade_tolerante() -> None:
    resultado = _tool(_semana_exemplo()).invoke({"especialidade": "Fono"})

    assert resultado.startswith(
        "Pacientes atendidos por profissional de Fonoaudiologia — semana de 28/09 a 03/10/2026"
    )
    assert "- Bruno: Seg 1" in resultado
    assert "Amanda" not in resultado
    assert "Psicologia" not in resultado


def test_filtro_por_especialidade_psicologa_no_dia() -> None:
    resultado = _tool(_semana_exemplo()).invoke(
        {"especialidade": "psicóloga", "escopo": "dia", "data": "2026-09-28"}
    )

    assert resultado.startswith("Pacientes por profissional de Psicologia — segunda-feira")
    assert "Bruno" not in resultado


def test_especialidade_desconhecida_lista_as_validas() -> None:
    resultado = _tool(_semana_exemplo()).invoke({"especialidade": "astrologia"})

    assert resultado == (
        "Especialidade 'astrologia' não reconhecida. Especialidades válidas: Terapia "
        "Ocupacional, Fonoaudiologia, Psicologia, Terapia Alimentar, Psicomotricidade, "
        "Psicopedagogia, Musicoterapia."
    )


def test_especialidade_sem_ninguem_com_agenda() -> None:
    resultado = _tool(_semana_exemplo()).invoke({"especialidade": "musicoterapia"})

    assert resultado == (
        "Nenhuma profissional de Musicoterapia tem agenda na semana de 28/09 a 03/10/2026."
    )


def test_profissional_de_outra_especialidade() -> None:
    resultado = _tool(_semana_exemplo()).invoke({"profissional": "Bruno", "especialidade": "TO"})

    assert resultado == "Bruno (Fonoaudiologia) não é de Terapia Ocupacional."


def test_domingo_no_escopo_dia() -> None:
    resultado = _tool(_semana_exemplo()).invoke({"escopo": "dia", "data": DOMINGO.isoformat()})

    assert resultado == "A clínica não tem agenda aos domingos (04/10/2026)."


def test_dia_com_falha_deixa_a_resposta_parcial() -> None:
    resultado = _tool(_semana_exemplo(dias_com_falha={SEXTA})).invoke({})

    assert "Não foi possível ler: Sexta (02/10); os números são parciais." in resultado
    assert resultado.endswith(NOTA_GRADE_SEMANAL)


def test_falha_em_todos_os_dias_vira_mensagem_de_erro() -> None:
    resultado = _tool(_semana_exemplo(dias_com_falha=set(SEMANA))).invoke({})

    assert resultado.startswith("Erro ao contar pacientes por profissional:")
