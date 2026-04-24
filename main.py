"""
Entrypoint da aplicação FastAPI.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.routes import auth, extract, health
from app.utils.security import SESSION_COOKIE_NAME, verify_session_token

settings = get_settings()

logging.basicConfig(
    level=settings.LOG_LEVEL.upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = FastAPI(
    title="PontoExtract v2",
    description="Extração de cartões de ponto via esqueletos aprendidos por empresa.",
    version="2.0.0",
    docs_url="/docs" if settings.is_dev else None,
    redoc_url="/redoc" if settings.is_dev else None,
    openapi_url="/openapi.json" if settings.is_dev else None,
)

if settings.allowed_origins_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )


_PUBLIC_API_PATHS = {"/api/health"}
_PUBLIC_API_PREFIXES = ("/api/auth/",)


@app.middleware("http")
async def auth_gate(request: Request, call_next):
    """
    Protege todas as rotas /api/* exceto health e auth. Rotas não-/api/*
    (static, docs em dev, etc) passam livremente.
    """
    path = request.url.path
    if not path.startswith("/api/"):
        return await call_next(request)
    if path in _PUBLIC_API_PATHS or any(path.startswith(p) for p in _PUBLIC_API_PREFIXES):
        return await call_next(request)

    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token and verify_session_token(token) is not None:
        return await call_next(request)

    return JSONResponse(
        status_code=401,
        content={"detail": "Não autenticado."},
    )


app.include_router(health.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(extract.router, prefix="/api")
