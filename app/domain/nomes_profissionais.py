"""Resolução do nome de profissional digitado na conversa, e os três estados da busca.

O nome digitado raramente é o da planilha: vem sem acento, em caixa alta, só
com o primeiro nome, com outra grafia ("ASSIZELLY" x "Assizely") ou com um
apelido ("beatriz" = Bia). A resolução tenta, em ordem, e para no primeiro
nível que encontra alguém:

1. o id exato, depois de trocar grafias confirmadas (`MAPA_ALIAS_PROFISSIONAL`)
   pelo canônico; sem ele, o apelido confirmado (`PROFISSIONAIS_APELIDOS`). Quem
   se chama de fato "Beatriz" na semana vence o apelido "beatriz" = Bia;
2. o nome inteiro igual pela chave de comparação (`chave_de_comparacao`: sem
   acento nem caixa, letras dobradas juntadas e y = i);
3. a sequência de palavras de um lado é prefixo da do outro, também pela
   chave ("Rossana Belfort" encontra Rossana; "Ana" encontra Ana Paula e Ana
   Carolina). A palavra precisa estar inteira: "João Pe" não é "João Pedro".

Mais de um candidato no mesmo nível é ambiguidade: quem chama devolve a lista
e pede para escolher, nunca escolhe sozinho. A chave serve só para COMPARAR;
o nome exibido é sempre o da planilha.
"""

import re
from collections.abc import Mapping
from enum import StrEnum

from app.domain.constants import MAPA_ALIAS_PROFISSIONAL, PROFISSIONAIS_APELIDOS
from app.domain.normalizacao import normalizar_id


class EstadoProfissional(StrEnum):
    """Por que a busca por um profissional não trouxe vaga."""

    #: Ninguém com esse nome na grade da semana.
    INEXISTENTE_NA_GRADE = "inexistente_na_grade"
    #: Existe na semana, mas não está escalado no dia pedido.
    NAO_ATENDE_NO_DIA = "nao_atende_no_dia"
    #: Está escalado no dia, mas sem vaga livre na janela pedida.
    ATENDE_SEM_VAGA_NA_JANELA = "atende_sem_vaga_na_janela"


def chave_de_comparacao(texto: str) -> str:
    """`ASSIZELLY` e `Assizely` -> `asizeli`: só para comparar, nunca para exibir."""
    chave = normalizar_id(texto).replace("y", "i")
    return re.sub(r"([a-z])\1+", r"\1", chave)


def _id_sem_grafia_antiga(texto: str) -> str:
    alvo = normalizar_id(texto)
    return MAPA_ALIAS_PROFISSIONAL.get(alvo, alvo)


def _uma_sequencia_e_prefixo_da_outra(palavras: list[str], outras: list[str]) -> bool:
    menor, maior = sorted((palavras, outras), key=len)
    return bool(menor) and maior[: len(menor)] == menor


def resolver_nome(nomes_por_id: Mapping[str, str], texto: str) -> tuple[str, ...]:
    """Ids que casam com `texto`, do primeiro nível que achou alguém (vazio: ninguém).

    `nomes_por_id`: id -> nome dos profissionais da semana.
    """
    alvo = _id_sem_grafia_antiga(texto)
    if not alvo:
        return ()
    if alvo in nomes_por_id:
        return (alvo,)
    apelido_de = PROFISSIONAIS_APELIDOS.get(alvo)
    if apelido_de is not None and apelido_de in nomes_por_id:
        return (apelido_de,)
    chave_alvo = chave_de_comparacao(alvo)
    chaves = {
        profissional_id: chave_de_comparacao(profissional_id) for profissional_id in nomes_por_id
    }
    iguais = tuple(sorted(i for i, chave in chaves.items() if chave == chave_alvo))
    if iguais:
        return iguais
    palavras_alvo = chave_alvo.split("-")
    return tuple(
        sorted(
            i
            for i, chave in chaves.items()
            if _uma_sequencia_e_prefixo_da_outra(palavras_alvo, chave.split("-"))
        )
    )
