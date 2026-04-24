#!/bin/sh
set -eu

echo "[entrypoint] iniciando"
echo "[entrypoint] PORT=${PORT:-<nao-definido>}"
echo "[entrypoint] ENV=${ENV:-<nao-definido>}"
echo "[entrypoint] python: $(python --version 2>&1)"

echo "[entrypoint] rodando alembic upgrade head..."
alembic upgrade head
echo "[entrypoint] alembic OK"

echo "[entrypoint] validando import de main.py..."
python -c "import main; print('[entrypoint] main:app disponivel:', hasattr(main, 'app'))"

echo "[entrypoint] iniciando uvicorn na porta ${PORT:-8000}..."
exec python -m uvicorn main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}" \
    --log-level info \
    --access-log
