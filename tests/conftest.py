"""
Configuração compartilhada de testes. Define env vars antes de qualquer
import de app/, para que pydantic-settings veja valores válidos.
"""
from __future__ import annotations

import os

# Env vars setados ANTES de qualquer import de app/. os.environ tem
# precedência sobre .env local, então testes são isolados de config real.
_TEST_ENV = {
    "ENV": "development",
    "ACCESS_PASSWORD": "test_password_com_16_chars_minimo",
    "SESSION_SECRET": "test_secret_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    "OPENROUTER_API_KEY": "sk-test-dummy",
    "DATABASE_URL": "sqlite:///:memory:",
    "ALLOWED_ORIGINS": "",
}
for k, v in _TEST_ENV.items():
    os.environ[k] = v

import pytest  # noqa: E402

# Importa depois dos env vars para garantir que Settings() inicializa ok.
from app.config import get_settings  # noqa: E402
from app.utils.rate_limit import login_limiter  # noqa: E402

get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    login_limiter.clear_all()
    yield
    login_limiter.clear_all()


@pytest.fixture(autouse=True)
def _reset_storage():
    from app.services import storage
    storage.clear_all_for_tests()
    yield
    storage.clear_all_for_tests()


@pytest.fixture
def test_password() -> str:
    return _TEST_ENV["ACCESS_PASSWORD"]


@pytest.fixture
def db_session():
    """
    Sessão SQLAlchemy contra SQLite in-memory, com schema recriado a cada
    teste. Isola testes que tocam DB.
    """
    from app.database import Base, engine, SessionLocal
    import app.models  # noqa: F401  garante que metadata conhece as tabelas

    Base.metadata.create_all(engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
