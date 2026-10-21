"""O RealocAI não envia e-mails: a rota de envio não existe mais (mudança incompatível)."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.api.conftest import HEADERS_AUTENTICADOS


@pytest.mark.parametrize("corpo", [{}, {"relatorios": ["ocupacao"]}])
def test_post_relatorio_enviar_devolve_404(client: TestClient, corpo: dict[str, object]) -> None:
    resposta = client.post("/relatorio/enviar", json=corpo, headers=HEADERS_AUTENTICADOS)

    assert resposta.status_code == 404


def test_nenhuma_rota_de_email() -> None:
    caminhos = {getattr(rota, "path", "") for rota in app.routes}

    assert not [caminho for caminho in caminhos if "enviar" in caminho or "email" in caminho]
