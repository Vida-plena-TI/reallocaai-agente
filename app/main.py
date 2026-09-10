"""Ponto de entrada da API do RealocAI."""

from fastapi import FastAPI

from app.config import get_settings

settings = get_settings()

app = FastAPI(
    title="RealocAI",
    description="Agente de IA para otimização de agenda de clínica multidisciplinar.",
    version="0.1.0",
)


@app.get("/health")
def health() -> dict[str, str]:
    """Verificação de disponibilidade do serviço."""
    return {"status": "ok", "service": "realocai"}
