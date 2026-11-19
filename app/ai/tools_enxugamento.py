"""Tool `planejar_enxugamento`: panorama de movimentações que liberam turnos.

A conta é de `app.engine.enxugamento` (determinística, sem LLM); aqui ficam a
leitura da semana (uma fotografia, cada dia lido uma vez), a tradução dos
argumentos e o texto/bloco (`app.ai.blocos_enxugamento`).

Privacidade e sensibilidade de RH: os argumentos `profissionais_foco` e
`profissionais_preservados` são nomes. Eles nunca vão para o log (só
contagens, o tipo do resultado e o tempo), nem para o `content` (que o
modelo lê: só contagens e códigos de cenário), nem para mensagens de erro
(só o tipo); "profissional não encontrada" não repete o nome dito. Os nomes
de pacientes e de profissionais vão só no bloco da tela, protegido por
`RELATORIOS_COM_NOMES_DE_PACIENTES`, e a guarda de texto
(`app.ai.fundamentacao.nomes_de_profissional_no_panorama`) troca pelo resumo
determinístico qualquer resposta desta rodada que cite um nome.
"""

import logging
import time as relogio
from collections.abc import Sequence
from datetime import date
from typing import Any, Literal

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field, create_model

from app.ai import blocos_enxugamento
from app.ai.parametros_catalogo import (
    como_especialidade,
    dias_validos,
    especialidades_validas,
    mensagem_de_parametro_invalido,
    tipo_dia,
    tipo_especialidade,
)
from app.ai.servico_agenda import (
    ProfissionalAmbiguo,
    ProfissionalNaoEncontrado,
    resolver_profissional,
)
from app.domain import Especialidade, ScheduleDataSource, dias_da_semana_de
from app.domain.catalogo_agenda import NOMES_DOS_DIAS, CatalogoAgenda
from app.domain.constants import (
    MAX_CENARIOS_ENXUGAMENTO,
    MAX_PACIENTES_TURNO_CANDIDATO,
    PERMITIR_TROCAR_DIA_ENXUGAMENTO,
    PERMITIR_ZERAR_SEMANA_ENXUGAMENTO,
    TETO_PACIENTES_TURNO_CANDIDATO,
)
from app.domain.formatacao import rotulo_enum
from app.engine.enxugamento import (
    Objetivo,
    PedidoEnxugamento,
    RegrasEnxugamento,
    Turno,
    planejar_enxugamento,
)
from app.engine.fotografia import FotografiaPeriodo, fotografar_periodo

logger = logging.getLogger(__name__)

NOME_DA_TOOL = "planejar_enxugamento"


class ArgsPlanejarEnxugamento(BaseModel):
    especialidade: str | None = Field(
        default=None,
        description="Só esta especialidade, quando a pergunta citar uma. Em branco: todas.",
    )
    profissionais_foco: list[str] | None = Field(
        default=None,
        description=(
            'Profissionais em foco, com o nome como foi dito ("e se a X ficar só de tarde?"). '
            "Só os turnos delas entram como candidatos."
        ),
    )
    liberar_turno: Literal["manha", "tarde"] | None = Field(
        default=None,
        description=(
            'Com profissionais_foco: o turno que se quer liberar delas ("ficar só de tarde" '
            "= liberar a manhã). Ignora o limite de pacientes por turno."
        ),
    )
    profissionais_preservados: list[str] | None = Field(
        default=None,
        description=(
            "Profissionais que não devem ter turno liberado nem pacientes mudados, com o nome "
            'como foi dito ("sem mexer na Y").'
        ),
    )
    dias_semana: list[str] | None = Field(
        default=None, description="Só estes dias da semana. Em branco: a semana toda."
    )
    objetivo: Literal["menos_mudanca", "mais_turnos", "equilibrado"] = Field(
        default="equilibrado",
        description=(
            "Qual cenário vem primeiro: 'menos_mudanca' (poucas mudanças), 'mais_turnos' (o "
            "máximo de turnos liberados) ou 'equilibrado' (padrão)."
        ),
    )
    permitir_trocar_dia: bool = Field(
        default=PERMITIR_TROCAR_DIA_ENXUGAMENTO,
        description="Só true quando pedirem para considerar mudar pacientes de dia.",
    )
    permitir_zerar_semana: bool = Field(
        default=PERMITIR_ZERAR_SEMANA_ENXUGAMENTO,
        description=(
            "False quando pedirem que nenhuma profissional fique sem atendimento na semana."
        ),
    )
    max_pacientes_por_turno: int = Field(
        default=MAX_PACIENTES_TURNO_CANDIDATO,
        ge=1,
        le=TETO_PACIENTES_TURNO_CANDIDATO,
        description="Turnos com até este número de pacientes são avaliados (padrão 4).",
    )
    max_cenarios: int = Field(
        default=MAX_CENARIOS_ENXUGAMENTO,
        ge=1,
        le=MAX_CENARIOS_ENXUGAMENTO,
        description="Quantos cenários mostrar (padrão 5).",
    )
    cenarios_ja_vistos: list[str] | None = Field(
        default=None,
        description=(
            "Códigos dos cenários já mostrados nesta conversa (ex.: 'E-3F2A'), quando pedirem "
            "mais opções."
        ),
    )


def _data(semana: list[date], dia: str) -> date | None:
    return next((d for d in semana if NOMES_DOS_DIAS[d.weekday()] == dia), None)


class _NomeNaoResolvidoError(Exception):
    def __init__(self, texto: str) -> None:
        super().__init__(texto)
        self.texto = texto


def _resolver(foto: FotografiaPeriodo, nomes: Sequence[str]) -> tuple[str, ...]:
    """Ids das profissionais citadas; pergunta (sem nomes) se alguma não casar."""
    ids: list[str] = []
    for nome in nomes:
        localizacao = resolver_profissional(foto.profissionais, nome)
        if isinstance(localizacao, ProfissionalAmbiguo):
            papeis = ", ".join(
                f"uma de {rotulo_enum(c.especialidade.value)}" for c in localizacao.candidatos
            )
            raise _NomeNaoResolvidoError(
                f"Mais de uma profissional corresponde a um dos nomes citados "
                f"({len(localizacao.candidatos)} candidatas: {papeis}). Pergunte qual delas, "
                "pelo nome completo, antes de seguir; não escolha por conta própria e não "
                "escreva nomes."
            )
        if isinstance(localizacao, ProfissionalNaoEncontrado):
            raise _NomeNaoResolvidoError(
                "Uma das profissionais citadas não está na grade desta semana. Pergunte o nome "
                "completo, sem repetir o nome dito."
            )
        ids.append(localizacao.profissional_id)
    return tuple(dict.fromkeys(ids))


def executar_planejar_enxugamento(
    fonte: ScheduleDataSource,
    data_referencia: date,
    args: dict[str, Any],
    regras: RegrasEnxugamento | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """Texto (só contagens e códigos) e bloco (em JSON, com os nomes) do panorama."""
    comeco = relogio.perf_counter()
    semana = dias_da_semana_de(data_referencia)
    foto = fotografar_periodo(fonte, semana[0], semana[-1])
    try:
        foco = _resolver(foto, args.get("profissionais_foco") or [])
        preservados = _resolver(foto, args.get("profissionais_preservados") or [])
    except _NomeNaoResolvidoError as pergunta:
        logger.info("%s: pergunta sobre profissional citada.", NOME_DA_TOOL)
        return pergunta.texto, None

    especialidade: Especialidade | None = args.get("especialidade")
    dias = tuple(d for d in (_data(semana, str(dia)) for dia in args.get("dias_semana") or []) if d)
    liberar = args.get("liberar_turno")
    pedido = PedidoEnxugamento(
        especialidade=especialidade,
        profissionais_foco=foco,
        liberar_turno=Turno(liberar) if liberar else None,
        profissionais_preservados=preservados,
        dias=dias,
        objetivo=Objetivo(args.get("objetivo") or Objetivo.EQUILIBRADO),
        permitir_trocar_dia=bool(args.get("permitir_trocar_dia", PERMITIR_TROCAR_DIA_ENXUGAMENTO)),
        permitir_zerar_semana=bool(
            args.get("permitir_zerar_semana", PERMITIR_ZERAR_SEMANA_ENXUGAMENTO)
        ),
        max_pacientes_por_turno=int(
            args.get("max_pacientes_por_turno") or MAX_PACIENTES_TURNO_CANDIDATO
        ),
        max_cenarios=int(args.get("max_cenarios") or MAX_CENARIOS_ENXUGAMENTO),
        cenarios_ja_vistos=tuple(str(c) for c in args.get("cenarios_ja_vistos") or []),
    )
    panorama = planejar_enxugamento(foto, pedido, regras)
    texto, bloco = blocos_enxugamento.panorama_enxugamento(foto, panorama)
    logger.info(
        "%s: %d cenário(s), %d passo(s), %d foco, %d preservada(s), %d dia(s) não lido(s), "
        "em %.2fs",
        NOME_DA_TOOL,
        len(panorama.cenarios),
        sum(len(c.passos) for c in panorama.cenarios),
        len(foco),
        len(preservados),
        len(foto.dias_nao_lidos),
        relogio.perf_counter() - comeco,
    )
    return texto, bloco.model_dump(mode="json") if bloco is not None else None


def criar_tool_planejar_enxugamento(
    fonte: ScheduleDataSource, data_referencia: date, catalogo: CatalogoAgenda | None
) -> BaseTool:
    """`planejar_enxugamento`, com especialidade e dias do catálogo da semana."""
    validas = especialidades_validas(catalogo)
    dias = dias_validos(catalogo)
    campos = ArgsPlanejarEnxugamento.model_fields
    dia = tipo_dia(dias)
    args_schema = create_model(
        "ArgsPlanejarEnxugamento",
        __base__=ArgsPlanejarEnxugamento,
        especialidade=(
            tipo_especialidade(validas) | None,
            Field(default=None, description=campos["especialidade"].description),
        ),
        dias_semana=(
            list[dia] | None,  # type: ignore[valid-type]
            Field(default=None, description=campos["dias_semana"].description),
        ),
    )

    @tool(NOME_DA_TOOL, args_schema=args_schema, response_format="content_and_artifact")
    def planejar_enxugamento_tool(**kwargs: Any) -> tuple[str, dict[str, Any] | None]:
        """Sugere um panorama de movimentações que liberam turnos de presença (manhã ou
        tarde) na grade semanal, com vários cenários na tela.

        Use quando perguntarem que movimentações dá para fazer para enxugar a agenda,
        reduzir turnos ou concentrar atendimentos, ou "e se a X ficar só de tarde?".
        Um turno só fica liberado se todos os pacientes dele forem para turnos que já
        acontecem; ninguém muda de terapia nem de duração. Para mais opções, chame de
        novo com cenarios_ja_vistos. Você recebe só contagens e códigos: NUNCA escreva
        nomes de pacientes nem de profissionais, nunca fale em valores, salário ou
        dinheiro (só turnos liberados) e nunca diga que moveu, liberou ou economizou:
        apenas sugira. Grade semanal: não cite datas.
        """
        argumentos = dict(kwargs)
        if argumentos.get("especialidade") is not None:
            argumentos["especialidade"] = como_especialidade(argumentos["especialidade"])
        if argumentos.get("dias_semana"):
            argumentos["dias_semana"] = [str(d) for d in argumentos["dias_semana"]]
        try:
            return executar_planejar_enxugamento(fonte, data_referencia, argumentos)
        except Exception as erro:
            # Só o tipo: a mensagem pode repetir dados da planilha (ou um nome citado).
            logger.warning("Falha ao gerar %s: %s", NOME_DA_TOOL, type(erro).__name__)
            return (
                f"Erro ao montar o panorama ({type(erro).__name__}). Tente de novo em instantes."
            ), None

    planejar_enxugamento_tool.handle_validation_error = mensagem_de_parametro_invalido(
        validas, dias
    )
    return planejar_enxugamento_tool
