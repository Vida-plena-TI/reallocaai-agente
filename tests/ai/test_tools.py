"""Testes das tools do agente RealocAI (Fase 5b), sem nenhuma LLM envolvida.

Cada tool é chamada diretamente via `.invoke(...)` contra os dublês de
`ScheduleDataSource`/`ContinuidadeDataSource` já usados na Fase 5a.
"""

from dataclasses import dataclass, field
from datetime import date, time
from typing import Any

import pytest
from langchain_core.tools import BaseTool

from app.ai.tools import criar_tools
from app.data_sources.base import EntradaGrade
from app.data_sources.continuidade import ContinuidadeDataSource
from app.domain import Atendimento, Especialidade, Paciente, Profissional, Sala, Slot
from app.reports.exceptions import ReportsEnvioError
from tests.support.fake_schedule_data_source import FakeScheduleDataSource

DIA = date(2026, 9, 8)
PACIENTE_UM = Paciente(id="paciente-um", nome="Paciente Um")


@dataclass
class FakeContinuidadeDataSource:
    """`ContinuidadeDataSource` em memória, só para teste."""

    habitual: dict[tuple[str, Especialidade], str] = field(default_factory=dict)

    def profissional_habitual(self, paciente_id: str, especialidade: Especialidade) -> str | None:
        return self.habitual.get((paciente_id, especialidade))


def _continuidade_vazia() -> ContinuidadeDataSource:
    return FakeContinuidadeDataSource()


def _tool(
    fonte: FakeScheduleDataSource,
    continuidade: ContinuidadeDataSource,
    nome: str,
    data_referencia: date = DIA,
) -> BaseTool:
    return next(
        item for item in criar_tools(fonte, continuidade, data_referencia) if item.name == nome
    )


def entrada(
    sala_id: str, profissional_id: str, especialidade: Especialidade, hora: time
) -> EntradaGrade:
    return EntradaGrade(
        sala_id=sala_id,
        profissional_id=profissional_id,
        especialidade=especialidade,
        slot=Slot(data=DIA, hora_inicio=hora),
    )


def grade_completa(
    sala_id: str, profissional_id: str, especialidade: Especialidade
) -> list[EntradaGrade]:
    return [
        entrada(sala_id, profissional_id, especialidade, slot.hora_inicio)
        for slot in Slot.slots_do_dia(DIA)
    ]


def profissional(id_: str, nome: str, especialidade: Especialidade) -> Profissional:
    return Profissional(id=id_, nome=nome, especialidade=especialidade)


def atendimento(
    id_: str,
    profissional_id: str,
    sala_id: str,
    especialidade: Especialidade,
    horas: list[time],
    paciente_ids: list[str] | None = None,
) -> Atendimento:
    return Atendimento(
        id=id_,
        paciente_ids=paciente_ids if paciente_ids is not None else ["pac-1"],
        profissional_id=profissional_id,
        sala_id=sala_id,
        especialidade=especialidade,
        slots=[Slot(data=DIA, hora_inicio=hora) for hora in horas],
    )


# ---- buscar_paciente ----


def test_buscar_paciente_tool_encontra_paciente() -> None:
    origem = FakeScheduleDataSource(pacientes={DIA: [PACIENTE_UM]})
    tool = _tool(origem, _continuidade_vazia(), "buscar_paciente")

    resultado = tool.invoke({"nome_ou_id": "Paciente Um", "data": "2026-09-08"})

    assert "Paciente Um" in resultado
    assert "paciente-um" in resultado


def test_buscar_paciente_tool_devolve_mensagem_clara_quando_nao_encontra() -> None:
    origem = FakeScheduleDataSource(pacientes={DIA: [PACIENTE_UM]})
    tool = _tool(origem, _continuidade_vazia(), "buscar_paciente")

    resultado = tool.invoke({"nome_ou_id": "paciente-fantasma", "data": "2026-09-08"})

    assert "não encontrado" in resultado.lower()


# ---- buscar_encaixe ----


def test_buscar_encaixe_tool_retorna_horario_exato_formatado() -> None:
    origem = FakeScheduleDataSource(
        pacientes={DIA: [PACIENTE_UM]},
        salas={DIA: [Sala(id="sala-1", nome="Sala 1")]},
        grade={DIA: grade_completa("sala-1", "prof-1", Especialidade.PSICOLOGIA)},
        profissionais={DIA: [profissional("prof-1", "Ana", Especialidade.PSICOLOGIA)]},
    )
    tool = _tool(origem, _continuidade_vazia(), "buscar_encaixe")

    resultado = tool.invoke(
        {
            "paciente": "Paciente Um",
            "data": "2026-09-08",
            # 45 minutos deve arredondar para 2 slots (60 minutos).
            "itens": [{"especialidade": "psicologia", "duracao_minutos": 45}],
            "horario_desejado": "10:00",
        }
    )

    assert "Horário encontrado" in resultado
    assert "10:00 às 11:00" in resultado
    assert "Ana" in resultado
    assert "Sala 1" in resultado
    assert "Psicologia" in resultado


def test_buscar_encaixe_tool_retorna_alternativas_quando_nao_ha_vaga_exata() -> None:
    origem = FakeScheduleDataSource(
        pacientes={DIA: [PACIENTE_UM]},
        grade={DIA: grade_completa("sala-1", "prof-1", Especialidade.PSICOLOGIA)},
        profissionais={DIA: [profissional("prof-1", "Ana", Especialidade.PSICOLOGIA)]},
        atendimentos={
            DIA: [atendimento("at-1", "prof-1", "sala-1", Especialidade.PSICOLOGIA, [time(8, 0)])]
        },
    )
    tool = _tool(origem, _continuidade_vazia(), "buscar_encaixe")

    resultado = tool.invoke(
        {
            "paciente": "Paciente Um",
            "data": "2026-09-08",
            "itens": [{"especialidade": "psicologia", "duracao_minutos": 30}],
            "horario_desejado": "08:00",
        }
    )

    assert "Alternativas" in resultado


def test_buscar_encaixe_tool_retorna_nenhum_sem_opcao() -> None:
    origem = FakeScheduleDataSource(pacientes={DIA: [PACIENTE_UM]})
    tool = _tool(origem, _continuidade_vazia(), "buscar_encaixe")

    resultado = tool.invoke(
        {
            "paciente": "Paciente Um",
            "data": "2026-09-08",
            "itens": [{"especialidade": "psicologia", "duracao_minutos": 30}],
        }
    )

    assert "Nenhum horário disponível" in resultado


def test_buscar_encaixe_tool_devolve_mensagem_clara_para_paciente_inexistente() -> None:
    origem = FakeScheduleDataSource()
    tool = _tool(origem, _continuidade_vazia(), "buscar_encaixe")

    resultado = tool.invoke(
        {
            "paciente": "paciente-fantasma",
            "data": "2026-09-08",
            "itens": [{"especialidade": "psicologia", "duracao_minutos": 30}],
        }
    )

    assert "não encontrado" in resultado.lower()


# ---- consultar_disponibilidade ----


def test_consultar_disponibilidade_tool_lista_horarios_livres() -> None:
    origem = FakeScheduleDataSource(
        salas={DIA: [Sala(id="sala-1", nome="Sala 1")]},
        grade={DIA: [entrada("sala-1", "prof-1", Especialidade.PSICOLOGIA, time(9, 0))]},
        profissionais={DIA: [profissional("prof-1", "Ana", Especialidade.PSICOLOGIA)]},
    )
    tool = _tool(origem, _continuidade_vazia(), "consultar_disponibilidade")

    resultado = tool.invoke({"data": "2026-09-08"})

    assert "09:00 às 09:30" in resultado
    assert "Ana" in resultado
    assert "Sala 1" in resultado


def test_consultar_disponibilidade_tool_sem_resultado() -> None:
    origem = FakeScheduleDataSource()
    tool = _tool(origem, _continuidade_vazia(), "consultar_disponibilidade")

    resultado = tool.invoke({"data": "2026-09-08"})

    assert "Nenhum horário livre" in resultado


def test_consultar_disponibilidade_tool_profissional_inexistente() -> None:
    origem = FakeScheduleDataSource()
    tool = _tool(origem, _continuidade_vazia(), "consultar_disponibilidade")

    resultado = tool.invoke({"data": "2026-09-08", "profissional": "fantasma"})

    assert "não encontrado" in resultado.lower()


# ---- consultar_ocupacao ----


def test_consultar_ocupacao_tool_sinaliza_abaixo_da_meta() -> None:
    origem = FakeScheduleDataSource(
        grade={DIA: grade_completa("sala-1", "prof-1", Especialidade.PSICOLOGIA)},
        profissionais={DIA: [profissional("prof-1", "Ana", Especialidade.PSICOLOGIA)]},
        atendimentos={
            DIA: [atendimento("at-1", "prof-1", "sala-1", Especialidade.PSICOLOGIA, [time(8, 0)])]
        },
    )
    tool = _tool(origem, _continuidade_vazia(), "consultar_ocupacao")

    resultado = tool.invoke({"data": "2026-09-08"})

    assert "Psicologia" in resultado
    assert "abaixo da meta de 80%" in resultado


def test_consultar_ocupacao_tool_sem_escala_no_dia() -> None:
    origem = FakeScheduleDataSource()
    tool = _tool(origem, _continuidade_vazia(), "consultar_ocupacao")

    resultado = tool.invoke({"data": "2026-09-08"})

    assert "Nenhuma escala encontrada" in resultado


# ---- sugerir_realocacao ----


def test_sugerir_realocacao_tool_atendimento_inexistente() -> None:
    origem = FakeScheduleDataSource()
    tool = _tool(origem, _continuidade_vazia(), "sugerir_realocacao")

    resultado = tool.invoke({"atendimento_id": "atendimento-fantasma", "data": "2026-09-08"})

    assert "não encontrado" in resultado.lower()


def test_sugerir_realocacao_tool_encontra_novo_horario() -> None:
    original = atendimento(
        "at-original", "prof-1", "sala-1", Especialidade.FONOAUDIOLOGIA, [time(9, 0)]
    )
    origem = FakeScheduleDataSource(
        grade={DIA: grade_completa("sala-1", "prof-1", Especialidade.FONOAUDIOLOGIA)},
        profissionais={DIA: [profissional("prof-1", "Ana", Especialidade.FONOAUDIOLOGIA)]},
        atendimentos={DIA: [original]},
    )
    tool = _tool(origem, _continuidade_vazia(), "sugerir_realocacao")

    resultado = tool.invoke({"atendimento_id": "at-original", "data": "2026-09-08"})

    assert "Nova opção de horário" in resultado


def test_sugerir_realocacao_tool_sem_alternativa_disponivel() -> None:
    original = atendimento(
        "at-original", "prof-1", "sala-1", Especialidade.FONOAUDIOLOGIA, [time(9, 0)]
    )
    origem = FakeScheduleDataSource(atendimentos={DIA: [original]})
    tool = _tool(origem, _continuidade_vazia(), "sugerir_realocacao")

    resultado = tool.invoke({"atendimento_id": "at-original", "data": "2026-09-08"})

    assert "Não há horário alternativo" in resultado


# ---- enviar_relatorio ----


def test_enviar_relatorio_tool_sucesso_confirma_quantidade_de_destinatarios(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chamadas: list[tuple[Any, date, list[str] | None]] = []
    monkeypatch.setattr(
        "app.ai.tools.enviar_relatorio_por_email",
        lambda fonte, data, destinatarios: chamadas.append((fonte, data, destinatarios)),
    )
    origem = FakeScheduleDataSource()
    tool = _tool(origem, _continuidade_vazia(), "enviar_relatorio")

    resultado = tool.invoke({"destinatarios": ["a@b.com", "c@d.com"]})

    assert "2" in resultado
    assert len(chamadas) == 1
    assert chamadas[0][2] == ["a@b.com", "c@d.com"]


def test_enviar_relatorio_tool_sem_data_usa_data_de_referencia_da_conversa(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chamadas: list[tuple[Any, date, list[str] | None]] = []
    monkeypatch.setattr(
        "app.ai.tools.enviar_relatorio_por_email",
        lambda fonte, data, destinatarios: chamadas.append((fonte, data, destinatarios)),
    )
    origem = FakeScheduleDataSource()
    tool = _tool(origem, _continuidade_vazia(), "enviar_relatorio", data_referencia=DIA)

    tool.invoke({})

    assert chamadas[0][1] == DIA


def test_enviar_relatorio_tool_erro_devolve_mensagem_clara(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _levanta_erro(fonte: Any, data: date, destinatarios: list[str] | None) -> None:
        raise ReportsEnvioError("falha simulada")

    monkeypatch.setattr("app.ai.tools.enviar_relatorio_por_email", _levanta_erro)
    origem = FakeScheduleDataSource()
    tool = _tool(origem, _continuidade_vazia(), "enviar_relatorio")

    resultado = tool.invoke({})

    assert "erro" in resultado.lower()
