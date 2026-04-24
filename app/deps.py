"""
Dependências compartilhadas de rotas FastAPI. Mantenha este arquivo fino:
somente dependências reutilizáveis (sessão de banco, autenticação, etc).
"""
from __future__ import annotations

from typing import Generator

from sqlalchemy.orm import Session

from app.database import SessionLocal


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
