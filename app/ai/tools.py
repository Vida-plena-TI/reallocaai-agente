"""Tools do LangChain para o agente RealocAI (Fase 5b).

Cada tool envolve uma função de `app.ai.servico_agenda` (Fase 5a) com `fonte`
e `continuidade` já fechados no closure de `criar_tools` — a LLM escolhe *o
que* perguntar, nunca *de onde* os dados vêm. Toda tool devolve texto simples
em português (nunca um objeto pydantic cru) e nunca deixa uma exceção da
camada de serviço subir: um erro vira mensagem de resultado, não uma
exceção que quebraria o turno inteiro da conversa.

API do LangChain usada (inspecionada em `langchain-core==1.4.x` instalado,
não assumida de memória): o decorator `langchain_core.tools.tool`, aceitando
`args_schema` (um `BaseModel` pydantic explícito) — é o schema que a LLM
preenche via function calling. `create_agent` (Parte D) espera uma lista de
`BaseTool`, que é o que o decorator devolve.
"""

import math
from collections import Counter
from collections.abc import Callable
from datetime import date, time

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from app.ai.servico_agenda import (
    ItemDemandaBruta,
    ResultadoAlternativas,
    ResultadoExato,
    buscar_encaixe,
    buscar_paciente,
    consultar_disponibilidade_do_dia,
    consultar_ocupacao_do_dia,
    sugerir_realocacao_por_id,
)
from app.config import get_settings
from app.data_sources.continuidade import ContinuidadeDataSource
from app.domain import (
    DURACAO_SLOT_MINUTOS,
    Convenio,
    Especialidade,
    ScheduleDataSource,
    Slot,
    normalizar_id,
)
from app.domain.constants import HORARIO_PREFERENCIAL_PADRAO
from app.engine.disponibilidade import SlotDisponivel
from app.engine.encaixe import CenarioSugestao, OpcaoEncaixe
from app.engine.ocupacao import OcupacaoAgregada

#: Assinatura de `enviar_relatorio_por_email` (`app.reports.envio`), recebida
#: como parâmetro em vez de importada direto: `app.ai` não pode depender de
#: `app.reports` (ver `tests/test_arquitetura.py`), então quem monta as tools
#: (a rota `/agenda/chat`) é quem decide qual implementação injetar.
EnviarRelatorio = Callable[[ScheduleDataSource, date, list[str] | None], None]

_ROTULOS_CENARIO: dict[CenarioSugestao, str] = {
    CenarioSugestao.MELHOR_PARA_CLINICA: "Melhor opção para a agenda da clínica",
    CenarioSugestao.MAIS_PROXIMO_SEGUINTE: "Horário mais próximo depois do pedido",
    CenarioSugestao.MAIS_PROXIMO_ANTERIOR: "Horário mais próximo antes do pedido",
}


def _rotulo_especialidade(especialidade: Especialidade) -> str:
    """`terapia_ocupacional` -> `Terapia Ocupacional`."""
    return especialidade.value.replace("_", " ").title()


def _rotulo_convenio(convenio: Convenio | None) -> str:
    if convenio is None:
        return "não informado"
    return convenio.value.replace("_", " ").title()


def _nome_profissional(fonte: ScheduleDataSource, dia: date, profissional_id: str) -> str:
    """Nome do profissional, ou o próprio id quando ele não está na agenda do dia."""
    profissional = next(
        (item for item in fonte.listar_profissionais(dia) if item.id == profissional_id), None
    )
    return profissional.nome if profissional is not None else profissional_id


def _chave_sem_posto(disponivel: SlotDisponivel) -> tuple[Slot, str, str]:
    """Horário, sala e profissional de uma vaga — tudo menos o posto."""
    return disponivel.slot, disponivel.sala_id, disponivel.profissional_id


def _sufixo_posto(
    disponivel: SlotDisponivel, vagas_por_chave: Counter[tuple[Slot, str, str]]
) -> str:
    """` (posto 2)` quando a vaga divide horário, sala e profissional com outra;
    vazio quando não há ambiguidade. O posto é contado a partir de 1 no texto,
    que é como uma pessoa lê as colunas da sala.
    """
    if vagas_por_chave[_chave_sem_posto(disponivel)] <= 1:
        return ""
    return f" (posto {disponivel.indice_posto + 1})"


def _nome_sala(fonte: ScheduleDataSource, dia: date, sala_id: str) -> str:
    """Nome da sala, ou o próprio id quando ela não está na agenda do dia."""
    sala = next((item for item in fonte.listar_salas(dia) if item.id == sala_id), None)
    return sala.nome if sala is not None else sala_id


def _resolver_profissional_id(fonte: ScheduleDataSource, dia: date, nome_ou_id: str) -> str | None:
    """Id do profissional cujo id normalizado casa com `nome_ou_id`, sem fuzzy matching."""
    alvo = normalizar_id(nome_ou_id)
    profissional = next((item for item in fonte.listar_profissionais(dia) if item.id == alvo), None)
    return profissional.id if profissional is not None else None


def _formatar_opcao_encaixe(fonte: ScheduleDataSource, dia: date, opcao: OpcaoEncaixe) -> str:
    linhas = [
        f"- {_rotulo_especialidade(item.especialidade)}: "
        f"{item.slots[0].hora_inicio.strftime('%H:%M')} às "
        f"{item.slots[-1].hora_fim.strftime('%H:%M')}, com "
        f"{_nome_profissional(fonte, dia, item.profissional_id)} na "
        f"{_nome_sala(fonte, dia, item.sala_id)}."
        for item in opcao.itens
    ]
    return "\n".join(linhas)


def _formatar_linha_ocupacao(rotulo: str, agregada: OcupacaoAgregada) -> str:
    aviso = " (abaixo da meta de 80%)" if agregada.abaixo_da_meta else ""
    return f"- {rotulo}: {agregada.percentual:.0%}{aviso}"


class ArgsBuscarPaciente(BaseModel):
    nome_ou_id: str = Field(description="Nome (ou parte do nome) ou id do paciente.")
    data: date = Field(description="Data da agenda a consultar, no formato AAAA-MM-DD.")


class ItemDemandaEntrada(BaseModel):
    """Um item de demanda de `buscar_encaixe`, na unidade que a LLM conhece (minutos)."""

    especialidade: Especialidade = Field(description="Especialidade do atendimento pedido.")
    duracao_minutos: int = Field(
        gt=0,
        description=(
            f"Duração do atendimento em minutos (a agenda trabalha em blocos de "
            f"{DURACAO_SLOT_MINUTOS} minutos; valores que não forem múltiplos exatos "
            "são arredondados para cima)."
        ),
    )
    profissional_id: str | None = Field(
        default=None,
        description=(
            "Id de um profissional específico, só quando for uma exigência explícita "
            "(ex.: pedido da família). Deixe em branco na maioria dos casos — o sistema "
            "já resolve sozinho a continuidade terapêutica do paciente."
        ),
    )


class ArgsBuscarEncaixe(BaseModel):
    paciente: str = Field(description="Nome ou id do paciente.")
    data: date = Field(description="Data do atendimento, no formato AAAA-MM-DD.")
    itens: list[ItemDemandaEntrada] = Field(
        min_length=1,
        description="Especialidades e durações necessárias, na ordem de preferência.",
    )
    horario_minimo: time | None = Field(
        default=None,
        description=(
            "Horário mais cedo aceitável para o início do atendimento (HH:MM). "
            "Deixe em branco para usar o padrão da clínica."
        ),
    )
    horario_desejado: time | None = Field(
        default=None,
        description=(
            "Horário exato desejado (HH:MM), quando o paciente ou a família pediu um "
            "horário específico. Deixe em branco se não houver preferência de horário."
        ),
    )


class ArgsConsultarDisponibilidade(BaseModel):
    data: date = Field(description="Data a consultar, no formato AAAA-MM-DD.")
    especialidade: Especialidade | None = Field(
        default=None, description="Filtra por essa especialidade, quando informada."
    )
    profissional: str | None = Field(
        default=None, description="Filtra por esse profissional (nome ou id), quando informado."
    )


class ArgsConsultarOcupacao(BaseModel):
    data: date = Field(description="Data a consultar, no formato AAAA-MM-DD.")


class ArgsSugerirRealocacao(BaseModel):
    atendimento_id: str = Field(description="Id do atendimento já existente na agenda.")
    data: date = Field(description="Data em que o atendimento está hoje, no formato AAAA-MM-DD.")
    nova_duracao_minutos: int | None = Field(
        default=None,
        gt=0,
        description=(
            "Nova duração em minutos, só quando ela mudar. Deixe em branco para manter "
            "a duração atual."
        ),
    )


class ArgsEnviarRelatorio(BaseModel):
    data: date | None = Field(
        default=None,
        description=(
            "Data do relatório, no formato AAAA-MM-DD. Deixe em branco para usar a data "
            "de referência da conversa (hoje)."
        ),
    )
    destinatarios: list[str] | None = Field(
        default=None,
        description=(
            "Lista de e-mails destinatários. Deixe em branco para usar a lista padrão "
            "já configurada na clínica."
        ),
    )


def criar_tools(
    fonte: ScheduleDataSource,
    continuidade: ContinuidadeDataSource,
    data_referencia: date,
    enviar_relatorio: EnviarRelatorio,
) -> list[BaseTool]:
    """Tools do agente RealocAI, já fechadas sobre `fonte` e `continuidade`.

    A LLM nunca decide qual fonte de dados ou de continuidade usar — isso é
    fixado aqui, em código, no momento em que as tools são construídas.
    `data_referencia` é a data "hoje" da conversa (ver `criar_agente`), usada
    como padrão pela tool `enviar_relatorio` quando a LLM não informar uma
    data. `enviar_relatorio` é a implementação de envio (em produção,
    `enviar_relatorio_por_email`) — recebida de fora, nunca importada daqui.
    """

    @tool("buscar_paciente", args_schema=ArgsBuscarPaciente)
    def buscar_paciente_tool(nome_ou_id: str, data: date) -> str:
        """Busca um paciente na agenda de um dia pelo nome ou id.

        Use antes de qualquer outra pergunta sobre um paciente específico,
        para confirmar que ele está na agenda daquele dia e obter o id exato
        a usar nas outras tools.
        """
        try:
            paciente = buscar_paciente(fonte, data, nome_ou_id)
        except Exception as erro:
            return f"Erro ao buscar paciente: {erro}"

        if paciente is None:
            return (
                f"Paciente não encontrado com o nome ou id '{nome_ou_id}' na agenda de "
                f"{data.strftime('%d/%m/%Y')}. Peça para confirmar a grafia do nome."
            )
        return (
            f"Paciente encontrado: {paciente.nome} (id: {paciente.id}, "
            f"convênio: {_rotulo_convenio(paciente.convenio)})."
        )

    @tool("buscar_encaixe", args_schema=ArgsBuscarEncaixe)
    def buscar_encaixe_tool(
        paciente: str,
        data: date,
        itens: list[ItemDemandaEntrada],
        horario_minimo: time | None = None,
        horario_desejado: time | None = None,
    ) -> str:
        """Busca um horário de encaixe, casando uma ou mais especialidades sem buraco entre elas.

        Use para "tem vaga para [paciente] em [data]?" ou para encaixar um
        atendimento novo. Não use para consultas gerais de disponibilidade
        sem paciente (para isso use `consultar_disponibilidade`).
        """
        try:
            encontrado = buscar_paciente(fonte, data, paciente)
            if encontrado is None:
                return (
                    f"Paciente não encontrado com o nome ou id '{paciente}' na agenda "
                    f"de {data.strftime('%d/%m/%Y')}. Confirme a grafia do nome antes "
                    "de tentar de novo."
                )

            itens_brutos = [
                ItemDemandaBruta(
                    especialidade=item.especialidade,
                    duracao_em_slots=math.ceil(item.duracao_minutos / DURACAO_SLOT_MINUTOS),
                    profissional_id=item.profissional_id,
                )
                for item in itens
            ]
            horario_minimo_efetivo = (
                horario_minimo if horario_minimo is not None else HORARIO_PREFERENCIAL_PADRAO
            )
            resultado = buscar_encaixe(
                fonte,
                continuidade,
                encontrado.id,
                data,
                itens_brutos,
                horario_minimo=horario_minimo_efetivo,
                horario_desejado=horario_desejado,
            )
        except Exception as erro:
            return f"Erro ao buscar encaixe: {erro}"

        if isinstance(resultado, ResultadoExato):
            return "Horário encontrado:\n" + _formatar_opcao_encaixe(fonte, data, resultado.opcao)

        if isinstance(resultado, ResultadoAlternativas):
            blocos = [
                f"{_ROTULOS_CENARIO[opcao_com_cenario.cenario]}:\n"
                + _formatar_opcao_encaixe(fonte, data, opcao_com_cenario.opcao)
                for opcao_com_cenario in resultado.opcoes
            ]
            return "Não há vaga exatamente no horário pedido. Alternativas:\n\n" + "\n\n".join(
                blocos
            )

        return (
            f"Nenhum horário disponível em {data.strftime('%d/%m/%Y')} para essa combinação "
            "de especialidades."
        )

    @tool("consultar_disponibilidade", args_schema=ArgsConsultarDisponibilidade)
    def consultar_disponibilidade_tool(
        data: date,
        especialidade: Especialidade | None = None,
        profissional: str | None = None,
    ) -> str:
        """Lista os horários livres de um dia, com filtro opcional de especialidade e profissional.

        Use para perguntas gerais como "quais horários estão livres de
        psicologia hoje?". Para casar várias especialidades de um mesmo
        atendimento, use `buscar_encaixe`.
        """
        try:
            profissional_id = None
            if profissional is not None:
                profissional_id = _resolver_profissional_id(fonte, data, profissional)
                if profissional_id is None:
                    return (
                        f"Profissional não encontrado com o nome ou id '{profissional}' na "
                        f"agenda de {data.strftime('%d/%m/%Y')}."
                    )

            disponiveis = consultar_disponibilidade_do_dia(
                fonte, data, especialidade=especialidade, profissional_id=profissional_id
            )
        except Exception as erro:
            return f"Erro ao consultar disponibilidade: {erro}"

        if not disponiveis:
            return (
                f"Nenhum horário livre encontrado em {data.strftime('%d/%m/%Y')} para os "
                "filtros informados."
            )

        # Mesmo horário, sala e profissional em mais de um posto são vagas
        # distintas (colunas diferentes da sala); sem o posto no texto elas
        # pareceriam a mesma linha repetida.
        vagas_por_chave = Counter(_chave_sem_posto(disponivel) for disponivel in disponiveis)
        linhas = [
            f"- {disponivel.slot.hora_inicio.strftime('%H:%M')} às "
            f"{disponivel.slot.hora_fim.strftime('%H:%M')}: "
            f"{_rotulo_especialidade(disponivel.especialidade)} com "
            f"{_nome_profissional(fonte, data, disponivel.profissional_id)} na "
            f"{_nome_sala(fonte, data, disponivel.sala_id)}"
            f"{_sufixo_posto(disponivel, vagas_por_chave)}."
            for disponivel in disponiveis
        ]
        return f"Horários livres em {data.strftime('%d/%m/%Y')}:\n" + "\n".join(linhas)

    @tool("consultar_ocupacao", args_schema=ArgsConsultarOcupacao)
    def consultar_ocupacao_tool(data: date) -> str:
        """Ocupação do dia, por especialidade e por sala, sinalizando quem está abaixo da meta.

        Use para perguntas do tipo "como está a ocupação hoje?" ou "tem sala
        ociosa?".
        """
        try:
            relatorio = consultar_ocupacao_do_dia(fonte, data)
        except Exception as erro:
            return f"Erro ao consultar ocupação: {erro}"

        por_especialidade = relatorio.por_especialidade()
        por_sala = relatorio.por_sala()
        if not por_especialidade and not por_sala:
            return f"Nenhuma escala encontrada para {data.strftime('%d/%m/%Y')}."

        linhas = [f"Ocupação de {data.strftime('%d/%m/%Y')}:", "", "Por especialidade:"]
        linhas.extend(
            _formatar_linha_ocupacao(_rotulo_especialidade(especialidade), agregada)
            for especialidade, agregada in sorted(
                por_especialidade.items(), key=lambda par: par[0].value
            )
        )
        linhas.append("")
        linhas.append("Por sala:")
        linhas.extend(
            _formatar_linha_ocupacao(_nome_sala(fonte, data, sala_id), agregada)
            for sala_id, agregada in sorted(por_sala.items())
        )
        return "\n".join(linhas)

    @tool("sugerir_realocacao", args_schema=ArgsSugerirRealocacao)
    def sugerir_realocacao_tool(
        atendimento_id: str,
        data: date,
        nova_duracao_minutos: int | None = None,
    ) -> str:
        """Sugere um novo horário, no mesmo dia e com o mesmo profissional, para um atendimento.

        Use quando for preciso remanejar um atendimento específico (atraso,
        cancelamento de outro paciente etc.). Nunca troca o profissional do
        atendimento — só propõe outro horário.
        """
        try:
            existe = any(item.id == atendimento_id for item in fonte.listar_atendimentos(data))
            if not existe:
                return (
                    f"Atendimento '{atendimento_id}' não encontrado na agenda de "
                    f"{data.strftime('%d/%m/%Y')}."
                )

            nova_duracao_em_slots = (
                math.ceil(nova_duracao_minutos / DURACAO_SLOT_MINUTOS)
                if nova_duracao_minutos is not None
                else None
            )
            opcao = sugerir_realocacao_por_id(fonte, data, atendimento_id, nova_duracao_em_slots)
        except Exception as erro:
            return f"Erro ao sugerir realocação: {erro}"

        if opcao is None:
            return (
                f"Não há horário alternativo disponível para o atendimento "
                f"'{atendimento_id}' em {data.strftime('%d/%m/%Y')}."
            )
        return "Nova opção de horário:\n" + _formatar_opcao_encaixe(fonte, data, opcao)

    @tool("enviar_relatorio", args_schema=ArgsEnviarRelatorio)
    def enviar_relatorio_tool(
        data: date | None = None, destinatarios: list[str] | None = None
    ) -> str:
        """Envia por e-mail o relatório de ocupação do dia (por sala e por especialidade).

        Só use quando o pedido for claro e explícito sobre mandar o relatório
        por e-mail (ex.: "manda o relatório de hoje"). Nunca aciona o envio
        por conta própria, nem como parte de outra resposta.
        """
        data_efetiva = data if data is not None else data_referencia
        try:
            enviar_relatorio(fonte, data_efetiva, destinatarios)
        except Exception as erro:
            return f"Erro ao enviar relatório: {erro}"

        destinatarios_efetivos = (
            destinatarios if destinatarios is not None else get_settings().report_email_to
        )
        return (
            f"Relatório de ocupação de {data_efetiva.strftime('%d/%m/%Y')} enviado para "
            f"{len(destinatarios_efetivos)} destinatário(s)."
        )

    return [
        buscar_paciente_tool,
        buscar_encaixe_tool,
        consultar_disponibilidade_tool,
        consultar_ocupacao_tool,
        sugerir_realocacao_tool,
        enviar_relatorio_tool,
    ]
