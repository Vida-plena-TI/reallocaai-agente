"""Tool `planejar_realocacao`: abrir espaço para um atendimento deslocando outros.

A conta é de `app.engine.realocacao` (determinística, sem LLM); aqui ficam a
leitura da semana (uma fotografia, cada dia lido uma vez), a tradução dos
argumentos e o texto/bloco (`app.ai.blocos_realocacao`).

Privacidade: o argumento `paciente` é um nome. Ele nunca vai para o log
(só contagens, o tipo do resultado e o tempo), nem para o `content` (que o
modelo lê: papéis e horários), nem para mensagens de erro (só o tipo). Os
nomes exibidos vão só no bloco da tela, protegido por
`RELATORIOS_COM_NOMES_DE_PACIENTES`, e a guarda de texto
(`app.ai.fundamentacao.nomes_de_paciente_na_resposta`) também protege o nome
passado no argumento.
"""

import logging
import time as relogio
from datetime import date, time
from typing import Any, Literal

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field, create_model

from app.ai import blocos_realocacao
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
from app.ai.textos_profissional import texto_ambiguo
from app.domain import Especialidade, ScheduleDataSource, dias_da_semana_de
from app.domain.catalogo_agenda import NOMES_DOS_DIAS, CatalogoAgenda
from app.engine.fotografia import fotografar_periodo
from app.engine.realocacao import (
    ROTULO_PACIENTE_NOVO,
    PedidoRealocacao,
    Pergunta,
    PlanoRealocacao,
    Regras,
    Situacao,
    planejar_realocacao,
)

logger = logging.getLogger(__name__)

NOME_DA_TOOL = "planejar_realocacao"


class ArgsPlanejarRealocacao(BaseModel):
    paciente: str | None = Field(
        default=None,
        description=(
            "Nome do paciente como foi dito, só quando a pergunta citar um. Em branco: "
            "paciente novo sem nome. Nunca peça id."
        ),
    )
    situacao: Literal["novo", "ja_na_grade"] | None = Field(
        default=None,
        description=(
            "'novo' para um atendimento que ainda não existe (mesmo de paciente que já faz "
            "outra terapia); 'ja_na_grade' para mudar um atendimento que já está na grade. "
            "Em branco: deduzido pelo nome."
        ),
    )
    especialidade: str | None = Field(
        default=None,
        description="Especialidade do atendimento ('fono', 'psico', 'TO').",
    )
    profissional: str | None = Field(
        default=None,
        description=(
            "Profissional pedida, com o nome como foi dito, só quando a pergunta citar uma."
        ),
    )
    duracao_minutos: int | None = Field(
        default=None,
        gt=0,
        description=(
            "Duração do atendimento novo, em minutos. Para quem já está na grade, a duração "
            "atual é mantida."
        ),
    )
    dias_semana: list[str] | None = Field(
        default=None,
        description="Dias da semana aceitos para o atendimento (grade semanal: sem datas).",
    )
    horario_desejado: time | None = Field(
        default=None,
        description=(
            "Horário de início pedido. Sozinho, a busca começa exatamente nele; com a faixa, "
            "só desempata."
        ),
    )
    horario_minimo: time | None = Field(
        default=None, description="Início mais cedo aceito, quando pedirem uma faixa."
    )
    horario_maximo: time | None = Field(
        default=None, description="Horário até o qual o atendimento precisa terminar."
    )
    dia_origem: str | None = Field(
        default=None,
        description="Dia da semana do atendimento atual, quando a ferramenta perguntar.",
    )
    horario_origem: time | None = Field(
        default=None,
        description="Horário do atendimento atual, quando a ferramenta perguntar.",
    )
    candidata: int | None = Field(
        default=None,
        ge=1,
        description="Número da opção escolhida, quando a ferramenta perguntar qual paciente.",
    )
    mesmo_paciente_da_grade: bool | None = Field(
        default=None,
        description=(
            "Resposta à pergunta da ferramenta se o paciente novo é a mesma pessoa que já "
            "está na grade com esse nome."
        ),
    )


def _data(semana: list[date], dia: str | None) -> date | None:
    if dia is None:
        return None
    return next((d for d in semana if NOMES_DOS_DIAS[d.weekday()] == dia), None)


def executar_planejar_realocacao(
    fonte: ScheduleDataSource,
    data_referencia: date,
    args: dict[str, Any],
    regras: Regras | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """Texto (sem nomes de pacientes) e bloco (em JSON, com os nomes) do pedido."""
    comeco = relogio.perf_counter()
    regras = regras or Regras()
    semana = dias_da_semana_de(data_referencia)
    foto = fotografar_periodo(fonte, semana[0], semana[-1])

    profissional_id: str | None = None
    if citado := args.get("profissional"):
        localizacao = resolver_profissional(foto.profissionais, citado)
        if isinstance(localizacao, ProfissionalAmbiguo):
            return texto_ambiguo(citado, localizacao), None
        if isinstance(localizacao, ProfissionalNaoEncontrado):
            disponiveis = ", ".join(localizacao.nomes_disponiveis) or "nenhuma"
            return (
                f"Não há profissional com o nome '{citado}' na grade desta semana. "
                f"Profissionais da semana: {disponiveis}."
            ), None
        profissional_id = localizacao.profissional_id

    especialidade: Especialidade | None = args.get("especialidade")
    dias = tuple(d for d in (_data(semana, str(dia)) for dia in args.get("dias_semana") or []) if d)
    pedido = PedidoRealocacao(
        dias=dias,
        paciente=args.get("paciente") or None,
        situacao=Situacao(args["situacao"]) if args.get("situacao") else None,
        especialidade=especialidade,
        profissional_id=profissional_id,
        duracao_minutos=args.get("duracao_minutos"),
        horario_desejado=args.get("horario_desejado"),
        horario_minimo=args.get("horario_minimo"),
        horario_maximo=args.get("horario_maximo"),
        dia_origem=_data(semana, args.get("dia_origem")),
        horario_origem=args.get("horario_origem"),
        candidata=args.get("candidata"),
        mesmo_paciente_da_grade=args.get("mesmo_paciente_da_grade"),
    )
    resultado = planejar_realocacao(foto, pedido, regras)

    bloco: Any
    if isinstance(resultado, Pergunta):
        texto, bloco = blocos_realocacao.pergunta(foto, resultado)
        logger.info(
            "%s: pergunta (%s), %d candidata(s), em %.2fs",
            NOME_DA_TOOL,
            resultado.motivo.value,
            len(resultado.candidatas),
            relogio.perf_counter() - comeco,
        )
    elif isinstance(resultado, PlanoRealocacao):
        texto, bloco = blocos_realocacao.plano_realocacao(foto, resultado)
        logger.info(
            "%s: %d opção(ões), %d passo(s), %d dia(s) não lido(s), em %.2fs",
            NOME_DA_TOOL,
            len(resultado.opcoes),
            sum(len(opcao.passos) for opcao in resultado.opcoes),
            len(foto.dias_nao_lidos),
            relogio.perf_counter() - comeco,
        )
    else:
        # O rótulo do pedido no bloco: o nome achado na grade, o dito, ou "Paciente novo".
        rotulo = pedido.paciente.strip() if pedido.paciente else ROTULO_PACIENTE_NOVO
        texto, bloco = blocos_realocacao.sem_solucao(foto, resultado, rotulo, regras)
        logger.info(
            "%s: sem opção (%s), em %.2fs",
            NOME_DA_TOOL,
            resultado.motivo.value,
            relogio.perf_counter() - comeco,
        )
    return texto, bloco.model_dump(mode="json") if bloco is not None else None


def criar_tool_planejar_realocacao(
    fonte: ScheduleDataSource, data_referencia: date, catalogo: CatalogoAgenda | None
) -> BaseTool:
    """`planejar_realocacao`, com especialidade e dias do catálogo da semana."""
    validas = especialidades_validas(catalogo)
    dias = dias_validos(catalogo)
    campos = ArgsPlanejarRealocacao.model_fields
    dia = tipo_dia(dias)
    args_schema = create_model(
        "ArgsPlanejarRealocacao",
        __base__=ArgsPlanejarRealocacao,
        especialidade=(
            tipo_especialidade(validas) | None,
            Field(default=None, description=campos["especialidade"].description),
        ),
        dias_semana=(
            list[dia] | None,  # type: ignore[valid-type]
            Field(default=None, description=campos["dias_semana"].description),
        ),
        dia_origem=(
            dia | None,
            Field(default=None, description=campos["dia_origem"].description),
        ),
    )

    @tool(NOME_DA_TOOL, args_schema=args_schema, response_format="content_and_artifact")
    def planejar_realocacao_tool(**kwargs: Any) -> tuple[str, dict[str, Any] | None]:
        """Sugere como abrir espaço para um atendimento deslocando outros pacientes, na
        grade semanal, com até 3 opções na tela.

        Use quando pedirem para mudar um paciente de horário, para abrir espaço movendo
        outros, ou quando, depois de um encaixe sem vaga, disserem "pode mover o que for
        necessário". NUNCA peça atendimento_id nem id de paciente: bastam o nome (ou nada,
        para paciente novo), a especialidade, a duração, o dia e o horário. Cada paciente
        deslocado mantém a mesma terapia, o mesmo profissional e a mesma duração: nunca
        proponha mudar terapia ou profissional. Você recebe só papéis e horários: NUNCA
        escreva nomes de pacientes. Se a ferramenta fizer uma pergunta, repita a pergunta
        sem nomes. Grade semanal: não cite datas.
        """
        argumentos = dict(kwargs)
        if argumentos.get("especialidade") is not None:
            argumentos["especialidade"] = como_especialidade(argumentos["especialidade"])
        if argumentos.get("dias_semana"):
            argumentos["dias_semana"] = [str(d) for d in argumentos["dias_semana"]]
        if argumentos.get("dia_origem") is not None:
            argumentos["dia_origem"] = str(argumentos["dia_origem"])
        try:
            return executar_planejar_realocacao(fonte, data_referencia, argumentos)
        except Exception as erro:
            # Só o tipo: a mensagem pode repetir dados da planilha (ou o nome pedido).
            logger.warning("Falha ao gerar %s: %s", NOME_DA_TOOL, type(erro).__name__)
            return (
                f"Erro ao planejar a realocação ({type(erro).__name__}). Tente de novo em "
                "instantes."
            ), None

    planejar_realocacao_tool.handle_validation_error = mensagem_de_parametro_invalido(validas, dias)
    return planejar_realocacao_tool
