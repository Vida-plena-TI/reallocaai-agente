"""Os exemplos de contrato em docs/exemplos-blocos/ acompanham o modelo atual."""

from scripts.gerar_exemplos_blocos import DIRETORIO, gerar_exemplos
from tests.support import agenda_clinica
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


def test_exemplos_gerais_so_citam_paciente_na_agenda_do_paciente() -> None:
    proibidos = [*agenda_clinica.NOMES_PACIENTES, *agenda_clinica.IDS_PACIENTES]
    for arquivo, conteudo in gerar_exemplos().items():
        if arquivo == "agenda_paciente.json":
            outros = [n for n in proibidos if "Alfa" not in n and "alfa" not in n]
            assert "Paciente Ficticio Alfa" in conteudo
            assert all(nome not in conteudo for nome in outros)
            assert agenda_clinica.IDS_PACIENTES[0] not in conteudo
        else:
            assert all(nome not in conteudo for nome in proibidos), arquivo


def test_ha_exemplo_de_cada_tipo_de_bloco() -> None:
    import json

    tipos = {
        json.loads(conteudo)["tipo"]
        for arquivo, conteudo in gerar_exemplos().items()
        if not arquivo.endswith(".schema.json")
    }
    assert tipos == {
        "ocupacao_profissional",
        "pacientes_por_profissional",
        "ocupacao_agregada",
        "disponibilidade",
        "convenio",
        "agenda_paciente",
        "qualidade_agenda",
        "tabela_generica",
    }
