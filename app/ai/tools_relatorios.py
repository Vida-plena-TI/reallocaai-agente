"""Tools de entendimento geral da agenda: `consultar_catalogo` e `gerar_relatorio`.

`gerar_relatorio` é uma tool única e parametrizada sobre o motor genérico da
engine (`app.engine.relatorio_generico`): qualquer período (um dia, a semana,
segunda a sexta) é lido uma vez e respondido numa chamada só, em vez de uma
tool fixa por relatório e uma chamada por dia. O bloco completo vai como
artefato (`content_and_artifact`); o modelo recebe só um resumo curto, com os
números já calculados.

Parâmetros que não fazem sentido voltam como mensagem útil ao modelo (nunca
como exceção que derrubaria o turno), inclusive erros de validação do schema.
"""

import logging
import time
from datetime import date
from datetime import time as hora_do_dia
from typing import Any, Final, Literal

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field, ValidationError

from app.ai import blocos_gerais
from app.ai.relatorios import NOTA_GRADE_SEMANAL
from app.ai.servico_agenda import (
    ProfissionalAmbiguo,
    ProfissionalNaoEncontrado,
    resolver_profissional,
)
from app.domain import (
    CORES_CONVENIO,
    Convenio,
    Especialidade,
    ScheduleDataSource,
    dias_da_semana_de,
    normalizar_id,
    reconhecer_especialidade,
)
from app.domain.constants import (
    DURACAO_SLOT_MINUTOS,
    FIM_PAUSA,
    HORARIO_ABERTURA,
    HORARIO_FECHAMENTO,
    INICIO_PAUSA,
)
from app.domain.formatacao import chave_natural, rotulo_enum
from app.engine.fotografia import (
    CONVENIO_NAO_INFORMADO,
    MAX_DIAS_POR_PERIODO,
    FotografiaPeriodo,
    Periodo,
    fotografar_periodo,
)
from app.engine.relatorio_generico import (
    DEFINICOES_METRICAS,
    MAX_SLOTS_POR_JANELA,
    ConsultaInvalidaError,
    ConsultaRelatorio,
    Dimensao,
    FiltrosRelatorio,
    Metrica,
    gerar_relatorio,
    rotulo_convenio,
)
from app.engine.relatorios_prontos import (
    agenda_do_paciente,
    capacidade_por_especialidade,
    comparativo_dias,
    convenios,
    qualidade_agenda,
    ranking_ocupacao,
    salas_ociosas,
    vagas_livres,
)

logger = logging.getLogger(__name__)

NomeRelatorio = Literal[
    "vagas_livres",
    "janelas_continuas",
    "ranking_ocupacao",
    "comparativo_dias",
    "convenios",
    "qualidade_agenda",
    "salas_ociosas",
    "capacidade_especialidade",
    "agenda_paciente",
    "personalizado",
]

#: O que cada relatório pronto responde (vai no catálogo e na descrição da tool).
DESCRICAO_RELATORIOS: Final[dict[str, str]] = {
    "vagas_livres": "vagas de 30 min em faixas, com resumo por dia e manhã/tarde",
    "janelas_continuas": "onde cabe um atendimento de duracao_minutos seguidos (padrão 60)",
    "ranking_ocupacao": "ocupação por profissional (ou outra dimensão), da maior p/ a menor",
    "comparativo_dias": "um dia contra o outro: ocupação, vagas, pacientes e sessões",
    "convenios": "sessões e pacientes por convênio e o que aguarda autorização",
    "qualidade_agenda": "buracos entre atendimentos e vagas isoladas por profissional",
    "salas_ociosas": "salas da menos para a mais ocupada",
    "capacidade_especialidade": "capacidade sobrando, equilibrada ou no limite por especialidade",
    "agenda_paciente": "horários, profissionais e esperas de UM paciente pedido pelo nome",
    "personalizado": "qualquer cruzamento: informe dimensoes e metricas",
}

_DIAS_DA_SEMANA_POR_NOME: Final[dict[str, int]] = {
    "segunda": 0,
    "terca": 1,
    "quarta": 2,
    "quinta": 3,
    "sexta": 4,
    "sabado": 5,
}

#: Grafias aceitas para cada convênio (sobre o texto normalizado).
_CONVENIOS_POR_TEXTO: Final[dict[str, str]] = {
    "unimed": Convenio.UNIMED.value,
    "klini": Convenio.KLINI_SAUDE.value,
    "klini-saude": Convenio.KLINI_SAUDE.value,
    "sulamerica": Convenio.SULAMERICA.value,
    "sul-america": Convenio.SULAMERICA.value,
    "particular": Convenio.PARTICULAR.value,
    "nao-informado": CONVENIO_NAO_INFORMADO,
    "sem-convenio": CONVENIO_NAO_INFORMADO,
}

#: Nome da cor de fonte de cada convênio na planilha.
_NOMES_DAS_CORES: Final[dict[str, str]] = {
    "#0000FF": "azul",
    "#FF0000": "vermelho",
    "#CC0000": "vermelho",
    "#274E13": "verde",
    "#38761D": "verde",
    "#6AA84F": "verde",
    "#9900FF": "roxo",
}


class ArgsConsultarCatalogo(BaseModel):
    data: date | None = Field(
        default=None,
        description="Qualquer data da semana a descrever (AAAA-MM-DD). Em branco: semana de hoje.",
    )


class ArgsGerarRelatorio(BaseModel):
    relatorio: NomeRelatorio = Field(
        description="Relatório pronto, ou 'personalizado' para escolher dimensoes e metricas."
    )
    inicio: date | None = Field(
        default=None,
        description=(
            "Primeiro dia (AAAA-MM-DD). Em branco com fim em branco: a semana de hoje "
            "(segunda a sábado). Só inicio: um dia."
        ),
    )
    fim: date | None = Field(default=None, description="Último dia, inclusive (AAAA-MM-DD).")
    profissionais: list[str] | None = Field(
        default=None, description="Filtra por estas profissionais (nome ou parte do nome)."
    )
    especialidades: list[str] | None = Field(
        default=None, description="Filtra por especialidade, como citada ('psico', 'fono', 'TO')."
    )
    salas: list[str] | None = Field(default=None, description="Filtra por sala ('Sala 3' ou '3').")
    convenios: list[str] | None = Field(
        default=None,
        description="Filtra por convênio: unimed, klini, sulamerica, particular, nao_informado.",
    )
    dias_semana: list[Literal["segunda", "terca", "quarta", "quinta", "sexta", "sabado"]] | None = (
        Field(default=None, description="Só estes dias da semana dentro do período.")
    )
    periodo_do_dia: Literal["manha", "tarde"] | None = Field(
        default=None, description="Só manhã (antes das 12h) ou só tarde (depois das 13h)."
    )
    duracao_minutos: int | None = Field(
        default=None,
        description="janelas_continuas: duração do atendimento em minutos (60 = 1 h, 90 = 1 h 30).",
    )
    dimensoes: list[Dimensao] | None = Field(
        default=None,
        description=(
            "personalizado: por onde agrupar (pode ser vazio = só o total). "
            "ranking_ocupacao: uma dimensão (padrão profissional)."
        ),
    )
    metricas: list[Metrica] | None = Field(
        default=None, description="personalizado (obrigatório): o que calcular."
    )
    ordenar_por: str | None = Field(
        default=None, description="personalizado: uma das metricas ou dimensoes pedidas."
    )
    ordem: Literal["desc", "asc"] | None = Field(
        default=None,
        description="Ordem de ordenar_por / do ranking. Padrão: desc (maior primeiro).",
    )
    limite: int | None = Field(default=None, ge=1, description="Máximo de linhas (ex.: top 5).")
    paciente: str | None = Field(
        default=None,
        description="Só em agenda_paciente: nome do paciente exatamente como citado.",
    )


class ParametroInvalidoError(Exception):
    """Parâmetro que não pôde ser resolvido; a mensagem vai ao modelo (ou ao 422 da API)."""


def _mensagem_validacao(erro: Exception) -> str:
    """Erro de schema em texto curto, com o campo e o problema, para o modelo corrigir."""
    if not isinstance(erro, ValidationError):
        return "Parâmetros inválidos para gerar_relatorio. Corrija e chame de novo."
    problemas = "; ".join(
        f"{'.'.join(str(parte) for parte in item['loc'])}: {item['msg']}" for item in erro.errors()
    )
    return (
        f"Parâmetros inválidos para gerar_relatorio ({problemas}). Dimensões válidas: "
        f"{', '.join(d.value for d in Dimensao)}. Métricas válidas: "
        f"{', '.join(m.value for m in Metrica)}. Corrija e chame de novo."
    )


def _periodo(inicio: date | None, fim: date | None, hoje: date) -> tuple[date, date]:
    if inicio is None and fim is None:
        semana = dias_da_semana_de(hoje)
        return semana[0], semana[-1]
    inicio = inicio or fim
    fim = fim or inicio
    assert inicio is not None and fim is not None
    if fim < inicio:
        raise ParametroInvalidoError("O fim do período é anterior ao início.")
    if (fim - inicio).days + 1 > MAX_DIAS_POR_PERIODO:
        raise ParametroInvalidoError(
            f"Período longo demais: o máximo é {MAX_DIAS_POR_PERIODO} dias. Divida o pedido."
        )
    return inicio, fim


def _resolver_profissionais(foto: FotografiaPeriodo, nomes: list[str]) -> frozenset[str]:
    ids: set[str] = set()
    for nome in nomes:
        localizacao = resolver_profissional(foto.profissionais, nome)
        if isinstance(localizacao, ProfissionalAmbiguo):
            candidatos = ", ".join(
                f"{c.nome} ({rotulo_enum(c.especialidade.value)})" for c in localizacao.candidatos
            )
            raise ParametroInvalidoError(
                f"Mais de uma profissional corresponde a '{nome}': {candidatos}. Pergunte qual "
                "delas a pessoa quis dizer."
            )
        if isinstance(localizacao, ProfissionalNaoEncontrado):
            raise ParametroInvalidoError(
                f"Nenhuma profissional '{nome}' na agenda do período. Disponíveis: "
                f"{', '.join(localizacao.nomes_disponiveis) or 'nenhuma'}."
            )
        ids.add(localizacao.profissional_id)
    return frozenset(ids)


def _resolver_especialidades(textos: list[str]) -> frozenset[Especialidade]:
    especialidades: set[Especialidade] = set()
    for texto in textos:
        especialidade = reconhecer_especialidade(texto)
        if especialidade is None:
            validas = ", ".join(rotulo_enum(e.value) for e in Especialidade)
            raise ParametroInvalidoError(
                f"Especialidade '{texto}' não reconhecida. Válidas: {validas}."
            )
        especialidades.add(especialidade)
    return frozenset(especialidades)


def _resolver_salas(foto: FotografiaPeriodo, textos: list[str]) -> frozenset[str]:
    ids: set[str] = set()
    for texto in textos:
        alvo = normalizar_id(texto)
        if alvo.isdigit():
            alvo = f"sala-{int(alvo)}"
        sala = next(
            (s for s in foto.salas.values() if s.id == alvo or normalizar_id(s.nome) == alvo),
            None,
        )
        if sala is None:
            disponiveis = ", ".join(
                sorted((s.nome for s in foto.salas.values()), key=chave_natural)
            )
            raise ParametroInvalidoError(
                f"Sala '{texto}' não está na agenda do período. Salas: {disponiveis}."
            )
        ids.add(sala.id)
    return frozenset(ids)


def _resolver_convenios(textos: list[str]) -> frozenset[str]:
    valores: set[str] = set()
    for texto in textos:
        valor = _CONVENIOS_POR_TEXTO.get(normalizar_id(texto))
        if valor is None:
            raise ParametroInvalidoError(
                f"Convênio '{texto}' não reconhecido. Válidos: unimed, klini, sulamerica, "
                "particular, nao_informado."
            )
        valores.add(valor)
    return frozenset(valores)


def _filtros(foto: FotografiaPeriodo, args: ArgsGerarRelatorio) -> FiltrosRelatorio:
    return FiltrosRelatorio(
        profissional_ids=_resolver_profissionais(foto, args.profissionais or []),
        especialidades=_resolver_especialidades(args.especialidades or []),
        sala_ids=_resolver_salas(foto, args.salas or []),
        convenios=_resolver_convenios(args.convenios or []),
        dias_semana=frozenset(_DIAS_DA_SEMANA_POR_NOME[d] for d in args.dias_semana or []),
        periodos=frozenset({Periodo(args.periodo_do_dia)}) if args.periodo_do_dia else frozenset(),
    )


def _recorte(foto: FotografiaPeriodo, filtros: FiltrosRelatorio) -> str:
    """` (Ana Lima · Psicologia · tarde)` para o título; vazio sem filtro."""
    nomes_dias = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado")
    partes = [
        *sorted(foto.nome_profissional(p) for p in filtros.profissional_ids),
        *sorted(rotulo_enum(e.value) for e in filtros.especialidades),
        *sorted((foto.nome_sala(s) for s in filtros.sala_ids), key=chave_natural),
        *sorted(rotulo_convenio(c) for c in filtros.convenios),
        *(nomes_dias[d] for d in sorted(filtros.dias_semana)),
        *("manhã" if p is Periodo.MANHA else "tarde" for p in sorted(filtros.periodos)),
    ]
    return f" ({' · '.join(partes)})" if partes else ""


def _avisos(foto: FotografiaPeriodo) -> list[str]:
    avisos = [NOTA_GRADE_SEMANAL]
    if (foto.fim - foto.inicio).days >= 7:
        avisos.append(
            "O período passa de uma semana: como a planilha é uma grade semanal, cada "
            "semana repete a mesma agenda."
        )
    if foto.parcial:
        dias = ", ".join(f"{d:%d/%m}" for d in foto.dias_nao_lidos)
        avisos.append(f"Não foi possível ler: {dias}; os números são parciais.")
    return avisos


def _ajustar_combinacao(args: ArgsGerarRelatorio) -> tuple[ArgsGerarRelatorio, list[str]]:
    """Recusa o que não faz sentido e ignora, com nota, o que só sobra.

    Parâmetro a mais num relatório pronto (ex.: `metricas` em `convenios`) não
    vale uma segunda chamada: é ignorado e a nota vai no texto para o modelo.
    """
    if args.paciente is not None and args.relatorio != "agenda_paciente":
        raise ParametroInvalidoError(
            "paciente só vale com relatorio='agenda_paciente'; os demais relatórios nunca "
            "trazem pacientes, só contagens."
        )
    if args.relatorio == "agenda_paciente" and not (args.paciente or "").strip():
        raise ParametroInvalidoError("agenda_paciente exige o nome do paciente em 'paciente'.")
    if args.relatorio == "personalizado" and not args.metricas:
        raise ParametroInvalidoError(
            "personalizado exige 'metricas' (ex.: ['vagas_livres']) e, opcionalmente, "
            "'dimensoes' (ex.: ['dia', 'especialidade'])."
        )
    if args.relatorio == "ranking_ocupacao" and args.dimensoes and len(args.dimensoes) != 1:
        raise ParametroInvalidoError("ranking_ocupacao aceita exatamente uma dimensão.")

    # "Vagas de 1 h" é pedido de janela contínua.
    if args.relatorio == "vagas_livres" and args.duracao_minutos is not None:
        args = args.model_copy(update={"relatorio": "janelas_continuas"})

    ignorados: list[str] = []
    if args.relatorio != "personalizado" and args.metricas:
        ignorados.append("metricas")
    if args.relatorio not in ("personalizado", "ranking_ocupacao") and args.dimensoes:
        ignorados.append("dimensoes")
    if args.relatorio != "personalizado" and args.ordenar_por:
        ignorados.append("ordenar_por")
    if args.relatorio != "janelas_continuas" and args.duracao_minutos is not None:
        ignorados.append("duracao_minutos")
    if not ignorados:
        return args, []
    nota = (
        f"Obs.: {', '.join(ignorados)} ignorado(s): o relatório {args.relatorio} já traz as "
        "próprias colunas. Para escolher dimensões e métricas, use 'personalizado'."
    )
    return args.model_copy(update=dict.fromkeys(ignorados)), [nota]


def _slots_da_janela(duracao_minutos: int | None) -> int:
    minutos = duracao_minutos if duracao_minutos is not None else 60
    slots = -(-minutos // DURACAO_SLOT_MINUTOS)
    if minutos <= 0 or slots > MAX_SLOTS_POR_JANELA:
        raise ParametroInvalidoError(
            f"duracao_minutos deve ficar entre {DURACAO_SLOT_MINUTOS} e "
            f"{MAX_SLOTS_POR_JANELA * DURACAO_SLOT_MINUTOS}."
        )
    return slots


def _ordenar_por(valor: str | None) -> Metrica | Dimensao | None:
    if valor is None:
        return None
    for tipo in (Metrica, Dimensao):
        try:
            return tipo(valor)
        except ValueError:
            continue
    raise ParametroInvalidoError(
        f"ordenar_por='{valor}' não é métrica nem dimensão. Use uma das métricas ou "
        "dimensões pedidas."
    )


#: Até quantas linhas de dado a resposta fica só em texto; acima disso, vai bloco.
MAXIMO_DE_LINHAS_EM_TEXTO: Final[int] = 3


def vagas_de_um_dia(
    fonte: ScheduleDataSource,
    dia: date,
    *,
    profissional_id: str | None = None,
    especialidade: Especialidade | None = None,
    inicio: hora_do_dia | None = None,
    fim: hora_do_dia | None = None,
) -> tuple[str, dict[str, Any] | None, int]:
    """Vagas de um dia pelo mesmo caminho de `gerar_relatorio(vagas_livres, inicio=fim=dia)`.

    É o núcleo de `consultar_disponibilidade`. A janela [`inicio`, `fim`] é
    aplicada nos slots antes de agrupar (a vaga precisa começar em `inicio` ou
    depois e terminar em `fim` ou antes). Devolve o texto, o bloco
    `disponibilidade` em JSON e quantas faixas ele tem; `None` sem agenda.
    """
    foto = fotografar_periodo(fonte, dia, dia)
    if not foto.slots:
        if dia.weekday() == 6:
            return (
                f"Não há agenda {blocos_gerais.rotulo_periodo(dia, dia)} (domingo não tem "
                "expediente).",
                None,
                0,
            )
        return (
            f"Nenhum horário livre encontrado em {dia:%d/%m/%Y} para os filtros informados.",
            None,
            0,
        )
    if inicio is not None or fim is not None:
        foto = foto.model_copy(
            update={
                "slots": [
                    slot
                    for slot in foto.slots
                    if (inicio is None or slot.hora_inicio >= inicio)
                    and (fim is None or slot.hora_fim <= fim)
                ]
            }
        )
    filtros = FiltrosRelatorio(
        profissional_ids=frozenset({profissional_id}) if profissional_id else frozenset(),
        especialidades=frozenset({especialidade}) if especialidade else frozenset(),
    )
    recorte = _recorte(foto, filtros)
    if inicio is not None or fim is not None:
        janela = f"{(inicio or HORARIO_ABERTURA):%H:%M}-{(fim or HORARIO_FECHAMENTO):%H:%M}"
        recorte = f"{recorte[:-1]} · {janela})" if recorte else f" ({janela})"
    relatorio = vagas_livres(foto, filtros, 1)
    texto, bloco = blocos_gerais.disponibilidade(foto, relatorio, _avisos(foto), recorte)
    return texto, bloco.model_dump(mode="json"), len(relatorio.faixas)


def executar_relatorio(
    fonte: ScheduleDataSource, hoje: date, args: ArgsGerarRelatorio
) -> tuple[str, dict[str, Any] | None]:
    """Texto curto e bloco (já em JSON) do relatório pedido em `args`.

    É o núcleo de `gerar_relatorio` e de `POST /relatorios/gerar`: os dois
    devolvem exatamente o mesmo. Parâmetro sem sentido levanta
    `ParametroInvalidoError` ou `ConsultaInvalidaError`, com mensagem útil.
    """
    args, notas = _ajustar_combinacao(args)
    inicio, fim = _periodo(args.inicio, args.fim, hoje)
    janela = _slots_da_janela(args.duracao_minutos) if args.relatorio == "janelas_continuas" else 1
    ordenar_por = _ordenar_por(args.ordenar_por)

    comeco = time.perf_counter()
    foto = fotografar_periodo(fonte, inicio, fim)
    if not foto.slots:
        rotulo = blocos_gerais.rotulo_periodo(inicio, fim)
        return f"Não há agenda {rotulo} (domingo não tem expediente).", None
    filtros = _filtros(foto, args)
    avisos = _avisos(foto)
    recorte = _recorte(foto, filtros)
    periodo = blocos_gerais.rotulo_periodo(inicio, fim)

    match args.relatorio:
        case "vagas_livres" | "janelas_continuas":
            texto, bloco = blocos_gerais.disponibilidade(
                foto, vagas_livres(foto, filtros, janela), avisos, recorte
            )
        case "ranking_ocupacao":
            dimensao = args.dimensoes[0] if args.dimensoes else Dimensao.PROFISSIONAL
            texto, bloco = blocos_gerais.tabela_generica(
                foto,
                ranking_ocupacao(
                    foto,
                    filtros,
                    dimensao,
                    decrescente=args.ordem != "asc",
                    limite=args.limite,
                ),
                relatorio="ranking_ocupacao",
                titulo=(
                    f"Ranking de ocupação por {blocos_gerais.NOMES_DIMENSOES[dimensao]}"
                    f"{recorte} {periodo}"
                ),
                avisos=avisos,
            )
        case "comparativo_dias":
            texto, bloco = blocos_gerais.tabela_generica(
                foto,
                comparativo_dias(foto, filtros),
                relatorio="comparativo_dias",
                titulo=f"Comparativo dia a dia{recorte} {periodo}",
                avisos=avisos,
            )
        case "convenios":
            texto, bloco = blocos_gerais.convenio(foto, convenios(foto, filtros), avisos, recorte)
        case "qualidade_agenda":
            texto, bloco = blocos_gerais.qualidade_agenda(
                foto, qualidade_agenda(foto, filtros), avisos, recorte
            )
        case "salas_ociosas":
            texto, bloco = blocos_gerais.tabela_generica(
                foto,
                salas_ociosas(foto, filtros),
                relatorio="salas_ociosas",
                titulo=f"Salas ociosas{recorte} {periodo}",
                avisos=avisos,
            )
        case "capacidade_especialidade":
            texto, bloco = blocos_gerais.capacidade(
                foto, capacidade_por_especialidade(foto, filtros), avisos, recorte
            )
        case "agenda_paciente":
            assert args.paciente is not None
            agenda = agenda_do_paciente(foto, normalizar_id(args.paciente))
            if agenda is None:
                return (
                    f"'{args.paciente}' não aparece na agenda {periodo}. A busca é pelo "
                    "nome exato como está na agenda; pacientes só aparecem nos dias em que "
                    "têm atendimento."
                ), None
            texto, bloco = blocos_gerais.agenda_paciente(foto, agenda, avisos)
        case "personalizado":
            dimensoes = tuple(args.dimensoes or ())
            metricas = tuple(args.metricas or ())
            resultado = gerar_relatorio(
                foto,
                ConsultaRelatorio(
                    dimensoes=dimensoes,
                    metricas=metricas,
                    filtros=filtros,
                    ordenar_por=ordenar_por,
                    decrescente=None if args.ordem is None else args.ordem == "desc",
                    limite=args.limite,
                ),
            )
            nomes = blocos_gerais.enumerar(
                [blocos_gerais.ROTULOS_METRICAS[m][0].lower() for m in metricas]
            )
            por = (
                " por "
                + blocos_gerais.enumerar([blocos_gerais.NOMES_DIMENSOES[d] for d in dimensoes])
                if dimensoes
                else ""
            )
            texto, bloco = blocos_gerais.tabela_generica(
                foto,
                resultado,
                relatorio="personalizado",
                titulo=f"{nomes.capitalize()}{por}{recorte} {periodo}",
                avisos=avisos,
            )

    # Só o tipo e o tamanho: nada de nomes, filtros ou números da agenda.
    logger.info(
        "gerar_relatorio %s: %d dia(s) em %.2fs",
        args.relatorio,
        len(foto.dias_consultados),
        time.perf_counter() - comeco,
    )
    return "\n".join([texto, *notas]), bloco.model_dump(mode="json")


def _catalogo(foto: FotografiaPeriodo) -> str:
    dias_por_profissional: dict[str, set[int]] = {}
    salas_por_profissional: dict[str, set[str]] = {}
    postos_por_sala: dict[str, set[int]] = {}
    slots_por_dia: dict[date, int] = {}
    for slot in foto.slots:
        if slot.na_grade and slot.profissional_id is not None:
            dias_por_profissional.setdefault(slot.profissional_id, set()).add(slot.data.weekday())
            salas_por_profissional.setdefault(slot.profissional_id, set()).add(slot.sala_id)
            slots_por_dia[slot.data] = slots_por_dia.get(slot.data, 0) + 1
        postos_por_sala.setdefault(slot.sala_id, set()).add(slot.indice_posto)

    curtos = ("seg", "ter", "qua", "qui", "sex", "sáb")
    linhas = [
        f"Catálogo da clínica — semana {blocos_gerais.rotulo_periodo(foto.inicio, foto.fim)}",
        (
            f"Expediente {HORARIO_ABERTURA:%H:%M}-{HORARIO_FECHAMENTO:%H:%M}, pausa "
            f"{INICIO_PAUSA:%H:%M}-{FIM_PAUSA:%H:%M}, slots de {DURACAO_SLOT_MINUTOS} min, "
            "segunda a sábado. Meta de ocupação: 80%."
        ),
        "Dias com agenda (slots escalados): "
        + ", ".join(f"{d:%d/%m} {slots_por_dia[d]}" for d in sorted(slots_por_dia))
        + ".",
        "",
        "Profissionais por especialidade (dias; salas):",
    ]
    por_especialidade: dict[str, list[str]] = {}
    for profissional_id in sorted(dias_por_profissional):
        profissional = foto.profissionais.get(profissional_id)
        especialidade = (
            rotulo_enum(profissional.especialidade.value) if profissional else "Sem especialidade"
        )
        dias = "/".join(curtos[d] for d in sorted(dias_por_profissional[profissional_id]))
        salas = ", ".join(
            sorted(
                (foto.nome_sala(s) for s in salas_por_profissional[profissional_id]),
                key=chave_natural,
            )
        )
        por_especialidade.setdefault(especialidade, []).append(
            f"{foto.nome_profissional(profissional_id)} ({dias}; {salas})"
        )
    linhas.extend(
        f"- {especialidade}: {'; '.join(sorted(nomes, key=normalizar_id))}"
        for especialidade, nomes in sorted(por_especialidade.items())
    )
    linhas.append("")
    linhas.append(
        "Salas (postos = colunas independentes da sala): "
        + ", ".join(
            f"{foto.nome_sala(s)} ({len(postos_por_sala[s])})"
            for s in sorted(postos_por_sala, key=lambda s: chave_natural(foto.nome_sala(s)))
        )
        + "."
    )
    cores: dict[Convenio, set[str]] = {}
    for cor, convenio in CORES_CONVENIO.items():
        cores.setdefault(convenio, set()).add(_NOMES_DAS_CORES.get(cor, cor))
    linhas.append(
        "Convênios (cor da fonte na planilha): "
        + ", ".join(
            f"{rotulo_enum(c.value)} ({'/'.join(sorted(n))})" for c, n in sorted(cores.items())
        )
        + "; preto = encaixe aguardando autorização (convênio não identificado)."
    )
    linhas.append("")
    linhas.append("Métricas de gerar_relatorio:")
    linhas.extend(f"- {m.value}: {d}" for m, d in DEFINICOES_METRICAS.items())
    linhas.append(
        "Dimensões: " + ", ".join(d.value for d in Dimensao) + " (posto = coluna dentro da sala)."
    )
    linhas.append("Relatórios prontos:")
    linhas.extend(f"- {nome}: {descricao}" for nome, descricao in DESCRICAO_RELATORIOS.items())
    linhas.append(
        "Estados de um slot: livre (escalado, sem paciente), ocupado, fechado (posto sem "
        "ninguém escalado no horário). Janela contínua: vagas seguidas do mesmo profissional, "
        "sala e posto, sem atravessar a pausa."
    )
    return "\n".join(linhas)


def texto_catalogo(foto: FotografiaPeriodo) -> str:
    """O texto de `consultar_catalogo` para a fotografia da semana, com o aviso de parcial."""
    texto = _catalogo(foto)
    if foto.parcial:
        texto += "\n" + _avisos(foto)[-1]
    return texto


def criar_tools_relatorios(fonte: ScheduleDataSource, data_referencia: date) -> list[BaseTool]:
    """`consultar_catalogo` e `gerar_relatorio`, fechadas sobre `fonte` e a data de hoje."""

    @tool("consultar_catalogo", args_schema=ArgsConsultarCatalogo)
    def consultar_catalogo_tool(data: date | None = None) -> str:
        """Mapa da clínica na semana: profissionais por especialidade (dias e salas), salas e
        postos, convênios por cor, dias com agenda e o que significa cada métrica e relatório.

        Use quando precisar saber quem/onde/o quê existe antes de montar um relatório, ou
        quando perguntarem o que significa uma métrica. Para perguntas sobre a equipe
        (especialidade, dias, turnos, salas e postos de cada profissional), use
        `consultar_profissionais`. Não traz pacientes.
        """
        semana = dias_da_semana_de(data or data_referencia)
        try:
            foto = fotografar_periodo(fonte, semana[0], semana[-1])
        except Exception as erro:
            return f"Erro ao ler a agenda para o catálogo ({type(erro).__name__})."
        return texto_catalogo(foto)

    @tool(
        "gerar_relatorio",
        args_schema=ArgsGerarRelatorio,
        response_format="content_and_artifact",
    )
    def gerar_relatorio_tool(**kwargs: Any) -> tuple[str, dict[str, Any] | None]:
        """Relatório de QUALQUER período (um dia, a semana, segunda a sexta) numa chamada só.

        Use para: vagas/horários livres de mais de um dia ou com recorte (profissional,
        especialidade, sala, manhã/tarde) -> vagas_livres; onde cabe 1 h ou 1 h 30 ->
        janelas_continuas; ranking_ocupacao; comparativo_dias; convenios e autorizações
        pendentes; qualidade_agenda (buracos); salas_ociosas; capacidade_especialidade;
        agenda_paciente (só com o nome pedido pelo usuário); ou qualquer cruzamento ->
        personalizado com dimensoes e metricas. Os números já vêm calculados: não recalcule.
        UMA chamada cobre o período inteiro: nunca chame uma vez por dia.
        """
        args = ArgsGerarRelatorio.model_validate(kwargs)
        try:
            return executar_relatorio(fonte, data_referencia, args)
        except (ParametroInvalidoError, ConsultaInvalidaError) as erro:
            return f"Não foi possível gerar o relatório: {erro}", None
        except Exception as erro:
            # Só o tipo: a mensagem pode repetir dados da planilha.
            logger.warning("Falha ao gerar relatório %s: %s", args.relatorio, type(erro).__name__)
            return (
                f"Erro ao gerar o relatório ({type(erro).__name__}). Tente de novo em instantes."
            ), None

    gerar_relatorio_tool.handle_validation_error = _mensagem_validacao
    return [consultar_catalogo_tool, gerar_relatorio_tool]


#: Usado pelos testes para checar a cobertura dos relatórios prontos.
RELATORIOS: Final[tuple[str, ...]] = tuple(DESCRICAO_RELATORIOS)
