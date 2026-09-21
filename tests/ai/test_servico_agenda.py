"""Testes da camada de serviço da agenda (Fase 5a)."""

from dataclasses import dataclass, field
from datetime import date, time

import pytest

from app.ai.servico_agenda import (
    ItemDemandaBruta,
    ResultadoAlternativas,
    ResultadoExato,
    ResultadoNenhum,
    buscar_encaixe,
    buscar_paciente,
    montar_solicitacao,
    sugerir_realocacao_por_id,
)
from app.data_sources.base import EntradaGrade
from app.data_sources.continuidade import ContinuidadeDataSource
from app.domain import Atendimento, Especialidade, Paciente, Profissional, Slot
from tests.support.fake_schedule_data_source import FakeScheduleDataSource

DIA = date(2026, 9, 8)


@dataclass
class FakeContinuidadeDataSource:
    """`ContinuidadeDataSource` em memória, só para teste."""

    habitual: dict[tuple[str, Especialidade], str] = field(default_factory=dict)

    def profissional_habitual(self, paciente_id: str, especialidade: Especialidade) -> str | None:
        return self.habitual.get((paciente_id, especialidade))


def _continuidade_vazia() -> ContinuidadeDataSource:
    return FakeContinuidadeDataSource()


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


def test_buscar_paciente_encontra_por_nome_com_variacao_de_caixa_e_acento() -> None:
    origem = FakeScheduleDataSource(
        pacientes={DIA: [Paciente(id="jose-felipe", nome="José Felipe")]}
    )

    encontrado = buscar_paciente(origem, DIA, "JOSÉ felipe")

    assert encontrado is not None
    assert encontrado.id == "jose-felipe"


def test_buscar_paciente_retorna_none_para_paciente_inexistente() -> None:
    origem = FakeScheduleDataSource(
        pacientes={DIA: [Paciente(id="jose-felipe", nome="José Felipe")]}
    )

    assert buscar_paciente(origem, DIA, "paciente-fantasma") is None


def test_montar_solicitacao_preenche_profissional_id_via_continuidade() -> None:
    origem = FakeScheduleDataSource()
    continuidade = FakeContinuidadeDataSource(
        habitual={("pac-1", Especialidade.FONOAUDIOLOGIA): "prof-ana"}
    )
    itens = [ItemDemandaBruta(especialidade=Especialidade.FONOAUDIOLOGIA, duracao_em_slots=1)]

    solicitacao = montar_solicitacao(
        origem, continuidade, "pac-1", DIA, itens, horario_minimo=time(8, 0), horario_desejado=None
    )

    assert solicitacao.itens[0].profissional_id == "prof-ana"


def test_montar_solicitacao_deixa_none_quando_continuidade_nao_sabe() -> None:
    origem = FakeScheduleDataSource()
    continuidade = _continuidade_vazia()
    itens = [ItemDemandaBruta(especialidade=Especialidade.FONOAUDIOLOGIA, duracao_em_slots=1)]

    solicitacao = montar_solicitacao(
        origem, continuidade, "pac-1", DIA, itens, horario_minimo=time(8, 0), horario_desejado=None
    )

    assert solicitacao.itens[0].profissional_id is None


def test_montar_solicitacao_nao_sobrescreve_profissional_id_explicito() -> None:
    origem = FakeScheduleDataSource()
    continuidade = FakeContinuidadeDataSource(
        habitual={("pac-1", Especialidade.FONOAUDIOLOGIA): "prof-continuidade"}
    )
    itens = [
        ItemDemandaBruta(
            especialidade=Especialidade.FONOAUDIOLOGIA,
            duracao_em_slots=1,
            profissional_id="prof-explicito",
        )
    ]

    solicitacao = montar_solicitacao(
        origem, continuidade, "pac-1", DIA, itens, horario_minimo=time(8, 0), horario_desejado=None
    )

    assert solicitacao.itens[0].profissional_id == "prof-explicito"


def test_buscar_encaixe_retorna_exato_quando_ha_vaga_no_horario_desejado() -> None:
    origem = FakeScheduleDataSource(
        grade={DIA: grade_completa("sala-1", "prof-1", Especialidade.PSICOLOGIA)},
        profissionais={DIA: [profissional("prof-1", "Ana", Especialidade.PSICOLOGIA)]},
    )
    itens = [ItemDemandaBruta(especialidade=Especialidade.PSICOLOGIA, duracao_em_slots=1)]

    resultado = buscar_encaixe(
        origem,
        _continuidade_vazia(),
        "pac-1",
        DIA,
        itens,
        horario_minimo=time(8, 0),
        horario_desejado=time(10, 0),
    )

    assert isinstance(resultado, ResultadoExato)
    assert resultado.opcao.horario_inicio == time(10, 0)


def test_buscar_encaixe_retorna_alternativas_quando_nao_ha_vaga_exata() -> None:
    origem = FakeScheduleDataSource(
        grade={DIA: grade_completa("sala-1", "prof-1", Especialidade.PSICOLOGIA)},
        profissionais={DIA: [profissional("prof-1", "Ana", Especialidade.PSICOLOGIA)]},
        atendimentos={
            DIA: [atendimento("at-1", "prof-1", "sala-1", Especialidade.PSICOLOGIA, [time(8, 0)])]
        },
    )
    itens = [ItemDemandaBruta(especialidade=Especialidade.PSICOLOGIA, duracao_em_slots=1)]

    # 8h (horario_desejado == horario_minimo padrão) já está ocupado: o melhor
    # esforço da engine encontra outro horário livre no dia, mas não é o
    # horário pedido — por isso vira "alternativas", não "exato".
    resultado = buscar_encaixe(
        origem,
        _continuidade_vazia(),
        "pac-1",
        DIA,
        itens,
        horario_minimo=time(8, 0),
        horario_desejado=time(8, 0),
    )

    assert isinstance(resultado, ResultadoAlternativas)
    assert resultado.opcoes


def test_buscar_encaixe_retorna_nenhum_com_horario_desejado_sem_opcao_alguma() -> None:
    origem = FakeScheduleDataSource()
    itens = [ItemDemandaBruta(especialidade=Especialidade.PSICOLOGIA, duracao_em_slots=1)]

    resultado = buscar_encaixe(
        origem,
        _continuidade_vazia(),
        "pac-1",
        DIA,
        itens,
        horario_minimo=time(8, 0),
        horario_desejado=time(9, 0),
    )

    assert isinstance(resultado, ResultadoNenhum)


def test_buscar_encaixe_retorna_nenhum_para_pedido_generico_sem_opcao() -> None:
    origem = FakeScheduleDataSource()
    itens = [ItemDemandaBruta(especialidade=Especialidade.PSICOLOGIA, duracao_em_slots=1)]

    resultado = buscar_encaixe(
        origem,
        _continuidade_vazia(),
        "pac-1",
        DIA,
        itens,
        horario_minimo=time(8, 0),
        horario_desejado=None,
    )

    assert isinstance(resultado, ResultadoNenhum)


def test_sugerir_realocacao_por_id_retorna_none_para_id_inexistente(
    caplog: pytest.LogCaptureFixture,
) -> None:
    origem = FakeScheduleDataSource()

    with caplog.at_level("WARNING"):
        resultado = sugerir_realocacao_por_id(origem, DIA, "atendimento-fantasma")

    assert resultado is None
    assert "atendimento-fantasma" in caplog.text


def test_sugerir_realocacao_por_id_encontra_realocacao_valida() -> None:
    original = atendimento(
        "at-original", "prof-1", "sala-1", Especialidade.FONOAUDIOLOGIA, [time(9, 0)]
    )
    origem = FakeScheduleDataSource(
        grade={DIA: grade_completa("sala-1", "prof-1", Especialidade.FONOAUDIOLOGIA)},
        profissionais={DIA: [profissional("prof-1", "Ana", Especialidade.FONOAUDIOLOGIA)]},
        atendimentos={DIA: [original]},
    )

    opcao = sugerir_realocacao_por_id(origem, DIA, "at-original")

    assert opcao is not None
    assert opcao.itens[0].profissional_id == "prof-1"
