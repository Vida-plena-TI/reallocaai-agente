"""Testes de `normalizar_id`."""

import unicodedata

import pytest

from app.domain import normalizar_id

#: Nomes com acento e cedilha e o id que a versão anterior de `normalizar_id`
#: já gerava para a forma composta (NFC) — regressão: os ids não podem mudar.
IDS_CONHECIDOS = [
    ("João Víctor", "joao-victor"),
    ("José Felipe", "jose-felipe"),
    ("Conceição Araújo", "conceicao-araujo"),
    ("Maria Yasmin (online)", "maria-yasmin-online"),
    ("Luíza Gonçalves", "luiza-goncalves"),
    ("Ângela Müller", "angela-muller"),
    ("Heitor Brandão", "heitor-brandao"),
    ("Raíssa", "raissa"),
    ("D'Ávila Sant'Anna", "d-avila-sant-anna"),
    ("Ana-Clara Peçanha", "ana-clara-pecanha"),
    ("Íris Côrtes", "iris-cortes"),
    ("Théo Souza", "theo-souza"),
]


@pytest.mark.parametrize(("nome", "id_esperado"), IDS_CONHECIDOS)
def test_nome_composto_gera_o_mesmo_id_de_antes(nome: str, id_esperado: str) -> None:
    assert normalizar_id(unicodedata.normalize("NFC", nome)) == id_esperado


@pytest.mark.parametrize(("nome", "id_esperado"), IDS_CONHECIDOS)
def test_formas_nfc_e_nfd_geram_o_mesmo_id(nome: str, id_esperado: str) -> None:
    nfc = unicodedata.normalize("NFC", nome)
    nfd = unicodedata.normalize("NFD", nome)
    assert nfc != nfd or nome.isascii()

    assert normalizar_id(nfd) == normalizar_id(nfc) == id_esperado


def test_acento_combinante_nao_vira_separador() -> None:
    assert normalizar_id("Joa\u0303o") == "joao"
