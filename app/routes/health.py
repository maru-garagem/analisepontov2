"""
Health check da aplicação. Propositalmente independente de banco e de
serviços externos — serve como liveness probe do Railway. Retorna 200
enquanto o processo estiver de pé.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "env": settings.ENV,
        "version": "2.0.0",
    }
