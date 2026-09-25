"""Regras estáticas da clínica.

São valores fixos do negócio (expediente, pausa, granularidade da agenda, meta
de ocupação e as convenções de preenchimento da planilha). Dados que variam por
unidade — como a capacidade de uma sala — não moram aqui: são atributos das
entidades.
"""

from datetime import time
from typing import Final

from app.domain.enums import Convenio, Especialidade

#: Início do expediente da clínica.
HORARIO_ABERTURA: Final[time] = time(7, 0)

#: Fim do expediente da clínica (nenhum atendimento pode terminar depois disso).
HORARIO_FECHAMENTO: Final[time] = time(18, 0)

#: Início da pausa geral: nenhum atendimento acontece nesta janela.
INICIO_PAUSA: Final[time] = time(12, 0)

#: Fim da pausa geral.
FIM_PAUSA: Final[time] = time(13, 0)

#: Granularidade da agenda: toda janela de atendimento tem 30 minutos.
DURACAO_SLOT_MINUTOS: Final[int] = 30

#: Piso padrão de horário ao sugerir um encaixe: 7:00 e 7:30 só entram na busca
#: quando um piso menor é explicitamente solicitado.
HORARIO_PREFERENCIAL_PADRAO: Final[time] = time(8, 0)

#: Meta de ocupação por sala (80% do expediente útil).
META_OCUPACAO_POR_SALA: Final[float] = 0.8

#: Convênio de cada cor de fonte usada nas células de paciente da planilha.
#:
#: Mais de um hex aponta para o mesmo convênio porque a planilha é preenchida à
#: mão e os tons próximos foram usados como se fossem a mesma cor.
CORES_CONVENIO: Final[dict[str, Convenio]] = {
    "#0000FF": Convenio.SULAMERICA,
    "#FF0000": Convenio.KLINI_SAUDE,
    "#CC0000": Convenio.KLINI_SAUDE,
    "#274E13": Convenio.UNIMED,
    "#38761D": Convenio.UNIMED,
    "#6AA84F": Convenio.UNIMED,
    "#9900FF": Convenio.PARTICULAR,
}

#: Fonte preta: o atendimento foi encaixado na agenda, mas ainda aguarda a
#: confirmação do responsável (plano ou família) — por isso o convênio ainda não
#: está identificado na célula.
COR_AGUARDANDO_AUTORIZACAO: Final[str] = "#000000"

#: Estagiários que dividem a coluna com o profissional responsável (`Aline/Raíssa`).
#: Eles não são profissionais alocáveis: a comparação é sempre feita com o nome
#: normalizado (minúsculo e sem acento).
PROFISSIONAIS_IGNORAR: Final[frozenset[str]] = frozenset({"raissa", "marley", "vitoria"})

#: Sala de colunas cujo cabeçalho de merge veio vazio na planilha.
#:
#: São erros de preenchimento já confirmados com a clínica que não serão
#: corrigidos na fonte por ora: a coluna tem profissional, tem atendimento e
#: perde a sala só porque a célula mesclada do cabeçalho ficou em branco. A
#: chave é (nome da aba normalizado, profissional normalizado) — o mesmo
#: profissional pode ocupar salas diferentes em dias diferentes.
#:
#: Isto é uma exceção pontual, não um caminho geral: coluna sem sala que não
#: esteja aqui continua sendo ignorada pelo parser.
MAPA_SALA_FALLBACK: Final[dict[tuple[str, str], str]] = {
    ("segunda", "aline"): "Sala 12",
    ("terca", "sophia"): "Sala 2",
    ("terca", "rossana"): "Sala 12",
}

#: Especialidade de quem nunca aparece com ela escrita na planilha.
#:
#: A célula do profissional traz só o nome em todos os blocos de todos os dias,
#: então não há de onde inferir. A chave é o nome normalizado.
MAPA_ESPECIALIDADE_FALLBACK: Final[dict[str, Especialidade]] = {
    "glaucia": Especialidade.PSICOMOTRICIDADE,
    "maria-fernanda": Especialidade.FONOAUDIOLOGIA,
}

#: Grafias divergentes que a clínica confirmou serem a mesma pessoa.
#:
#: Mapeia o id normalizado alternativo para o canônico. Sem isso a mesma
#: profissional viraria dois registros na saída do parser, cada um com metade
#: da agenda dela.
MAPA_ALIAS_PROFISSIONAL: Final[dict[str, str]] = {
    "natieli-est": "natieli",
    "talyta-ayla": "talita-aylla",
}
