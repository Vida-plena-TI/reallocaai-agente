"""Contrato numérico, privacidade e extração dos artefatos do turno."""

import json
import logging
import re
from datetime import date
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_openai.chat_models.base import _convert_message_to_dict
from pydantic import ValidationError

from app.ai.agente import extrair_blocos_do_turno
from app.ai.relatorios import (
    AVISO_ACIMA_DE_CEM,
    BlocoRelatorio,
    DadosOcupacaoAgregada,
    DadosOcupacaoProfissional,
    DadosPacientesProfissional,
    TabelaExportacao,
)
from app.ai.tools import criar_tools
from app.data_sources.continuidade import SemHistoricoContinuidadeDataSource
from app.domain import Profissional, Sala
from app.domain.formatacao import formatar_razao
from app.engine.carga_profissionais import construir_carga_profissionais
from app.engine.ocupacao import construir_relatorio_ocupacao_do_dia
from app.engine.ocupacao_profissional import construir_ocupacao_semanal_profissional
from tests.support.agenda_semanal import (
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
from tests.support.relatorios import ANA, NOMES_PACIENTES, fonte_relatorios


def mensagem_tool(
    nome: str,
    args: dict[str, Any],
    fonte: FakeScheduleDataSource | None = None,
) -> ToolMessage:
    tools = criar_tools(
        fonte or fonte_relatorios(), SemHistoricoContinuidadeDataSource(), SEGUNDA, lambda *_: None
    )
    ferramenta = next(t for t in tools if t.name == nome)
    assert ferramenta.response_format == "content_and_artifact"
    mensagem = ferramenta.invoke({"name": nome, "args": args, "id": "c1", "type": "tool_call"})
    assert isinstance(mensagem, ToolMessage)
    return mensagem


@pytest.mark.parametrize(
    "nome,args,tipo",
    [
        ("consultar_ocupacao_profissional", {"profissional": "Ana"}, "ocupacao_profissional"),
        ("consultar_pacientes_por_profissional", {}, "pacientes_por_profissional"),
        ("consultar_ocupacao", {"data": SEGUNDA.isoformat()}, "ocupacao_agregada"),
    ],
)
def test_contrato_tabelas_privacidade_e_transporte(
    nome: str, args: dict[str, Any], tipo: str
) -> None:
    mensagem = mensagem_tool(nome, args)
    bloco = BlocoRelatorio.model_validate(mensagem.artifact)
    assert bloco.tipo == tipo and bloco.versao == 1
    assert bloco.dados.tipo == tipo
    serializado = bloco.model_dump_json()
    assert all(nome not in serializado for nome in NOMES_PACIENTES)
    assert "paciente_ids" not in serializado
    assert "artifact" not in _convert_message_to_dict(mensagem)
    assert not bloco.parcial and bloco.dias_nao_lidos == []
    chaves_de_meta = {"abaixo_da_meta", "slots_para_meta", "meta"}
    if tipo == "pacientes_por_profissional":
        assert bloco.meta is None
        assert all(not chaves_de_meta & {c.chave for c in t.colunas} for t in bloco.tabelas)
    else:
        assert bloco.meta == 0.8
    for tabela in bloco.tabelas:
        chaves = {c.chave for c in tabela.colunas}
        assert tabela.linhas
        for linha in tabela.linhas:
            assert set(linha) == chaves
            for coluna in tabela.colunas:
                valor = linha[coluna.chave]
                if coluna.formato == "data":
                    assert isinstance(valor, str) and len(valor) == 10
                    assert valor == date.fromisoformat(valor).isoformat()
                    assert "dia_semana" in linha
                elif coluna.formato == "percentual":
                    assert isinstance(valor, float) and valor >= 0
                elif coluna.formato == "inteiro":
                    assert type(valor) is int
    with pytest.raises(ValidationError):
        bloco.__setattr__("titulo", "outro")
    assert BlocoRelatorio.model_validate(json.loads(serializado)) == bloco


def test_ocupacao_profissional_copia_todas_as_metricas_da_engine() -> None:
    mensagem = mensagem_tool("consultar_ocupacao_profissional", {"profissional": "Ana"})
    bloco = BlocoRelatorio.model_validate(mensagem.artifact)
    dados = bloco.dados
    assert isinstance(dados, DadosOcupacaoProfissional)
    engine = construir_ocupacao_semanal_profissional(fonte_relatorios(), ANA.id, SEGUNDA)
    assert dados.profissional.nome == engine.nome
    assert dados.profissional.id != engine.profissional_id
    for campo in ("escalados", "ocupados", "livres", "para_meta"):
        chave = "slots_para_meta" if campo == "para_meta" else campo
        assert getattr(dados.semana, chave) == getattr(engine, f"slots_{campo}")
    assert dados.semana.percentual == engine.percentual
    assert dados.semana.abaixo_da_meta == engine.abaixo_da_meta
    assert [d.data for d in dados.dias_sem_agenda] == engine.dias_sem_agenda
    for dia, e in zip(dados.dias, engine.dias, strict=True):
        assert dia.data == e.data
        assert (
            dia.escalados,
            dia.ocupados,
            dia.livres,
            dia.percentual,
            dia.abaixo_da_meta,
            dia.slots_para_meta,
        ) == (
            e.slots_escalados,
            e.slots_ocupados,
            e.slots_livres,
            e.percentual,
            e.abaixo_da_meta,
            e.slots_para_meta,
        )
        assert (dia.manha.escalados, dia.manha.ocupados) == (e.manha_escalados, e.manha_ocupados)
        assert (dia.tarde.escalados, dia.tarde.ocupados) == (e.tarde_escalados, e.tarde_ocupados)
        for posto, p in zip(dia.por_sala_posto, e.por_sala_posto, strict=True):
            assert (
                posto.sala_id,
                posto.sala_nome,
                posto.posto,
                posto.escalados,
                posto.ocupados,
            ) == (p.sala_id, p.sala_nome, p.indice_posto + 1, p.slots_escalados, p.slots_ocupados)
    assert all(item.exibicao in str(mensagem.content) for item in bloco.resumo)
    assert all(aviso in str(mensagem.content) for aviso in bloco.avisos)


@pytest.mark.parametrize(
    "args",
    [
        {},
        {"profissional": "Ana"},
        {"escopo": "dia"},
        {"escopo": "dia", "profissional": "Ana"},
        {"especialidade": "fono"},
    ],
)
def test_pacientes_copia_engine_e_respeita_filtros(args: dict[str, Any]) -> None:
    mensagem = mensagem_tool("consultar_pacientes_por_profissional", args)
    bloco = BlocoRelatorio.model_validate(mensagem.artifact)
    dados = bloco.dados
    assert isinstance(dados, DadosPacientesProfissional)
    engine = construir_carga_profissionais(
        fonte_relatorios(), [SEGUNDA] if args.get("escopo") == "dia" else SEMANA
    )
    por_nome = {e.nome: e for e in engine.profissionais}
    for p in dados.profissionais:
        e = por_nome[p.nome]
        assert p.media_pacientes_por_dia == e.media_pacientes_por_dia
        assert p.pacientes_distintos_semana == e.pacientes_distintos_semana
        assert [d.data for d in p.dias_sem_agenda] == e.dias_sem_agenda
        assert [d.model_dump(exclude={"dia_semana"}) for d in p.dias] == [
            d.model_dump() for d in e.dias
        ]
    assert [(d.data, d.pacientes_distintos) for d in dados.clinica_por_dia] == [
        (d.data, d.pacientes_distintos_clinica) for d in engine.por_dia
    ]
    assert all(item.exibicao in str(mensagem.content) for item in bloco.resumo)
    if "profissional" in args:
        assert len(dados.profissionais) == 1
    if "especialidade" in args:
        assert all(p.especialidade == "fonoaudiologia" for p in dados.profissionais)


def test_agregada_copia_engine_e_texto_usa_a_mesma_exibicao() -> None:
    mensagem = mensagem_tool("consultar_ocupacao", {"data": SEGUNDA.isoformat()})
    bloco = BlocoRelatorio.model_validate(mensagem.artifact)
    dados = bloco.dados
    assert isinstance(dados, DadosOcupacaoAgregada)
    engine = construir_relatorio_ocupacao_do_dia(fonte_relatorios(), SEGUNDA)
    for itens, esperados in [
        (dados.por_sala, list(engine.por_sala().values())),
        (dados.por_especialidade, list(engine.por_especialidade().values())),
    ]:
        assert sorted(
            (i.slots_escalados, i.slots_ocupados, i.percentual, i.abaixo_da_meta) for i in itens
        ) == sorted(
            (e.slots_escalados, e.slots_ocupados, e.percentual, e.abaixo_da_meta) for e in esperados
        )
        for i in itens:
            assert formatar_razao(i.slots_ocupados, i.slots_escalados, percentual=True) in str(
                mensagem.content
            )


@pytest.mark.parametrize(
    "nome,args,cenario",
    [
        ("consultar_ocupacao_profissional", {"profissional": "Ana"}, "ambiguo"),
        ("consultar_pacientes_por_profissional", {"profissional": "Ana"}, "ambiguo"),
        ("consultar_ocupacao_profissional", {"profissional": "Zelda"}, "normal"),
        ("consultar_pacientes_por_profissional", {"profissional": "Zelda"}, "normal"),
        ("consultar_ocupacao_profissional", {"profissional": "Ana"}, "sem_agenda"),
        ("consultar_pacientes_por_profissional", {"profissional": "Ana"}, "sem_agenda"),
        ("consultar_ocupacao", {"data": SEGUNDA.isoformat()}, "vazio"),
        ("consultar_pacientes_por_profissional", {}, "vazio"),
        ("consultar_pacientes_por_profissional", {"especialidade": "astronomia"}, "normal"),
        ("consultar_ocupacao_profissional", {"profissional": "Ana"}, "erro"),
        ("consultar_pacientes_por_profissional", {}, "erro"),
        ("consultar_ocupacao", {"data": SEGUNDA.isoformat()}, "erro"),
    ],
)
def test_caminhos_sem_bloco(nome: str, args: dict[str, Any], cenario: str) -> None:
    fonte = fonte_relatorios()
    if cenario == "ambiguo":
        fonte.profissionais[SEGUNDA].append(
            Profissional(id="ana-outra", nome="Ana Outra", especialidade=ANA.especialidade)
        )
    elif cenario == "sem_agenda":
        fonte.grade.clear()
        fonte.atendimentos.clear()
    elif cenario == "vazio":
        fonte = FakeScheduleDataSource()
    elif cenario == "erro":
        fonte.dias_com_falha = set(SEMANA)
    mensagem = mensagem_tool(nome, args, fonte)
    assert isinstance(mensagem.content, str) and mensagem.content
    assert mensagem.artifact is None


def test_parcial_e_inconsistencia_aparecem_tambem_nos_avisos() -> None:
    fonte = fonte_relatorios()
    fonte.dias_com_falha = {QUARTA}
    fonte.grade[TERCA] = []
    mensagem = mensagem_tool("consultar_ocupacao_profissional", {"profissional": "Ana"}, fonte)
    bloco = BlocoRelatorio.model_validate(mensagem.artifact)
    assert bloco.parcial and bloco.dias_nao_lidos == [QUARTA]
    assert isinstance(bloco.dados, DadosOcupacaoProfissional) and bloco.dados.inconsistencia
    assert len(bloco.avisos) == 3
    assert all(aviso in str(mensagem.content) for aviso in bloco.avisos)
    mensagem = mensagem_tool("consultar_pacientes_por_profissional", {}, fonte)
    bloco = BlocoRelatorio.model_validate(mensagem.artifact)
    assert bloco.parcial and bloco.dias_nao_lidos == [QUARTA]
    assert mensagem.artifact["dias_nao_lidos"] == [QUARTA.isoformat()]
    assert all(aviso in str(mensagem.content) for aviso in bloco.avisos)


def test_extracao_so_turno_atual_ordem_validacao_e_limite(caplog: pytest.LogCaptureFixture) -> None:
    primeira = mensagem_tool("consultar_ocupacao_profissional", {"profissional": "Ana"})
    segunda = mensagem_tool("consultar_pacientes_por_profissional", {})
    mensagens = [
        HumanMessage("antes"),
        primeira,
        AIMessage("antes"),
        HumanMessage("agora"),
        segunda,
        primeira,
        ToolMessage("erro", tool_call_id="x", artifact={"tipo": "x"}),
    ]
    with caplog.at_level(logging.WARNING):
        blocos = extrair_blocos_do_turno(mensagens)
    assert [b.tipo for b in blocos] == ["pacientes_por_profissional", "ocupacao_profissional"]
    assert "inválido" in caplog.text
    caplog.clear()
    assert len(extrair_blocos_do_turno([HumanMessage("agora"), *([primeira] * 12)])) == 10
    assert "Limite de 10" in caplog.text
    assert extrair_blocos_do_turno([primeira]) == []


def test_artefatos_rejeitam_tipo_versao_e_linhas_incoerentes() -> None:
    artifact = mensagem_tool("consultar_ocupacao", {"data": SEGUNDA.isoformat()}).artifact
    alteracoes: list[dict[str, Any]] = [
        {"versao": 2},
        {"tipo": "pacientes_por_profissional"},
        {"meta": None},
        {"parcial": True},
        {"dias_nao_lidos": [SEGUNDA.isoformat()]},
    ]
    for alteracao in alteracoes:
        with pytest.raises(ValidationError):
            BlocoRelatorio.model_validate({**artifact, **alteracao})
    with pytest.raises(ValidationError):
        TabelaExportacao(nome="Inválida", colunas=[], linhas=[{"extra": 1}])


def test_arredondamento_comercial_empates_e_zero() -> None:
    assert formatar_razao(1, 16, percentual=True) == "6,3%"
    assert formatar_razao(25, 20) == "1,3"
    assert formatar_razao(44, 54, percentual=True) == "81,5%"
    assert formatar_razao(0, 0, percentual=True) == "0,0%"


def test_posto_e_o_numero_de_exibicao_contado_a_partir_de_um() -> None:
    fonte = fonte_relatorios()
    fonte.salas[SEGUNDA].append(Sala(id="sala-20", nome="Sala Verde", capacidade_simultanea=3))
    horas = horas_da_manha(SEGUNDA)
    distribuicao = [("sala-12", 0), ("sala-12", 1), ("sala-20", 0), ("sala-20", 1), ("sala-20", 2)]
    fonte.grade[SEGUNDA] = [
        entrada
        for i, (sala, posto) in enumerate(distribuicao)
        for entrada in grade(SEGUNDA, horas[2 * i : 2 * i + 2], ANA, sala, posto)
    ]
    fonte.atendimentos[SEGUNDA] = []
    mensagem = mensagem_tool("consultar_ocupacao_profissional", {"profissional": "Ana"}, fonte)
    bloco = BlocoRelatorio.model_validate(mensagem.artifact)
    assert isinstance(bloco.dados, DadosOcupacaoProfissional)
    segunda = bloco.dados.dias[0]
    assert sorted((p.sala_id, p.posto) for p in segunda.por_sala_posto) == [
        ("sala-12", 1),
        ("sala-12", 2),
        ("sala-20", 1),
        ("sala-20", 2),
        ("sala-20", 3),
    ]
    for p in segunda.por_sala_posto:
        assert f"{p.sala_nome} (posto {p.posto}):" in str(mensagem.content)
    assert "posto 0" not in str(mensagem.content)


def fonte_cento_e_cinco_por_cento() -> FakeScheduleDataSource:
    """Ana com 20 slots escalados e 21 ocupados na segunda (um atendimento fora da grade)."""
    fonte = fonte_relatorios()
    horas = horas_da_manha(SEGUNDA) + horas_da_tarde(SEGUNDA)
    fonte.grade = {SEGUNDA: grade(SEGUNDA, horas, ANA)}
    fonte.atendimentos = {
        SEGUNDA: atendimentos(SEGUNDA, horas, ANA)
        + atendimentos(SEGUNDA, horas[:1], ANA, indice_posto=1)
    }
    return fonte


def test_ocupacao_profissional_acima_de_cem_por_cento_devolve_bloco_com_valor_real() -> None:
    fonte = fonte_cento_e_cinco_por_cento()
    engine = construir_ocupacao_semanal_profissional(fonte, ANA.id, SEGUNDA)
    assert engine.percentual == 1.05 and not engine.tem_inconsistencia
    mensagem = mensagem_tool("consultar_ocupacao_profissional", {"profissional": "Ana"}, fonte)
    bloco = BlocoRelatorio.model_validate(mensagem.artifact)
    dados = bloco.dados
    assert isinstance(dados, DadosOcupacaoProfissional)
    assert dados.semana.percentual == dados.dias[0].percentual == 1.05
    assert (dados.semana.ocupados, dados.semana.escalados) == (21, 20)
    assert not dados.semana.abaixo_da_meta and not dados.dias[0].abaixo_da_meta
    assert dados.inconsistencia
    assert AVISO_ACIMA_DE_CEM in bloco.avisos
    assert all(aviso in str(mensagem.content) for aviso in bloco.avisos)
    assert bloco.resumo[0].exibicao == "105,0%"
    assert BlocoRelatorio.model_validate(json.loads(bloco.model_dump_json())) == bloco


def test_ocupacao_agregada_acima_de_cem_por_cento_devolve_bloco_com_aviso() -> None:
    fonte = fonte_cento_e_cinco_por_cento()
    mensagem = mensagem_tool("consultar_ocupacao", {"data": SEGUNDA.isoformat()}, fonte)
    bloco = BlocoRelatorio.model_validate(mensagem.artifact)
    dados = bloco.dados
    assert isinstance(dados, DadosOcupacaoAgregada)
    assert [(i.percentual, i.abaixo_da_meta) for i in dados.por_sala] == [(1.05, False)]
    assert bloco.avisos == [AVISO_ACIMA_DE_CEM]
    assert AVISO_ACIMA_DE_CEM in str(mensagem.content)
    assert "105,0%" in str(mensagem.content)


@pytest.mark.parametrize(
    "nome,args",
    [
        ("consultar_ocupacao_profissional", {"profissional": "Ana"}),
        ("consultar_ocupacao", {"data": SEGUNDA.isoformat()}),
    ],
)
def test_ocupacao_acima_de_cem_por_cento_mantem_o_valor_da_engine(
    nome: str,
    args: dict[str, Any],
) -> None:
    fonte = fonte_relatorios()
    fonte.grade[SEGUNDA] = fonte.grade[SEGUNDA][:1]
    engine = construir_ocupacao_semanal_profissional(fonte, ANA.id, SEGUNDA)
    assert engine.dias[0].percentual == 3.0
    mensagem = mensagem_tool(nome, args, fonte)
    bloco = BlocoRelatorio.model_validate(mensagem.artifact)
    assert AVISO_ACIMA_DE_CEM in bloco.avisos
    assert "300,0%" in str(mensagem.content)


def test_texto_voltado_ao_usuario_usa_data_legivel_e_nunca_iso() -> None:
    fonte = fonte_relatorios()
    fonte.dias_com_falha = {QUARTA}
    iso = re.compile(r"\d{4}-\d{2}-\d{2}")
    for nome, args in [
        ("consultar_ocupacao_profissional", {"profissional": "Ana"}),
        ("consultar_pacientes_por_profissional", {}),
        ("consultar_pacientes_por_profissional", {"escopo": "dia", "profissional": "Ana"}),
        ("consultar_ocupacao", {"data": SEGUNDA.isoformat()}),
    ]:
        bloco = BlocoRelatorio.model_validate(mensagem_tool(nome, args, fonte).artifact)
        textos = [bloco.titulo, *bloco.avisos]
        textos += [f"{i.rotulo} {i.exibicao}" for i in bloco.resumo]
        textos += [t.nome for t in bloco.tabelas]
        textos += [c.rotulo for t in bloco.tabelas for c in t.colunas]
        assert not [t for t in textos if iso.search(t)], nome


def test_titulo_do_agregado_traz_a_data_em_dd_mm_aaaa() -> None:
    dia = date(2026, 10, 5)
    fonte = FakeScheduleDataSource(
        profissionais={dia: [ANA]},
        salas={dia: [Sala(id="sala-12", nome="Sala Azul")]},
        grade={dia: grade(dia, horas_da_manha(dia), ANA)},
    )
    bloco = BlocoRelatorio.model_validate(
        mensagem_tool("consultar_ocupacao", {"data": dia.isoformat()}, fonte).artifact
    )
    assert bloco.titulo == "Ocupação de 05/10/2026"
    assert bloco.periodo.inicio == dia
    assert isinstance(bloco.dados, DadosOcupacaoAgregada) and bloco.dados.data == dia


def test_salas_em_ordem_natural_no_texto_nos_dados_e_na_tabela() -> None:
    horas = horas_da_manha(SEGUNDA)
    salas = [("sala-2", "Sala 2"), ("sala-10", "Sala 10"), ("sala-1", "Sala 1")]
    fonte = FakeScheduleDataSource(
        profissionais={SEGUNDA: [ANA]},
        salas={SEGUNDA: [Sala(id=i, nome=n) for i, n in salas]},
        grade={
            SEGUNDA: [
                entrada
                for posicao, (sala_id, _) in enumerate(salas)
                for entrada in grade(SEGUNDA, horas[2 * posicao : 2 * posicao + 2], ANA, sala_id)
            ]
        },
    )
    mensagem = mensagem_tool("consultar_ocupacao", {"data": SEGUNDA.isoformat()}, fonte)
    bloco = BlocoRelatorio.model_validate(mensagem.artifact)
    esperado = ["Sala 1", "Sala 2", "Sala 10"]
    assert isinstance(bloco.dados, DadosOcupacaoAgregada)
    assert [i.rotulo for i in bloco.dados.por_sala] == esperado
    tabela = next(t for t in bloco.tabelas if t.nome == "Por sala")
    assert [linha["rotulo"] for linha in tabela.linhas] == esperado
    texto = str(mensagem.content).split("Por sala:")[1]
    assert re.findall(r"- (Sala \d+):", texto) == esperado
