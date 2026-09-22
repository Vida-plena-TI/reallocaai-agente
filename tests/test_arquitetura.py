"""Fronteiras arquiteturais (Fase 8).

Analisa os imports de cada arquivo `.py` de `app/` via `ast` (nenhum módulo é
importado/executado de verdade) e confirma duas coisas:

1. Direção de dependência entre camadas: cada camada só importa das camadas
   listadas como permitidas, nunca "para cima".
2. Isolamento de bibliotecas externas: bibliotecas de terceiros específicas de
   uma camada (langchain* em `app/ai`, fastapi/starlette em `app/api`,
   gspread/google.auth em `app/data_sources`, resend em `app/reports`) não
   vazam para o resto do código.

`scripts/` fica fora do escopo: nem é `app/`, nem é varrido aqui.
"""

import ast
from pathlib import Path
from typing import NamedTuple

import pytest

RAIZ_APP = Path(__file__).resolve().parent.parent / "app"

_CAMADAS = ("domain", "engine", "data_sources", "ai", "reports", "api")

#: Para cada camada, o conjunto de outras camadas de `app.*` que ela pode importar.
_PERMITIDO_POR_CAMADA: dict[str, frozenset[str]] = {
    "domain": frozenset(),
    "engine": frozenset({"domain"}),
    "data_sources": frozenset({"domain"}),
    "ai": frozenset({"domain", "engine", "data_sources"}),
    "reports": frozenset({"domain", "engine", "data_sources"}),
    "api": frozenset({"domain", "engine", "data_sources", "ai", "reports"}),
}

#: Prefixo do módulo importado -> camada (relativa a `app/`) onde ele é permitido.
#: `app/main.py` é liberado à parte para fastapi/starlette, é o entrypoint da API.
_BIBLIOTECAS_RESTRITAS: dict[str, str] = {
    "langchain": "ai",
    "fastapi": "api",
    "starlette": "api",
    "gspread": "data_sources",
    "google.oauth2": "data_sources",
    "google.auth": "data_sources",
    "resend": "reports",
}

#: Exceções revisadas e aceitas (Fase 8) ao isolamento de bibliotecas externas:
#: cada uma é reuso deliberado de um tipo estável do `langchain_core` na borda
#: da API, não acoplamento acidental. `ArmazenamentoConversas`
#: (`app/api/sessoes.py`) guarda o histórico da conversa como
#: `list[BaseMessage]` porque é exatamente o formato que
#: `app.ai.agente.perguntar` já espera receber e devolver; `obter_chat_model`
#: (`app/api/dependencies.py`) só usa `BaseChatModel` como tipo de retorno da
#: injeção de dependência do FastAPI; `conversar_com_agente`
#: (`app/api/routes.py`) monta o `HumanMessage` de entrada e inspeciona
#: `BaseMessage` para renderizar o histórico em `GET /agenda/chat/{id}`. Criar
#: um tipo de mensagem próprio da API e converter em cada chamada seria um
#: refactor maior, sem ganho real agora.
#: TODO: revisitar se algum dia precisarmos de um formato de mensagem
#: persistível fora de memória (hoje `ArmazenamentoConversas` é só em memória,
#: então `BaseMessage` nunca precisa ser serializado).
_EXCECOES_DE_ISOLAMENTO: frozenset[tuple[str, str]] = frozenset(
    {
        ("api/sessoes.py", "langchain_core.messages"),
        ("api/dependencies.py", "langchain_core.language_models"),
        ("api/routes.py", "langchain_core.language_models"),
        ("api/routes.py", "langchain_core.messages"),
    }
)


class _Violacao(NamedTuple):
    arquivo: str
    linha: int
    mensagem: str

    def __str__(self) -> str:
        return f"{self.arquivo}:{self.linha}: {self.mensagem}"


def _arquivos_py() -> list[Path]:
    return sorted(RAIZ_APP.rglob("*.py"))


def _modulo_do_arquivo(caminho: Path) -> str:
    """Nome do módulo Python de um arquivo dentro de `app/` (para resolver imports relativos)."""
    relativo = caminho.relative_to(RAIZ_APP.parent).with_suffix("")
    partes = list(relativo.parts)
    if partes[-1] == "__init__":
        partes = partes[:-1]
    return ".".join(partes)


def _nomes_importados(caminho: Path) -> list[tuple[str, int]]:
    """`[(nome_absoluto_do_modulo, linha), ...]` de todo import do arquivo."""
    arvore = ast.parse(caminho.read_text(encoding="utf-8"), filename=str(caminho))
    modulo_atual = _modulo_do_arquivo(caminho)
    pacote_atual = modulo_atual.rsplit(".", 1)[0] if "." in modulo_atual else modulo_atual

    resultado: list[tuple[str, int]] = []
    for node in ast.walk(arvore):
        if isinstance(node, ast.Import):
            for alias in node.names:
                resultado.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                if node.module is not None:
                    resultado.append((node.module, node.lineno))
                continue
            # Import relativo (`from . import x` / `from ..y import z`): resolve
            # contra o pacote do próprio arquivo. Nenhum import de `app/` usa
            # isso hoje, mas o teste precisa continuar correto se algum passar a usar.
            partes_base = pacote_atual.split(".")
            subir = node.level - 1
            base = ".".join(partes_base[: len(partes_base) - subir]) if subir else pacote_atual
            alvo = f"{base}.{node.module}" if node.module else base
            resultado.append((alvo, node.lineno))
    return resultado


def _camada_do_modulo(nome_do_modulo: str) -> str | None:
    """Camada de `app.*` a que o módulo importado pertence, ou `None` quando não
    é uma das seis camadas do projeto (ex.: `app.config`, `app.main`, ou algo
    fora de `app`, que ficam fora da checagem de direção de dependência)."""
    partes = nome_do_modulo.split(".")
    if len(partes) < 2 or partes[0] != "app":
        return None
    candidata = partes[1]
    return candidata if candidata in _CAMADAS else None


def _biblioteca_restrita(nome_do_modulo: str) -> str | None:
    """Nome-base da biblioteca restrita que `nome_do_modulo` importa, ou `None`."""
    for prefixo in _BIBLIOTECAS_RESTRITAS:
        if (
            nome_do_modulo == prefixo
            or nome_do_modulo.startswith(f"{prefixo}.")
            or nome_do_modulo.startswith(f"{prefixo}_")
        ):
            return prefixo
    return None


def _camada_do_arquivo(caminho: Path) -> str | None:
    """A camada (pasta de 1º nível dentro de `app/`) a que o arquivo pertence,
    ou `None` para arquivos soltos na raiz de `app/` (`main.py`, `config.py`)."""
    primeira = caminho.relative_to(RAIZ_APP).parts[0]
    return primeira if primeira in _CAMADAS else None


def test_direcao_de_dependencia_entre_camadas() -> None:
    violacoes: list[_Violacao] = []
    for caminho in _arquivos_py():
        camada_do_arquivo = _camada_do_arquivo(caminho)
        if camada_do_arquivo is None:
            continue
        permitido = _PERMITIDO_POR_CAMADA[camada_do_arquivo]
        for nome_do_modulo, linha in _nomes_importados(caminho):
            camada_alvo = _camada_do_modulo(nome_do_modulo)
            if camada_alvo is None or camada_alvo == camada_do_arquivo:
                continue
            if camada_alvo not in permitido:
                violacoes.append(
                    _Violacao(
                        arquivo=str(caminho.relative_to(RAIZ_APP.parent)),
                        linha=linha,
                        mensagem=(
                            f"app.{camada_do_arquivo} não pode importar de app.{camada_alvo} "
                            f"(import {nome_do_modulo!r})"
                        ),
                    )
                )

    if violacoes:
        detalhe = "\n".join(str(violacao) for violacao in violacoes)
        pytest.fail(f"Violações de direção de dependência entre camadas:\n{detalhe}")


def test_isolamento_de_bibliotecas_externas_por_camada() -> None:
    violacoes: list[_Violacao] = []
    for caminho in _arquivos_py():
        e_main = caminho.relative_to(RAIZ_APP) == Path("main.py")
        camada_do_arquivo = _camada_do_arquivo(caminho)
        for nome_do_modulo, linha in _nomes_importados(caminho):
            biblioteca = _biblioteca_restrita(nome_do_modulo)
            if biblioteca is None:
                continue
            camada_permitida = _BIBLIOTECAS_RESTRITAS[biblioteca]
            caminho_relativo = caminho.relative_to(RAIZ_APP).as_posix()
            liberado = (
                camada_do_arquivo == camada_permitida
                or (e_main and camada_permitida == "api")
                or (caminho_relativo, nome_do_modulo) in _EXCECOES_DE_ISOLAMENTO
            )
            if not liberado:
                violacoes.append(
                    _Violacao(
                        arquivo=str(caminho.relative_to(RAIZ_APP.parent)),
                        linha=linha,
                        mensagem=(
                            f"{biblioteca!r} só é permitido em app/{camada_permitida}/ "
                            f"(import {nome_do_modulo!r})"
                        ),
                    )
                )

    if violacoes:
        detalhe = "\n".join(str(violacao) for violacao in violacoes)
        pytest.fail(f"Violações de isolamento de bibliotecas externas por camada:\n{detalhe}")
