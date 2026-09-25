"""Testes do parser da planilha.

Tudo aqui roda sobre as abas fictícias de `fixtures.py`: nenhum teste toca a
planilha real, precisa de credencial ou sai para a rede.
"""

import logging
from datetime import date, time

import pytest

from app.data_sources.google_sheets_parser import (
    DadosAgendaDoDia,
    encontrar_titulo_da_aba,
    nome_da_aba,
    parse_worksheet_data,
)
from app.domain import (
    MAPA_ALIAS_PROFISSIONAL,
    MAPA_ESPECIALIDADE_FALLBACK,
    MAPA_SALA_FALLBACK,
    Atendimento,
    Convenio,
    Especialidade,
)
from app.engine.disponibilidade import listar_disponibilidade
from tests.data_sources.fixtures import (
    ALIAS_DE_PROFISSIONAL_FICTICIA,
    COLUNAS_SEM_SALA_FICTICIA,
    ESPECIALIDADE_INVISIVEL_FICTICIA,
    ESTAGIARIO_DIVIDE_COLUNA_FICTICIA,
    POSTOS_DO_MESMO_TITULAR_FICTICIA,
    SEGUNDA_FICTICIA,
    TERCA_FICTICIA,
    AbaFicticia,
    aba_de_uma_celula,
)
from tests.support.fake_schedule_data_source import FakeScheduleDataSource

SEGUNDA = date(2026, 9, 7)
TERCA = date(2026, 9, 8)
SABADO = date(2026, 9, 12)
DOMINGO = date(2026, 9, 13)


def analisar(aba: AbaFicticia, dia: date = SEGUNDA) -> DadosAgendaDoDia:
    return parse_worksheet_data(dia, aba.valores, aba.merges, aba.cores)


@pytest.fixture
def segunda() -> DadosAgendaDoDia:
    return analisar(SEGUNDA_FICTICIA)


def horarios_na_grade(dados: DadosAgendaDoDia, sala_id: str, profissional_id: str) -> set[time]:
    """Horários em que aquele par (sala, profissional) aparece na grade."""
    return {
        entrada.slot.hora_inicio
        for entrada in dados.grade
        if entrada.sala_id == sala_id and entrada.profissional_id == profissional_id
    }


def atendimento_de(dados: DadosAgendaDoDia, paciente_id: str) -> list[Atendimento]:
    return [item for item in dados.atendimentos if paciente_id in item.paciente_ids]


# --- abas, blocos e salas ---------------------------------------------------


def test_detecta_os_dois_blocos_da_aba(segunda: DadosAgendaDoDia) -> None:
    horarios = {entrada.slot.hora_inicio for entrada in segunda.grade}

    assert time(8, 0) in horarios
    assert time(13, 0) in horarios


def test_detecta_bloco_em_aba_com_outro_offset() -> None:
    dados = analisar(TERCA_FICTICIA, TERCA)

    assert [sala.id for sala in dados.salas] == ["sala-1", "sala-2"]
    assert {entrada.slot.hora_inicio for entrada in dados.grade} == {
        time(9, 0),
        time(9, 30),
        time(10, 0),
    }


def test_capacidade_simultanea_vem_do_merge_do_cabecalho(segunda: DadosAgendaDoDia) -> None:
    capacidades = {sala.id: sala.capacidade_simultanea for sala in segunda.salas}

    assert capacidades == {"sala-1": 1, "sala-2": 1, "sala-3": 2, "sala-4": 1}


def test_sala_escrita_com_zero_a_esquerda_vira_a_mesma_sala(segunda: DadosAgendaDoDia) -> None:
    sala = next(sala for sala in segunda.salas if sala.id == "sala-4")

    assert sala.nome == "Sala 4"


def test_coluna_herda_a_sala_do_outro_bloco(segunda: DadosAgendaDoDia) -> None:
    """A tarde não repete o cabeçalho da Sala 2, mas mantém o profissional embaixo."""
    assert time(13, 0) in horarios_na_grade(segunda, "sala-2", "bruno")


def test_divisoria_do_almoco_nao_vira_slot(segunda: DadosAgendaDoDia) -> None:
    assert time(12, 0) not in {entrada.slot.hora_inicio for entrada in segunda.grade}


def test_aba_sem_cabecalho_de_sala_nao_quebra(caplog: pytest.LogCaptureFixture) -> None:
    aba = AbaFicticia(valores=[["", "Coluna solta"], ["09:00", "Alguém"]])

    with caplog.at_level(logging.WARNING):
        dados = analisar(aba)

    assert dados == DadosAgendaDoDia()
    assert "cabeçalho de sala" in caplog.text


# --- profissionais ----------------------------------------------------------


def test_estagiaria_e_descartada_ao_dividir_a_coluna(segunda: DadosAgendaDoDia) -> None:
    bruno = next(item for item in segunda.profissionais if item.id == "bruno")

    assert bruno.nome == "Bruno"
    assert bruno.especialidade is Especialidade.TERAPIA_OCUPACIONAL
    assert "raissa" not in {item.id for item in segunda.profissionais}


def test_estagiario_do_mapa_de_ignorados_some_da_coluna_dividida(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mesmo mecanismo confirmado para a Raíssa real: qualquer nome em
    `PROFISSIONAIS_IGNORAR` some da coluna dividida, sobrando só o titular."""
    monkeypatch.setattr(
        "app.data_sources.google_sheets_parser.PROFISSIONAIS_IGNORAR",
        frozenset({"otavia"}),
    )

    dados = analisar(ESTAGIARIO_DIVIDE_COLUNA_FICTICIA)
    nadia = next(item for item in dados.profissionais if item.id == "nadia")

    assert nadia.especialidade is Especialidade.TERAPIA_OCUPACIONAL
    assert "otavia" not in {item.id for item in dados.profissionais}


def test_especialidade_e_reaproveitada_do_outro_bloco(segunda: DadosAgendaDoDia) -> None:
    """`Ana Beatriz` só aparece com `(Fono)` no bloco da manhã."""
    ana = next(item for item in segunda.profissionais if item.id == "ana-beatriz")

    assert ana.especialidade is Especialidade.FONOAUDIOLOGIA
    assert time(13, 0) in horarios_na_grade(segunda, "sala-1", "ana-beatriz")


def test_profissional_sem_especialidade_em_lugar_nenhum_e_pulado(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        dados = analisar(SEGUNDA_FICTICIA)

    assert "fabio" not in {item.id for item in dados.profissionais}
    assert "Fábio" in caplog.text


def test_coluna_com_dois_profissionais_e_pulada(caplog: pytest.LogCaptureFixture) -> None:
    aba = AbaFicticia(
        valores=[
            ["", "Sala 1"],
            ["", "Ivo (Fono)/Jonas (Fono)"],
            ["09:00", "Paciente Um"],
        ]
    )

    with caplog.at_level(logging.WARNING):
        dados = analisar(aba)

    assert dados.profissionais == []
    assert dados.grade == []
    assert "não resolve para um único profissional" in caplog.text


@pytest.mark.parametrize(
    ("celula", "esperada"),
    [
        ("Ivo (Fono)", Especialidade.FONOAUDIOLOGIA),
        ("Ivo\nPPG", Especialidade.PSICOPEDAGOGIA),
        ("Ivo NeuroPPG", Especialidade.PSICOPEDAGOGIA),
        ("Ivo (Psicopedagogia)", Especialidade.PSICOPEDAGOGIA),
        ("Ivo (Psicóloga)", Especialidade.PSICOLOGIA),
        ("Ivo Psico", Especialidade.PSICOLOGIA),
        ("Ivo (Psicomotricidade)", Especialidade.PSICOMOTRICIDADE),
        ("Ivo Fisio", Especialidade.PSICOMOTRICIDADE),
        ("Ivo (Musicoterapia)", Especialidade.MUSICOTERAPIA),
        ("Musicoterapia (Ivo)", Especialidade.MUSICOTERAPIA),
        ("TO - Ivo", Especialidade.TERAPIA_OCUPACIONAL),
        ("Ivo\nNutri", Especialidade.TERAPIA_ALIMENTAR),
    ],
)
def test_especialidade_e_inferida_das_variacoes_de_escrita(
    celula: str, esperada: Especialidade
) -> None:
    aba = AbaFicticia(valores=[["", "Sala 1"], ["", celula], ["09:00", "Paciente Um"]])

    profissionais = analisar(aba).profissionais

    assert [(item.id, item.especialidade) for item in profissionais] == [("ivo", esperada)]


# --- grade ------------------------------------------------------------------


def test_fechado_nao_entra_na_grade(segunda: DadosAgendaDoDia) -> None:
    horarios = horarios_na_grade(segunda, "sala-3", "carla")

    assert time(8, 0) not in horarios
    assert time(8, 30) not in horarios


def test_celula_vazia_entra_na_grade(segunda: DadosAgendaDoDia) -> None:
    """Sem paciente na célula o profissional está livre — e livre é o que interessa."""
    assert time(9, 0) in horarios_na_grade(segunda, "sala-3", "carla")
    assert atendimento_de(segunda, "carla") == []


def test_sala_mesclada_atende_as_duas_colunas(segunda: DadosAgendaDoDia) -> None:
    profissionais_da_sala_3 = {
        entrada.profissional_id for entrada in segunda.grade if entrada.sala_id == "sala-3"
    }

    assert profissionais_da_sala_3 == {"carla", "dora"}


@pytest.fixture
def postos(monkeypatch: pytest.MonkeyPatch) -> DadosAgendaDoDia:
    monkeypatch.setattr(
        "app.data_sources.google_sheets_parser.PROFISSIONAIS_IGNORAR",
        frozenset({"iara", "joana"}),
    )
    return analisar(POSTOS_DO_MESMO_TITULAR_FICTICIA)


def test_mesmo_titular_em_tres_colunas_vira_tres_postos_distintos(
    postos: DadosAgendaDoDia,
) -> None:
    """Sem o posto, as três entradas das 09:00 seriam idênticas."""
    entradas = [
        entrada
        for entrada in postos.grade
        if entrada.sala_id == "sala-5" and entrada.slot.hora_inicio == time(9, 0)
    ]

    assert {entrada.profissional_id for entrada in entradas} == {"helena"}
    assert sorted(entrada.indice_posto for entrada in entradas) == [0, 1, 2]


def test_atendimento_leva_o_posto_da_coluna_de_origem(postos: DadosAgendaDoDia) -> None:
    posto_por_paciente = {
        paciente: atendimento_de(postos, paciente)[0].indice_posto
        for paciente in ("paciente-um", "paciente-dois", "paciente-tres")
    }

    assert posto_por_paciente == {"paciente-um": 0, "paciente-dois": 1, "paciente-tres": 2}


def test_sala_de_capacidade_um_fica_sempre_no_posto_zero(postos: DadosAgendaDoDia) -> None:
    sala_6 = next(sala for sala in postos.salas if sala.id == "sala-6")

    assert sala_6.capacidade_simultanea == 1
    assert {entrada.indice_posto for entrada in postos.grade if entrada.sala_id == "sala-6"} == {0}
    assert atendimento_de(postos, "paciente-quatro")[0].indice_posto == 0


def test_postos_do_mesmo_titular_viram_vagas_distintas_na_disponibilidade(
    postos: DadosAgendaDoDia,
) -> None:
    """Da aba à disponibilidade: às 09:30 o posto 0 segue com o Paciente Um e os
    postos 1 e 2 estão livres — nenhum dos dois pode sumir por causa do posto 0."""
    origem = FakeScheduleDataSource(
        salas={SEGUNDA: postos.salas},
        profissionais={SEGUNDA: postos.profissionais},
        grade={SEGUNDA: postos.grade},
        atendimentos={SEGUNDA: postos.atendimentos},
    )

    livres = [
        (item.slot.hora_inicio, item.indice_posto)
        for item in listar_disponibilidade(origem, SEGUNDA, sala_id="sala-5")
    ]

    assert livres == [(time(9, 30), 1), (time(9, 30), 2)]


def test_coluna_que_herda_a_sala_do_outro_bloco_herda_tambem_o_posto() -> None:
    """A tarde não repete o merge `B1:C1`: as colunas herdam sala e posto da manhã."""
    aba = AbaFicticia(
        valores=[
            ["", "Sala 5", "", "Sala 9"],
            ["", "Lia TO", "Lia TO", "Mel (Fono)"],
            ["09:00", "", "", ""],
            ["", "", "", "Sala 9"],
            ["", "Lia TO", "Lia TO", "Mel (Fono)"],
            ["13:00", "", "", ""],
        ],
        merges=["B1:C1"],
    )

    dados = analisar(aba)
    postos_da_tarde = sorted(
        entrada.indice_posto
        for entrada in dados.grade
        if entrada.sala_id == "sala-5" and entrada.slot.hora_inicio == time(13, 0)
    )

    assert postos_da_tarde == [0, 1]


def test_coluna_resgatada_pelo_mapa_de_fallback_fica_no_posto_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(MAPA_SALA_FALLBACK, ("segunda", "jonas"), "Sala 12")

    dados = analisar(COLUNAS_SEM_SALA_FICTICIA)

    assert {entrada.indice_posto for entrada in dados.grade if entrada.sala_id == "sala-12"} == {0}


def test_estagiaria_vitoria_e_descartada_ao_dividir_a_coluna() -> None:
    """Mesmo padrão da Raíssa e do Marley, com o nome confirmado pela clínica."""
    dados = analisar(
        AbaFicticia(valores=[["", "Sala 1"], ["", "Bruno (TO)/Vitória"], ["09:00", ""]])
    )

    assert [item.id for item in dados.profissionais] == ["bruno"]


# --- atendimentos -----------------------------------------------------------


def test_slots_consecutivos_com_o_mesmo_paciente_viram_um_atendimento(
    segunda: DadosAgendaDoDia,
) -> None:
    manha = [item for item in atendimento_de(segunda, "zezinho-mendes") if item.data == SEGUNDA]
    primeiro = min(manha, key=lambda item: item.slots[0])

    assert primeiro.duracao_minutos == 60
    assert [slot.hora_inicio for slot in primeiro.slots] == [time(8, 0), time(8, 30)]
    assert primeiro.id == "2026-09-07-B4"


def test_paciente_diferente_no_slot_seguinte_abre_outro_atendimento(
    segunda: DadosAgendaDoDia,
) -> None:
    marina = [
        item
        for item in atendimento_de(segunda, "marina-duarte")
        if item.sala_id == "sala-1" and item.slots[0].hora_inicio == time(9, 0)
    ]

    assert len(marina) == 1
    assert marina[0].duracao_minutos == 30


def test_sessao_em_grupo_vira_um_atendimento_com_dois_pacientes(
    segunda: DadosAgendaDoDia,
) -> None:
    grupo = atendimento_de(segunda, "ravy-ficticio")

    assert len(grupo) == 1
    assert grupo[0].paciente_ids == ["ravy-ficticio", "samuel-ficto"]
    assert grupo[0].duracao_minutos == 60


def test_horario_duplicado_usa_a_primeira_ocorrencia(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        dados = analisar(TERCA_FICTICIA, TERCA)

    pacientes = {
        identificador for item in dados.atendimentos for identificador in item.paciente_ids
    }

    assert pacientes == {"paciente-um", "paciente-dois"}
    assert atendimento_de(dados, "paciente-um")[0].duracao_minutos == 60
    assert "09:30 aparece de novo na linha 5" in caplog.text


def test_celula_so_com_pontuacao_e_tratada_como_vazia(
    segunda: DadosAgendaDoDia, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        dados = analisar(SEGUNDA_FICTICIA)

    assert [item for item in dados.atendimentos if item.sala_id == "sala-4"] == []
    assert time(8, 0) in horarios_na_grade(segunda, "sala-4", "elis")
    assert "sem nome nenhum" in caplog.text


# --- convênio por cor de fonte ----------------------------------------------


@pytest.mark.parametrize(
    ("cor", "esperado"),
    [
        ("#0000FF", Convenio.SULAMERICA),
        ("#FF0000", Convenio.KLINI_SAUDE),
        ("#CC0000", Convenio.KLINI_SAUDE),
        ("#274E13", Convenio.UNIMED),
        ("#38761D", Convenio.UNIMED),
        ("#6AA84F", Convenio.UNIMED),
        ("#9900FF", Convenio.PARTICULAR),
    ],
)
def test_cada_cor_de_fonte_resolve_o_convenio(cor: str, esperado: Convenio) -> None:
    dados = analisar(aba_de_uma_celula("Paciente Um", cor))

    assert dados.pacientes[0].convenio is esperado
    assert dados.atendimentos[0].aguardando_autorizacao is False


def test_fonte_preta_marca_aguardando_autorizacao() -> None:
    dados = analisar(aba_de_uma_celula("Paciente Um", "#000000"))

    assert dados.atendimentos[0].aguardando_autorizacao is True
    assert dados.pacientes[0].convenio is None


def test_cor_fora_do_mapa_vira_convenio_desconhecido(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        dados = analisar(aba_de_uma_celula("Paciente Um", "#123456"))

    assert dados.pacientes[0].convenio is None
    assert dados.atendimentos[0].aguardando_autorizacao is False
    assert "#123456" in caplog.text


def test_celula_sem_cor_informada_nao_vira_aviso(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        dados = analisar(aba_de_uma_celula("Paciente Um"))

    assert dados.pacientes[0].convenio is None
    assert caplog.text == ""


def test_convenio_ja_conhecido_nao_e_apagado_por_uma_celula_preta(
    segunda: DadosAgendaDoDia,
) -> None:
    zezinho = next(item for item in segunda.pacientes if item.id == "zezinho-mendes")
    tarde = next(
        item for item in atendimento_de(segunda, "zezinho-mendes") if item.id.endswith("11")
    )

    assert zezinho.convenio is Convenio.SULAMERICA
    assert tarde.aguardando_autorizacao is True


# --- fallbacks confirmados com a clínica ------------------------------------
#
# Os mapas de `app.domain.constants` carregam nomes reais da clínica; aqui eles
# são estendidos com nomes fictícios para que o teste exercite o mecanismo sem
# depender de quem está no mapa de produção.


def test_sala_do_mapa_de_fallback_resgata_coluna_sem_cabecalho(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(MAPA_SALA_FALLBACK, ("segunda", "jonas"), "Sala 12")

    dados = analisar(COLUNAS_SEM_SALA_FICTICIA)
    sala = next(item for item in dados.salas if item.id == "sala-12")

    assert sala.nome == "Sala 12"
    assert sala.capacidade_simultanea == 1
    assert time(9, 0) in horarios_na_grade(dados, "sala-12", "jonas")
    assert atendimento_de(dados, "paciente-dois")[0].sala_id == "sala-12"


def test_sala_do_mapa_de_fallback_vale_por_aba(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A chave inclui o nome da aba: o mesmo mecanismo vale pra qualquer dia,
    não só pra Segunda — é o que permite salas diferentes em dias diferentes
    pro mesmo profissional."""
    monkeypatch.setitem(MAPA_SALA_FALLBACK, ("terca", "jonas"), "Sala 12")

    dados = analisar(COLUNAS_SEM_SALA_FICTICIA, TERCA)
    sala = next(item for item in dados.salas if item.id == "sala-12")

    assert sala.nome == "Sala 12"
    assert time(9, 0) in horarios_na_grade(dados, "sala-12", "jonas")


def test_coluna_sem_sala_fora_do_mapa_continua_sendo_pulada(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """O fallback vale só para os casos confirmados: o resto segue sendo descartado."""
    monkeypatch.setitem(MAPA_SALA_FALLBACK, ("segunda", "jonas"), "Sala 12")

    with caplog.at_level(logging.WARNING):
        dados = analisar(COLUNAS_SEM_SALA_FICTICIA)

    assert "kelly" not in {item.id for item in dados.profissionais}
    assert atendimento_de(dados, "paciente-tres") == []
    assert "'Kelly'" in caplog.text
    assert "nenhuma sala no cabeçalho: coluna ignorada" in caplog.text


def test_resumo_de_colunas_sem_sala_e_logado_no_fim_da_aba(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        analisar(COLUNAS_SEM_SALA_FICTICIA)

    assert (
        "2 coluna(s) ignorada(s) por sala não identificada na aba 'Segunda': "
        "coluna C, coluna D" in caplog.text
    )


def test_especialidade_vem_do_mapa_de_fallback_quando_nao_da_para_inferir(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setitem(MAPA_ESPECIALIDADE_FALLBACK, "lia", Especialidade.MUSICOTERAPIA)

    with caplog.at_level(logging.WARNING):
        dados = analisar(ESPECIALIDADE_INVISIVEL_FICTICIA)

    assert [(item.id, item.especialidade) for item in dados.profissionais] == [
        ("lia", Especialidade.MUSICOTERAPIA)
    ]
    assert atendimento_de(dados, "paciente-um")[0].especialidade is Especialidade.MUSICOTERAPIA
    assert caplog.text == ""


def test_duas_grafias_do_mesmo_profissional_viram_um_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(MAPA_ALIAS_PROFISSIONAL, "mirna-sousa", "mirna-souza")

    dados = analisar(ALIAS_DE_PROFISSIONAL_FICTICIA)

    assert [(item.id, item.nome) for item in dados.profissionais] == [
        ("mirna-souza", "Mirna Souza")
    ]
    assert {item.profissional_id for item in dados.atendimentos} == {"mirna-souza"}
    assert {entrada.profissional_id for entrada in dados.grade} == {"mirna-souza"}
    assert {entrada.sala_id for entrada in dados.grade} == {"sala-1", "sala-2"}


def test_sala_do_mapa_de_fallback_com_nome_fora_do_formato_e_ignorada(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """O mapa de fallback é preenchido à mão: um valor torto não pode derrubar
    o parser, só a coluna daquele profissional específico."""
    monkeypatch.setitem(MAPA_SALA_FALLBACK, ("segunda", "jonas"), "Bloco B")

    with caplog.at_level(logging.WARNING):
        dados = analisar(COLUNAS_SEM_SALA_FICTICIA)

    assert "jonas" not in {item.id for item in dados.profissionais}
    assert "não está no formato 'Sala N'" in caplog.text


def test_convenio_desconhecido_e_atualizado_quando_uma_ocorrencia_seguinte_tem_cor() -> None:
    """O inverso de `test_convenio_ja_conhecido_nao_e_apagado_por_uma_celula_preta`:
    paciente visto primeiro sem cor (convênio desconhecido) tem o convênio
    preenchido assim que uma ocorrência seguinte traz a cor."""
    aba = AbaFicticia(
        valores=[
            ["", "Sala 1", "Sala 2"],
            ["", "Ivo (Fono)", "Jade (Fono)"],
            ["09:00", "Paciente Um", ""],
            ["09:30", "", "Paciente Um"],
        ],
        cores={"C4": "#0000FF"},
    )

    dados = analisar(aba)

    [paciente] = dados.pacientes
    assert paciente.convenio is Convenio.SULAMERICA


def test_cabecalho_de_salas_sem_linhas_de_horario_depois_e_ignorado(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Cabeçalho de sala colado no fim da aba, sem espaço para a linha de
    profissionais e ao menos uma linha de horário: o bloco é descartado, não o
    parser inteiro."""
    aba = AbaFicticia(valores=[["", "Sala 1"], ["", "Ivo (Fono)"]])

    with caplog.at_level(logging.WARNING):
        dados = analisar(aba)

    assert dados == DadosAgendaDoDia()
    assert "bloco ignorado" in caplog.text


def test_intervalo_mesclado_fora_da_notacao_a1_e_ignorado_com_aviso(
    caplog: pytest.LogCaptureFixture,
) -> None:
    aba = AbaFicticia(
        valores=[["", "Sala 1"], ["", "Ivo (Fono)"], ["09:00", "Paciente Um"]],
        merges=["B2"],
    )

    with caplog.at_level(logging.WARNING):
        dados = analisar(aba)

    assert "Intervalo mesclado 'B2' não está em notação A1: ignorado." in caplog.text
    # O resto do parsing segue normalmente: só o merge quebrado é descartado.
    assert dados.salas[0].id == "sala-1"


def test_linha_de_dados_mais_curta_que_o_cabecalho_e_tratada_como_celula_vazia() -> None:
    """A API do Sheets omite células vazias no fim da linha: a coluna que
    sobra numa linha mais curta não pode estourar índice, só valer vazio."""
    aba = AbaFicticia(
        valores=[
            ["", "Sala 1", "Sala 2"],
            ["", "Ivo (Fono)", "Jade (Fono)"],
            ["09:00", "Paciente Um"],
        ]
    )

    dados = analisar(aba)

    assert atendimento_de(dados, "paciente-um") != []
    assert time(9, 0) in horarios_na_grade(dados, "sala-2", "jade")


def test_horario_fora_do_intervalo_valido_e_tratado_como_linha_sem_dados() -> None:
    """`_PADRAO_HORA` aceita `\\d{1,2}:\\d{2}`, então `25:00` casa o regex mas
    não é um horário válido — precisa ser descartado sem quebrar a aba."""
    aba = AbaFicticia(
        valores=[
            ["", "Sala 1"],
            ["", "Ivo (Fono)"],
            ["25:00", "Paciente Um"],
            ["09:00", "Paciente Dois"],
        ]
    )

    dados = analisar(aba)

    assert atendimento_de(dados, "paciente-um") == []
    assert atendimento_de(dados, "paciente-dois") != []


def test_sem_alias_as_duas_grafias_seriam_profissionais_diferentes() -> None:
    """Contraprova: é o mapa que junta as duas, não a normalização do nome."""
    dados = analisar(ALIAS_DE_PROFISSIONAL_FICTICIA)

    assert {item.id for item in dados.profissionais} == {"mirna-sousa", "mirna-souza"}


# --- dias sem aba -----------------------------------------------------------


def test_domingo_nao_tem_aba() -> None:
    assert nome_da_aba(DOMINGO) is None
    assert encontrar_titulo_da_aba(DOMINGO, ["Segunda", " Sábado "]) is None


def test_domingo_devolve_agenda_vazia_sem_erro() -> None:
    assert analisar(SEGUNDA_FICTICIA, DOMINGO) == DadosAgendaDoDia()


def test_aba_do_sabado_e_achada_apesar_dos_espacos() -> None:
    assert encontrar_titulo_da_aba(SABADO, ["Segunda", " Sábado ", "Nutri Ester"]) == " Sábado "


def test_titulo_da_aba_ausente_devolve_none() -> None:
    assert encontrar_titulo_da_aba(SEGUNDA, ["Terça", "Quarta"]) is None
