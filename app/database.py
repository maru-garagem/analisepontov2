"""
Engine + SessionLocal do SQLAlchemy. Base declarativa compartilhada por
todos os modelos. Usado pelo FastAPI via dependência `get_db` em `app.deps`.
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings

_settings = get_settings()

# SQLite precisa de connect_args para funcionar com o pool do FastAPI.
_connect_args: dict = {}
if _settings.DATABASE_URL.startswith("sqlite"):
    _connect_args = {"check_same_thread": False}

engine = create_engine(
    _settings.DATABASE_URL,
    pool_pre_ping=True,
    connect_args=_connect_args,
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Base declarativa para todos os modelos ORM do projeto."""
    pass
