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
from typing import Any, Literal

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from app.ai.formatacao import formatar_razao
from app.ai.relatorios import (
    AVISO_ACIMA_DE_CEM,
    bloco_ocupacao_agregada,
    bloco_ocupacao_profissional,
    bloco_pacientes_profissional,
    ocupacao_agregada_acima_de_cem,
    ocupacao_profissional_acima_de_cem,
)
from app.ai.servico_agenda import (
    ItemDemandaBruta,
    ProfissionalAmbiguo,
    ProfissionalNaoEncontrado,
    ResultadoAlternativas,
    ResultadoBuscaEncaixe,
    ResultadoExato,
    buscar_encaixe,
    buscar_paciente,
    consultar_carga_profissionais,
    consultar_disponibilidade_do_dia,
    consultar_ocupacao_do_dia,
    consultar_ocupacao_semanal_profissional,
    localizar_paciente,
    localizar_profissional,
    sugerir_realocacao_por_id,
)
from app.config import get_settings
from app.data_sources.continuidade import (
    ContinuidadeDataSource,
    SemHistoricoContinuidadeDataSource,
)
from app.domain import (
    DURACAO_SLOT_MINUTOS,
    Convenio,
    EntradaGrade,
    Especialidade,
    ScheduleDataSource,
    Slot,
    dias_da_semana_de,
    normalizar_id,
    reconhecer_especialidade,
)
from app.domain.constants import (
    HORARIO_FECHAMENTO,
    HORARIO_PREFERENCIAL_PADRAO,
    META_OCUPACAO_POR_SALA,
)
from app.engine.carga_profissionais import CargaProfissionais, CargaProfissional
from app.engine.disponibilidade import SlotDisponivel
from app.engine.encaixe import CenarioSugestao, ItemEncaixeResolvido, OpcaoEncaixe
from app.engine.ocupacao import OcupacaoAgregada
from app.engine.ocupacao_profissional import (
    OcupacaoDiaProfissional,
    OcupacaoSemanalProfissional,
)

#: Assinatura de `enviar_relatorio_por_email` (`app.reports.envio`), recebida
#: como parâmetro em vez de importada direto: `app.ai` não pode depender de
#: `app.reports` (ver `tests/test_arquitetura.py`), então quem monta as tools
#: (a rota `/agenda/chat`) é quem decide qual implementação injetar.
EnviarRelatorio = Callable[[ScheduleDataSource, date, list[str] | None], None]

#: Nome do dia da semana por `date.weekday()` — usado no prompt de sistema
#: (ver `criar_agente`) e nas mensagens das tools.
DIAS_DA_SEMANA = (
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
)

#: Id usado na `SolicitacaoAtendimento` quando o pedido de encaixe não cita
#: paciente (ex.: "consigo encaixar psicologia às 10h?"). A engine não usa o
#: id do paciente para achar vaga, só a continuidade — que não é consultada
#: nesse caso. Nunca aparece no texto devolvido ao usuário.
PACIENTE_NAO_IDENTIFICADO = "paciente-nao-identificado"

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


def _sufixo_posto_do_item(grade: list[EntradaGrade], item: ItemEncaixeResolvido) -> str:
    """` (posto 2)` quando o profissional tem mais de um posto naquela sala nos
    horários do item — mesma regra de `_sufixo_posto`; vazio sem ambiguidade.
    """
    slots_do_item = set(item.slots)
    postos = {
        entrada.indice_posto
        for entrada in grade
        if entrada.profissional_id == item.profissional_id
        and entrada.sala_id == item.sala_id
        and entrada.slot in slots_do_item
    }
    if len(postos) <= 1:
        return ""
    return f" (posto {item.indice_posto + 1})"


def _formatar_opcao_encaixe(fonte: ScheduleDataSource, dia: date, opcao: OpcaoEncaixe) -> str:
    grade = fonte.listar_grade(dia)
    linhas = [
        f"- {_rotulo_especialidade(item.especialidade)}: "
        f"{item.slots[0].hora_inicio.strftime('%H:%M')} às "
        f"{item.slots[-1].hora_fim.strftime('%H:%M')}, com "
        f"{_nome_profissional(fonte, dia, item.profissional_id)} na "
        f"{_nome_sala(fonte, dia, item.sala_id)}"
        f"{_sufixo_posto_do_item(grade, item)}."
        for item in opcao.itens
    ]
    return "\n".join(linhas)


def _formatar_linha_ocupacao(rotulo: str, agregada: OcupacaoAgregada) -> str:
    aviso = " (abaixo da meta de 80%)" if agregada.abaixo_da_meta else ""
    percentual = _formatar_percentual(agregada.slots_escalados, agregada.slots_ocupados)
    return f"- {rotulo}: {percentual}{aviso}"


#: Nome curto do dia da semana por `date.weekday()`, usado no texto de
#: `consultar_ocupacao_profissional`.
_DIAS_DA_SEMANA_CURTOS = ("Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo")

#: Ressalva fixa de `consultar_ocupacao_profissional` enquanto a fonte for a
#: planilha. Remover quando a agenda passar a vir do Agendador, com datas reais.
NOTA_GRADE_SEMANAL = (
    "Obs.: a fonte de dados atual é a grade semanal da planilha (uma aba por dia da "
    "semana, sem datas); os números refletem a grade vigente, não uma semana específica "
    "do calendário."
)


def _formatar_percentual(slots_escalados: int, slots_ocupados: int) -> str:
    """`31` de `40` -> `77,5%` (uma casa, arredondamento comercial, vírgula decimal).

    Conta em `Decimal` a partir dos inteiros para não herdar artefato de ponto
    flutuante na casa decimal exibida.
    """
    return formatar_razao(slots_ocupados, slots_escalados, percentual=True)


def _rotulo_meta() -> str:
    """`80%` — a meta da clínica, sem casa decimal."""
    return f"{round(META_OCUPACAO_POR_SALA * 100)}%"


def _plural(quantidade: int, singular: str, plural: str) -> str:
    return singular if quantidade == 1 else plural


def _situacao_meta(slots_para_meta: int) -> str:
    if slots_para_meta == 0:
        return "meta atingida"
    return (
        f"abaixo da meta; {_plural(slots_para_meta, 'falta', 'faltam')} {slots_para_meta} "
        f"{_plural(slots_para_meta, 'slot', 'slots')} para {_rotulo_meta()}"
    )


def _resumo_ocupacao(slots_escalados: int, slots_ocupados: int, slots_livres: int) -> str:
    """`77,5% — 31 de 40 slots ocupados, 9 livres`."""
    return (
        f"{_formatar_percentual(slots_escalados, slots_ocupados)} — {slots_ocupados} de "
        f"{slots_escalados} {_plural(slots_escalados, 'slot ocupado', 'slots ocupados')}, "
        f"{slots_livres} {_plural(slots_livres, 'livre', 'livres')}"
    )


def _rotulo_dia(dia: date) -> str:
    return f"{_DIAS_DA_SEMANA_CURTOS[dia.weekday()]} ({dia.strftime('%d/%m')})"


def _formatar_dia_ocupacao_profissional(dia: OcupacaoDiaProfissional) -> list[str]:
    """Linha do dia e, quando ela passou por mais de uma sala/posto, uma linha por combinação.

    O posto só aparece quando a mesma sala teve mais de um posto dela no dia;
    é contado a partir de 1 no texto, como em `_sufixo_posto`.
    """
    blocos = [
        f"{rotulo} {ocupados}/{escalados}"
        for rotulo, escalados, ocupados in (
            ("manhã", dia.manha_escalados, dia.manha_ocupados),
            ("tarde", dia.tarde_escalados, dia.tarde_ocupados),
        )
        if escalados or ocupados
    ]
    resumo = _resumo_ocupacao(dia.slots_escalados, dia.slots_ocupados, dia.slots_livres)
    linhas = [
        f"- {_rotulo_dia(dia.data)}: {resumo} ({', '.join(blocos)}) — "
        f"{_situacao_meta(dia.slots_para_meta)}"
    ]
    if len(dia.por_sala_posto) > 1:
        postos_por_sala = Counter(item.sala_id for item in dia.por_sala_posto)
        for item in dia.por_sala_posto:
            posto = f" (posto {item.indice_posto + 1})" if postos_por_sala[item.sala_id] > 1 else ""
            linhas.append(
                f"  · {item.sala_nome}{posto}: {item.slots_ocupados} de {item.slots_escalados}"
            )
    return linhas


def _formatar_ocupacao_profissional(ocupacao: OcupacaoSemanalProfissional) -> str:
    """Texto de `consultar_ocupacao_profissional`: dia a dia, total da semana e ressalvas."""
    especialidade = (
        f" ({_rotulo_especialidade(ocupacao.especialidade)})"
        if ocupacao.especialidade is not None
        else ""
    )
    periodo = (
        f"{ocupacao.semana_inicio.strftime('%d/%m')} a {ocupacao.semana_fim.strftime('%d/%m/%Y')}"
    )
    aviso_parcial = None
    if ocupacao.parcial:
        falhas = ", ".join(_rotulo_dia(dia) for dia in ocupacao.dias_com_falha)
        aviso_parcial = f"Não foi possível ler: {falhas}; os totais da semana são parciais."

    if not ocupacao.dias:
        linhas = [f"{ocupacao.nome}{especialidade} não tem agenda na semana de {periodo}."]
        if aviso_parcial is not None:
            linhas.append(aviso_parcial)
        linhas.append(NOTA_GRADE_SEMANAL)
        return "\n".join(linhas)

    linhas = [
        f"Ocupação de {ocupacao.nome}{especialidade} — semana de {periodo}",
        f"Meta: {_rotulo_meta()}",
        "",
        "Por dia:",
    ]
    for dia in ocupacao.dias:
        linhas.extend(_formatar_dia_ocupacao_profissional(dia))
    if ocupacao.dias_sem_agenda:
        sem_agenda = ", ".join(
            _DIAS_DA_SEMANA_CURTOS[dia.weekday()] for dia in ocupacao.dias_sem_agenda
        )
        linhas.append(f"Sem agenda: {sem_agenda}.")
    resumo_semana = _resumo_ocupacao(
        ocupacao.slots_escalados, ocupacao.slots_ocupados, ocupacao.slots_livres
    )
    linhas.append("")
    linhas.append(f"Semana: {resumo_semana} — {_situacao_meta(ocupacao.slots_para_meta)}")
    if aviso_parcial is not None:
        linhas.append(aviso_parcial)
    if ocupacao.tem_inconsistencia:
        linhas.append(
            "Atenção: há atendimentos fora dos horários escalados da profissional "
            "(possível inconsistência na planilha)."
        )
    if ocupacao_profissional_acima_de_cem(ocupacao):
        linhas.append(AVISO_ACIMA_DE_CEM)
    linhas.append("")
    linhas.append(NOTA_GRADE_SEMANAL)
    return "\n".join(linhas)


#: Abreviação do dia da semana por `date.weekday()`, usada nas linhas compactas
#: de `consultar_pacientes_por_profissional`.
_DIAS_DA_SEMANA_ABREVIADOS = ("Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom")

#: Definição de "paciente" que acompanha toda resposta de
#: `consultar_pacientes_por_profissional`.
DEFINICAO_PACIENTE = (
    "Cada paciente conta uma vez por profissional e dia; em sessão em grupo, cada paciente conta."
)


def _formatar_media(soma: int, quantidade: int) -> str:
    """`31` em `3` dias -> `10,3` (uma casa, arredondamento comercial, vírgula decimal).

    Conta em `Decimal` a partir dos inteiros, como `_formatar_percentual`.
    """
    return formatar_razao(soma, quantidade)


def _contagem(quantidade: int, singular: str, plural: str) -> str:
    """`1 paciente`, `2 pacientes`."""
    return f"{quantidade} {_plural(quantidade, singular, plural)}"


def _periodo(dias: list[date]) -> str:
    """`28/09 a 03/10/2026`."""
    return f"{dias[0].strftime('%d/%m')} a {dias[-1].strftime('%d/%m/%Y')}"


def _rotulo_dia_longo(dia: date) -> str:
    """`segunda-feira, 28/09/2026`."""
    return f"{DIAS_DA_SEMANA[dia.weekday()]}, {dia.strftime('%d/%m/%Y')}"


def _aviso_parcial_pacientes(dias_com_falha: list[date]) -> list[str]:
    if not dias_com_falha:
        return []
    falhas = ", ".join(_rotulo_dia(dia) for dia in dias_com_falha)
    return [f"Não foi possível ler: {falhas}; os números são parciais."]


def _media_da_profissional(carga: CargaProfissional) -> str:
    """Média de pacientes por dia com agenda, recalculada dos inteiros para formatar sem float."""
    return _formatar_media(sum(dia.pacientes for dia in carga.dias), len(carga.dias))


def _distintos_na_semana(carga: CargaProfissional) -> str:
    """`25 pacientes distintos na semana`."""
    quantidade = carga.pacientes_distintos_semana
    return f"{_contagem(quantidade, 'paciente distinto', 'pacientes distintos')} na semana"


def _cabecalho_profissional(carga: CargaProfissional) -> str:
    """`Luciana Xavier (Psicologia)`, ou só o nome quando a especialidade é desconhecida."""
    if carga.especialidade is None:
        return carga.nome
    return f"{carga.nome} ({_rotulo_especialidade(carga.especialidade)})"


def _agrupar_por_especialidade(
    cargas: list[CargaProfissional],
) -> list[tuple[str, list[CargaProfissional]]]:
    """Grupos por especialidade, na ordem em que a engine já entrega as profissionais."""
    grupos: list[tuple[str, list[CargaProfissional]]] = []
    for carga in cargas:
        rotulo = (
            _rotulo_especialidade(carga.especialidade)
            if carga.especialidade is not None
            else "Sem especialidade"
        )
        if not grupos or grupos[-1][0] != rotulo:
            grupos.append((rotulo, []))
        grupos[-1][1].append(carga)
    return grupos


def _detalhe_do_dia(pacientes: int, sessoes: int, slots_ocupados: int) -> str:
    """`12 pacientes, 13 sessões, 24 slots ocupados`."""
    return (
        f"{_contagem(pacientes, 'paciente', 'pacientes')}, "
        f"{_contagem(sessoes, 'sessão', 'sessões')}, "
        f"{_contagem(slots_ocupados, 'slot ocupado', 'slots ocupados')}"
    )


def _formatar_pacientes_semana(
    resultado: CargaProfissionais, cargas: list[CargaProfissional], filtro: str
) -> str:
    """Semana, várias profissionais: uma linha compacta por profissional, por especialidade."""
    linhas = [
        f"Pacientes atendidos por profissional{filtro} — semana de {_periodo(resultado.dias)}",
        "(pacientes distintos por dia; a média considera só os dias com agenda)",
        DEFINICAO_PACIENTE,
    ]
    for rotulo, grupo in _agrupar_por_especialidade(cargas):
        linhas.append("")
        linhas.append(rotulo)
        for carga in grupo:
            por_dia = " · ".join(
                f"{_DIAS_DA_SEMANA_ABREVIADOS[dia.data.weekday()]} {dia.pacientes}"
                for dia in carga.dias
            )
            linhas.append(
                f"- {carga.nome}: {por_dia} — média {_media_da_profissional(carga)}/dia em "
                f"{_contagem(len(carga.dias), 'dia', 'dias')} · {_distintos_na_semana(carga)}"
            )
    clinica = " · ".join(
        f"{_DIAS_DA_SEMANA_ABREVIADOS[dia.data.weekday()]} {dia.pacientes_distintos_clinica}"
        for dia in resultado.por_dia
        if dia.pacientes_distintos_clinica > 0
    )
    if clinica:
        linhas.append("")
        linhas.append(f"Pacientes distintos na clínica por dia: {clinica}")
    linhas.extend(_aviso_parcial_pacientes(resultado.dias_com_falha))
    linhas.append("")
    linhas.append(NOTA_GRADE_SEMANAL)
    return "\n".join(linhas)


def _formatar_pacientes_dia(
    resultado: CargaProfissionais, cargas: list[CargaProfissional], filtro: str
) -> str:
    """Um dia, várias profissionais: o total de pacientes de cada uma, por especialidade."""
    linhas = [
        f"Pacientes por profissional{filtro} — {_rotulo_dia_longo(resultado.dias[0])}",
        DEFINICAO_PACIENTE,
    ]
    for rotulo, grupo in _agrupar_por_especialidade(cargas):
        linhas.append("")
        linhas.append(rotulo)
        linhas.extend(
            f"- {carga.nome}: {_contagem(carga.dias[0].pacientes, 'paciente', 'pacientes')}"
            for carga in grupo
        )
    distintos = sum(dia.pacientes_distintos_clinica for dia in resultado.por_dia)
    linhas.append("")
    linhas.append(f"Pacientes distintos na clínica no dia: {distintos}")
    return "\n".join(linhas)


def _formatar_pacientes_da_profissional(
    resultado: CargaProfissionais, carga: CargaProfissional
) -> str:
    """Uma profissional: pacientes, sessões e slots de cada dia com agenda e a média.

    No escopo de um dia, só a linha desse dia (sem média nem nota da grade
    semanal, como no formato de um dia com todas as profissionais).
    """
    if len(resultado.dias) == 1:
        dia = carga.dias[0]
        return "\n".join(
            [
                f"{_cabecalho_profissional(carga)} — {_rotulo_dia_longo(dia.data)}",
                DEFINICAO_PACIENTE,
                f"- {_detalhe_do_dia(dia.pacientes, dia.sessoes, dia.slots_ocupados)}",
            ]
        )

    linhas = [
        f"{_cabecalho_profissional(carga)} — semana de {_periodo(resultado.dias)}",
        DEFINICAO_PACIENTE,
    ]
    linhas.extend(
        f"- {_rotulo_dia(dia.data)}: "
        f"{_detalhe_do_dia(dia.pacientes, dia.sessoes, dia.slots_ocupados)}"
        for dia in carga.dias
    )
    linhas.append(
        f"Média: {_media_da_profissional(carga)} pacientes por dia em "
        f"{_contagem(len(carga.dias), 'dia', 'dias')} · {_distintos_na_semana(carga)}"
    )
    if carga.dias_sem_agenda:
        sem_agenda = ", ".join(
            _DIAS_DA_SEMANA_CURTOS[dia.weekday()] for dia in carga.dias_sem_agenda
        )
        linhas.append(f"Sem agenda: {sem_agenda}.")
    linhas.extend(_aviso_parcial_pacientes(carga.dias_com_falha))
    linhas.append("")
    linhas.append(NOTA_GRADE_SEMANAL)
    return "\n".join(linhas)


def _formatar_resultado_encaixe(
    fonte: ScheduleDataSource,
    dia: date,
    resultado: ResultadoBuscaEncaixe,
    horario_minimo: time,
    duracao_total_minutos: int,
) -> str:
    """Texto de `buscar_encaixe` para cada um dos três resultados possíveis.

    `horario_minimo` e `duracao_total_minutos` só entram no texto de "nenhum":
    deixam explícito que o dia inteiro foi varrido, e não só o horário pedido.
    """
    if isinstance(resultado, ResultadoExato):
        return "Horário encontrado:\n" + _formatar_opcao_encaixe(fonte, dia, resultado.opcao)

    if isinstance(resultado, ResultadoAlternativas):
        blocos = [
            f"{_ROTULOS_CENARIO[opcao_com_cenario.cenario]}:\n"
            + _formatar_opcao_encaixe(fonte, dia, opcao_com_cenario.opcao)
            for opcao_com_cenario in resultado.opcoes
        ]
        return "Não há vaga exatamente no horário pedido. Alternativas:\n\n" + "\n\n".join(blocos)

    return (
        f"Nenhum horário disponível em {dia.strftime('%d/%m/%Y')} para essa combinação "
        f"de especialidades. O dia inteiro foi verificado, de "
        f"{horario_minimo.strftime('%H:%M')} até o fechamento "
        f"({HORARIO_FECHAMENTO.strftime('%H:%M')}), e não existe nenhum bloco contínuo "
        f"livre de {duracao_total_minutos} minutos que atenda ao pedido."
    )


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
    paciente: str | None = Field(
        default=None,
        description=(
            "Opcional. Nome ou id do paciente, quando o pedido citar um — pode ser "
            "qualquer nome, esteja ou não na agenda; não verifique antes com "
            "buscar_paciente. Deixe em branco em perguntas de viabilidade sem paciente "
            '(ex.: "consigo encaixar psicologia às 10h?").'
        ),
    )
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


class ArgsConsultarOcupacaoProfissional(BaseModel):
    profissional: str = Field(
        description="Nome (ou parte do nome) da profissional, como citado na pergunta."
    )
    data: date | None = Field(
        default=None,
        description=(
            "Qualquer data da semana a consultar, no formato AAAA-MM-DD. Deixe em branco "
            "para usar a semana de hoje."
        ),
    )


class ArgsConsultarPacientesPorProfissional(BaseModel):
    data: date | None = Field(
        default=None,
        description=(
            "Data no formato AAAA-MM-DD: no escopo 'semana', qualquer dia da semana a "
            "consultar; no escopo 'dia', o próprio dia. Deixe em branco para usar hoje."
        ),
    )
    escopo: Literal["semana", "dia"] = Field(
        default="semana",
        description=(
            "'semana' (padrão): segunda a sábado da semana da data, com o detalhe por dia. "
            "'dia': só a data informada — use quando a pergunta citar um dia ('hoje', "
            "'na terça', uma data)."
        ),
    )
    profissional: str | None = Field(
        default=None,
        description=(
            "Nome (ou parte do nome) de uma profissional, só quando a pergunta citar uma. "
            "Deixe em branco para todas."
        ),
    )
    especialidade: str | None = Field(
        default=None,
        description=(
            "Especialidade como citada na pergunta (ex.: 'psicologia', 'fono', 'TO'), só "
            "quando a pergunta filtrar por uma. Deixe em branco para todas."
        ),
    )


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
        """Localiza um paciente na agenda de um dia e nos outros dias da mesma semana.

        Use somente quando o usuário perguntar sobre um paciente (convênio, se
        está na agenda, em que dias aparece) ou para localizar um atendimento
        existente. NÃO chame antes de `buscar_encaixe` nem de consultas de
        disponibilidade: essas tools não dependem de o paciente estar na agenda.
        """
        try:
            localizacao = localizar_paciente(fonte, data, nome_ou_id)
        except Exception as erro:
            return f"Erro ao buscar paciente: {erro}"

        paciente = localizacao.paciente_no_dia
        if paciente is not None:
            return (
                f"Paciente encontrado: {paciente.nome} (id: {paciente.id}, "
                f"convênio: {_rotulo_convenio(paciente.convenio)})."
            )

        referencia = localizacao.paciente_referencia
        if referencia is not None:
            dias = ", ".join(
                f"{DIAS_DA_SEMANA[dia.weekday()]} ({dia.strftime('%d/%m')})"
                for dia in localizacao.outros_dias
            )
            convenio = (
                f" Convênio no registro encontrado: {_rotulo_convenio(referencia.convenio)}."
                if referencia.convenio is not None
                else ""
            )
            return (
                f"{referencia.nome} não tem atendimentos na agenda de "
                f"{DIAS_DA_SEMANA[data.weekday()]} ({data.strftime('%d/%m')}). "
                f"Nesta semana aparece em: {dias}.{convenio}"
            )

        semana = dias_da_semana_de(data)
        texto = (
            f"'{nome_ou_id}' não aparece na agenda de nenhum dia da semana (período "
            f"verificado: {semana[0].strftime('%d/%m/%Y')} a "
            f"{semana[-1].strftime('%d/%m/%Y')})."
        )
        if localizacao.sugestoes:
            texto += (
                f" Nomes parecidos na agenda: {', '.join(localizacao.sugestoes)} (podem ser "
                "a mesma pessoa ou não; é só uma sugestão)."
            )
        return texto + (
            " Pacientes só aparecem na agenda quando têm atendimento marcado; isso não "
            "impede buscar encaixe, que aceita qualquer nome."
        )

    @tool("buscar_encaixe", args_schema=ArgsBuscarEncaixe)
    def buscar_encaixe_tool(
        data: date,
        itens: list[ItemDemandaEntrada],
        paciente: str | None = None,
        horario_minimo: time | None = None,
        horario_desejado: time | None = None,
    ) -> str:
        """Busca um horário de encaixe, casando uma ou mais especialidades sem buraco entre elas.

        Use para "tem vaga para [paciente] em [data]?", para encaixar um
        atendimento novo e para perguntas de viabilidade ("consigo encaixar
        X às Y?"), com ou sem paciente. É a única fonte de combinações de
        horário (sessões longas, várias especialidades em sequência,
        alternativas). O paciente é opcional e pode ser qualquer nome, esteja
        ou não na agenda: chame direto, sem verificar o paciente antes com
        `buscar_paciente`.
        """
        nota = ""
        try:
            # Paciente sem atendimento no dia (novo, ou que não vem nesse dia
            # da semana) não bloqueia a busca: a agenda não tem cadastro de
            # pacientes, e a identidade só serve para consultar continuidade.
            continuidade_efetiva: ContinuidadeDataSource = SemHistoricoContinuidadeDataSource()
            if paciente is None:
                paciente_id = PACIENTE_NAO_IDENTIFICADO
            else:
                encontrado = buscar_paciente(fonte, data, paciente)
                if encontrado is not None:
                    paciente_id = encontrado.id
                    continuidade_efetiva = continuidade
                else:
                    paciente_id = normalizar_id(paciente) or PACIENTE_NAO_IDENTIFICADO
                    nota = (
                        f"\n\nObs.: '{paciente}' não tem atendimentos na agenda de "
                        f"{DIAS_DA_SEMANA[data.weekday()]} ({data.strftime('%d/%m/%Y')}); "
                        "a busca foi feita sem considerar histórico de profissional habitual."
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
                continuidade_efetiva,
                paciente_id,
                data,
                itens_brutos,
                horario_minimo=horario_minimo_efetivo,
                horario_desejado=horario_desejado,
            )
        except Exception as erro:
            return f"Erro ao buscar encaixe: {erro}"

        duracao_total_minutos = sum(
            item.duracao_em_slots * DURACAO_SLOT_MINUTOS for item in itens_brutos
        )
        return (
            _formatar_resultado_encaixe(
                fonte, data, resultado, horario_minimo_efetivo, duracao_total_minutos
            )
            + nota
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

    # langchain_core instalado: convert.tool aceita content_and_artifact;
    # base.BaseTool desempacota (content, artifact). ToolMessage.artifact não
    # vai ao modelo (messages/tool.py; conversor OpenAI só envia content/role/tool_call_id).
    @tool(
        "consultar_ocupacao",
        args_schema=ArgsConsultarOcupacao,
        response_format="content_and_artifact",
    )
    def consultar_ocupacao_tool(data: date) -> tuple[str, dict[str, Any] | None]:
        """Ocupação do dia, por especialidade e por sala, sinalizando quem está abaixo da meta.

        Use para perguntas do tipo "como está a ocupação hoje?" ou "tem sala
        ociosa?". Para a taxa de ocupação de UMA profissional específica, use
        `consultar_ocupacao_profissional`. NÃO serve para contar pacientes
        (ocupação mede slots): para quantos pacientes cada profissional
        atende, use `consultar_pacientes_por_profissional`.
        """
        try:
            relatorio = consultar_ocupacao_do_dia(fonte, data)
            nomes_salas = {s.id: s.nome for s in fonte.listar_salas(data)}
            bloco = bloco_ocupacao_agregada(relatorio, nomes_salas)
        except Exception as erro:
            return f"Erro ao consultar ocupação: {erro}", None

        por_especialidade = relatorio.por_especialidade()
        por_sala = relatorio.por_sala()
        if not por_especialidade and not por_sala:
            return f"Nenhuma escala encontrada para {data.strftime('%d/%m/%Y')}.", None

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
            _formatar_linha_ocupacao(nomes_salas.get(sala_id, sala_id), agregada)
            for sala_id, agregada in sorted(por_sala.items())
        )
        if ocupacao_agregada_acima_de_cem(relatorio):
            linhas.extend(["", AVISO_ACIMA_DE_CEM])
        return "\n".join(linhas), bloco.model_dump(mode="json")

    @tool(
        "consultar_ocupacao_profissional",
        args_schema=ArgsConsultarOcupacaoProfissional,
        response_format="content_and_artifact",
    )
    def consultar_ocupacao_profissional_tool(
        profissional: str,
        data: date | None = None,
    ) -> tuple[str, dict[str, Any] | None]:
        """Taxa de ocupação de UMA profissional, dia a dia e no total da semana, frente à meta.

        Use quando pedirem a taxa de ocupação de uma profissional específica
        ("qual a ocupação da Rossana?"), por dia e por semana. NÃO use para
        ocupação por especialidade ou por sala — para isso, use
        `consultar_ocupacao`. NÃO serve para contar pacientes (mede slots):
        para quantos pacientes uma ou todas as profissionais atendem, use
        `consultar_pacientes_por_profissional`.
        """
        data_efetiva = data if data is not None else data_referencia
        try:
            localizacao = localizar_profissional(fonte, data_efetiva, profissional)
            if isinstance(localizacao, ProfissionalAmbiguo):
                candidatos = ", ".join(
                    f"{candidato.nome} ({_rotulo_especialidade(candidato.especialidade)})"
                    for candidato in localizacao.candidatos
                )
                return (
                    f"Mais de uma profissional corresponde a '{profissional}': {candidatos}. "
                    "Pergunte qual delas a pessoa quis dizer antes de consultar a ocupação."
                ), None
            if isinstance(localizacao, ProfissionalNaoEncontrado):
                semana = dias_da_semana_de(data_efetiva)
                disponiveis = ", ".join(localizacao.nomes_disponiveis) or "nenhum"
                return (
                    f"Não há profissional com o nome '{profissional}' na agenda da semana de "
                    f"{semana[0].strftime('%d/%m')} a {semana[-1].strftime('%d/%m/%Y')}. "
                    f"Profissionais disponíveis: {disponiveis}."
                ), None

            ocupacao = consultar_ocupacao_semanal_profissional(
                fonte, localizacao.profissional_id, data_efetiva
            )
            texto = _formatar_ocupacao_profissional(ocupacao)
            if not ocupacao.dias:
                return texto, None
            avisos = [NOTA_GRADE_SEMANAL]
            if ocupacao.parcial:
                falhas = ", ".join(_rotulo_dia(dia) for dia in ocupacao.dias_com_falha)
                avisos.append(f"Não foi possível ler: {falhas}; os totais da semana são parciais.")
            if ocupacao.tem_inconsistencia:
                avisos.append(
                    "Atenção: há atendimentos fora dos horários escalados da profissional "
                    "(possível inconsistência na planilha)."
                )
            bloco = bloco_ocupacao_profissional(ocupacao, avisos)
        except Exception as erro:
            return f"Erro ao consultar ocupação da profissional: {erro}", None

        return texto, bloco.model_dump(mode="json")

    @tool(
        "consultar_pacientes_por_profissional",
        args_schema=ArgsConsultarPacientesPorProfissional,
        response_format="content_and_artifact",
    )
    def consultar_pacientes_por_profissional_tool(
        data: date | None = None,
        escopo: Literal["semana", "dia"] = "semana",
        profissional: str | None = None,
        especialidade: str | None = None,
    ) -> tuple[str, dict[str, Any] | None]:
        """QUANTOS PACIENTES cada profissional (ou uma) atende, por dia ou na semana.

        Use quando perguntarem quantos pacientes as profissionais atendem
        ("quantos pacientes cada profissional atende por dia?", "quantos
        pacientes a Rossana atende?", "quantos pacientes de fono na
        terça?"). Padrão: semana de hoje, todas as profissionais, com o
        detalhe por dia. NÃO use para taxa de ocupação (slots) — para isso,
        `consultar_ocupacao_profissional` ou `consultar_ocupacao`.
        """
        data_efetiva = data if data is not None else data_referencia
        if escopo == "dia" and data_efetiva not in dias_da_semana_de(data_efetiva):
            return (
                f"A clínica não tem agenda aos domingos ({data_efetiva.strftime('%d/%m/%Y')}).",
                None,
            )
        dias = [data_efetiva] if escopo == "dia" else dias_da_semana_de(data_efetiva)
        periodo = (
            f"em {_rotulo_dia_longo(data_efetiva)}"
            if escopo == "dia"
            else f"na semana de {_periodo(dias)}"
        )

        especialidade_filtro: Especialidade | None = None
        if especialidade is not None:
            especialidade_filtro = reconhecer_especialidade(especialidade)
            if especialidade_filtro is None:
                validas = ", ".join(_rotulo_especialidade(item) for item in Especialidade)
                return (
                    f"Especialidade '{especialidade}' não reconhecida. Especialidades "
                    f"válidas: {validas}."
                ), None

        try:
            profissional_id: str | None = None
            nome_encontrado = ""
            if profissional is not None:
                localizacao = localizar_profissional(fonte, data_efetiva, profissional)
                if isinstance(localizacao, ProfissionalAmbiguo):
                    candidatos = ", ".join(
                        f"{candidato.nome} ({_rotulo_especialidade(candidato.especialidade)})"
                        for candidato in localizacao.candidatos
                    )
                    return (
                        f"Mais de uma profissional corresponde a '{profissional}': "
                        f"{candidatos}. Pergunte qual delas a pessoa quis dizer antes de "
                        "contar os pacientes."
                    ), None
                if isinstance(localizacao, ProfissionalNaoEncontrado):
                    semana = dias_da_semana_de(data_efetiva)
                    disponiveis = ", ".join(localizacao.nomes_disponiveis) or "nenhum"
                    return (
                        f"Não há profissional com o nome '{profissional}' na agenda da semana "
                        f"de {_periodo(semana)}. Profissionais disponíveis: {disponiveis}."
                    ), None
                profissional_id = localizacao.profissional_id
                nome_encontrado = localizacao.nome

            resultado = consultar_carga_profissionais(fonte, dias)
        except Exception as erro:
            return f"Erro ao contar pacientes por profissional: {erro}", None

        aviso_parcial = _aviso_parcial_pacientes(resultado.dias_com_falha)
        cargas = resultado.profissionais
        if profissional_id is not None:
            carga = next((item for item in cargas if item.profissional_id == profissional_id), None)
            if carga is None:
                return "\n".join(
                    [f"{nome_encontrado} não tem agenda {periodo}.", *aviso_parcial]
                ), None
            if especialidade_filtro is not None and carga.especialidade != especialidade_filtro:
                return (
                    f"{_cabecalho_profissional(carga)} não é de "
                    f"{_rotulo_especialidade(especialidade_filtro)}."
                ), None
            cargas = [carga]

        filtro = ""
        if especialidade_filtro is not None:
            filtro = f" de {_rotulo_especialidade(especialidade_filtro)}"
            cargas = [item for item in cargas if item.especialidade == especialidade_filtro]
        if not cargas:
            return "\n".join(
                [f"Nenhuma profissional{filtro} tem agenda {periodo}.", *aviso_parcial]
            ), None
        try:
            avisos = [DEFINICAO_PACIENTE, *aviso_parcial]
            if escopo == "semana":
                avisos.append(NOTA_GRADE_SEMANAL)
            if profissional_id is not None:
                texto = _formatar_pacientes_da_profissional(resultado, cargas[0])
            elif escopo == "dia":
                texto = _formatar_pacientes_dia(resultado, cargas, filtro)
            else:
                texto = _formatar_pacientes_semana(resultado, cargas, filtro)
            bloco = bloco_pacientes_profissional(
                resultado,
                cargas,
                escopo,
                avisos,
                individual=profissional_id is not None,
            )
            return texto, bloco.model_dump(mode="json")
        except Exception as erro:
            return f"Erro ao contar pacientes por profissional: {erro}", None

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
        consultar_ocupacao_profissional_tool,
        consultar_pacientes_por_profissional_tool,
        sugerir_realocacao_tool,
        enviar_relatorio_tool,
    ]
