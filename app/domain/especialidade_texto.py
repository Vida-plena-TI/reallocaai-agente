"""Reconhecimento tolerante da especialidade escrita em texto livre.

A mesma tabela serve ao parser da planilha (célula "TO - Rossana") e às tools
da IA (o usuário escreve "psicóloga", "fono", "TO"), para as duas camadas
nunca discordarem sobre o que cada grafia significa.
"""

import re
from typing import Final

from app.domain.enums import Especialidade
from app.domain.normalizacao import normalizar_id

#: Como a especialidade aparece escrita, testado sobre texto sem acento e em
#: minúsculas. A ordem importa: `psicomotricidade` e `psicopedagogia` precisam
#: ser testadas antes de `psico`, senão as duas cairiam em psicologia. Fisio e
#: Nutri viram psicomotricidade e terapia alimentar (decisão da clínica: não
#: existem como especialidades à parte).
PADROES_ESPECIALIDADE: Final[tuple[tuple[re.Pattern[str], Especialidade], ...]] = (
    (
        re.compile(r"\b(?:psicomotricidade|psicomotora|psicomotor|psicomo|fisio\w*)\b"),
        Especialidade.PSICOMOTRICIDADE,
    ),
    (
        re.compile(r"\b(?:psicopedagogia|psicopedagoga|psicopedagogo|neuroppg|ppg)\b"),
        Especialidade.PSICOPEDAGOGIA,
    ),
    (
        re.compile(r"\b(?:psicologia|psicologa|psicologo|psicol|psico)\b"),
        Especialidade.PSICOLOGIA,
    ),
    (
        re.compile(r"\b(?:fonoaudiologia|fonoaudiologa|fonoaudiologo|fonoaudio|fono)\b"),
        Especialidade.FONOAUDIOLOGIA,
    ),
    (
        re.compile(r"\b(?:musicoterapia|musicoterapeuta|musicoterapeuto|musico)\b"),
        Especialidade.MUSICOTERAPIA,
    ),
    (
        re.compile(r"\b(?:nutricionista|nutricao|nutri)\b"),
        Especialidade.TERAPIA_ALIMENTAR,
    ),
    (
        re.compile(r"\b(?:terapia\s+ocupacional|to)\b"),
        Especialidade.TERAPIA_OCUPACIONAL,
    ),
)


def reconhecer_especialidade(texto: str) -> Especialidade | None:
    """Especialidade citada em `texto`, sem diferenciar acento e caixa; `None` se nenhuma.

    Aceita o nome da especialidade (`"Terapia Alimentar"`, `"terapia_alimentar"`)
    e as grafias de `PADROES_ESPECIALIDADE` (`"psicóloga"`, `"fono"`, `"TO"`).
    """
    palavras = normalizar_id(texto)
    if not palavras:
        return None
    for especialidade in Especialidade:
        if normalizar_id(especialidade.value) == palavras:
            return especialidade
    chave = palavras.replace("-", " ")
    for padrao, especialidade in PADROES_ESPECIALIDADE:
        if padrao.search(chave):
            return especialidade
    return None
