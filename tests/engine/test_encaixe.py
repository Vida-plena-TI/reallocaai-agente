"""Testes de encaixe casado e sugestão de realocação (Fase 4b)."""

from datetime import date, time

import pytest
from pydantic import ValidationError

from app.domain import (
    Atendimento,
    EntradaGrade,
    Especialidade,
    ItemSolicitacao,
    Profissional,
    Slot,
    SolicitacaoAtendimento,
)
from app.engine.encaixe import (
    CenarioSugestao,
    ItemEncaixeResolvido,
    _tentar_a_partir_de,
    buscar_alternativas,
    buscar_melhor_encaixe,
    sugerir_realocacao,
)
from tests.support.fake_schedule_data_source import FakeScheduleDataSource

DIA = date(2026, 9, 8)


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
    sala_id: str,
    profissional_id: str,
    especialidade: Especialidade,
    excluir: set[time] | None = None,
    indice_posto: int = 0,
) -> list[EntradaGrade]:
    horarios_excluidos = excluir or set()
    return [
        entrada(sala_id, profissional_id, especialidade, slot.hora_inicio, indice_posto)
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
    indice_posto: int = 0,
) -> Atendimento:
    return Atendimento(
        id=id_,
        paciente_ids=paciente_ids if paciente_ids is not None else ["pac-1"],
        profissional_id=profissional_id,
        sala_id=sala_id,
        especialidade=especialidade,
        slots=[Slot(data=DIA, hora_inicio=hora) for hora in horas],
        indice_posto=indice_posto,
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


def test_item_encaixe_resolvido_recusa_slots_de_dias_diferentes() -> None:
    outro_dia = date(2026, 9, 9)

    with pytest.raises(ValidationError, match="mesmo dia"):
        ItemEncaixeResolvido(
            especialidade=Especialidade.PSICOLOGIA,
            profissional_id="prof-1",
            sala_id="sala-1",
            indice_posto=0,
            slots=[
                Slot(data=DIA, hora_inicio=time(9, 0)),
                Slot(data=outro_dia, hora_inicio=time(9, 30)),
            ],
        )


def test_item_encaixe_resolvido_recusa_slots_nao_contiguos() -> None:
    with pytest.raises(ValidationError, match="contíguos"):
        ItemEncaixeResolvido(
            especialidade=Especialidade.PSICOLOGIA,
            profissional_id="prof-1",
            sala_id="sala-1",
            indice_posto=0,
            slots=[
                Slot(data=DIA, hora_inicio=time(9, 0)),
                Slot(data=DIA, hora_inicio=time(10, 0)),
            ],
        )


def test_encaixe_casado_ignora_profissional_que_troca_de_sala_entre_slots() -> None:
    """Mesmo profissional livre nos dois slots, mas em salas diferentes: não serve.

    O item pede um bloco de 2 slots numa única sala — um profissional que só
    cobre a duração inteira trocando de sala no meio não é candidato válido.
    """
    origem = FakeScheduleDataSource(
        grade={
            DIA: [
                entrada("sala-1", "prof-1", Especialidade.FONOAUDIOLOGIA, time(9, 0)),
                entrada("sala-2", "prof-1", Especialidade.FONOAUDIOLOGIA, time(9, 30)),
            ]
        },
        profissionais={DIA: [profissional("prof-1", "Ana", Especialidade.FONOAUDIOLOGIA)]},
    )
    solicitacao = SolicitacaoAtendimento(
        paciente_id="pac-1",
        data=DIA,
        itens=[ItemSolicitacao(especialidade=Especialidade.FONOAUDIOLOGIA, duracao_em_slots=2)],
    )

    assert buscar_melhor_encaixe(origem, solicitacao) is None


def test_buscar_alternativas_sem_horario_desejado_retorna_lista_vazia() -> None:
    origem = FakeScheduleDataSource(
        grade={DIA: grade_completa("sala-1", "prof-1", Especialidade.PSICOLOGIA)},
        profissionais={DIA: [profissional("prof-1", "Ana", Especialidade.PSICOLOGIA)]},
    )
    solicitacao = SolicitacaoAtendimento(
        paciente_id="pac-1",
        data=DIA,
        itens=[ItemSolicitacao(especialidade=Especialidade.PSICOLOGIA, duracao_em_slots=1)],
    )

    assert buscar_alternativas(origem, solicitacao) == []


def test_score_desfragmentacao_ignora_atendimento_excluido() -> None:
    """Ao realocar um atendimento, o próprio atendimento não deve inflar o
    score de desfragmentação de uma opção adjacente a ele."""
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

    # Sem exclusão, 13:00 venceria por encostar no atendimento das 13:30 (ver
    # test_melhor_para_clinica_prefere_opcao_adjacente_a_atendimento_existente).
    # Excluindo esse mesmo atendimento (caso de `sugerir_realocacao`), ele não
    # deve mais contar como "ocupado" para efeito de score.
    cenarios = buscar_alternativas(origem, solicitacao, excluir_atendimento_id="at-1")

    melhor = next(c for c in cenarios if c.cenario is CenarioSugestao.MELHOR_PARA_CLINICA)
    assert melhor.opcao.horario_inicio != time(13, 0)
    assert melhor.opcao.horario_inicio == time(8, 0)


# --- postos: o mesmo profissional em várias colunas da mesma sala ----------

TO = Especialidade.TERAPIA_OCUPACIONAL


def _solicitacao_de_to(
    duracao_em_slots: int, horario_desejado: time | None = None
) -> SolicitacaoAtendimento:
    return SolicitacaoAtendimento(
        paciente_id="pac-1",
        data=DIA,
        itens=[ItemSolicitacao(especialidade=TO, duracao_em_slots=duracao_em_slots)],
        horario_minimo=time(7, 0),
        horario_desejado=horario_desejado,
    )


def _helena() -> dict[date, list[Profissional]]:
    return {DIA: [profissional("helena", "Helena", TO)]}


def test_item_de_dois_slots_nao_mistura_postos_diferentes() -> None:
    """Posto 0 livre só às 09:00 e posto 1 livre só às 09:30: juntos cobririam
    uma hora, mas nenhum posto sozinho cobre — não há encaixe."""
    origem = FakeScheduleDataSource(
        grade={
            DIA: [
                entrada("sala-5", "helena", TO, time(9, 0), indice_posto=0),
                entrada("sala-5", "helena", TO, time(9, 30), indice_posto=1),
            ]
        },
        profissionais=_helena(),
    )
    solicitacao = _solicitacao_de_to(2)

    assert _tentar_a_partir_de(origem, solicitacao, time(9, 0)) is None
    assert buscar_melhor_encaixe(origem, solicitacao) is None


def test_item_de_dois_slots_fica_no_posto_que_cobre_a_duracao_inteira() -> None:
    """Mesmo cenário, com o posto 1 livre também às 09:00: só ele serve."""
    origem = FakeScheduleDataSource(
        grade={
            DIA: [
                entrada("sala-5", "helena", TO, time(9, 0), indice_posto=0),
                entrada("sala-5", "helena", TO, time(9, 0), indice_posto=1),
                entrada("sala-5", "helena", TO, time(9, 30), indice_posto=1),
            ]
        },
        profissionais=_helena(),
    )

    opcao = buscar_melhor_encaixe(origem, _solicitacao_de_to(2))

    assert opcao is not None
    assert opcao.horario_inicio == time(9, 0)
    assert opcao.itens[0].indice_posto == 1


def test_varios_postos_livres_escolhe_sempre_o_menor() -> None:
    """Desempate determinístico: com os três postos livres, sai o posto 0 em
    toda execução, qualquer que seja a ordem da grade."""
    grade = [
        item
        for posto in (2, 0, 1)
        for item in grade_completa("sala-5", "helena", TO, indice_posto=posto)
    ]

    postos_escolhidos: set[int] = set()
    for grade_da_vez in [grade, list(reversed(grade))] * 5:
        origem = FakeScheduleDataSource(grade={DIA: grade_da_vez}, profissionais=_helena())
        opcao = buscar_melhor_encaixe(origem, _solicitacao_de_to(2))
        assert opcao is not None
        postos_escolhidos.add(opcao.itens[0].indice_posto)

    assert postos_escolhidos == {0}


def test_posto_menor_ocupado_cede_para_o_proximo_livre() -> None:
    origem = FakeScheduleDataSource(
        grade={
            DIA: [
                *grade_completa("sala-5", "helena", TO, indice_posto=0),
                *grade_completa("sala-5", "helena", TO, indice_posto=1),
            ]
        },
        profissionais=_helena(),
        atendimentos={
            DIA: [atendimento("at-1", "helena", "sala-5", TO, [time(7, 30)], indice_posto=0)]
        },
    )

    opcao = buscar_melhor_encaixe(origem, _solicitacao_de_to(2))

    assert opcao is not None
    assert opcao.horario_inicio == time(7, 0)
    assert opcao.itens[0].indice_posto == 1


def _melhor_para_clinica(indice_posto_do_vizinho: int) -> time:
    """Horário escolhido como MELHOR_PARA_CLINICA com um atendimento às 13:30.

    O posto 0 tem a grade toda (menos 09:00, o horário pedido); o posto 1 só
    existe às 13:30, onde fica o atendimento vizinho quando ele é do posto 1.
    """
    origem = FakeScheduleDataSource(
        grade={
            DIA: [
                *grade_completa("sala-5", "helena", TO, excluir={time(9, 0)}),
                entrada("sala-5", "helena", TO, time(13, 30), indice_posto=1),
            ]
        },
        profissionais=_helena(),
        atendimentos={
            DIA: [
                atendimento(
                    "at-1",
                    "helena",
                    "sala-5",
                    TO,
                    [time(13, 30)],
                    indice_posto=indice_posto_do_vizinho,
                )
            ]
        },
    )

    cenarios = buscar_alternativas(origem, _solicitacao_de_to(1, horario_desejado=time(9, 0)))
    melhor = next(c for c in cenarios if c.cenario is CenarioSugestao.MELHOR_PARA_CLINICA)
    return melhor.opcao.horario_inicio


def test_desfragmentacao_conta_vizinho_do_mesmo_posto() -> None:
    """Controle: com o vizinho no mesmo posto, 13:00 vence por encostar nele."""
    assert _melhor_para_clinica(indice_posto_do_vizinho=0) == time(13, 0)


def test_desfragmentacao_ignora_vizinho_de_outro_posto() -> None:
    """O atendimento das 13:30 está no posto 1: a vaga das 13:00 no posto 0
    continua isolada, então nenhuma opção pontua e vale o primeiro horário."""
    assert _melhor_para_clinica(indice_posto_do_vizinho=1) == time(7, 0)


def test_sala_de_capacidade_um_resolve_no_posto_zero() -> None:
    origem = FakeScheduleDataSource(
        grade={DIA: grade_completa("sala-1", "prof-1", Especialidade.FONOAUDIOLOGIA)},
        profissionais={DIA: [profissional("prof-1", "Ana", Especialidade.FONOAUDIOLOGIA)]},
    )
    solicitacao = SolicitacaoAtendimento(
        paciente_id="pac-1",
        data=DIA,
        itens=[ItemSolicitacao(especialidade=Especialidade.FONOAUDIOLOGIA, duracao_em_slots=2)],
    )

    opcao = buscar_melhor_encaixe(origem, solicitacao)

    assert opcao is not None
    assert opcao.horario_inicio == time(8, 0)
    assert [(item.sala_id, item.indice_posto) for item in opcao.itens] == [("sala-1", 0)]
