"""Os 422 de validação nunca devolvem o valor recebido, em nenhuma rota.

O corpo de `/agenda/chat` é texto de conversa, que pode ter nome de paciente;
o de `/relatorios/gerar` pode ter o nome pedido em `agenda_paciente`. O
handler global (`app.main.responder_erro_de_validacao`) só devolve `loc`,
`msg` e `type` de cada erro.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.api.conftest import HEADERS_AUTENTICADOS

NOME = "Paciente Ficticio Alfa"

PEDIDOS_INVALIDOS: list[tuple[str, str, dict[str, Any]]] = [
    ("post", "/agenda/chat", {"json": {"mensagem": [f"{NOME} tem vaga amanhã?"]}}),
    ("post", "/agenda/chat", {"json": {"mensagem": NOME, "renderiza_relatorios": NOME}}),
    ("post", "/agenda/chat", {"json": {"mensagem": NOME, "conversa_id": {"nome": NOME}}}),
    (
        "post",
        "/relatorios/gerar",
        {"json": {"relatorio": "agenda_paciente", "paciente": NOME, "limite": 0}},
    ),
    ("post", "/relatorios/gerar", {"json": {"relatorio": NOME}}),
    ("get", "/agenda/disponibilidade", {"params": {"data": NOME}}),
    ("get", "/agenda/ocupacao", {"params": {"data": NOME}}),
    ("get", "/relatorios/catalogo", {"params": {"data": NOME}}),
]


@pytest.mark.parametrize(
    ("metodo", "caminho", "argumentos"),
    PEDIDOS_INVALIDOS,
    ids=[f"{m} {c} {i}" for i, (m, c, _) in enumerate(PEDIDOS_INVALIDOS)],
)
def test_422_nao_devolve_o_valor_recebido(
    client: TestClient, metodo: str, caminho: str, argumentos: dict[str, Any]
) -> None:
    resposta = getattr(client, metodo)(caminho, headers=HEADERS_AUTENTICADOS, **argumentos)

    assert resposta.status_code == 422
    assert NOME not in resposta.text
    assert "Ficticio" not in resposta.text
    for erro in resposta.json()["detail"]:
        assert set(erro) == {"loc", "msg", "type"}


def test_422_continua_dizendo_onde_e_o_que_esta_errado(client: TestClient) -> None:
    resposta = client.post("/agenda/chat", json={"mensagem": [NOME]}, headers=HEADERS_AUTENTICADOS)

    assert resposta.json()["detail"] == [
        {
            "loc": ["body", "mensagem"],
            "msg": "Input should be a valid string",
            "type": "string_type",
        }
    ]
