"""Tool `consultar_profissionais`: a equipe da semana, a partir do catálogo.

Responde "você sabe as especialidades deles?", "quem atende fono no sábado?",
"em que sala a Bia fica na terça?". Tudo vem do catálogo (`app.ai.catalogo`):
especialidade, dias, turnos, salas e postos, sem paciente nenhum. Nome dito na
conversa passa pelo resolvedor (apelido, grafia, primeiro nome); nome ambíguo
lista os candidatos e pede escolha.
"""

from datetime import date
from typing import Any

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field, create_model

from app.ai.catalogo import catalogo_da_semana, profissionais_do_catalogo
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
from app.ai.textos_profissional import (
    dia_curto,
    no_dia,
    texto_ambiguo,
    texto_inexistente,
    texto_nao_atende,
)
from app.domain import ScheduleDataSource, dias_da_semana_de
from app.domain.catalogo_agenda import NOMES_DOS_DIAS, CatalogoAgenda, ProfissionalCatalogo
from app.domain.formatacao import rotulo_enum

_TURNOS = {("manha",): "manhã", ("tarde",): "tarde", ("manha", "tarde"): "manhã e tarde"}


class ArgsConsultarProfissionais(BaseModel):
    nomes: list[str] | None = Field(
        default=None,
        description=(
            "Profissionais citadas, com o nome como foi dito (apelido e grafia diferente "
            "valem). Em branco: toda a equipe (ou a filtrada)."
        ),
    )
    especialidade: str | None = Field(
        default=None, description="Só desta especialidade, quando a pergunta citar uma."
    )
    dia: str | None = Field(
        default=None, description="Só quem atende neste dia da semana, quando a pergunta citar um."
    )


def _onde(profissional: ProfissionalCatalogo, catalogo: CatalogoAgenda, dia: date) -> str:
    salas = []
    for posto in profissional.postos:
        if posto.dia != dia:
            continue
        sala = catalogo.salas.get(posto.sala_id)
        com_posto = sala is not None and sala.capacidade > 1
        salas.append(f"{posto.sala_nome}, posto {posto.posto}" if com_posto else posto.sala_nome)
    return f"{dia_curto(dia)} ({_TURNOS[profissional.turnos_no_dia(dia)]}; {'; '.join(salas)})"


def _descrever(
    profissional: ProfissionalCatalogo, catalogo: CatalogoAgenda, dia: date | None
) -> str:
    cabecalho = f"- {profissional.nome}: {rotulo_enum(profissional.especialidade.value)}"
    if profissional.apelidos:
        cabecalho += f" (também chamada de {', '.join(profissional.apelidos)})"
    dias = [dia] if dia is not None else list(profissional.dias)
    if not dias:
        return f"{cabecalho}. Sem horário na grade desta semana."
    return f"{cabecalho}. Atende: {', '.join(_onde(profissional, catalogo, d) for d in dias)}."


def responder(
    catalogo: CatalogoAgenda,
    nomes: list[str] | None,
    especialidade: Any,
    dia: date | None,
) -> str:
    """Texto de `consultar_profissionais` para um catálogo já lido."""
    linhas: list[str] = []
    escolhidos: list[ProfissionalCatalogo] = []
    if nomes:
        profissionais = profissionais_do_catalogo(catalogo)
        for nome in nomes:
            localizacao = resolver_profissional(profissionais, nome)
            if isinstance(localizacao, ProfissionalAmbiguo):
                linhas.append(texto_ambiguo(nome, localizacao))
            elif isinstance(localizacao, ProfissionalNaoEncontrado):
                linhas.append(texto_inexistente(nome, catalogo))
            else:
                profissional = catalogo.profissionais[localizacao.profissional_id]
                if dia is not None and not profissional.atende_no_dia(dia):
                    linhas.append(texto_nao_atende(profissional, dia))
                else:
                    escolhidos.append(profissional)
    else:
        escolhidos = sorted(
            catalogo.profissionais.values(), key=lambda p: (p.especialidade.value, p.id)
        )
        if dia is not None:
            escolhidos = [p for p in escolhidos if p.atende_no_dia(dia)]

    if especialidade is not None:
        alvo = como_especialidade(especialidade)
        if not nomes:
            escolhidos = [p for p in escolhidos if p.especialidade == alvo]
        else:
            fora = [p for p in escolhidos if p.especialidade != alvo]
            linhas.extend(
                f"{p.nome} é de {rotulo_enum(p.especialidade.value)}, não de "
                f"{rotulo_enum(alvo.value)}."
                for p in fora
            )
            escolhidos = [p for p in escolhidos if p.especialidade == alvo]

    inicio, fim = catalogo.dias[0], catalogo.dias[-1]
    titulo = f"Equipe da semana de {inicio:%d/%m} a {fim:%d/%m/%Y}"
    if escolhidos:
        linhas = [f"{titulo}:", *(_descrever(p, catalogo, dia) for p in escolhidos), *linhas]
    elif not nomes:
        partes = []
        if especialidade is not None:
            partes.append(f"de {rotulo_enum(como_especialidade(especialidade).value)}")
        if dia is not None:
            partes.append(f"atende {no_dia(dia)}")
        linhas.append(f"{titulo}: ninguém {' '.join(partes)} na grade.")
    if catalogo.parcial:
        falhas = ", ".join(f"{d:%d/%m}" for d in catalogo.dias_com_falha)
        linhas.append(f"Não foi possível ler: {falhas}; a equipe pode estar incompleta.")
    return "\n".join(linhas)


def criar_tool_consultar_profissionais(
    fonte: ScheduleDataSource, data_referencia: date, catalogo: CatalogoAgenda | None
) -> BaseTool:
    """`consultar_profissionais`, com especialidade e dia restritos ao catálogo da semana."""
    validas = especialidades_validas(catalogo)
    dias = dias_validos(catalogo)
    campos = ArgsConsultarProfissionais.model_fields
    args = create_model(
        "ArgsConsultarProfissionais",
        __base__=ArgsConsultarProfissionais,
        especialidade=(
            tipo_especialidade(validas) | None,
            Field(default=None, description=campos["especialidade"].description),
        ),
        dia=(tipo_dia(dias) | None, Field(default=None, description=campos["dia"].description)),
    )
    semana = dias_da_semana_de(data_referencia)

    @tool("consultar_profissionais", args_schema=args)
    def consultar_profissionais_tool(
        nomes: list[str] | None = None, especialidade: Any = None, dia: Any = None
    ) -> str:
        """A equipe da semana: especialidade de cada profissional, dias e turnos em que
        atende, salas e postos.

        Use para "você sabe as especialidades deles?", "quem atende fono no sábado?",
        "em que sala a Bia fica na terça?". Não traz vagas (para isso,
        `consultar_disponibilidade` ou `gerar_relatorio`) nem pacientes.
        """
        data_do_dia = semana[NOMES_DOS_DIAS.index(str(dia))] if dia is not None else None
        try:
            atual = catalogo_da_semana(fonte, data_referencia)
        except Exception as erro:
            return f"Erro ao ler a agenda para consultar a equipe ({type(erro).__name__})."
        return responder(atual, nomes, especialidade, data_do_dia)

    consultar_profissionais_tool.handle_validation_error = mensagem_de_parametro_invalido(
        validas, dias
    )
    return consultar_profissionais_tool
