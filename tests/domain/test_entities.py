"""Testes das entidades do domínio."""

from datetime import date, time

import pytest
from pydantic import ValidationError

from app.domain import (
    HORARIO_PREFERENCIAL_PADRAO,
    Atendimento,
    AtendimentoComBuracoError,
    AtendimentoEmDiasDiferentesError,
    Convenio,
    EntradaGrade,
    Especialidade,
    ItemSolicitacao,
    Paciente,
    Profissional,
    Sala,
    Slot,
    SolicitacaoAtendimento,
)

DIA = date(2026, 9, 10)
OUTRO_DIA = date(2026, 9, 11)


def slot(hora: time, data: date = DIA) -> Slot:
    return Slot(data=data, hora_inicio=hora)


def atendimento(slots: list[Slot], paciente_ids: list[str] | None = None) -> Atendimento:
    return Atendimento(
        id="at-1",
        paciente_ids=paciente_ids if paciente_ids is not None else ["pac-1"],
        profissional_id="prof-1",
        sala_id="sala-1",
        especialidade=Especialidade.FONOAUDIOLOGIA,
        slots=slots,
    )


def test_profissional_tem_exatamente_uma_especialidade() -> None:
    profissional = Profissional(id="prof-1", nome="Ana", especialidade=Especialidade.PSICOLOGIA)

    assert profissional.especialidade is Especialidade.PSICOLOGIA


def test_profissional_com_especialidade_desconhecida_falha() -> None:
    with pytest.raises(ValidationError):
        Profissional(id="prof-1", nome="Ana", especialidade="acupuntura")


def test_sala_tem_capacidade_simultanea_um_por_padrao() -> None:
    assert Sala(id="sala-1", nome="Sala Azul").capacidade_simultanea == 1


def test_sala_aceita_capacidade_simultanea_maior_que_um() -> None:
    assert Sala(id="sala-2", nome="Sala Verde", capacidade_simultanea=3).capacidade_simultanea == 3


@pytest.mark.parametrize("capacidade", [0, -1, -10])
def test_sala_com_capacidade_invalida_falha(capacidade: int) -> None:
    with pytest.raises(ValidationError):
        Sala(id="sala-1", nome="Sala Azul", capacidade_simultanea=capacidade)


def test_paciente_com_convenio_opcional() -> None:
    sem_convenio = Paciente(id="pac-1", nome="João")
    com_convenio = Paciente(id="pac-2", nome="Maria", convenio=Convenio.UNIMED)

    assert sem_convenio.convenio is None
    assert com_convenio.convenio is Convenio.UNIMED


def test_paciente_com_convenio_desconhecido_falha() -> None:
    with pytest.raises(ValidationError):
        Paciente(id="pac-1", nome="João", convenio="amil")


def test_atendimento_em_grupo_tem_mais_de_um_paciente() -> None:
    resultado = atendimento([slot(time(9, 0))], paciente_ids=["pac-1", "pac-2"])

    assert resultado.paciente_ids == ["pac-1", "pac-2"]


def test_atendimento_sem_paciente_falha() -> None:
    with pytest.raises(ValidationError):
        atendimento([slot(time(9, 0))], paciente_ids=[])


def test_atendimento_com_paciente_vazio_falha() -> None:
    with pytest.raises(ValidationError):
        atendimento([slot(time(9, 0))], paciente_ids=["pac-1", ""])


def test_atendimento_nao_aguarda_autorizacao_por_padrao() -> None:
    assert atendimento([slot(time(9, 0))]).aguardando_autorizacao is False


def test_atendimento_fica_no_posto_zero_por_padrao() -> None:
    assert atendimento([slot(time(9, 0))]).indice_posto == 0


def test_atendimento_com_posto_negativo_falha() -> None:
    campos = atendimento([slot(time(9, 0))]).model_dump()

    with pytest.raises(ValidationError):
        Atendimento.model_validate({**campos, "indice_posto": -1})


def entrada_de_grade(**campos: object) -> EntradaGrade:
    return EntradaGrade.model_validate(
        {
            "sala_id": "sala-1",
            "profissional_id": "prof-1",
            "especialidade": Especialidade.FONOAUDIOLOGIA,
            "slot": slot(time(9, 0)),
            **campos,
        }
    )


def test_entrada_de_grade_exige_o_posto() -> None:
    with pytest.raises(ValidationError):
        entrada_de_grade()


def test_entrada_de_grade_com_posto_negativo_falha() -> None:
    with pytest.raises(ValidationError):
        entrada_de_grade(indice_posto=-1)


def test_entradas_de_postos_diferentes_nao_sao_iguais() -> None:
    assert entrada_de_grade(indice_posto=0) != entrada_de_grade(indice_posto=1)


def test_atendimento_com_slots_contiguos_calcula_duracao() -> None:
    resultado = atendimento([slot(time(9, 0)), slot(time(9, 30)), slot(time(10, 0))])

    assert resultado.duracao_minutos == 90
    assert resultado.data == DIA


def test_atendimento_normaliza_os_slots_em_ordem() -> None:
    resultado = atendimento([slot(time(10, 0)), slot(time(9, 0)), slot(time(9, 30))])

    assert [s.hora_inicio for s in resultado.slots] == [time(9, 0), time(9, 30), time(10, 0)]


def test_atendimento_com_slots_nao_contiguos_levanta_erro() -> None:
    with pytest.raises(AtendimentoComBuracoError):
        atendimento([slot(time(9, 0)), slot(time(10, 0))])


def test_atendimento_atravessando_a_pausa_e_buraco() -> None:
    with pytest.raises(AtendimentoComBuracoError):
        atendimento([slot(time(11, 30)), slot(time(13, 0))])


def test_atendimento_com_slots_em_dias_diferentes_levanta_erro() -> None:
    with pytest.raises(AtendimentoEmDiasDiferentesError):
        atendimento([slot(time(9, 0)), slot(time(9, 30), data=OUTRO_DIA)])


def test_atendimento_sem_slots_falha() -> None:
    with pytest.raises(ValidationError):
        atendimento([])


def test_atendimento_de_um_unico_slot_e_valido() -> None:
    assert atendimento([slot(time(9, 0))]).duracao_minutos == 30


def test_solicitacao_soma_os_slots_dos_itens() -> None:
    solicitacao = SolicitacaoAtendimento(
        paciente_id="pac-1",
        data=DIA,
        itens=[
            ItemSolicitacao(especialidade=Especialidade.FONOAUDIOLOGIA, duracao_em_slots=2),
            ItemSolicitacao(especialidade=Especialidade.PSICOLOGIA, duracao_em_slots=1),
        ],
    )

    assert solicitacao.total_de_slots == 3
    assert solicitacao.itens[0].duracao_minutos == 60


@pytest.mark.parametrize("duracao", [0, -1])
def test_item_de_solicitacao_com_duracao_invalida_falha(duracao: int) -> None:
    with pytest.raises(ValidationError):
        ItemSolicitacao(especialidade=Especialidade.MUSICOTERAPIA, duracao_em_slots=duracao)


def test_solicitacao_com_item_de_duracao_zero_falha() -> None:
    with pytest.raises(ValidationError):
        SolicitacaoAtendimento(
            paciente_id="pac-1",
            data=DIA,
            itens=[{"especialidade": Especialidade.MUSICOTERAPIA, "duracao_em_slots": 0}],
        )


def test_solicitacao_sem_itens_falha() -> None:
    with pytest.raises(ValidationError):
        SolicitacaoAtendimento(paciente_id="pac-1", data=DIA, itens=[])


def test_item_solicitacao_sem_profissional_id_por_padrao() -> None:
    item = ItemSolicitacao(especialidade=Especialidade.FONOAUDIOLOGIA, duracao_em_slots=1)

    assert item.profissional_id is None


def test_item_solicitacao_aceita_profissional_id_para_continuidade() -> None:
    item = ItemSolicitacao(
        especialidade=Especialidade.FONOAUDIOLOGIA, duracao_em_slots=1, profissional_id="prof-1"
    )

    assert item.profissional_id == "prof-1"


def test_solicitacao_tem_horario_minimo_preferencial_por_padrao() -> None:
    solicitacao = SolicitacaoAtendimento(
        paciente_id="pac-1",
        data=DIA,
        itens=[ItemSolicitacao(especialidade=Especialidade.PSICOLOGIA, duracao_em_slots=1)],
    )

    assert solicitacao.horario_minimo == HORARIO_PREFERENCIAL_PADRAO
    assert solicitacao.horario_desejado is None


def test_solicitacao_aceita_horario_minimo_e_desejado_explicitos() -> None:
    solicitacao = SolicitacaoAtendimento(
        paciente_id="pac-1",
        data=DIA,
        itens=[ItemSolicitacao(especialidade=Especialidade.PSICOLOGIA, duracao_em_slots=1)],
        horario_minimo=time(7, 0),
        horario_desejado=time(10, 0),
    )

    assert solicitacao.horario_minimo == time(7, 0)
    assert solicitacao.horario_desejado == time(10, 0)


@pytest.mark.parametrize("horario_minimo", [time(6, 30), time(18, 30)])
def test_solicitacao_com_horario_minimo_fora_do_expediente_falha(horario_minimo: time) -> None:
    with pytest.raises(ValidationError):
        SolicitacaoAtendimento(
            paciente_id="pac-1",
            data=DIA,
            itens=[ItemSolicitacao(especialidade=Especialidade.PSICOLOGIA, duracao_em_slots=1)],
            horario_minimo=horario_minimo,
        )


@pytest.mark.parametrize("horario_desejado", [time(6, 30), time(18, 30)])
def test_solicitacao_com_horario_desejado_fora_do_expediente_falha(
    horario_desejado: time,
) -> None:
    with pytest.raises(ValidationError):
        SolicitacaoAtendimento(
            paciente_id="pac-1",
            data=DIA,
            itens=[ItemSolicitacao(especialidade=Especialidade.PSICOLOGIA, duracao_em_slots=1)],
            horario_desejado=horario_desejado,
        )
