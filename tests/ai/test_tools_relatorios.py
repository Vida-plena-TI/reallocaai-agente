"""Tools `gerar_relatorio` e `consultar_catalogo` sobre a semana fictícia de `agenda_clinica`."""

import json
import logging
from datetime import date
from typing import Any

import pytest
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool

from app.ai.prompts import prompt_de_sistema
from app.ai.relatorios import BlocoRelatorio
from app.ai.tools import criar_tools
from app.ai.tools_relatorios import RELATORIOS
from app.data_sources.continuidade import SemHistoricoContinuidadeDataSource
from tests.engine.test_fotografia import FonteContadora
from tests.support.agenda_clinica import IDS_PACIENTES, NOMES_PACIENTES, PACIENTES, fonte_clinica
from tests.support.agenda_semanal import QUARTA, SABADO, SEGUNDA, SEXTA, TERCA
from tests.support.fake_schedule_data_source import FakeScheduleDataSource

#: Argumentos que geram cada relatório pronto (agenda_paciente com o nome pedido).
ARGS_POR_RELATORIO: dict[str, dict[str, Any]] = {
    "vagas_livres": {},
    "janelas_continuas": {"duracao_minutos": 60},
    "ranking_ocupacao": {},
    "comparativo_dias": {},
    "convenios": {},
    "qualidade_agenda": {},
    "salas_ociosas": {},
    "capacidade_especialidade": {},
    "agenda_paciente": {"paciente": PACIENTES["A"][0]},
    "personalizado": {
        "dimensoes": ["convenio", "profissional"],
        "metricas": ["sessoes", "pacientes_distintos", "aguardando_autorizacao"],
    },
}


def _tools(fonte: FakeScheduleDataSource | None = None) -> dict[str, BaseTool]:
    return {
        t.name: t
        for t in criar_tools(
            fonte or fonte_clinica(),
            SemHistoricoContinuidadeDataSource(),
            SEGUNDA,
        )
    }


def _gerar(
    fonte: FakeScheduleDataSource | None = None, **args: Any
) -> tuple[str, BlocoRelatorio | None]:
    mensagem = _tools(fonte)["gerar_relatorio"].invoke(
        {"name": "gerar_relatorio", "args": args, "id": "c1", "type": "tool_call"}
    )
    assert isinstance(mensagem, ToolMessage)
    bloco = BlocoRelatorio.model_validate(mensagem.artifact) if mensagem.artifact else None
    return str(mensagem.content), bloco


def test_vagas_de_segunda_a_sexta_numa_chamada_lendo_cada_dia_uma_vez() -> None:
    fonte = FonteContadora(fonte_clinica())
    tools = _tools(fonte)
    # Criar as tools lê o catálogo da semana (uma vez por dia); aqui conta só o relatório.
    fonte.leituras.clear()

    mensagem = tools["gerar_relatorio"].invoke(
        {
            "name": "gerar_relatorio",
            "args": {
                "relatorio": "vagas_livres",
                "inicio": SEGUNDA.isoformat(),
                "fim": SEXTA.isoformat(),
            },
            "id": "c1",
            "type": "tool_call",
        }
    )
    texto = str(mensagem.content)
    bloco = BlocoRelatorio.model_validate(mensagem.artifact) if mensagem.artifact else None

    assert bloco is not None and bloco.tipo == "disponibilidade"
    assert bloco.periodo is not None
    assert (bloco.periodo.inicio, bloco.periodo.fim) == (SEGUNDA, SEXTA)
    assert bloco.titulo == "Vagas livres de 28/09/2026 a 02/10/2026"
    assert set(fonte.leituras.values()) == {1}
    assert {dia for _, dia in fonte.leituras} == {SEGUNDA, TERCA, QUARTA, date(2026, 10, 1), SEXTA}
    assert texto.startswith("Vagas livres de 28/09/2026 a 02/10/2026")
    # O texto do modelo é curto; o bloco traz todas as faixas.
    assert len(texto.splitlines()) < 12
    assert [t.nome for t in bloco.tabelas] == ["Vagas", "Resumo por dia"]


def test_sem_datas_usa_a_semana_de_hoje() -> None:
    _, bloco = _gerar(relatorio="comparativo_dias")

    assert bloco is not None
    assert bloco.periodo is not None
    assert (bloco.periodo.inicio, bloco.periodo.fim) == (SEGUNDA, SABADO)
    assert len(bloco.tabelas[0].linhas) == 6


@pytest.mark.parametrize("relatorio", RELATORIOS)
def test_todo_relatorio_gera_bloco_valido(relatorio: str) -> None:
    texto, bloco = _gerar(relatorio=relatorio, **ARGS_POR_RELATORIO[relatorio])

    assert bloco is not None, texto
    assert bloco.tabelas and all(tabela.nome for tabela in bloco.tabelas)
    assert not bloco.parcial


@pytest.mark.parametrize("relatorio", [r for r in RELATORIOS if r != "agenda_paciente"])
def test_relatorios_nunca_trazem_paciente(relatorio: str) -> None:
    texto, bloco = _gerar(relatorio=relatorio, **ARGS_POR_RELATORIO[relatorio])

    assert bloco is not None
    conteudo = texto + json.dumps(bloco.model_dump(mode="json"), ensure_ascii=False)
    for proibido in [*NOMES_PACIENTES, *IDS_PACIENTES, "Paciente Ficticio"]:
        assert proibido not in conteudo, (relatorio, proibido)


def test_agenda_do_paciente_so_cita_o_paciente_pedido() -> None:
    texto, bloco = _gerar(relatorio="agenda_paciente", paciente=PACIENTES["A"][0])

    assert bloco is not None and bloco.tipo == "agenda_paciente"
    conteudo = texto + json.dumps(bloco.model_dump(mode="json"), ensure_ascii=False)
    assert PACIENTES["A"][0] in conteudo
    assert all(nome not in conteudo for nome in NOMES_PACIENTES[1:])
    # Nem o id (nome normalizado) do próprio paciente.
    assert IDS_PACIENTES[0] not in conteudo
    assert "Espera total entre terapias (sem o almoço): 210 min." in texto


def test_agenda_de_paciente_que_nao_aparece_nao_sugere_outros_nomes() -> None:
    texto, bloco = _gerar(relatorio="agenda_paciente", paciente="Paciente Ficticio Alfo")

    assert bloco is None
    assert "não aparece na agenda" in texto
    assert all(nome not in texto for nome in NOMES_PACIENTES)


def test_logs_nao_vazam_pacientes(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        for relatorio in RELATORIOS:
            _gerar(fonte_clinica({TERCA}), relatorio=relatorio, **ARGS_POR_RELATORIO[relatorio])
        _tools()["consultar_catalogo"].invoke({})

    assert "gerar_relatorio vagas_livres" in caplog.text
    for proibido in [*NOMES_PACIENTES, *IDS_PACIENTES]:
        assert proibido not in caplog.text


def test_filtros_por_nome_texto_livre_e_titulo_com_recorte() -> None:
    _, bloco = _gerar(
        relatorio="vagas_livres",
        profissionais=["Cau"],
        especialidades=["TO"],
        salas=["2"],
        periodo_do_dia="tarde",
        dias_semana=["segunda", "terca"],
    )

    assert bloco is not None
    assert bloco.titulo == (
        "Vagas livres (Cau Melo · Terapia Ocupacional · Sala 2 · segunda · terça · tarde) "
        "de 28/09/2026 a 03/10/2026"
    )
    linhas = bloco.tabelas[0].linhas
    assert {linha["profissional"] for linha in linhas} == {"Cau Melo"}
    assert {linha["dia_semana"] for linha in linhas} == {"segunda-feira", "terça-feira"}


@pytest.mark.parametrize(
    ("args", "trecho"),
    [
        ({"relatorio": "personalizado"}, "exige 'metricas'"),
        ({"relatorio": "vagas_livres", "profissionais": ["Zé"]}, "Disponíveis: Ana Lima"),
        ({"relatorio": "vagas_livres", "salas": ["Sala 99"]}, "Salas: Sala 1, Sala 2, Sala 10"),
        ({"relatorio": "vagas_livres", "especialidades": ["astrologia"]}, "não reconhecida"),
        ({"relatorio": "convenios", "convenios": ["bradesco"]}, "não reconhecido"),
        ({"relatorio": "vagas_livres", "paciente": "Alguém"}, "nunca trazem pacientes"),
        ({"relatorio": "agenda_paciente"}, "exige o nome"),
        ({"relatorio": "janelas_continuas", "duracao_minutos": 600}, "deve ficar entre"),
        (
            {"relatorio": "vagas_livres", "inicio": "2026-10-02", "fim": "2026-09-28"},
            "anterior ao início",
        ),
        (
            {"relatorio": "vagas_livres", "inicio": "2026-01-01", "fim": "2026-12-31"},
            "longo demais",
        ),
        (
            {"relatorio": "personalizado", "dimensoes": ["convenio"], "metricas": ["ocupacao"]},
            "não combina com métricas de oferta",
        ),
        (
            {"relatorio": "personalizado", "metricas": ["sessoes"], "ordenar_por": "xpto"},
            "não é métrica nem dimensão",
        ),
        (
            {"relatorio": "personalizado", "dimensoes": ["andar"], "metricas": ["sessoes"]},
            "Dimensões válidas: dia, profissional",
        ),
        ({"relatorio": "relatorio_inexistente"}, "Parâmetros inválidos"),
    ],
)
def test_parametros_invalidos_voltam_como_mensagem_util(args: dict[str, Any], trecho: str) -> None:
    texto, bloco = _gerar(**args)

    assert bloco is None
    assert trecho in texto


def test_profissional_ambigua_pede_para_perguntar() -> None:
    from app.domain import Especialidade, Profissional
    from tests.support.agenda_semanal import grade, horas_da_manha

    ana_a = Profissional(id="ana-a", nome="Ana A", especialidade=Especialidade.PSICOLOGIA)
    ana_b = Profissional(id="ana-b", nome="Ana B", especialidade=Especialidade.PSICOLOGIA)
    horas = horas_da_manha(SEGUNDA)
    fonte = FakeScheduleDataSource(
        profissionais={SEGUNDA: [ana_a, ana_b]},
        grade={SEGUNDA: grade(SEGUNDA, horas, ana_a) + grade(SEGUNDA, horas, ana_b, "sala-2")},
    )

    texto, bloco = _gerar(fonte, relatorio="vagas_livres", profissionais=["Ana"])

    assert bloco is None and "Pergunte qual" in texto


def test_dia_ilegivel_deixa_o_bloco_parcial() -> None:
    texto, bloco = _gerar(fonte_clinica({QUARTA}), relatorio="ranking_ocupacao")

    assert bloco is not None
    assert bloco.parcial and bloco.dias_nao_lidos == [QUARTA]
    assert "Não foi possível ler: Qua 30/09" in texto


def test_periodo_so_com_domingo_nao_gera_bloco() -> None:
    texto, bloco = _gerar(relatorio="vagas_livres", inicio="2026-10-04")

    assert bloco is None and "domingo" in texto


def test_ocupacao_no_texto_sai_dos_inteiros() -> None:
    texto, bloco = _gerar(
        relatorio="personalizado",
        metricas=["ocupacao", "slots_ocupados", "slots_escalados"],
        profissionais=["Diva"],
    )

    assert bloco is not None
    assert "ocupação 33,3%, slots ocupados 6, slots escalados 18" in texto
    assert bloco.resumo[0].exibicao == "33,3%"
    assert bloco.meta == 0.8


def test_catalogo_descreve_a_clinica_sem_pacientes() -> None:
    texto = str(_tools()["consultar_catalogo"].invoke({}))

    assert "Psicologia: Ana Lima (seg/ter/qua/qui/sex; Sala 1); Diva Rocha (qui/sáb; Sala 10)" in (
        texto
    )
    assert "Sala 2 (2)" in texto
    assert "janelas_livres:" in texto and "agenda_paciente:" in texto
    assert all(nome not in texto for nome in NOMES_PACIENTES)


def test_tools_antigas_apontam_para_gerar_relatorio_em_periodos() -> None:
    tools = _tools()

    assert "gerar_relatorio" in str(tools["consultar_disponibilidade"].description)
    assert "gerar_relatorio" in str(tools["consultar_ocupacao"].description)
    assert "nunca chame uma vez por dia" in str(tools["gerar_relatorio"].description)


@pytest.mark.parametrize("renderiza", [True, False])
def test_prompt_orienta_uma_chamada_por_periodo(renderiza: bool) -> None:
    prompt = prompt_de_sistema(renderiza_relatorios=renderiza)

    assert "## Mapa da clínica" in prompt
    assert "## Como escolher o relatório" in prompt
    assert "UMA chamada a `gerar_relatorio`" in prompt
    assert "Nunca chame uma ferramenta uma vez por dia" in prompt


def test_contrato_de_meta_dos_tipos_novos() -> None:
    from pydantic import ValidationError

    _, convenio = _gerar(relatorio="convenios")
    _, tabela = _gerar(relatorio="personalizado", metricas=["sessoes"])
    assert convenio is not None and tabela is not None
    assert convenio.meta is None and tabela.meta is None

    with pytest.raises(ValidationError, match="não tem meta"):
        BlocoRelatorio.model_validate({**convenio.model_dump(mode="json"), "meta": 0.8})
    # tabela_generica aceita meta (quando pede ocupação) ou não.
    BlocoRelatorio.model_validate({**tabela.model_dump(mode="json"), "meta": 0.8})


def test_parametro_que_sobra_num_relatorio_pronto_e_ignorado_com_nota() -> None:
    texto, bloco = _gerar(
        relatorio="convenios", metricas=["aguardando_autorizacao"], dimensoes=["dia"]
    )

    assert bloco is not None and bloco.tipo == "convenio"
    assert "Obs.: metricas, dimensoes ignorado(s)" in texto


def test_vagas_com_duracao_viram_janelas_continuas() -> None:
    _, bloco = _gerar(relatorio="vagas_livres", duracao_minutos=90)

    assert bloco is not None
    assert bloco.titulo.startswith("Janelas livres de 1 h 30")


# --- regras do contrato v1 nos blocos novos ----------------------------------

#: Variantes que cobrem todos os blocos novos, inclusive especialidade como dimensão
#: e ocupação (coluna booleana `abaixo_da_meta`).
VARIANTES_CONTRATO: list[dict[str, Any]] = [
    *({"relatorio": r, **a} for r, a in ARGS_POR_RELATORIO.items()),
    {
        "relatorio": "personalizado",
        "dimensoes": ["dia", "especialidade"],
        "metricas": ["ocupacao", "vagas_livres"],
    },
]


def _artefatos() -> list[dict[str, Any]]:
    """O dicionário que vai ao app visual (`ToolMessage.artifact`) de cada variante."""
    artefatos = []
    for args in VARIANTES_CONTRATO:
        mensagem = _tools()["gerar_relatorio"].invoke(
            {"name": "gerar_relatorio", "args": args, "id": "c1", "type": "tool_call"}
        )
        assert isinstance(mensagem, ToolMessage) and mensagem.artifact, args
        artefatos.append(mensagem.artifact)
    return artefatos


def _percorrer(valor: object, chave: str = "") -> list[tuple[str, object]]:
    """Todos os pares (chave, valor) do JSON, em qualquer profundidade."""
    if isinstance(valor, dict):
        return [par for k, v in valor.items() for par in [(k, v), *_percorrer(v, k)]]
    if isinstance(valor, list):
        return [par for v in valor for par in _percorrer(v, chave)]
    return []


def test_especialidade_sai_sempre_como_rotulo_nos_blocos_novos() -> None:
    from app.domain import Especialidade
    from app.domain.formatacao import rotulo_enum

    identificadores = {e.value for e in Especialidade}
    rotulos = {rotulo_enum(e.value) for e in Especialidade}
    vistos: set[object] = set()
    for artefato in _artefatos():
        # Nenhum valor de texto do bloco é um identificador de especialidade.
        textos = {v for _, v in _percorrer(artefato) if isinstance(v, str)}
        assert not textos & identificadores, (artefato["tipo"], textos & identificadores)
        # Campo `especialidade` dos dados e coluna `especialidade` das tabelas.
        valores = [v for k, v in _percorrer(artefato) if k == "especialidade"]
        assert set(valores) <= rotulos | {None, "—", "Sem especialidade"}, artefato["tipo"]
        vistos.update(valores)
    assert {"Terapia Ocupacional", "Psicologia", "Fonoaudiologia"} <= vistos


def test_booleanos_das_tabelas_saem_como_booleano_json() -> None:
    textos_proibidos = {"Sim", "Não", "sim", "não", "true", "false", "True", "False"}
    colunas_booleanas: set[str] = set()
    for artefato in _artefatos():
        tabelas = list(artefato["tabelas"])
        if artefato["tipo"] == "tabela_generica":
            # `dados.total` só tem as métricas: as dimensões ficam ausentes.
            dados = artefato["dados"]
            tabelas.append(
                {"colunas": dados["colunas"], "linhas": [*dados["linhas"], dados["total"]]}
            )
        for tabela_ in tabelas:
            for coluna in tabela_["colunas"]:
                valores = [linha.get(coluna["chave"]) for linha in tabela_["linhas"]]
                assert not {v for v in valores if isinstance(v, str)} & textos_proibidos, coluna
                if any(isinstance(v, bool) for v in valores):
                    colunas_booleanas.add(coluna["chave"])
                    assert all(isinstance(v, bool) or v is None for v in valores), coluna
        # E sobrevive à serialização: `true`/`false`, nunca `"true"`.
        assert '"true"' not in json.dumps(artefato) and '"false"' not in json.dumps(artefato)
    assert {"abaixo_da_meta", "aguardando_autorizacao"} <= colunas_booleanas


def test_meta_sempre_presente_e_nula_quando_nao_se_aplica() -> None:
    from pathlib import Path

    from app.api.schemas import ChatResponse

    artefatos = _artefatos()
    for artefato in artefatos:
        assert "meta" in artefato, artefato["tipo"]
    nulas = [a for a in artefatos if a["meta"] is None]
    assert nulas
    # No JSON da API o campo também aparece, como `null`.
    resposta = ChatResponse(
        conversa_id="c",
        resposta="r",
        blocos=[BlocoRelatorio.model_validate(a) for a in nulas],
    )
    assert all(b["meta"] is None for b in json.loads(resposta.model_dump_json())["blocos"])

    caminho = Path(__file__).parents[2] / "docs/exemplos-blocos/bloco_relatorio.schema.json"
    for schema in (
        BlocoRelatorio.model_json_schema(),
        json.loads(caminho.read_text(encoding="utf-8")),
    ):
        assert "meta" in schema["required"]
        assert {"type": "null"} in schema["properties"]["meta"]["anyOf"]


def test_tabela_de_vagas_ordenada_por_data_especialidade_inicio_profissional_sala_posto() -> None:
    from app.ai.blocos_gerais import ordem_vaga
    from app.ai.relatorios import DadosDisponibilidade

    _, bloco = _gerar(relatorio="vagas_livres", inicio=SEGUNDA.isoformat(), fim=SEXTA.isoformat())

    assert bloco is not None and isinstance(bloco.dados, DadosDisponibilidade)
    esperado = sorted(bloco.dados.faixas, key=ordem_vaga)
    linhas = bloco.tabelas[0].linhas
    assert len({f.profissional.especialidade for f in esperado}) > 1
    chaves = ["data", "especialidade", "inicio", "profissional", "sala", "posto"]
    assert [tuple(linha[c] for c in chaves) for linha in linhas] == [
        (
            f.data.isoformat(),
            f.profissional.especialidade,
            f.inicio,
            f.profissional.nome,
            f.sala_nome,
            f.posto,
        )
        for f in esperado
    ]
