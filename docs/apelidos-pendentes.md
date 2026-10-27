# Apelidos de profissionais

O RealocAI resolve o nome de profissional digitado na conversa
(`app/domain/nomes_profissionais.py`). Sem nenhuma configuração, ele já entende:

- acento e caixa: "DÉBORA" = Débora;
- primeiro nome e nome a mais: "Rossana Belfort" = Rossana; "Ana" lista Ana Paula e Ana
  Carolina e pede para escolher;
- grafia com letra dobrada ou y/i: "Assizely" = ASSIZELLY, "Rosana" = Rossana.

O que ele **não** adivinha é apelido: "beatriz" = Bia não se deduz do texto. Apelido só entra
em `PROFISSIONAIS_APELIDOS` (`app/domain/constants.py`) depois que a coordenação confirma, com a
origem anotada num comentário.

## Confirmados

| Apelido digitado | Profissional | Origem |
| --- | --- | --- |
| beatriz | Bia | Coordenadora, no chat real: "a beatriz seria bia". |

## Pendentes de confirmação

Nenhum, por enquanto. Não há outro apelido citado no código, na planilha ou nas conversas
recebidas até agora.

Quando aparecer um nome que o RealocAI não reconheceu e que parece apelido, anote aqui e pergunte
à coordenadora antes de colocar no código:

| Apelido digitado | Quem parece ser | Onde apareceu | Confirmado? |
| --- | --- | --- | --- |
| | | | |

## Como confirmar um apelido

1. A coordenadora confirma ("o X é a Y").
2. Acrescente em `PROFISSIONAIS_APELIDOS` o apelido normalizado (sem acento, minúsculas, palavras
   unidas por hífen) apontando para o id da profissional (o nome dela normalizado do mesmo
   jeito), com um comentário de origem.
3. Mova a linha da tabela de pendentes para a de confirmados.
4. Se existir na semana alguém que se chama de fato como o apelido, essa pessoa vence: o apelido
   só vale quando não há ninguém com aquele nome.
