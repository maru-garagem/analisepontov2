"""
Dependências compartilhadas de rotas FastAPI.
"""
from __future__ import annotations

from typing import Generator

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.utils.security import SESSION_COOKIE_NAME, verify_session_token


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_auth(request: Request) -> dict:
    """
    Dependência FastAPI que valida o cookie de sessão.
    Retorna o payload (`{"sid": ..., "iat": ...}`) ou levanta 401.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Não autenticado.",
        )
    payload = verify_session_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sessão inválida ou expirada.",
        )
    return payload


def session_id_short(auth: dict) -> str:
    """Últimos 8 chars do session_id — usado como `criada_por` nos modelos."""
    sid = auth.get("sid", "")
    return sid[-8:] if len(sid) >= 8 else sid
