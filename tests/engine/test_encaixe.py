"""Testes de encaixe casado e sugestão de realocação (Fase 4b)."""

from datetime import date, time

from app.data_sources.base import EntradaGrade
from app.domain import (
    Atendimento,
    Especialidade,
    ItemSolicitacao,
    Profissional,
    Slot,
    SolicitacaoAtendimento,
)
from app.engine.encaixe import (
    CenarioSugestao,
    _tentar_a_partir_de,
    buscar_alternativas,
    buscar_melhor_encaixe,
    sugerir_realocacao,
)
from tests.support.fake_schedule_data_source import FakeScheduleDataSource

DIA = date(2026, 9, 8)


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
    sala_id: str,
    profissional_id: str,
    especialidade: Especialidade,
    excluir: set[time] | None = None,
) -> list[EntradaGrade]:
    horarios_excluidos = excluir or set()
    return [
        entrada(sala_id, profissional_id, especialidade, slot.hora_inicio)
        for slot in Slot.slots_do_dia(DIA)
        if slot.hora_inicio not in horarios_excluidos
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


def test_sem_horario_desejado_retorna_a_partir_do_horario_preferencial_padrao() -> None:
    origem = FakeScheduleDataSource(
        grade={DIA: grade_completa("sala-1", "prof-1", Especialidade.FONOAUDIOLOGIA)},
        profissionais={DIA: [profissional("prof-1", "Ana", Especialidade.FONOAUDIOLOGIA)]},
    )
    solicitacao = SolicitacaoAtendimento(
        paciente_id="pac-1",
        data=DIA,
        itens=[ItemSolicitacao(especialidade=Especialidade.FONOAUDIOLOGIA, duracao_em_slots=1)],
    )

    opcao = buscar_melhor_encaixe(origem, solicitacao)

    assert opcao is not None
    assert opcao.horario_inicio == time(8, 0)


def test_horario_minimo_explicito_permite_horario_antes_das_8h() -> None:
    origem = FakeScheduleDataSource(
        grade={DIA: grade_completa("sala-1", "prof-1", Especialidade.FONOAUDIOLOGIA)},
        profissionais={DIA: [profissional("prof-1", "Ana", Especialidade.FONOAUDIOLOGIA)]},
    )
    solicitacao = SolicitacaoAtendimento(
        paciente_id="pac-1",
        data=DIA,
        itens=[ItemSolicitacao(especialidade=Especialidade.FONOAUDIOLOGIA, duracao_em_slots=1)],
        horario_minimo=time(7, 0),
    )

    opcao = buscar_melhor_encaixe(origem, solicitacao)

    assert opcao is not None
    assert opcao.horario_inicio == time(7, 0)


def test_encaixe_casado_encontra_ordem_diferente_da_solicitada() -> None:
    origem = FakeScheduleDataSource(
        grade={
            DIA: [
                entrada("sala-2", "prof-psico", Especialidade.PSICOLOGIA, time(8, 0)),
                entrada("sala-2", "prof-psico", Especialidade.PSICOLOGIA, time(8, 30)),
                entrada("sala-1", "prof-fono", Especialidade.FONOAUDIOLOGIA, time(8, 30)),
            ]
        },
        profissionais={
            DIA: [
                profissional("prof-psico", "Beto", Especialidade.PSICOLOGIA),
                profissional("prof-fono", "Ana", Especialidade.FONOAUDIOLOGIA),
            ]
        },
    )
    # A ordem pedida é FONO antes de PSICO, mas FONO só tem vaga às 8:30 —
    # só a ordem invertida (PSICO às 8:00, FONO às 8:30) funciona.
    solicitacao = SolicitacaoAtendimento(
        paciente_id="pac-1",
        data=DIA,
        itens=[
            ItemSolicitacao(especialidade=Especialidade.FONOAUDIOLOGIA, duracao_em_slots=1),
            ItemSolicitacao(especialidade=Especialidade.PSICOLOGIA, duracao_em_slots=1),
        ],
    )

    opcao = buscar_melhor_encaixe(origem, solicitacao)

    assert opcao is not None
    assert [item.especialidade for item in opcao.itens] == [
        Especialidade.PSICOLOGIA,
        Especialidade.FONOAUDIOLOGIA,
    ]
    assert opcao.horario_inicio == time(8, 0)
    assert opcao.horario_fim == time(9, 0)


def test_profissional_id_forcado_restringe_a_busca_mesmo_com_vaga_melhor_alhures() -> None:
    origem = FakeScheduleDataSource(
        grade={
            DIA: [
                entrada("sala-1", "prof-a", Especialidade.FONOAUDIOLOGIA, time(8, 0)),
                entrada("sala-2", "prof-b", Especialidade.FONOAUDIOLOGIA, time(9, 0)),
            ]
        },
        profissionais={
            DIA: [
                profissional("prof-a", "Ana", Especialidade.FONOAUDIOLOGIA),
                profissional("prof-b", "Beto", Especialidade.FONOAUDIOLOGIA),
            ]
        },
    )
    solicitacao = SolicitacaoAtendimento(
        paciente_id="pac-1",
        data=DIA,
        itens=[
            ItemSolicitacao(
                especialidade=Especialidade.FONOAUDIOLOGIA,
                duracao_em_slots=1,
                profissional_id="prof-b",
            )
        ],
    )

    opcao = buscar_melhor_encaixe(origem, solicitacao)

    assert opcao is not None
    assert opcao.itens[0].profissional_id == "prof-b"
    assert opcao.horario_inicio == time(9, 0)


def test_encaixe_casado_nao_atravessa_a_pausa_do_almoco() -> None:
    origem = FakeScheduleDataSource(
        grade={
            DIA: [
                entrada("sala-1", "prof-fono", Especialidade.FONOAUDIOLOGIA, time(11, 30)),
                entrada("sala-1", "prof-fono", Especialidade.FONOAUDIOLOGIA, time(13, 0)),
                entrada("sala-1", "prof-fono", Especialidade.FONOAUDIOLOGIA, time(13, 30)),
                entrada("sala-2", "prof-psico", Especialidade.PSICOLOGIA, time(13, 0)),
                entrada("sala-2", "prof-psico", Especialidade.PSICOLOGIA, time(13, 30)),
            ]
        },
        profissionais={
            DIA: [
                profissional("prof-fono", "Ana", Especialidade.FONOAUDIOLOGIA),
                profissional("prof-psico", "Beto", Especialidade.PSICOLOGIA),
            ]
        },
    )
    solicitacao = SolicitacaoAtendimento(
        paciente_id="pac-1",
        data=DIA,
        itens=[
            ItemSolicitacao(especialidade=Especialidade.FONOAUDIOLOGIA, duracao_em_slots=1),
            ItemSolicitacao(especialidade=Especialidade.PSICOLOGIA, duracao_em_slots=1),
        ],
        horario_desejado=time(11, 30),
    )

    # FONO às 11:30 + PSICO logo em seguida cairia às 12:00, dentro da pausa:
    # nenhuma ordem deve produzir uma opção começando ali.
    assert _tentar_a_partir_de(origem, solicitacao, time(11, 30)) is None

    opcao = buscar_melhor_encaixe(origem, solicitacao)

    assert opcao is not None
    assert opcao.horario_inicio == time(13, 0)


def test_horario_desejado_disponivel_retorna_direto_via_buscar_melhor_encaixe() -> None:
    origem = FakeScheduleDataSource(
        grade={DIA: grade_completa("sala-1", "prof-1", Especialidade.PSICOLOGIA)},
        profissionais={DIA: [profissional("prof-1", "Ana", Especialidade.PSICOLOGIA)]},
    )
    solicitacao = SolicitacaoAtendimento(
        paciente_id="pac-1",
        data=DIA,
        itens=[ItemSolicitacao(especialidade=Especialidade.PSICOLOGIA, duracao_em_slots=1)],
        horario_desejado=time(10, 0),
    )

    opcao = buscar_melhor_encaixe(origem, solicitacao)

    assert opcao is not None
    assert opcao.horario_inicio == time(10, 0)


def test_buscar_alternativas_omite_cenario_anterior_inexistente() -> None:
    origem = FakeScheduleDataSource(
        grade={DIA: grade_completa("sala-1", "prof-1", Especialidade.PSICOLOGIA)},
        profissionais={DIA: [profissional("prof-1", "Ana", Especialidade.PSICOLOGIA)]},
        atendimentos={
            DIA: [atendimento("at-1", "prof-1", "sala-1", Especialidade.PSICOLOGIA, [time(8, 0)])]
        },
    )
    # horario_minimo padrão (8h) é igual ao horario_desejado: não há nenhum
    # horário válido antes dele, então MAIS_PROXIMO_ANTERIOR não existe.
    solicitacao = SolicitacaoAtendimento(
        paciente_id="pac-1",
        data=DIA,
        itens=[ItemSolicitacao(especialidade=Especialidade.PSICOLOGIA, duracao_em_slots=1)],
        horario_desejado=time(8, 0),
    )

    cenarios = buscar_alternativas(origem, solicitacao)

    rotulos = {c.cenario for c in cenarios}
    assert CenarioSugestao.MAIS_PROXIMO_ANTERIOR not in rotulos
    assert CenarioSugestao.MAIS_PROXIMO_SEGUINTE in rotulos
    assert CenarioSugestao.MELHOR_PARA_CLINICA in rotulos

    seguinte = next(c for c in cenarios if c.cenario is CenarioSugestao.MAIS_PROXIMO_SEGUINTE)
    assert seguinte.opcao.horario_inicio == time(8, 30)


def test_melhor_para_clinica_prefere_opcao_adjacente_a_atendimento_existente() -> None:
    origem = FakeScheduleDataSource(
        grade={
            DIA: grade_completa(
                "sala-1", "prof-1", Especialidade.FONOAUDIOLOGIA, excluir={time(9, 0)}
            )
        },
        profissionais={DIA: [profissional("prof-1", "Ana", Especialidade.FONOAUDIOLOGIA)]},
        atendimentos={
            DIA: [
                atendimento(
                    "at-1", "prof-1", "sala-1", Especialidade.FONOAUDIOLOGIA, [time(13, 30)]
                )
            ]
        },
    )
    solicitacao = SolicitacaoAtendimento(
        paciente_id="pac-1",
        data=DIA,
        itens=[ItemSolicitacao(especialidade=Especialidade.FONOAUDIOLOGIA, duracao_em_slots=1)],
        horario_desejado=time(9, 0),
    )

    cenarios = buscar_alternativas(origem, solicitacao)

    seguinte = next(c for c in cenarios if c.cenario is CenarioSugestao.MAIS_PROXIMO_SEGUINTE)
    melhor = next(c for c in cenarios if c.cenario is CenarioSugestao.MELHOR_PARA_CLINICA)

    # 9:30 é a vaga isolada mais próxima (a "seguinte" óbvia); 13:00 encosta
    # no atendimento já existente às 13:30 e desfragmenta melhor a agenda.
    assert seguinte.opcao.horario_inicio == time(9, 30)
    assert melhor.opcao.horario_inicio == time(13, 0)
    assert melhor.opcao.horario_inicio != seguinte.opcao.horario_inicio


def test_sugerir_realocacao_ignora_conflito_consigo_mesmo() -> None:
    horas_ocupadas = [time(8, 0), time(8, 30), time(9, 0), time(9, 30)]
    grade = [
        entrada("sala-1", "prof-1", Especialidade.FONOAUDIOLOGIA, hora)
        for hora in [*horas_ocupadas, time(10, 0)]
    ]
    original = atendimento(
        "at-original", "prof-1", "sala-1", Especialidade.FONOAUDIOLOGIA, [time(10, 0)]
    )
    outros = [
        atendimento(f"at-{hora}", "prof-1", "sala-1", Especialidade.FONOAUDIOLOGIA, [hora])
        for hora in horas_ocupadas
    ]
    origem = FakeScheduleDataSource(
        grade={DIA: grade},
        profissionais={DIA: [profissional("prof-1", "Ana", Especialidade.FONOAUDIOLOGIA)]},
        atendimentos={DIA: [original, *outros]},
    )

    opcao = sugerir_realocacao(origem, original)

    assert opcao is not None
    assert opcao.itens[0].profissional_id == "prof-1"
    assert opcao.horario_inicio == time(10, 0)


def test_sugerir_realocacao_com_duracao_maior_busca_bloco_maior() -> None:
    horas = [time(8, 0), time(8, 30), time(9, 0), time(9, 30), time(10, 0), time(10, 30)]
    grade = [entrada("sala-1", "prof-1", Especialidade.FONOAUDIOLOGIA, hora) for hora in horas]
    original = atendimento(
        "at-original", "prof-1", "sala-1", Especialidade.FONOAUDIOLOGIA, [time(9, 0)]
    )
    outros = [
        atendimento(f"at-{hora}", "prof-1", "sala-1", Especialidade.FONOAUDIOLOGIA, [hora])
        for hora in [time(8, 0), time(8, 30), time(9, 30)]
    ]
    origem = FakeScheduleDataSource(
        grade={DIA: grade},
        profissionais={DIA: [profissional("prof-1", "Ana", Especialidade.FONOAUDIOLOGIA)]},
        atendimentos={DIA: [original, *outros]},
    )

    opcao = sugerir_realocacao(origem, original, nova_duracao_em_slots=2)

    assert opcao is not None
    assert opcao.horario_inicio == time(10, 0)
    assert len(opcao.itens[0].slots) == 2


def test_sugerir_realocacao_retorna_none_sem_opcao_valida() -> None:
    original = atendimento(
        "at-original", "prof-1", "sala-1", Especialidade.FONOAUDIOLOGIA, [time(9, 0)]
    )
    origem = FakeScheduleDataSource(
        profissionais={DIA: [profissional("prof-1", "Ana", Especialidade.FONOAUDIOLOGIA)]},
        atendimentos={DIA: [original]},
    )

    assert sugerir_realocacao(origem, original) is None
