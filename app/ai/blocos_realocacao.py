"""Texto (para o modelo) e bloco (para a tela) do plano de realocação.

Privacidade, como em `pacientes_por_profissional_nomes`: os nomes de pacientes
vão só nas linhas do bloco (`contem_dados_pacientes: true`). O texto que o
modelo lê descreve papéis ("quem está na quarta das 10:00 às 11:00") e horários,
nunca nomes. O rótulo de paciente novo sem nome (`ROTULO_PACIENTE_NOVO`) não
é nome de paciente e fica fora da proteção do texto.

Tanto o plano quanto a pergunta de "qual paciente?" usam
`dados.relatorio = "plano_realocacao"`: um valor só para o front.
"""

from datetime import date
from typing import Final

from app.ai.relatorios import (
    RELATORIO_PLANO_REALOCACAO,
    BlocoRelatorio,
    ColunaTabela,
    DadosTabelaGenerica,
    Formato,
    ItemResumo,
    UnidadeGrupo,
    ValorSimples,
    tabela,
)
from app.domain import Especialidade
from app.domain.formatacao import rotulo_enum
from app.engine.fotografia import FotografiaPeriodo
from app.engine.realocacao import (
    Candidata,
    Lugar,
    MotivoPergunta,
    MotivoSemSolucao,
    OpcaoRealocacao,
    Passo,
    Pergunta,
    PlanoRealocacao,
    Regras,
    SemSolucao,
)

TITULO_PLANO_REALOCACAO: Final[str] = "Plano de realocação (grade semanal vigente)"
TITULO_PERGUNTA_REALOCACAO: Final[str] = "Plano de realocação: qual paciente?"

COLUNAS_PLANO_REALOCACAO: Final[list[tuple[str, str, Formato]]] = [
    ("opcao", "Opção", "texto"),
    ("passo", "Passo", "inteiro"),
    ("paciente", "Quem", "texto"),
    ("terapia", "Terapia", "texto"),
    ("profissional", "Profissional", "texto"),
    ("de", "De", "texto"),
    ("para", "Para", "texto"),
    ("observacao", "Observação", "texto"),
]
COLUNAS_RESUMO_POR_OPCAO: Final[list[tuple[str, str, Formato]]] = [
    ("opcao", "Opção", "texto"),
    ("deslocados", "Pacientes deslocados", "inteiro"),
    ("mudam_de_dia", "Mudam de dia", "inteiro"),
    ("pedido", "O pedido fica em", "texto"),
]
COLUNAS_PERGUNTA_REALOCACAO: Final[list[tuple[str, str, Formato]]] = [
    ("candidata", "Candidata", "inteiro"),
    ("paciente", "Nome", "texto"),
    ("terapia", "Terapia", "texto"),
    ("profissional", "Profissional", "texto"),
    ("dias", "Dias", "texto"),
]
AGRUPAR_PLANO_REALOCACAO: Final[tuple[str, ...]] = ("opcao",)

#: Limites da sugestão, sempre presentes nos avisos do plano.
AVISOS_PLANO_REALOCACAO: Final[tuple[str, ...]] = (
    "Sugestão: considera apenas os atendimentos presentes na semana lida (terapias "
    "quinzenais ou fora desta semana não aparecem).",
    "Não considera a disponibilidade dos pacientes.",
    "Cada mudança precisa ser confirmada com os pacientes e feita manualmente na planilha.",
    "Vagas livres na grade podem estar reservadas (reuniões, supervisão); confira antes de mover.",
)
AVISO_BUSCA_LIMITADA: Final[str] = (
    "Busca limitada: havia combinações demais e nem todas foram avaliadas; pode existir "
    "opção melhor."
)
AVISO_HOMONIMOS: Final[str] = (
    "O paciente é identificado só pelo nome: homônimos aparecem como um só."
)

#: Candidatas descritas no texto; a tela mostra todas.
MAX_CANDIDATAS_NO_TEXTO: Final[int] = 10

_DIAS: Final[tuple[str, ...]] = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado")
_DIAS_CURTOS: Final[tuple[str, ...]] = ("Seg", "Ter", "Qua", "Qui", "Sex", "Sáb")
_DE_PACIENTE_NOVO: Final[str] = "— (paciente novo)"


def _n(quantidade: int, singular: str, plural: str) -> str:
    return f"{quantidade} {singular if quantidade == 1 else plural}"


def _terapia(especialidade: Especialidade) -> str:
    return rotulo_enum(especialidade.value)


def _faixa(lugar: Lugar) -> str:
    return f"{lugar.hora_inicio:%H:%M} às {lugar.hora_fim:%H:%M}"


def _sala(foto: FotografiaPeriodo, lugar: Lugar) -> str:
    sala = foto.nome_sala(lugar.sala_id)
    return f"{sala} (coluna {lugar.indice_posto + 1})" if lugar.indice_posto else sala


def lugar_na_tela(foto: FotografiaPeriodo, lugar: Lugar) -> str:
    """`Qua 10:00 às 11:00 · Carol · Sala 3` (formato que a régua relê)."""
    return (
        f"{_DIAS_CURTOS[lugar.data.weekday()]} {_faixa(lugar)} · "
        f"{foto.nome_profissional(lugar.profissional_id)} · {_sala(foto, lugar)}"
    )


def _lugar_no_texto(foto: FotografiaPeriodo, lugar: Lugar) -> str:
    """`quarta das 10:00 às 11:00 com Carol (Sala 3)`, sem nomes de pacientes."""
    return (
        f"{_DIAS[lugar.data.weekday()]} das {_faixa(lugar)} com "
        f"{foto.nome_profissional(lugar.profissional_id)} ({_sala(foto, lugar)})"
    )


def _observacao(passo: Passo) -> str:
    partes = ["atendimento pedido"] if passo.pedido else ["mesma terapia e profissional"]
    if passo.outro_profissional:
        partes = ["mesma terapia; outro profissional — confirmar"]
    if passo.outro_dia:
        partes.append("outro dia")
    if passo.outra_sala:
        partes.append("outra sala")
    return "; ".join(partes)


def _dias_da_semana(dias: tuple[date, ...]) -> str:
    return ", ".join(_DIAS_CURTOS[dia.weekday()] for dia in sorted(set(dias)))


def _rotulo_da_opcao(numero: int) -> str:
    return f"Opção {numero}"


# ------------------------------------------------------------------- plano


def _descricao_da_opcao(foto: FotografiaPeriodo, numero: int, opcao: OpcaoRealocacao) -> str:
    deslocados = opcao.deslocados
    if not deslocados:
        cabeca = "sem deslocar ninguém"
    else:
        mudam = opcao.mudam_de_dia
        cabeca = _n(len(deslocados), "paciente deslocado", "pacientes deslocados") + (
            ", mesmo dia"
            if not mudam
            else f", {mudam} muda de dia"
            if mudam == 1
            else f", {mudam} mudam de dia"
        )
    passos = []
    for indice, passo in enumerate(opcao.passos, 1):
        if passo.pedido:
            de = (
                f"o pedido, que hoje está na {_lugar_no_texto(foto, passo.de)}, fica na"
                if passo.de is not None
                else "o pedido fica na"
            )
            passos.append(
                f"{indice}) {de} {_lugar_no_texto(foto, passo.para)}, "
                f"{_terapia(passo.especialidade)}"
            )
        else:
            assert passo.de is not None
            passos.append(
                f"{indice}) quem está na {_lugar_no_texto(foto, passo.de)} vai para "
                f"{_lugar_no_texto(foto, passo.para)} ({_observacao(passo)})"
            )
    return (
        f"- {_rotulo_da_opcao(numero)}: {cabeca}. Passos, nesta ordem: " + "; ".join(passos) + "."
    )


def avisos_do_plano(foto: FotografiaPeriodo, busca_limitada: bool) -> list[str]:
    avisos = list(AVISOS_PLANO_REALOCACAO)
    if busca_limitada:
        avisos.append(AVISO_BUSCA_LIMITADA)
    if foto.dias_nao_lidos:
        avisos.append(
            f"Não foi possível ler: {_dias_da_semana(tuple(foto.dias_nao_lidos))}; a sugestão "
            "não considera esses dias."
        )
    return avisos


def _linhas(foto: FotografiaPeriodo, plano: PlanoRealocacao) -> list[dict[str, ValorSimples]]:
    return [
        {
            "opcao": _rotulo_da_opcao(numero),
            "passo": indice,
            "paciente": passo.paciente,
            "terapia": _terapia(passo.especialidade),
            "profissional": foto.nome_profissional(passo.para.profissional_id),
            "de": lugar_na_tela(foto, passo.de) if passo.de is not None else _DE_PACIENTE_NOVO,
            "para": lugar_na_tela(foto, passo.para),
            "observacao": _observacao(passo),
        }
        for numero, opcao in enumerate(plano.opcoes, 1)
        for indice, passo in enumerate(opcao.passos, 1)
    ]


def _bloco(
    foto: FotografiaPeriodo,
    titulo: str,
    avisos: list[str],
    resumo: list[ItemResumo],
    colunas: list[tuple[str, str, Formato]],
    linhas: list[dict[str, ValorSimples]],
    tabelas: list[object],
    *,
    agrupar: bool,
) -> BlocoRelatorio:
    dias_nao_lidos = sorted(set(foto.dias_nao_lidos))
    return BlocoRelatorio.model_validate(
        {
            "tipo": "tabela_generica",
            "titulo": titulo,
            # A fonte é a grade semanal, sem datas: o bloco não inventa período.
            "periodo": None,
            "meta": None,
            "parcial": bool(dias_nao_lidos),
            "dias_nao_lidos": dias_nao_lidos,
            "avisos": avisos,
            "resumo": resumo,
            "contem_dados_pacientes": True,
            "dados": DadosTabelaGenerica(
                relatorio=RELATORIO_PLANO_REALOCACAO,
                dimensoes=[],
                metricas=[],
                colunas=[ColunaTabela(chave=c, rotulo=r, formato=f) for c, r, f in colunas],
                linhas=linhas,
                total={},
                linhas_omitidas=0,
                # As linhas já saem contíguas por opção, na ordem de execução.
                agrupar_por=list(AGRUPAR_PLANO_REALOCACAO) if agrupar else None,
                unidade_grupo=UnidadeGrupo(singular="passo", plural="passos") if agrupar else None,
            ),
            "tabelas": tabelas,
        }
    )


def _inteiro(rotulo: str, valor: int) -> ItemResumo:
    return ItemResumo(rotulo=rotulo, valor=valor, formato="inteiro", exibicao=str(valor))


_FECHO: Final[str] = (
    "Os nomes dos pacientes estão só no relatório da tela: não escreva nomes de pacientes. "
    'Responda em 1 a 3 frases, como sugestão ("sugiro mover..."), e avise uma vez que tudo '
    "precisa ser confirmado com os pacientes e feito manualmente na planilha."
)


def plano_realocacao(foto: FotografiaPeriodo, plano: PlanoRealocacao) -> tuple[str, BlocoRelatorio]:
    """Texto (papéis e horários) e bloco (com os nomes) das opções encontradas."""
    minimo = min(len(opcao.deslocados) for opcao in plano.opcoes)
    melhor = (
        "a melhor não desloca ninguém"
        if minimo == 0
        else f"a melhor desloca {_n(minimo, 'paciente', 'pacientes')}"
    )
    avisos = avisos_do_plano(foto, plano.estatistica.busca_limitada)
    texto = [
        f"{TITULO_PLANO_REALOCACAO}: {_n(len(plano.opcoes), 'opção', 'opções')} para "
        f"{_terapia(plano.especialidade)} de {plano.duracao_minutos} minutos; {melhor}. "
        "Cada paciente deslocado mantém a mesma terapia, o mesmo profissional e a mesma duração.",
        *(_descricao_da_opcao(foto, numero, opcao) for numero, opcao in enumerate(plano.opcoes, 1)),
        *avisos,
        _FECHO,
    ]
    resumo = [
        _inteiro("Opções", len(plano.opcoes)),
        _inteiro("Mínimo de pacientes deslocados", minimo),
    ]
    por_opcao: list[dict[str, ValorSimples]] = [
        {
            "opcao": _rotulo_da_opcao(numero),
            "deslocados": len(opcao.deslocados),
            "mudam_de_dia": opcao.mudam_de_dia,
            "pedido": lugar_na_tela(foto, opcao.pedido.para),
        }
        for numero, opcao in enumerate(plano.opcoes, 1)
    ]
    linhas = _linhas(foto, plano)
    bloco = _bloco(
        foto,
        TITULO_PLANO_REALOCACAO,
        avisos,
        resumo,
        COLUNAS_PLANO_REALOCACAO,
        linhas,
        [
            tabela("Passos", COLUNAS_PLANO_REALOCACAO, linhas),
            tabela("Resumo por opção", COLUNAS_RESUMO_POR_OPCAO, por_opcao),
        ],
        agrupar=True,
    )
    return "\n".join(texto), bloco


def sem_solucao(
    foto: FotografiaPeriodo, resultado: SemSolucao, rotulo_do_pedido: str, regras: Regras
) -> tuple[str, BlocoRelatorio]:
    """Texto e bloco quando nenhuma combinação serve (com o encaixe mais perto, se houver)."""
    terapia = _terapia(resultado.especialidade)
    if resultado.motivo is MotivoSemSolucao.SEM_OFERTA:
        explicacao = (
            f"nenhum profissional de {terapia} atende nesses dias e horários na grade desta semana"
        )
    elif resultado.motivo is MotivoSemSolucao.SESSAO_EM_GRUPO:
        explicacao = "o atendimento pedido é uma sessão em grupo, que não é movida automaticamente"
    else:
        explicacao = (
            f"nenhuma combinação em até {regras.max_deslocados} deslocamentos mantendo a "
            "terapia, o profissional e a duração de cada paciente"
        )
    avisos = avisos_do_plano(foto, resultado.estatistica.busca_limitada)
    texto = [
        f"{TITULO_PLANO_REALOCACAO}: não encontrei opção para {terapia} de "
        f"{resultado.duracao_minutos} minutos: {explicacao}."
    ]
    linhas: list[dict[str, ValorSimples]] = []
    alternativa = resultado.alternativa_fora_da_janela
    if alternativa is not None:
        texto.append(
            "O horário livre mais perto, fora da janela pedida e sem deslocar ninguém: "
            f"{_lugar_no_texto(foto, alternativa)}."
        )
        linhas.append(
            {
                "opcao": "Fora da janela pedida",
                "passo": 1,
                "paciente": rotulo_do_pedido,
                "terapia": terapia,
                "profissional": foto.nome_profissional(alternativa.profissional_id),
                "de": _DE_PACIENTE_NOVO,
                "para": lugar_na_tela(foto, alternativa),
                "observacao": "fora da janela pedida; sem deslocar ninguém",
            }
        )
    texto.extend(avisos)
    texto.append("Não escreva nomes de pacientes; responda em 1 a 3 frases.")
    bloco = _bloco(
        foto,
        f"{TITULO_PLANO_REALOCACAO}: nenhuma opção",
        [f"Sem opção: {explicacao}.", *avisos],
        [_inteiro("Opções", 0)],
        COLUNAS_PLANO_REALOCACAO,
        linhas,
        [tabela("Passos", COLUNAS_PLANO_REALOCACAO, linhas)],
        agrupar=True,
    )
    return "\n".join(texto), bloco


# ---------------------------------------------------------------- pergunta


def _candidata_no_texto(foto: FotografiaPeriodo, candidata: Candidata) -> str:
    return (
        f"candidata {candidata.numero}: {_terapia(candidata.especialidade)} com "
        f"{foto.nome_profissional(candidata.profissional_id)}, "
        f"{_dias_da_semana(candidata.dias).lower()}"
    )


_PERGUNTAS: Final[dict[MotivoPergunta, str]] = {
    MotivoPergunta.NOME_AMBIGUO: ("Há mais de um paciente na grade com esse nome. Qual deles?"),
    MotivoPergunta.HOMONIMO_POSSIVEL: (
        "Esse nome aparece em dois lugares no mesmo horário na grade: pode haver duas "
        "pessoas com o mesmo nome. Qual delas?"
    ),
    MotivoPergunta.QUAL_ATENDIMENTO: (
        "Esse paciente tem mais de um atendimento na semana. Qual deles mudar?"
    ),
    MotivoPergunta.CANDIDATA_INVALIDA: "Não há opção com esse número. Qual delas?",
    MotivoPergunta.MESMO_PACIENTE_DA_GRADE: (
        "Esse nome já aparece na grade desta semana. É a mesma pessoa?"
    ),
    MotivoPergunta.NAO_ENCONTRADO: (
        "Não encontrei esse nome na grade desta semana. É um paciente novo ou o nome está "
        "escrito de outro jeito?"
    ),
    MotivoPergunta.QUAL_HORARIO_DE_ORIGEM: (
        "Esse atendimento acontece em mais de um horário na semana. Qual deles mudar?"
    ),
    MotivoPergunta.DURACAO_DIFERENTE: "A duração pedida é diferente da atual. Qual vale?",
    MotivoPergunta.FALTA_ESPECIALIDADE: "Qual a especialidade do atendimento?",
    MotivoPergunta.FALTA_DURACAO: "Qual a duração do atendimento?",
    MotivoPergunta.FALTA_DIA: "Em que dia da semana?",
    MotivoPergunta.FALTA_HORARIO: "Em que horário, ou em que faixa de horário?",
}

_COMO_CONTINUAR: Final[dict[MotivoPergunta, str]] = {
    MotivoPergunta.NOME_AMBIGUO: "Com a resposta, chame de novo com candidata=N.",
    MotivoPergunta.HOMONIMO_POSSIVEL: "Com a resposta, chame de novo com candidata=N.",
    MotivoPergunta.QUAL_ATENDIMENTO: "Com a resposta, chame de novo com candidata=N.",
    MotivoPergunta.CANDIDATA_INVALIDA: "Com a resposta, chame de novo com candidata=N.",
    MotivoPergunta.MESMO_PACIENTE_DA_GRADE: (
        "Com a resposta, chame de novo com mesmo_paciente_da_grade (e candidata=N, se for "
        "a mesma pessoa)."
    ),
    MotivoPergunta.QUAL_HORARIO_DE_ORIGEM: (
        "Com a resposta, chame de novo com dia_origem e horario_origem."
    ),
}


def pergunta(foto: FotografiaPeriodo, resultado: Pergunta) -> tuple[str, BlocoRelatorio | None]:
    """A pergunta para quem pediu: texto sem nomes; bloco com os nomes das candidatas."""
    texto = [_PERGUNTAS[resultado.motivo]]
    if resultado.candidatas:
        citadas = resultado.candidatas[:MAX_CANDIDATAS_NO_TEXTO]
        resto = len(resultado.candidatas) - len(citadas)
        texto.append(
            "Opções (os nomes estão só no relatório da tela): "
            + "; ".join(_candidata_no_texto(foto, c) for c in citadas)
            + (f"; e mais {resto} na tela. Peça o nome completo." if resto else ".")
        )
    if resultado.origens:
        texto.append(
            "Horários desse atendimento: "
            + ", ".join(
                f"{_DIAS[lugar.data.weekday()]} das {_faixa(lugar)}" for lugar in resultado.origens
            )
            + "."
        )
    if resultado.duracao_atual is not None:
        texto.append(
            f"O atendimento atual dura {resultado.duracao_atual} minutos; a sugestão mantém a "
            "duração atual."
        )
    if continuar := _COMO_CONTINUAR.get(resultado.motivo):
        texto.append(continuar)
    texto.append(
        "Faça essa pergunta a quem pediu, em uma frase, sem escrever nomes de pacientes; "
        "não sugira nada antes da resposta."
    )
    if not resultado.candidatas:
        return "\n".join(texto), None
    linhas: list[dict[str, ValorSimples]] = [
        {
            "candidata": c.numero,
            "paciente": c.paciente,
            "terapia": _terapia(c.especialidade),
            "profissional": foto.nome_profissional(c.profissional_id),
            "dias": _dias_da_semana(c.dias),
        }
        for c in resultado.candidatas
    ]
    bloco = _bloco(
        foto,
        TITULO_PERGUNTA_REALOCACAO,
        [AVISO_HOMONIMOS],
        [_inteiro("Candidatas", len(linhas))],
        COLUNAS_PERGUNTA_REALOCACAO,
        linhas,
        [tabela("Candidatas", COLUNAS_PERGUNTA_REALOCACAO, linhas)],
        agrupar=False,
    )
    return "\n".join(texto), bloco
