"""Testes das tools do agente RealocAI (Fase 5b), sem nenhuma LLM envolvida.

Cada tool é chamada diretamente via `.invoke(...)` contra os dublês de
`ScheduleDataSource`/`ContinuidadeDataSource` já usados na Fase 5a.
"""

from dataclasses import dataclass, field
from datetime import date, time
from typing import Any

import pytest
from langchain_core.tools import BaseTool

from app.ai.tools import EnviarRelatorio, criar_tools
from app.data_sources.continuidade import ContinuidadeDataSource
from app.domain import (
    Atendimento,
    EntradaGrade,
    Especialidade,
    Paciente,
    Profissional,
    Sala,
    ScheduleDataSource,
    Slot,
)
from app.reports.exceptions import ReportsEnvioError
from tests.support.fake_schedule_data_source import FakeScheduleDataSource

DIA = date(2026, 9, 8)
PACIENTE_UM = Paciente(id="paciente-um", nome="Paciente Um")


def _enviar_relatorio_nao_usado(fonte: Any, data: date, destinatarios: list[str] | None) -> None:
    """Padrão para testes que não exercitam a tool `enviar_relatorio`: chamar
    isto é sempre um erro de teste, nunca um cenário esperado."""
    raise AssertionError("enviar_relatorio não deveria ter sido chamado neste teste")


@dataclass
class FakeContinuidadeDataSource:
    """`ContinuidadeDataSource` em memória, só para teste."""

    habitual: dict[tuple[str, Especialidade], str] = field(default_factory=dict)

    def profissional_habitual(self, paciente_id: str, especialidade: Especialidade) -> str | None:
        return self.habitual.get((paciente_id, especialidade))


def _continuidade_vazia() -> ContinuidadeDataSource:
    return FakeContinuidadeDataSource()


def _tool(
    fonte: ScheduleDataSource,
    continuidade: ContinuidadeDataSource,
    nome: str,
    data_referencia: date = DIA,
    enviar_relatorio: EnviarRelatorio = _enviar_relatorio_nao_usado,
) -> BaseTool:
    return next(
        item
        for item in criar_tools(fonte, continuidade, data_referencia, enviar_relatorio)
        if item.name == nome
    )


def entrada(
    sala_id: str,
    profissional_id: str,
    especialidade: Especialidade,
    hora: time,
    indice_posto: int = 0,
) -> EntradaGrade:
    return EntradaGrade(
        sala_id=sala_id,
        profissional_id=profissional_id,
        especialidade=especialidade,
        slot=Slot(data=DIA, hora_inicio=hora),
        indice_posto=indice_posto,
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
    # Sala de posto único: o texto não muda com a introdução dos postos.
    assert "- Psicologia: 10:00 às 11:00, com Ana na Sala 1." in resultado
    assert "posto" not in resultado


def test_buscar_encaixe_tool_indica_o_posto_em_sala_com_mais_de_um_posto_do_titular() -> None:
    """Helena tem os postos 1 e 2 da Sala 5; o posto 1 está ocupado às 10:00,
    então a sugestão cai no posto 2 — e o texto precisa dizer qual."""
    origem = FakeScheduleDataSource(
        pacientes={DIA: [PACIENTE_UM]},
        salas={DIA: [Sala(id="sala-5", nome="Sala 5", capacidade_simultanea=2)]},
        grade={
            DIA: [
                entrada(
                    "sala-5", "helena", Especialidade.TERAPIA_OCUPACIONAL, slot.hora_inicio, posto
                )
                for posto in (0, 1)
                for slot in Slot.slots_do_dia(DIA)
            ]
        },
        profissionais={DIA: [profissional("helena", "Helena", Especialidade.TERAPIA_OCUPACIONAL)]},
        atendimentos={
            DIA: [
                Atendimento(
                    id="at-posto-1",
                    paciente_ids=["pac-2"],
                    profissional_id="helena",
                    sala_id="sala-5",
                    especialidade=Especialidade.TERAPIA_OCUPACIONAL,
                    slots=[Slot(data=DIA, hora_inicio=time(10, 0))],
                    indice_posto=0,
                )
            ]
        },
    )
    tool = _tool(origem, _continuidade_vazia(), "buscar_encaixe")

    resultado = tool.invoke(
        {
            "paciente": "Paciente Um",
            "data": "2026-09-08",
            "itens": [{"especialidade": "terapia_ocupacional", "duracao_minutos": 60}],
            "horario_desejado": "10:00",
        }
    )

    assert "Horário encontrado" in resultado
    assert "- Terapia Ocupacional: 10:00 às 11:00, com Helena na Sala 5 (posto 2)." in resultado


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


def test_consultar_disponibilidade_tool_nao_mescla_slots_consecutivos() -> None:
    """4 slots seguidos do mesmo profissional/sala saem um a um, nunca numa faixa só."""
    horas = [time(7, 0), time(7, 30), time(8, 0), time(8, 30)]
    origem = FakeScheduleDataSource(
        salas={DIA: [Sala(id="sala-1", nome="Sala 1")]},
        grade={
            DIA: [entrada("sala-1", "prof-1", Especialidade.PSICOLOGIA, hora) for hora in horas]
        },
        profissionais={DIA: [profissional("prof-1", "Ana", Especialidade.PSICOLOGIA)]},
    )
    tool = _tool(origem, _continuidade_vazia(), "consultar_disponibilidade")

    resultado = tool.invoke({"data": "2026-09-08"})

    assert "07:00 às 07:30" in resultado
    assert "07:30 às 08:00" in resultado
    assert "08:00 às 08:30" in resultado
    assert "08:30 às 09:00" in resultado
    assert "07:00 às 08:30" not in resultado
    assert "07:00 às 09:00" not in resultado


def test_consultar_disponibilidade_tool_distingue_postos_do_mesmo_titular() -> None:
    """Três colunas da mesma sala com a mesma titular são três vagas, não uma repetida."""
    origem = FakeScheduleDataSource(
        salas={DIA: [Sala(id="sala-5", nome="Sala 5", capacidade_simultanea=3)]},
        grade={
            DIA: [
                entrada("sala-5", "helena", Especialidade.TERAPIA_OCUPACIONAL, time(9, 0), posto)
                for posto in (0, 1, 2)
            ]
        },
        profissionais={DIA: [profissional("helena", "Helena", Especialidade.TERAPIA_OCUPACIONAL)]},
    )
    tool = _tool(origem, _continuidade_vazia(), "consultar_disponibilidade")

    resultado = tool.invoke({"data": "2026-09-08"})

    for posto in (1, 2, 3):
        assert (
            f"09:00 às 09:30: Terapia Ocupacional com Helena na Sala 5 (posto {posto})."
            in resultado
        )


def test_consultar_disponibilidade_tool_nao_cita_posto_sem_ambiguidade() -> None:
    """Sala de capacidade 1 e sala mesclada com profissionais diferentes por
    coluna saem como antes: o nome do profissional já distingue as vagas."""
    origem = FakeScheduleDataSource(
        salas={
            DIA: [
                Sala(id="sala-1", nome="Sala 1"),
                Sala(id="sala-7", nome="Sala 7", capacidade_simultanea=2),
            ]
        },
        grade={
            DIA: [
                entrada("sala-1", "prof-1", Especialidade.PSICOLOGIA, time(9, 0)),
                entrada("sala-7", "bia", Especialidade.PSICOMOTRICIDADE, time(9, 0), 0),
                entrada("sala-7", "lia", Especialidade.PSICOMOTRICIDADE, time(9, 0), 1),
            ]
        },
        profissionais={
            DIA: [
                profissional("prof-1", "Ana", Especialidade.PSICOLOGIA),
                profissional("bia", "Bia", Especialidade.PSICOMOTRICIDADE),
                profissional("lia", "Lia", Especialidade.PSICOMOTRICIDADE),
            ]
        },
    )
    tool = _tool(origem, _continuidade_vazia(), "consultar_disponibilidade")

    resultado = tool.invoke({"data": "2026-09-08"})

    assert "09:00 às 09:30: Psicologia com Ana na Sala 1." in resultado
    assert "Psicomotricidade com Bia na Sala 7." in resultado
    assert "Psicomotricidade com Lia na Sala 7." in resultado
    assert "posto" not in resultado


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


def test_enviar_relatorio_tool_sucesso_confirma_quantidade_de_destinatarios() -> None:
    chamadas: list[tuple[Any, date, list[str] | None]] = []
    origem = FakeScheduleDataSource()
    tool = _tool(
        origem,
        _continuidade_vazia(),
        "enviar_relatorio",
        enviar_relatorio=lambda fonte, data, destinatarios: chamadas.append(
            (fonte, data, destinatarios)
        ),
    )

    resultado = tool.invoke({"destinatarios": ["a@b.com", "c@d.com"]})

    assert "2" in resultado
    assert len(chamadas) == 1
    assert chamadas[0][2] == ["a@b.com", "c@d.com"]


def test_enviar_relatorio_tool_sem_data_usa_data_de_referencia_da_conversa() -> None:
    chamadas: list[tuple[Any, date, list[str] | None]] = []
    origem = FakeScheduleDataSource()
    tool = _tool(
        origem,
        _continuidade_vazia(),
        "enviar_relatorio",
        data_referencia=DIA,
        enviar_relatorio=lambda fonte, data, destinatarios: chamadas.append(
            (fonte, data, destinatarios)
        ),
    )

    tool.invoke({})

    assert chamadas[0][1] == DIA


def test_enviar_relatorio_tool_erro_devolve_mensagem_clara() -> None:
    def _levanta_erro(fonte: Any, data: date, destinatarios: list[str] | None) -> None:
        raise ReportsEnvioError("falha simulada")

    origem = FakeScheduleDataSource()
    tool = _tool(origem, _continuidade_vazia(), "enviar_relatorio", enviar_relatorio=_levanta_erro)

    resultado = tool.invoke({})

    assert "erro" in resultado.lower()


# ---- erro inesperado da fonte de dados ----


@dataclass
class FonteQuebrada:
    """`ScheduleDataSource` que sempre falha, só para provar que nenhuma tool
    deixa uma exceção da camada de serviço subir e quebrar o turno da conversa."""

    def listar_salas(self, dia: date) -> list[Sala]:
        raise RuntimeError("falha simulada")

    def listar_profissionais(self, dia: date) -> list[Profissional]:
        raise RuntimeError("falha simulada")

    def listar_grade(self, dia: date) -> list[EntradaGrade]:
        raise RuntimeError("falha simulada")

    def listar_atendimentos(self, dia: date) -> list[Atendimento]:
        raise RuntimeError("falha simulada")

    def listar_pacientes(self, dia: date) -> list[Paciente]:
        raise RuntimeError("falha simulada")


@pytest.mark.parametrize(
    ("nome_tool", "args", "prefixo_esperado"),
    [
        (
            "buscar_paciente",
            {"nome_ou_id": "Paciente Um", "data": "2026-09-08"},
            "Erro ao buscar paciente:",
        ),
        (
            "buscar_encaixe",
            {
                "paciente": "Paciente Um",
                "data": "2026-09-08",
                "itens": [{"especialidade": "psicologia", "duracao_minutos": 30}],
            },
            "Erro ao buscar encaixe:",
        ),
        (
            "consultar_disponibilidade",
            {"data": "2026-09-08"},
            "Erro ao consultar disponibilidade:",
        ),
        ("consultar_ocupacao", {"data": "2026-09-08"}, "Erro ao consultar ocupação:"),
        (
            "sugerir_realocacao",
            {"atendimento_id": "at-1", "data": "2026-09-08"},
            "Erro ao sugerir realocação:",
        ),
    ],
)
def test_tool_converte_excecao_inesperada_da_fonte_em_mensagem_de_erro(
    nome_tool: str, args: dict[str, Any], prefixo_esperado: str
) -> None:
    tool = _tool(FonteQuebrada(), _continuidade_vazia(), nome_tool)

    resultado = tool.invoke(args)

    assert resultado.startswith(prefixo_esperado)
    assert "falha simulada" in resultado
