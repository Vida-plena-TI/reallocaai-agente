"""Teste de CORS: localhost liberado automaticamente em desenvolvimento (Fase 6a).

O `CORSMiddleware` é configurado uma única vez, na importação de `app.main`,
a partir de `settings` lida naquele momento — por isso este teste não usa a
fixture `client` (que sobrescreve `get_settings` só para as dependencies de
rota, sem efeito sobre um middleware já registrado) e depende de
`APP_ENV=development` no `.env` local, que é o padrão do projeto.
"""

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


def test_localhost_e_liberado_automaticamente_em_desenvolvimento() -> None:
    settings = get_settings()
    assert settings.app_env == "development", "Teste assume APP_ENV=development no .env local."

    client = TestClient(app)
    resposta = client.get("/health", headers={"Origin": "http://localhost:5173"})

    assert resposta.status_code == 200
    assert resposta.headers["access-control-allow-origin"] == "http://localhost:5173"
