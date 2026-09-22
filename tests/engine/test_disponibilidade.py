"""Testes de `listar_disponibilidade`."""

from datetime import date, time

from app.domain import Atendimento, EntradaGrade, Especialidade, Profissional
from app.domain.slot import Slot
from app.engine.disponibilidade import SlotDisponivel, listar_disponibilidade
from tests.support.fake_schedule_data_source import FakeScheduleDataSource

DIA = date(2026, 9, 8)


def slot(hora: time) -> Slot:
    return Slot(data=DIA, hora_inicio=hora)


def entrada(
    sala_id: str, profissional_id: str, especialidade: Especialidade, hora: time
) -> EntradaGrade:
    return EntradaGrade(
        sala_id=sala_id,
        profissional_id=profissional_id,
        especialidade=especialidade,
        slot=slot(hora),
    )


def atendimento(
    profissional_id: str,
    sala_id: str,
    especialidade: Especialidade,
    hora: time,
    paciente_ids: list[str] | None = None,
) -> Atendimento:
    return Atendimento(
        id=f"at-{profissional_id}-{hora}",
        paciente_ids=paciente_ids if paciente_ids is not None else ["pac-1"],
        profissional_id=profissional_id,
        sala_id=sala_id,
        especialidade=especialidade,
        slots=[slot(hora)],
    )


def profissional(id_: str, nome: str, especialidade: Especialidade) -> Profissional:
    return Profissional(id=id_, nome=nome, especialidade=especialidade)


def test_slot_escalado_sem_atendimento_esta_disponivel() -> None:
    origem = FakeScheduleDataSource(
        grade={DIA: [entrada("sala-1", "prof-1", Especialidade.FONOAUDIOLOGIA, time(9, 0))]},
        profissionais={
            DIA: [profissional("prof-1", "Ana", Especialidade.FONOAUDIOLOGIA)],
        },
    )

    disponiveis = listar_disponibilidade(origem, DIA)

    assert disponiveis == [
        SlotDisponivel(
            slot=slot(time(9, 0)),
            sala_id="sala-1",
            profissional_id="prof-1",
            especialidade=Especialidade.FONOAUDIOLOGIA,
        )
    ]


def test_slot_com_atendimento_nao_aparece_disponivel() -> None:
    origem = FakeScheduleDataSource(
        grade={DIA: [entrada("sala-1", "prof-1", Especialidade.FONOAUDIOLOGIA, time(9, 0))]},
        profissionais={DIA: [profissional("prof-1", "Ana", Especialidade.FONOAUDIOLOGIA)]},
        atendimentos={
            DIA: [atendimento("prof-1", "sala-1", Especialidade.FONOAUDIOLOGIA, time(9, 0))]
        },
    )

    assert listar_disponibilidade(origem, DIA) == []


def test_atendimento_em_grupo_ocupa_o_slot_por_completo() -> None:
    origem = FakeScheduleDataSource(
        grade={DIA: [entrada("sala-1", "prof-1", Especialidade.FONOAUDIOLOGIA, time(9, 0))]},
        profissionais={DIA: [profissional("prof-1", "Ana", Especialidade.FONOAUDIOLOGIA)]},
        atendimentos={
            DIA: [
                atendimento(
                    "prof-1",
                    "sala-1",
                    Especialidade.FONOAUDIOLOGIA,
                    time(9, 0),
                    paciente_ids=["pac-1", "pac-2"],
                )
            ]
        },
    )

    assert listar_disponibilidade(origem, DIA) == []


def _origem_com_dois_profissionais() -> FakeScheduleDataSource:
    return FakeScheduleDataSource(
        grade={
            DIA: [
                entrada("sala-1", "prof-1", Especialidade.FONOAUDIOLOGIA, time(9, 0)),
                entrada("sala-2", "prof-2", Especialidade.PSICOLOGIA, time(9, 0)),
                entrada("sala-1", "prof-1", Especialidade.FONOAUDIOLOGIA, time(9, 30)),
            ]
        },
        profissionais={
            DIA: [
                profissional("prof-1", "Ana", Especialidade.FONOAUDIOLOGIA),
                profissional("prof-2", "Beto", Especialidade.PSICOLOGIA),
            ]
        },
    )


def test_filtro_por_especialidade() -> None:
    origem = _origem_com_dois_profissionais()

    disponiveis = listar_disponibilidade(origem, DIA, especialidade=Especialidade.PSICOLOGIA)

    assert [d.profissional_id for d in disponiveis] == ["prof-2"]


def test_filtro_por_profissional_id() -> None:
    origem = _origem_com_dois_profissionais()

    disponiveis = listar_disponibilidade(origem, DIA, profissional_id="prof-1")

    assert {d.slot.hora_inicio for d in disponiveis} == {time(9, 0), time(9, 30)}
    assert all(d.profissional_id == "prof-1" for d in disponiveis)


def test_filtro_por_sala_id() -> None:
    origem = _origem_com_dois_profissionais()

    disponiveis = listar_disponibilidade(origem, DIA, sala_id="sala-2")

    assert [d.profissional_id for d in disponiveis] == ["prof-2"]


def test_filtros_combinados() -> None:
    origem = _origem_com_dois_profissionais()

    disponiveis = listar_disponibilidade(
        origem,
        DIA,
        especialidade=Especialidade.FONOAUDIOLOGIA,
        sala_id="sala-1",
        profissional_id="prof-1",
    )

    assert len(disponiveis) == 2
    assert all(d.sala_id == "sala-1" and d.profissional_id == "prof-1" for d in disponiveis)


def test_filtro_por_especialidade_combinado_com_sala_sem_correspondencia_fica_vazio() -> None:
    origem = _origem_com_dois_profissionais()

    disponiveis = listar_disponibilidade(
        origem, DIA, especialidade=Especialidade.PSICOLOGIA, sala_id="sala-1"
    )

    assert disponiveis == []


def test_resultado_ordenado_por_horario_e_depois_por_sala() -> None:
    origem = FakeScheduleDataSource(
        grade={
            DIA: [
                entrada("sala-2", "prof-2", Especialidade.PSICOLOGIA, time(9, 0)),
                entrada("sala-1", "prof-1", Especialidade.FONOAUDIOLOGIA, time(9, 0)),
            ]
        },
        profissionais={
            DIA: [
                profissional("prof-1", "Ana", Especialidade.FONOAUDIOLOGIA),
                profissional("prof-2", "Beto", Especialidade.PSICOLOGIA),
            ]
        },
    )

    disponiveis = listar_disponibilidade(origem, DIA)

    assert [d.sala_id for d in disponiveis] == ["sala-1", "sala-2"]
