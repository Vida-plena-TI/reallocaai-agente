"""Os exemplos de contrato em docs/exemplos-blocos/ acompanham o modelo atual."""

from scripts.gerar_exemplos_blocos import DIRETORIO, gerar_exemplos
from tests.support.relatorios import ANA, BIA, NOMES_PACIENTES


def test_exemplos_versionados_estao_atualizados() -> None:
    for arquivo, conteudo in gerar_exemplos().items():
        caminho = DIRETORIO / arquivo
        assert caminho.read_text(encoding="utf-8") == conteudo, (
            f"{caminho.name} desatualizado: rode uv run python scripts/gerar_exemplos_blocos.py"
        )


def test_exemplos_so_tem_dados_ficticios() -> None:
    for conteudo in gerar_exemplos().values():
        assert all(nome not in conteudo for nome in NOMES_PACIENTES)
        assert ANA.id not in conteudo and BIA.id not in conteudo
