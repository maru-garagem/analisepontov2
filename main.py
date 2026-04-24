"""
Entrypoint da aplicação FastAPI.
"""
from __future__ import annotations

import logging

import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.routes import auth, empresas, esqueletos, extract, health, history
from app.utils.errors import PontoExtractError
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


@app.exception_handler(PontoExtractError)
async def _handle_domain_error(_: Request, exc: PontoExtractError):
    return JSONResponse(
        status_code=exc.http_status,
        content={"detail": str(exc) or exc.code, "code": exc.code},
    )


_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.tailwindcss.com https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://cdn.jsdelivr.net; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "connect-src 'self' https://cdn.jsdelivr.net; "
    # Permitir iframe interno com blob: (preview de PDF usa viewer nativo do navegador).
    "frame-src 'self' blob:; "
    "child-src 'self' blob:; "
    "frame-ancestors 'none';"
)


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


# Registrado DEPOIS do auth_gate para ficar no topo do stack (outer) —
# garante que 401s e erros também recebam os security headers.
@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
    )
    response.headers.setdefault("Content-Security-Policy", _CSP)
    if settings.is_prod:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
        )
    return response


app.include_router(health.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(extract.router, prefix="/api")
app.include_router(empresas.router, prefix="/api")
app.include_router(esqueletos.router, prefix="/api")
app.include_router(history.router, prefix="/api")


@app.on_event("startup")
def _sweeper_startup() -> None:
    """
    Limpa órfãos ao subir: Processamentos stuck em `em_processamento` ou
    `aguardando_cadastro` ficariam travados para sempre sem esta varredura.
    """
    from app.database import SessionLocal
    from app.services.sweeper import varrer_orfaos

    db = SessionLocal()
    try:
        n = varrer_orfaos(db)
        if n:
            logging.getLogger(__name__).info("startup_sweep_removeu=%d", n)
    except Exception:
        logging.getLogger(__name__).exception("startup_sweep_falhou")
    finally:
        db.close()

# Static files (frontend): servidos a partir de ./static. O HTML é público;
# as APIs /api/* é que são protegidas pelo middleware acima.
_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")
