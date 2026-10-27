"""Tool `planejar_semana`: plano de um paciente, uma sessão por especialidade por dia.

Responde "toda quarta e quinta, das 10h às 12h, fono, psicomotricidade,
psicologia e TO, 30 min cada": para cada dia, um horário por especialidade
dentro da janela, sem sobreposição e sem exigir sessões seguidas (isso é
`buscar_encaixe`). O que não coube vem com até três alternativas fora da
janela. A conta é de `app.engine.planejamento`; aqui ficam a leitura (uma
fotografia da semana, uma chamada à planilha), a resolução de nomes pelo
catálogo e o texto/bloco.

A recorrência é a grade semanal, sem datas: a tool nunca itera por data e
nunca afirma ter verificado datas do calendário.
"""

import logging
import time
from datetime import date
from datetime import time as hora_do_dia
from typing import Any

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field, create_model

from app.ai import blocos_gerais
from app.ai.catalogo import catalogo_da_semana, profissionais_do_catalogo
from app.ai.parametros_catalogo import (
    como_especialidade,
    dias_validos,
    especialidades_validas,
    mensagem_de_parametro_invalido,
    tipo_dia,
    tipo_especialidade,
)
from app.ai.relatorios import DIAS_DA_SEMANA
from app.ai.servico_agenda import (
    ProfissionalAmbiguo,
    ProfissionalNaoEncontrado,
    resolver_profissional,
)
from app.ai.textos_profissional import texto_ambiguo, texto_inexistente
from app.ai.tools_relatorios import ParametroInvalidoError, resolver_salas
from app.domain import DURACAO_SLOT_MINUTOS, Especialidade, ScheduleDataSource, dias_da_semana_de
from app.domain.catalogo_agenda import NOMES_DOS_DIAS, CatalogoAgenda
from app.domain.constants import HORARIO_ABERTURA, HORARIO_FECHAMENTO
from app.domain.formatacao import rotulo_enum
from app.engine.fotografia import fotografar_periodo
from app.engine.planejamento import (
    MAX_ITENS_POR_PLANO,
    ItemPlano,
    PedidoPlano,
    planejar_semana,
)

logger = logging.getLogger(__name__)


class ArgsPlanejarSemana(BaseModel):
    dias_semana: list[str] = Field(
        min_length=1,
        description="Dias da semana do plano ('quartas e quintas' -> quarta, quinta).",
    )
    especialidades: list[str] = Field(
        min_length=1,
        max_length=MAX_ITENS_POR_PLANO,
        description="Especialidades pedidas, uma sessão de cada por dia.",
    )
    hora_inicio: hora_do_dia | None = Field(
        default=None,
        description="Início da janela (HH:MM): a sessão começa nele ou depois. Vazio: abertura.",
    )
    hora_fim: hora_do_dia | None = Field(
        default=None,
        description="Fim da janela (HH:MM): a sessão termina até ele. Em branco: fechamento.",
    )
    duracao_minutos: int = Field(
        default=DURACAO_SLOT_MINUTOS,
        gt=0,
        description=(
            f"Duração de cada sessão em minutos (padrão {DURACAO_SLOT_MINUTOS}; múltiplos de "
            f"{DURACAO_SLOT_MINUTOS}, arredondado para cima)."
        ),
    )
    profissionais_preferidos: list[str] | None = Field(
        default=None,
        description=(
            "Profissionais preferidas, com o nome como foi dito (apelido vale): têm prioridade "
            "na especialidade delas, sem ser exigência. Só quando o pedido citar."
        ),
    )
    evitar_salas: list[str] | None = Field(
        default=None, description="Salas a evitar ('Sala 3' ou '3'), quando o pedido citar."
    )


def _resolver_preferidos(
    fonte: ScheduleDataSource,
    data_referencia: date,
    nomes: list[str],
    especialidades: list[Especialidade],
) -> dict[Especialidade, str] | str:
    """Especialidade -> id da preferida; texto de erro quando um nome não resolve."""
    catalogo = catalogo_da_semana(fonte, data_referencia)
    profissionais = profissionais_do_catalogo(catalogo)
    preferidos: dict[Especialidade, str] = {}
    for nome in nomes:
        localizacao = resolver_profissional(profissionais, nome)
        if isinstance(localizacao, ProfissionalAmbiguo):
            return texto_ambiguo(nome, localizacao)
        if isinstance(localizacao, ProfissionalNaoEncontrado):
            return texto_inexistente(nome, catalogo)
        profissional = catalogo.profissionais[localizacao.profissional_id]
        if profissional.especialidade not in especialidades:
            return (
                f"{profissional.nome} é de {rotulo_enum(profissional.especialidade.value)}, "
                "que não está entre as especialidades pedidas."
            )
        preferidos[profissional.especialidade] = profissional.id
    return preferidos


def executar_plano(
    fonte: ScheduleDataSource,
    data_referencia: date,
    dias_semana: list[str],
    especialidades: list[Especialidade],
    *,
    hora_inicio: hora_do_dia | None = None,
    hora_fim: hora_do_dia | None = None,
    duracao_minutos: int = DURACAO_SLOT_MINUTOS,
    profissionais_preferidos: list[str] | None = None,
    evitar_salas: list[str] | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """Texto e bloco (já em JSON) de `planejar_semana`. Uma leitura da semana de hoje."""
    inicio = hora_inicio or HORARIO_ABERTURA
    fim = hora_fim or HORARIO_FECHAMENTO
    if fim <= inicio:
        return "O fim da janela precisa ser depois do início.", None
    especialidades = list(dict.fromkeys(especialidades))
    nomes_dos_dias = list(dict.fromkeys(dias_semana))

    comeco = time.perf_counter()
    semana = dias_da_semana_de(data_referencia)
    foto = fotografar_periodo(fonte, semana[0], semana[-1])
    preferidos: dict[Especialidade, str] = {}
    if profissionais_preferidos:
        resolvidos = _resolver_preferidos(
            fonte, data_referencia, profissionais_preferidos, especialidades
        )
        if isinstance(resolvidos, str):
            return resolvidos, None
        preferidos = resolvidos
    try:
        salas_evitadas = resolver_salas(foto, evitar_salas or [])
    except ParametroInvalidoError as erro:
        return f"Não foi possível montar o plano: {erro}", None

    duracao_em_slots = -(-duracao_minutos // DURACAO_SLOT_MINUTOS)
    pedido = PedidoPlano(
        dias=tuple(semana[NOMES_DOS_DIAS.index(nome)] for nome in nomes_dos_dias),
        inicio=inicio,
        fim=fim,
        itens=tuple(
            ItemPlano(
                especialidade=especialidade,
                duracao_em_slots=duracao_em_slots,
                profissional_preferido=preferidos.get(especialidade),
            )
            for especialidade in especialidades
        ),
        salas_evitadas=salas_evitadas,
    )
    plano = planejar_semana(foto, pedido)

    dias_texto = blocos_gerais.enumerar(
        [DIAS_DA_SEMANA[d.weekday()].split("-")[0] for d in pedido.dias]
    )
    titulo = (
        f"Plano semanal: {dias_texto}, das {inicio:%H:%M} às {fim:%H:%M} "
        f"(sessões de {duracao_em_slots * DURACAO_SLOT_MINUTOS} min)"
    )
    avisos = [blocos_gerais.AVISO_PLANO_GRADE]
    if salas_evitadas:
        avisos.append(
            "Salas evitadas: " + ", ".join(sorted(foto.nome_sala(s) for s in salas_evitadas)) + "."
        )
    if foto.parcial:
        nao_lidos = ", ".join(f"{d:%d/%m}" for d in foto.dias_nao_lidos)
        avisos.append(f"Não foi possível ler: {nao_lidos}; o plano pode estar incompleto.")
    texto, bloco = blocos_gerais.plano_semanal(foto, plano, titulo, avisos)
    # Só o tamanho: nada de nomes, horários ou especialidades da agenda.
    logger.info(
        "planejar_semana: %d dia(s), %d especialidade(s) em %.2fs",
        len(pedido.dias),
        len(pedido.itens),
        time.perf_counter() - comeco,
    )
    return texto, bloco.model_dump(mode="json")


def criar_tool_planejar_semana(
    fonte: ScheduleDataSource, data_referencia: date, catalogo: CatalogoAgenda | None
) -> BaseTool:
    """`planejar_semana`, com especialidades e dias restritos ao catálogo da semana."""
    validas = especialidades_validas(catalogo)
    dias = dias_validos(catalogo)
    campos = ArgsPlanejarSemana.model_fields
    args = create_model(
        "ArgsPlanejarSemana",
        __base__=ArgsPlanejarSemana,
        dias_semana=(
            list[tipo_dia(dias)],  # type: ignore[misc]
            Field(min_length=1, description=campos["dias_semana"].description),
        ),
        especialidades=(
            list[tipo_especialidade(validas)],  # type: ignore[misc]
            Field(
                min_length=1,
                max_length=MAX_ITENS_POR_PLANO,
                description=campos["especialidades"].description,
            ),
        ),
    )

    @tool("planejar_semana", args_schema=args, response_format="content_and_artifact")
    def planejar_semana_tool(
        dias_semana: list[Any],
        especialidades: list[Any],
        hora_inicio: hora_do_dia | None = None,
        hora_fim: hora_do_dia | None = None,
        duracao_minutos: int = DURACAO_SLOT_MINUTOS,
        profissionais_preferidos: list[str] | None = None,
        evitar_salas: list[str] | None = None,
    ) -> tuple[str, dict[str, Any] | None]:
        """Plano de um paciente: UMA sessão por especialidade em cada dia pedido, dentro da
        janela, sem sobreposição entre elas e SEM exigir que sejam seguidas.

        Use para "semanalmente, quartas e quintas, fono, psicomotricidade, psicologia e TO,
        das 10h às 12h, 30 min" e para pedidos de várias especialidades num dia que não
        precisam ser em sequência. UMA chamada cobre todos os dias: nunca chame por data.
        Diz o que não coube, com até 3 alternativas fora da janela. A recorrência é a grade
        semanal (sem datas): nunca diga que datas do calendário foram verificadas.
        Sessões seguidas, uma logo após a outra -> `buscar_encaixe`.
        """
        try:
            return executar_plano(
                fonte,
                data_referencia,
                [str(dia) for dia in dias_semana],
                [como_especialidade(e) for e in especialidades],
                hora_inicio=hora_inicio,
                hora_fim=hora_fim,
                duracao_minutos=duracao_minutos,
                profissionais_preferidos=profissionais_preferidos,
                evitar_salas=evitar_salas,
            )
        except Exception as erro:
            # Só o tipo: a mensagem pode repetir dados da planilha.
            logger.warning("Falha ao planejar a semana: %s", type(erro).__name__)
            return (
                f"Erro ao montar o plano ({type(erro).__name__}). Tente de novo em instantes."
            ), None

    planejar_semana_tool.handle_validation_error = mensagem_de_parametro_invalido(validas, dias)
    return planejar_semana_tool
