"""
Entrypoint do container. Usa Python (não shell) para ter flush confiável
do stdout e traceback limpo em caso de erro de import. Rodado pelo Dockerfile.
"""
from __future__ import annotations

import os
import subprocess
import sys


def log(msg: str) -> None:
    print(f"[entrypoint] {msg}", flush=True)


def main() -> int:
    log("iniciando")
    log(f"PORT={os.environ.get('PORT', 'undefined')}")
    log(f"ENV={os.environ.get('ENV', 'undefined')}")
    log(f"python: {sys.version.split()[0]}")

    log("rodando alembic upgrade head...")
    ret = subprocess.run(["alembic", "upgrade", "head"])
    if ret.returncode != 0:
        log(f"alembic falhou (codigo {ret.returncode})")
        return ret.returncode
    log("alembic OK")

    log("importando main.py...")
    try:
        import main as _main  # noqa: F401
        log(f"main importado (hasattr app: {hasattr(_main, 'app')})")
    except Exception as exc:
        log(f"ERRO importando main: {exc.__class__.__name__}: {exc}")
        import traceback
        traceback.print_exc()
        return 1

    port = os.environ.get("PORT", "8000")
    log(f"iniciando uvicorn em 0.0.0.0:{port}...")
    # execvp substitui o processo — uvicorn passa a ser PID 1 e recebe sinais.
    os.execvp(
        "python",
        [
            "python", "-m", "uvicorn", "main:app",
            "--host", "0.0.0.0",
            "--port", port,
            "--log-level", "info",
            "--access-log",
        ],
    )
    return 0  # pragma: no cover (execvp nunca retorna)


if __name__ == "__main__":
    sys.exit(main())
