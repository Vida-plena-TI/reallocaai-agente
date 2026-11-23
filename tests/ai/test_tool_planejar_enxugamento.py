"""Tool `planejar_enxugamento` sobre a semana fictícia: texto, bloco e privacidade.

O panorama leva nomes de pacientes E é sensível de RH: nomes de pacientes e de
profissionais nunca podem aparecer em log, no tracing, no `content` que o
modelo lê nem no texto final (inclusive os nomes passados nos argumentos
`profissionais_foco` e `profissionais_preservados`); vão só no bloco da tela.
Nomes fictícios.
"""

import json
import logging
import re
from collections.abc import Iterator
from dataclasses import replace
from datetime import date
from itertools import pairwise
from typing import Any, cast

import gspread
import langsmith
import langsmith.utils
import pytest
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatResult
from pydantic import Field

from app.ai.agente import criar_agente, perguntar_com_mensagens
from app.ai.blocos_enxugamento import (
    ABA_CENARIOS,
    ABA_PASSOS,
    AVISO_BUSCA_LIMITADA,
    AVISO_CONFIRMAR,
    AVISO_LEGENDA_COBERTURA,
    AVISOS_PANORAMA,
    OBSERVACAO_ABRE_ESPACO,
    OBSERVACAO_SEM_PRESENCA,
    TIPOS_DE_MOVIMENTO,
    TITULO_COMPARATIVO,
    TITULO_PASSOS_RECOMENDADO,
    nomes_de_profissionais_no_bloco,
    panorama_enxugamento,
)
from app.ai.blocos_gerais import nomes_de_pacientes_no_bloco
from app.ai.fundamentacao import (
    codigos_de_cenario_na_resposta,
    nomes_de_profissional_no_panorama,
    resumo_deterministico,
    termos_proibidos,
)
from app.ai.rastreamento import desligar_rastreamento_externo
from app.ai.relatorios import (
    RELATORIO_PANORAMA_ENXUGAMENTO,
    RELATORIOS_COM_NOMES_DE_PACIENTES,
    BlocoRelatorio,
    DadosTabelaGenerica,
)
from app.ai.tools import criar_tools
from app.api.sessoes import ArmazenamentoConversas
from app.data_sources.continuidade import SemHistoricoContinuidadeDataSource
from app.data_sources.google_sheets import GoogleSheetsDataSource
from app.domain import ScheduleDataSource, dias_da_semana_de
from app.engine.enxugamento import (
    Objetivo,
    PanoramaEnxugamento,
    PedidoEnxugamento,
    planejar_enxugamento,
)
from app.engine.fotografia import FotografiaPeriodo, fotografar_periodo
from app.engine.grade_simulada import Lugar
from tests.casos_reais import oraculo
from tests.casos_reais.planilha import PlanilhaContada, payload_da_semana
from tests.support.fake_chat_model import FakeToolCallingChatModel

HOJE = date(2026, 9, 28)
TOOL = "planejar_enxugamento"
CENARIO = "enxugamento"
#: Limites da exportação do chatbot (Excel/PDF): linhas por tabela e nome da aba.
MAX_LINHAS_EXPORTACAO = 5_000
MAX_CARACTERES_ABA = 31
_CODIGO = re.compile(r"\bE-[0-9A-F]{4}\b")


def _fonte(cenario: str | None = CENARIO) -> GoogleSheetsDataSource:
    planilha = PlanilhaContada(lambda _params: payload_da_semana(cenario))
    return GoogleSheetsDataSource(cast(gspread.Spreadsheet, planilha))


def _invocar(args: dict[str, Any], fonte: ScheduleDataSource | None = None) -> ToolMessage:
    tools = {
        t.name: t
        for t in criar_tools(fonte or _fonte(), SemHistoricoContinuidadeDataSource(), HOJE)
    }
    mensagem = tools[TOOL].invoke({"name": TOOL, "args": args, "id": "t", "type": "tool_call"})
    assert isinstance(mensagem, ToolMessage)
    return mensagem


def _sem_nomes(texto: str) -> bool:
    """Nenhum nome de paciente (todos começam assim) nem de profissional da semana."""
    baixo = texto.lower()
    return all(
        nome not in baixo
        for nome in ("paciente ficticio", "ficticia", "acacia", "bromelia", "camelia", "zuleide")
    )


def _blocos(mensagem: ToolMessage) -> list[dict[str, Any]]:
    """O artifact do panorama: a lista de blocos (quadro comparativo e passos)."""
    assert isinstance(mensagem.artifact, list)
    return cast(list[dict[str, Any]], mensagem.artifact)


def _passos(mensagem: ToolMessage) -> dict[str, Any]:
    blocos = _blocos(mensagem)
    assert len(blocos) == 2
    return blocos[1]


def _mapa(texto: str) -> dict[int, str]:
    """`Cenário N = código`, como o content entrega ao modelo."""
    return {int(n): codigo for n, codigo in re.findall(r"Cenário (\d+) = (E-[0-9A-F]{4})", texto)}


def test_texto_so_com_contagens_e_dois_blocos_com_os_nomes() -> None:
    mensagem = _invocar({})

    texto = str(mensagem.content)
    assert _sem_nomes(texto)
    assert texto.startswith("Panorama de enxugamento (grade semanal vigente): 5 cenários")
    assert "sem abrir turno novo" in texto
    # Os códigos aparecem só no mapa "Cenário N = código" (uma vez cada).
    assert len(_CODIGO.findall(texto)) == 5 and sorted(_mapa(texto)) == [1, 2, 3, 4, 5]
    assert "Cenário 1 (recomendado; equilibrado)" in texto
    assert "não escreva nenhum nome nem código" in texto
    assert 'termine com a pergunta "Quer ver os passos do Cenário 2?"' in texto
    assert termos_proibidos(texto) == []

    comparativo, passos = _blocos(mensagem)
    for bloco in (comparativo, passos):
        BlocoRelatorio.model_validate(bloco)
        assert bloco["tipo"] == "tabela_generica"
        assert bloco["dados"]["relatorio"] == RELATORIO_PANORAMA_ENXUGAMENTO
        assert bloco["contem_dados_pacientes"] is True
        assert bloco["periodo"] is None and bloco["meta"] is None
    assert comparativo["titulo"] == TITULO_COMPARATIVO
    assert passos["titulo"] == TITULO_PASSOS_RECOMENDADO
    assert comparativo["titulo"] != passos["titulo"]
    assert "agrupar_por" not in comparativo["dados"]
    assert [c["chave"] for c in comparativo["dados"]["colunas"]] == [
        "cenario", "turnos_liberados", "passos", "profissionais_afetadas", "pacientes_movidos",
        "trocam_de_profissional", "trocam_de_dia", "profissionais_sem_atendimento",
        "turnos_sem_profissional", "passos_iguais_ao_recomendado", "recomendado",
    ]  # fmt: skip
    dados = passos["dados"]
    assert dados["agrupar_por"] == ["cenario"]
    assert dados["unidade_grupo"] == {"singular": "passo", "plural": "passos"}
    assert [c["chave"] for c in dados["colunas"]] == [
        "cenario", "turno_liberado", "passo", "paciente", "terapia", "de", "para",
        "tipo_de_movimento", "observacao",
    ]  # fmt: skip
    primeira = dados["linhas"][0]
    assert re.fullmatch(r"Cenário 1 · equilibrado · libera \d turnos?", primeira["cenario"])
    assert re.fullmatch(
        r"(Seg|Ter|Qua|Qui|Sex|Sáb) (manhã|tarde) · .+ · .+", primeira["turno_liberado"]
    )
    assert primeira["paciente"].startswith("Paciente Ficticio ")
    assert primeira["terapia"] == "Terapia Alimentar"
    assert re.fullmatch(r"\w{3} \d\d:\d\d às \d\d:\d\d · Ficticia \w+ · Sala \d", primeira["de"])
    # O quadro, o resumo e os avisos (que o resumo determinístico pode ler): sem nomes.
    assert _sem_nomes(json.dumps(comparativo, ensure_ascii=False))
    assert all(_sem_nomes(json.dumps(item, ensure_ascii=False)) for item in passos["resumo"])
    assert _sem_nomes(" ".join(passos["avisos"]))
    assert set(AVISOS_PANORAMA) <= set(comparativo["avisos"])
    assert AVISO_LEGENDA_COBERTURA in comparativo["avisos"]
    assert AVISO_CONFIRMAR in passos["avisos"]
    validado = BlocoRelatorio.model_validate(passos)
    assert "Paciente Ficticio Aroeira" in nomes_de_pacientes_no_bloco(validado)
    assert "Ficticia Acacia" in nomes_de_profissionais_no_bloco(validado)


def _sem_caracteres_proibidos_no_excel(nome: str) -> bool:
    return not set(nome) & set("\\/?*[]:")


@pytest.mark.parametrize(
    "detalhar",
    [None, ["E-9DD6"], ["E-5EB1", "E-88EB", "E-9DD6", "E-2934", "E-4B1C"]],
    ids=["padrao", "um-cenario", "todos"],
)
def test_exportacao_dentro_dos_limites_do_chatbot(detalhar: list[str] | None) -> None:
    blocos = _blocos(_invocar({"detalhar_cenarios": detalhar} if detalhar else {}))
    assert len(blocos) == 2
    abas = [t["nome"] for bloco in blocos for t in bloco["tabelas"]]
    assert abas == [ABA_CENARIOS, ABA_PASSOS]
    # Únicas entre os dois blocos (o Excel não distingue maiúsculas no nome da aba).
    assert len({a.casefold() for a in abas}) == len(abas)
    for bloco in blocos:
        for aba in bloco["tabelas"]:
            assert len(aba["nome"]) <= MAX_CARACTERES_ABA
            assert _sem_caracteres_proibidos_no_excel(aba["nome"])
            assert len(aba["linhas"]) <= MAX_LINHAS_EXPORTACAO
            assert aba["linhas"] == bloco["dados"]["linhas"]
    assert len(blocos[0]["tabelas"][0]["linhas"]) == 5


def test_cenarios_ja_vistos_pela_tool() -> None:
    primeiro = _invocar({"max_cenarios": 2})
    vistos = _CODIGO.findall(str(primeiro.content))
    assert len(set(vistos)) == 2

    segundo = _invocar({"max_cenarios": 2, "cenarios_ja_vistos": vistos})
    novos = set(_CODIGO.findall(str(segundo.content)))
    assert novos and novos.isdisjoint(vistos)


def test_quem_fica_sem_atendimento_na_semana_vai_na_coluna() -> None:
    mensagem = _invocar({}, _fonte("enxugamento_tres"))
    comparativo, passos = _blocos(mensagem)
    linhas = comparativo["dados"]["linhas"]
    assert [linha["profissionais_sem_atendimento"] for linha in linhas] == [1, 1]
    assert [linha["turnos_sem_profissional"] for linha in linhas] == [0, 0]
    # A observação do passo continua (no bloco restrito); o aviso por cenário, não.
    assert any(
        OBSERVACAO_SEM_PRESENCA in linha["observacao"] for linha in passos["dados"]["linhas"]
    )
    assert "1 profissional sem atendimento na semana" in str(mensagem.content)
    assert _sem_nomes(str(mensagem.content))


#: Avisos por cenário que saíram do quadro (viraram as colunas de cobertura).
_AVISO_POR_CENARIO = re.compile(r"^Cenário \d+:|sem atendimento na semana|ficariam? sem nenhum")


@pytest.mark.parametrize(
    ("cenario", "args"),
    [
        ("enxugamento", {}),
        ("enxugamento", {"permitir_trocar_dia": True}),
        ("enxugamento_tres", {}),
        ("enxugamento_cadeia", {}),
        ("enxugamento_presos", {}),
    ],
    ids=["padrao", "trocar-dia", "tres", "cadeia", "presos"],
)
def test_avisos_so_os_gerais_e_a_legenda(cenario: str, args: dict[str, Any]) -> None:
    mensagem = _invocar(args, _fonte(cenario))
    comparativo, passos = _blocos(mensagem)
    avisos = comparativo["avisos"]
    gerais = list(AVISOS_PANORAMA)
    assert avisos[: len(gerais)] == gerais
    assert avisos[-1] == AVISO_LEGENDA_COBERTURA
    # Entre os fixos e a legenda, só os turnos presos por regra, a busca limitada e os
    # dias não lidos (quando houver).
    for aviso in avisos[len(gerais) : -1]:
        assert (
            "por regra: " in aviso
            or aviso == AVISO_BUSCA_LIMITADA
            or aviso.startswith("Não foi possível ler: ")
        ), aviso
    assert any("por regra: " in aviso for aviso in avisos) is (cenario == "enxugamento_presos")
    assert not any(_AVISO_POR_CENARIO.search(aviso) for aviso in avisos)
    # Passos: só o confirmar com a família (e o da comparação, quando há).
    assert passos["avisos"] == [AVISO_CONFIRMAR]
    assert not any(_AVISO_POR_CENARIO.search(aviso) for aviso in passos["avisos"])
    # A legenda e os números saem também no texto do modelo, sem nomes.
    assert AVISO_LEGENDA_COBERTURA in str(mensagem.content)
    assert _sem_nomes(str(mensagem.content))


def test_turnos_presos_so_como_contagem() -> None:
    mensagem = _invocar({}, _fonte("enxugamento_presos"))
    texto = str(mensagem.content)
    assert "presos por regra: 2 (1 atendimento aguardando autorização, 1 sessão em grupo)" in texto
    assert _sem_nomes(texto)


def test_sem_cenario_nao_manda_bloco() -> None:
    mensagem = _invocar({"dias_semana": ["sexta"]})

    assert mensagem.artifact is None
    assert "não encontrei movimentação que libere turno" in str(mensagem.content)


def test_foco_ambiguo_pergunta_por_papel_sem_bloco() -> None:
    mensagem = _invocar({"profissionais_foco": ["Ficticia"], "liberar_turno": "manha"})

    texto = str(mensagem.content)
    assert mensagem.artifact is None
    assert texto.startswith("Mais de uma profissional corresponde a um dos nomes citados")
    assert "2 candidatas: uma de Terapia Alimentar, uma de Terapia Alimentar" in texto
    assert _sem_nomes(texto)


def test_profissional_nao_encontrada_nao_repete_o_nome() -> None:
    mensagem = _invocar({"profissionais_preservados": ["Zuleide"]})

    assert mensagem.artifact is None
    assert "não está na grade desta semana" in str(mensagem.content)
    assert _sem_nomes(str(mensagem.content))


def test_foco_resolvido_libera_so_o_turno_pedido() -> None:
    mensagem = _invocar({"profissionais_foco": ["ficticia acacia"], "liberar_turno": "tarde"})

    turnos = {linha["turno_liberado"] for linha in _passos(mensagem)["dados"]["linhas"]}
    assert turnos == {"Ter tarde · Ficticia Acacia · Terapia Alimentar"}
    assert _sem_nomes(str(mensagem.content))


def test_valor_fora_do_catalogo_e_recusado_sem_repetir_o_valor() -> None:
    mensagem = _invocar({"dias_semana": ["domingo"]})

    assert "Parâmetros inválidos (dias_semana" in str(mensagem.content)
    assert "domingo" not in str(mensagem.content)


def test_descricao_da_tool() -> None:
    tools = {t.name: t for t in criar_tools(_fonte(), SemHistoricoContinuidadeDataSource(), HOJE)}
    descricao = " ".join(tools[TOOL].description.split())

    assert "e se a X ficar só de tarde?" in descricao
    assert "NUNCA escreva nomes de pacientes nem de profissionais nem códigos" in descricao
    assert "detalhar_cenarios" in descricao
    assert "nunca fale em valores, salário ou dinheiro" in descricao
    assert "nunca diga que moveu, liberou ou economizou" in descricao


def test_registrado_entre_os_relatorios_com_nomes() -> None:
    assert RELATORIO_PANORAMA_ENXUGAMENTO in RELATORIOS_COM_NOMES_DE_PACIENTES


@pytest.mark.parametrize(
    "frase",
    [
        "Dá para cortar a manhã da terapeuta.",
        "Sugiro o corte de dois turnos.",
        "Você pode demitir uma profissional.",
        "Dá para dispensar a colega na quarta.",
        "Hoje há prejuízo nesse turno.",
        "O custo da profissional é alto.",
        "Isso reduz o salário.",
        "A economia é de R$300 por semana.",
        "Economizei três turnos.",
        "Liberei a manhã de quarta.",
        "Reduzi a folha.",
    ],
)
def test_vocabulario_sensivel_de_rh_e_recusado(frase: str) -> None:
    assert termos_proibidos(frase)


def test_sugerir_liberar_turno_continua_permitido() -> None:
    texto = "Sugiro liberar o turno de quarta: o cenário E-3F2A libera 3 turnos por semana."
    assert termos_proibidos(texto) == []


def test_guarda_de_nome_de_profissional_so_na_rodada_do_panorama() -> None:
    bloco = BlocoRelatorio.model_validate(_passos(_invocar({})))
    assert nomes_de_profissional_no_panorama("Sugiro mudar a Ficticia Acacia.", [bloco]) == 1
    assert nomes_de_profissional_no_panorama("Sugiro o cenário E-3F2A.", [bloco]) == 0
    # Nome da equipe que não está no bloco também conta.
    assert nomes_de_profissional_no_panorama("A Carol absorve.", [bloco], ["Carol"]) == 1
    # Fora da rodada do panorama, a guarda não age.
    assert nomes_de_profissional_no_panorama("Sugiro mudar a Ficticia Acacia.", []) == 0


class _FonteQuebrada:
    def __getattr__(self, _nome: str) -> Any:
        def falhar(*_args: Any) -> Any:
            raise RuntimeError("falha simulada com Ficticia Acacia")

        return falhar


def _registros(caplog: pytest.LogCaptureFixture) -> str:
    return "\n".join(f"{r.name} {r.getMessage()} {r.args!r}" for r in caplog.records)


def test_erro_so_pelo_tipo(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        mensagem = _invocar(
            {"profissionais_foco": ["Ficticia Acacia"]}, cast(Any, _FonteQuebrada())
        )

    assert str(mensagem.content).startswith("Erro ao montar o panorama (RuntimeError)")
    assert _sem_nomes(str(mensagem.content))
    assert _sem_nomes(_registros(caplog))


def test_log_sem_os_nomes_dos_argumentos(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        _invocar({"profissionais_foco": ["Ficticia Acacia"], "liberar_turno": "tarde"})
        _invocar({"profissionais_preservados": ["Ficticia Bromelia"]})

    registros = _registros(caplog)
    assert "planejar_enxugamento" in registros
    assert _sem_nomes(registros)


# --- agente: nomes não vazam para log, tracing, content nem texto final -------


class _ModeloComCaptura(FakeToolCallingChatModel):
    entradas: list[list[BaseMessage]] = Field(default_factory=list)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.entradas.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


@pytest.fixture
def tracing_vigiado(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Liga o tracing pelo ambiente e registra qualquer cliente do LangSmith criado."""
    for variavel in ("LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING", "LANGCHAIN_TRACING"):
        monkeypatch.setenv(variavel, "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "chave-falsa")
    criados: list[str] = []
    original = langsmith.Client.__init__

    def vigiar(self: Any, *args: Any, **kwargs: Any) -> None:
        criados.append("langsmith.Client")
        original(self, *args, **kwargs)

    monkeypatch.setattr(langsmith.Client, "__init__", vigiar)
    desligar_rastreamento_externo()
    yield criados
    langsmith.configure(enabled=None)


def _conversar(
    args: dict[str, Any], resposta: str, pergunta: str
) -> tuple[str, list[BaseMessage], list[BlocoRelatorio], _ModeloComCaptura]:
    chamada = AIMessage("", tool_calls=[{"name": TOOL, "args": args, "id": "e1"}])
    modelo = _ModeloComCaptura(responses=[chamada, AIMessage(resposta)])
    agente = criar_agente(
        _fonte(), SemHistoricoContinuidadeDataSource(), modelo, HOJE, renderiza_relatorios=True
    )
    texto, mensagens, blocos = perguntar_com_mensagens(agente, [HumanMessage(pergunta)])
    return texto, mensagens, blocos, modelo


@pytest.mark.parametrize(
    "resposta",
    [
        "Sugiro o primeiro cenário: a Ficticia Acacia fica só de manhã na terça.",
        "Sugiro passar o Paciente Ficticio Figueira para a manhã.",
    ],
    ids=["nome-de-profissional", "nome-de-paciente"],
)
def test_nomes_nunca_vazam(
    resposta: str, tracing_vigiado: list[str], caplog: pytest.LogCaptureFixture
) -> None:
    pergunta = "e se a Ficticia Acacia ficar só de manhã? que movimentações eu posso fazer?"
    args = {"profissionais_foco": ["Ficticia Acacia"], "liberar_turno": "tarde"}
    with caplog.at_level(logging.DEBUG):
        texto, mensagens, blocos, modelo = _conversar(args, resposta, pergunta)

    resultados = [m for m in mensagens if isinstance(m, ToolMessage)]
    assert len(resultados) == 1 and resultados[0].name == TOOL
    assert [b.titulo for b in blocos] == [TITULO_COMPARATIVO, TITULO_PASSOS_RECOMENDADO]
    # content: o que o modelo lê da tool.
    for entrada in modelo.entradas:
        for mensagem in entrada:
            if isinstance(mensagem, ToolMessage):
                assert _sem_nomes(str(mensagem.content))
    # Texto final: a resposta com nome vira o resumo determinístico (sem nomes).
    assert _sem_nomes(texto)
    assert texto.startswith("Sugiro o Cenário 1")
    assert _CODIGO.search(texto) is None
    # Log: nenhum nome, nem os dos argumentos.
    registros = _registros(caplog)
    assert _sem_nomes(registros)
    assert "resumo determinístico" in registros
    # Tracing: desligado mesmo com o ambiente ligando, e nenhum cliente criado.
    assert langsmith.utils.tracing_is_enabled() is False
    assert tracing_vigiado == []
    # Persistência: o histórico guarda a resposta do RealocAI sem nomes.
    conversas = ArmazenamentoConversas()
    conversa = conversas.criar_conversa()
    conversas.registrar_troca(conversa, pergunta, texto, blocos)
    historico = conversas.obter_historico(conversa)
    assert historico is not None
    assert _sem_nomes(str(historico[-1].content))


def test_resposta_so_com_contagens_passa(caplog: pytest.LogCaptureFixture) -> None:
    mensagem = _invocar({})
    turnos = re.search(r"Cenário 1, que libera (\d+) turnos", str(mensagem.content))
    assert turnos is not None
    resposta = (
        f"Sugiro o Cenário 1, que libera {turnos.group(1)} turnos por semana e muda 4 "
        "pacientes de lugar, 3 deles com troca de profissional e nenhum com troca de dia. "
        "Cada mudança precisa ser confirmada com as famílias e feita manualmente na planilha. "
        "Quer ver os passos do Cenário 2?"
    )
    with caplog.at_level(logging.DEBUG):
        texto, _, _, _ = _conversar({}, resposta, "que movimentações eu posso fazer?")

    assert texto == resposta
    assert "resumo determinístico" not in _registros(caplog)


# --- quadro comparativo, detalhe por cenário e texto de abertura ---------------


def _lugar_no_oraculo(foto: FotografiaPeriodo, lugar: Lugar) -> oraculo.LugarDoPlano:
    return oraculo.LugarDoPlano(
        dia=lugar.data,
        inicio=lugar.hora_inicio,
        fim=lugar.hora_fim,
        profissional=foto.nome_profissional(lugar.profissional_id),
        sala=int(foto.nome_sala(lugar.sala_id).split()[-1]),
    )


def _no_oraculo(
    payload: dict[str, Any], foto: FotografiaPeriodo, passos: Any
) -> oraculo.ResultadoCenario:
    """O cenário aplicado à grade crua pelo oráculo (independente do motor)."""
    resultado = oraculo.validar_cenario(
        payload,
        dias_da_semana_de(HOJE),
        [
            oraculo.PassoDoCenario(
                paciente=p.paciente,
                de=_lugar_no_oraculo(foto, p.de),
                para=_lugar_no_oraculo(foto, p.para),
                abre_espaco=p.abre_espaco,
            )
            for p in passos
        ],
    )
    assert resultado.problemas == []
    return resultado


def _recontar(
    foto: FotografiaPeriodo, panorama: PanoramaEnxugamento, cenario_da_semana: str = CENARIO
) -> list[dict[str, Any]]:
    """Recontagem independente do quadro, direto dos passos do motor (pelos nomes, que são
    únicos na semana fictícia, e pelos lugares); as colunas de cobertura, pelo oráculo
    na grade crua."""
    payload = payload_da_semana(cenario_da_semana)
    recomendado = {(p.paciente, p.de, p.para) for p in panorama.cenarios[0].passos}
    linhas = []
    for numero, cenario in enumerate(panorama.cenarios, 1):
        passos = cenario.passos
        pelo_oraculo = _no_oraculo(payload, foto, passos)
        linhas.append(
            {
                "turnos_liberados": len(cenario.turnos_liberados),
                "passos": len(passos),
                "profissionais_afetadas": len(
                    {p.de.profissional_id for p in passos}
                    | {p.para.profissional_id for p in passos}
                ),
                "pacientes_movidos": len({p.paciente for p in passos}),
                "trocam_de_profissional": len(
                    {p.paciente for p in passos if p.de.profissional_id != p.para.profissional_id}
                ),
                "trocam_de_dia": len({p.paciente for p in passos if p.de.data != p.para.data}),
                "profissionais_sem_atendimento": pelo_oraculo.profissionais_sem_atendimento,
                "turnos_sem_profissional": pelo_oraculo.turnos_sem_profissional,
                "passos_iguais_ao_recomendado": 0
                if numero == 1
                else sum((p.paciente, p.de, p.para) in recomendado for p in passos),
                "recomendado": numero == 1,
            }
        )
    return linhas


def test_quadro_confere_com_a_recontagem() -> None:
    comparativo, _ = _blocos(_invocar({}))
    semana = dias_da_semana_de(HOJE)
    foto = fotografar_periodo(_fonte(), semana[0], semana[-1])
    panorama = planejar_enxugamento(foto, PedidoEnxugamento())

    linhas = comparativo["dados"]["linhas"]
    assert [linha["cenario"] for linha in linhas] == [
        "Cenário 1 · equilibrado", "Cenário 2 · mais turnos", "Cenário 3 · menos mudança",
        "Cenário 4 · equilibrado", "Cenário 5 · mais turnos",
    ]  # fmt: skip
    assert [{k: v for k, v in linha.items() if k != "cenario"} for linha in linhas] == _recontar(
        foto, panorama
    )
    # Recomendado é booleano JSON; os números são inteiros (não bool, não texto).
    for linha in linhas:
        assert linha["recomendado"] is True or linha["recomendado"] is False
        for chave, valor in linha.items():
            if chave not in ("cenario", "recomendado"):
                assert type(valor) is int, chave
    assert [linha["recomendado"] for linha in linhas] == [True, False, False, False, False]
    assert [linha["turnos_sem_profissional"] for linha in linhas] == [2, 2, 1, 1, 1]
    assert [linha["profissionais_sem_atendimento"] for linha in linhas] == [0, 0, 0, 0, 0]
    # A aba "Cenários" (Excel) leva as mesmas colunas e linhas, com inteiros como inteiros.
    (aba,) = comparativo["tabelas"]
    assert aba["nome"] == ABA_CENARIOS
    assert aba["colunas"] == comparativo["dados"]["colunas"]
    assert aba["linhas"] == linhas
    formatos = {c["chave"]: c["formato"] for c in aba["colunas"]}
    assert formatos["profissionais_sem_atendimento"] == "inteiro"
    assert formatos["turnos_sem_profissional"] == "inteiro"


@pytest.mark.parametrize(
    ("cenario", "pedido"),
    [
        ("enxugamento", PedidoEnxugamento()),
        ("enxugamento", PedidoEnxugamento(permitir_trocar_dia=True)),
        ("enxugamento_tres", PedidoEnxugamento()),
        ("enxugamento_cadeia", PedidoEnxugamento()),
        ("enxugamento_presos", PedidoEnxugamento()),
    ],
    ids=["padrao", "trocar-dia", "zera-a-semana", "cadeia", "presos"],
)
def test_colunas_de_cobertura_conferem_com_o_oraculo(
    cenario: str, pedido: PedidoEnxugamento
) -> None:
    """Quem fica sem atendimento na semana e os turnos de terapia sem profissional,
    recontados pelo oráculo na grade crua; `enxugamento_tres` zera a semana de uma
    profissional e `enxugamento` deixa turnos de terapia sem ninguém."""
    semana = dias_da_semana_de(HOJE)
    foto = fotografar_periodo(_fonte(cenario), semana[0], semana[-1])
    panorama = planejar_enxugamento(foto, pedido)
    _, blocos = panorama_enxugamento(foto, panorama)
    linhas = blocos[0].dados.linhas  # type: ignore[union-attr]
    payload = payload_da_semana(cenario)
    recontado = [
        (r.profissionais_sem_atendimento, r.turnos_sem_profissional)
        for r in (_no_oraculo(payload, foto, c.passos) for c in panorama.cenarios)
    ]
    assert [
        (linha["profissionais_sem_atendimento"], linha["turnos_sem_profissional"])
        for linha in linhas
    ] == recontado
    for linha in linhas:
        assert type(linha["profissionais_sem_atendimento"]) is int
        assert type(linha["turnos_sem_profissional"]) is int
    if cenario == "enxugamento_tres":
        assert all(sem >= 1 for sem, _ in recontado)
    if cenario == "enxugamento" and not pedido.permitir_trocar_dia:
        assert all(turnos >= 1 for _, turnos in recontado)


def test_por_padrao_so_o_recomendado_e_detalhado() -> None:
    comparativo, passos = _blocos(_invocar({}))
    grupos = {linha["cenario"] for linha in passos["dados"]["linhas"]}
    assert grupos == {"Cenário 1 · equilibrado · libera 3 turnos"}
    assert len(passos["dados"]["linhas"]) == comparativo["dados"]["linhas"][0]["passos"]
    assert [linha["passo"] for linha in passos["dados"]["linhas"]] == [1, 2, 3, 4]


def test_detalhar_cenario_nao_recomendado_mostra_so_o_que_difere() -> None:
    padrao = _invocar({})
    codigo = _mapa(str(padrao.content))[4]
    mensagem = _invocar({"detalhar_cenarios": [codigo]})
    comparativo, passos = _blocos(mensagem)

    quarto = comparativo["dados"]["linhas"][3]
    iguais = quarto["passos_iguais_ao_recomendado"]
    assert passos["titulo"] == "Passos do Cenário 4 (apenas o que difere do Cenário 1)"
    assert (
        f"Mostra só o que difere do Cenário 1; {iguais} passo é igual aos dele."
        in (passos["avisos"])
    )
    linhas = passos["dados"]["linhas"]
    assert {linha["cenario"] for linha in linhas} == {"Cenário 4 · equilibrado · libera 2 turnos"}
    assert len(linhas) == quarto["passos"] - iguais
    do_recomendado = {
        (linha["paciente"], linha["de"], linha["para"])
        for linha in _passos(padrao)["dados"]["linhas"]
    }
    assert all((x["paciente"], x["de"], x["para"]) not in do_recomendado for x in linhas)
    # A aba continua curta e não repete o título.
    assert [t["nome"] for t in passos["tabelas"]] == [ABA_PASSOS]
    assert 'termine com a pergunta "Quer ver os passos do Cenário 1?"' in str(mensagem.content)


def test_detalhar_varios_cenarios_mantem_as_linhas_de_cada_um_seguidas() -> None:
    mapa = _mapa(str(_invocar({}).content))
    passos = _passos(_invocar({"detalhar_cenarios": [mapa[4], mapa[1], mapa[2]]}))

    assert passos["titulo"] == (
        "Passos dos Cenários 1, 2 e 4 (os Cenários 2 e 4 só com o que difere do Cenário 1)"
    )
    grupos = [linha["cenario"].split(" · ")[0] for linha in passos["dados"]["linhas"]]
    vistos = list(dict.fromkeys(grupos))
    assert vistos == ["Cenário 1", "Cenário 2", "Cenário 4"]
    # Consecutivas: cada grupo aparece num trecho só.
    assert sum(1 for a, b in pairwise(grupos) if a != b) == len(vistos) - 1
    assert any(aviso.startswith("Cenário 2: Mostra só o que difere") for aviso in passos["avisos"])


def test_codigo_inexistente_responde_em_texto_sem_bloco_de_passos() -> None:
    mensagem = _invocar({"detalhar_cenarios": ["E-0000"]})

    blocos = _blocos(mensagem)
    assert [b["titulo"] for b in blocos] == [TITULO_COMPARATIVO]
    texto = str(mensagem.content)
    assert "não está entre os gerados agora com estes parâmetros" in texto
    assert "na tela vai só o comparativo, sem passos" in texto
    assert _sem_nomes(texto)


def test_mesmos_parametros_regeram_os_mesmos_cenarios_e_codigos() -> None:
    primeiro, segundo = _invocar({}), _invocar({})
    assert primeiro.content == segundo.content
    assert primeiro.artifact == segundo.artifact
    # Pedir o detalhe não muda os cenários nem os códigos.
    codigo = _mapa(str(primeiro.content))[3]
    detalhe = _invocar({"detalhar_cenarios": [codigo.lower()]})
    assert _mapa(str(detalhe.content)) == _mapa(str(primeiro.content))
    assert _blocos(detalhe)[0] == _blocos(primeiro)[0]


@pytest.mark.parametrize(
    "args",
    [{}, {"detalhar_cenarios": ["E-9DD6", "E-2934"]}, {"detalhar_cenarios": ["E-0000"]}],
    ids=["padrao", "detalhe", "inexistente"],
)
def test_nenhum_codigo_em_bloco_algum(args: dict[str, Any]) -> None:
    for bloco in _blocos(_invocar(args)):
        assert _CODIGO.search(json.dumps(bloco, ensure_ascii=False)) is None


def test_tipos_de_movimento_legiveis_e_observacao_so_nas_excecoes() -> None:
    linhas = [
        linha
        for cenario in ("enxugamento", "enxugamento_tres", "enxugamento_cadeia")
        for linha in _passos(
            _invocar(
                {
                    "detalhar_cenarios": list(
                        _mapa(str(_invocar({}, _fonte(cenario)).content)).values()
                    )
                },
                _fonte(cenario),
            )
        )["dados"]["linhas"]
    ]
    assert linhas
    assert {linha["tipo_de_movimento"] for linha in linhas} <= set(TIPOS_DE_MOVIMENTO.values())
    assert not any(re.search(r"\bL[1-5]\b", json.dumps(linha)) for linha in linhas)
    excecoes = {
        OBSERVACAO_ABRE_ESPACO,
        "outra sala",
        "posto simultâneo da profissional",
        OBSERVACAO_SEM_PRESENCA,
    }
    for linha in linhas:
        partes = [p for p in str(linha["observacao"]).split("; ") if p]
        assert set(partes) <= excecoes
    # Caso padrão (mesmo profissional, outro turno, mesma sala): observação vazia.
    padrao = _passos(_invocar({}))["dados"]["linhas"][0]
    assert padrao["tipo_de_movimento"] == "Mesmo profissional · outro turno"
    assert padrao["observacao"] == ""


def _frases(texto: str) -> list[str]:
    return [f for f in re.split(r"(?<=[.!?])\s+", texto.strip()) if f]


@pytest.mark.parametrize(
    ("args", "proximo"),
    [({}, 2), ({"detalhar_cenarios": ["E-5EB1", "E-88EB"]}, 3)],
    ids=["padrao", "dois-detalhados"],
)
def test_resumo_deterministico_em_3_a_4_frases_com_a_pergunta(
    args: dict[str, Any], proximo: int
) -> None:
    blocos = [BlocoRelatorio.model_validate(b) for b in _blocos(_invocar(args))]
    texto = resumo_deterministico(blocos, [])

    frases = _frases(texto)
    assert 3 <= len(frases) <= 4
    assert frases[0].startswith("Sugiro o Cenário 1, que equilibra turnos liberados e mudanças")
    assert "libera 3 turnos" in frases[0] and "4 pacientes" in frases[0]
    assert "3 com troca de profissional e 0 com troca de dia" in frases[0]
    assert any("confirmada com as famílias" in f and "planilha" in f for f in frases)
    assert frases[-1] == f"Quer ver os passos do Cenário {proximo}?"
    assert _sem_nomes(texto) and _CODIGO.search(texto) is None
    assert termos_proibidos(texto) == []


def test_codigo_na_resposta_vira_o_resumo_deterministico(caplog: pytest.LogCaptureFixture) -> None:
    resposta = (
        "Sugiro o cenário E-5EB1, que libera 3 turnos por semana. Cada mudança precisa ser "
        "confirmada com as famílias e feita manualmente na planilha. Quer ver o E-88EB?"
    )
    assert codigos_de_cenario_na_resposta(resposta) == 2
    assert codigos_de_cenario_na_resposta("Quer ver os passos do Cenário 2?") == 0
    with caplog.at_level(logging.DEBUG):
        texto, _, blocos, _ = _conversar({}, resposta, "que movimentações eu posso fazer?")

    assert _CODIGO.search(texto) is None
    assert texto.startswith("Sugiro o Cenário 1")
    assert texto.endswith("Quer ver os passos do Cenário 2?")
    assert len(blocos) == 2
    registros = _registros(caplog)
    assert "código(s) de cenário" in registros and "E-5EB1" not in registros


# --- detalhe de um cenário: só o que difere x passos completos -----------------


def _foto_e_panorama() -> tuple[FotografiaPeriodo, PanoramaEnxugamento]:
    semana = dias_da_semana_de(HOJE)
    foto = fotografar_periodo(_fonte(), semana[0], semana[-1])
    return foto, planejar_enxugamento(foto, PedidoEnxugamento())


def test_subconjunto_do_recomendado_sai_com_os_passos_completos() -> None:
    """O Cenário 3 da semana fictícia faz só passos do Cenário 1: nada a mostrar "só do
    que difere", então saem os passos completos com o aviso de quantos ele faz."""
    padrao = _invocar({})
    comparativo, _ = _blocos(padrao)
    terceiro, primeiro = comparativo["dados"]["linhas"][2], comparativo["dados"]["linhas"][0]
    assert terceiro["passos_iguais_ao_recomendado"] == terceiro["passos"] == 3
    assert primeiro["passos"] == 4

    passos = _passos(_invocar({"detalhar_cenarios": [_mapa(str(padrao.content))[3]]}))

    assert passos["titulo"] == "Passos do Cenário 3"
    assert passos["avisos"][0] == (
        "Este cenário faz 3 dos 4 passos do Cenário 1 e deixa de fazer 1; libera 1 turno a menos."
    )
    assert not any("Mostra só o que difere" in aviso for aviso in passos["avisos"])
    linhas = passos["dados"]["linhas"]
    assert len(linhas) == terceiro["passos"]
    assert {linha["cenario"] for linha in linhas} == {"Cenário 3 · menos mudança · libera 2 turnos"}
    assert [linha["passo"] for linha in linhas] == [1, 2, 3]


def test_diferenca_parcial_continua_so_com_o_que_difere() -> None:
    passos = _passos(_invocar({"detalhar_cenarios": [_mapa(str(_invocar({}).content))[2]]}))

    assert passos["titulo"] == "Passos do Cenário 2 (apenas o que difere do Cenário 1)"
    assert (
        passos["avisos"][0] == "Mostra só o que difere do Cenário 1; 3 passos são iguais aos dele."
    )
    # O número do passo é o do cenário inteiro (os iguais ficam de fora).
    assert [linha["passo"] for linha in passos["dados"]["linhas"]] == [2]


def test_cenario_identico_ao_recomendado_sai_com_os_passos_completos() -> None:
    foto, panorama = _foto_e_panorama()
    recomendado = panorama.cenarios[0]
    # Mesmos passos, outro código e outro perfil (o motor não repete cenário; aqui é
    # montado para testar o bloco).
    identico = replace(recomendado, codigo="E-FFFF", perfil=Objetivo.MAIS_TURNOS)
    montado = replace(panorama, cenarios=(recomendado, identico))

    _, blocos = panorama_enxugamento(foto, montado, ["E-FFFF"])
    passos = blocos[1]

    assert passos.titulo == "Passos do Cenário 2"
    assert passos.avisos[0] == "Este cenário é idêntico ao Cenário 1 (os mesmos 4 passos)."
    assert isinstance(passos.dados, DadosTabelaGenerica)
    assert [linha["passo"] for linha in passos.dados.linhas] == [1, 2, 3, 4]
    assert blocos[0].dados.linhas[1]["passos_iguais_ao_recomendado"] == 4  # type: ignore[union-attr]


@pytest.mark.parametrize("cenario", ["enxugamento", "enxugamento_tres", "enxugamento_cadeia"])
def test_nunca_sai_bloco_de_passos_sem_linhas(cenario: str) -> None:
    fonte = _fonte(cenario)
    codigos = list(_mapa(str(_invocar({}, fonte).content)).values())
    pedidos: list[list[str]] = [[], *([c] for c in codigos), codigos]
    for pedido in pedidos:
        mensagem = _invocar({"detalhar_cenarios": pedido} if pedido else {}, fonte)
        for bloco in _blocos(mensagem):
            assert bloco["dados"]["linhas"], (cenario, len(pedido), bloco["titulo"])
            assert not any("Nenhum passo difere" in aviso for aviso in bloco["avisos"])
