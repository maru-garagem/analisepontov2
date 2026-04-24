"""
Envio de webhook com backoff exponencial e assinatura HMAC opcional.

O receptor pode validar a assinatura com o mesmo SESSION_SECRET via:
    import hmac, hashlib
    expected = hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()
    # compara com header X-PontoExtract-Signature: sha256=...
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 30
MAX_RETRIES = 3


def _assinar(body_bytes: bytes) -> str:
    secret = get_settings().SESSION_SECRET.encode("utf-8")
    return "sha256=" + hmac.new(secret, body_bytes, hashlib.sha256).hexdigest()


def enviar_webhook(
    url: str,
    payload: dict[str, Any],
    *,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = MAX_RETRIES,
    assinar: bool = True,
) -> tuple[bool, str]:
    """
    POST JSON com retries em erros 5xx / rede. Não retry em 4xx.
    Retorna (sucesso, resposta_truncada).
    """
    body_bytes = json.dumps(payload, default=str, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "PontoExtract-v2-Webhook/1.0",
    }
    if assinar:
        headers["X-PontoExtract-Signature"] = _assinar(body_bytes)

    last_resposta = ""
    for tentativa in range(max_retries + 1):
        try:
            resp = httpx.post(url, content=body_bytes, headers=headers, timeout=timeout)
            last_resposta = f"HTTP {resp.status_code}: {resp.text[:300]}"
            if 200 <= resp.status_code < 300:
                return True, last_resposta
            if 400 <= resp.status_code < 500:
                logger.warning("webhook_4xx url=%s status=%d", url, resp.status_code)
                return False, last_resposta
            logger.info(
                "webhook_retry url=%s status=%d tentativa=%d",
                url, resp.status_code, tentativa,
            )
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            last_resposta = f"erro_rede: {exc}"
            logger.info(
                "webhook_erro_rede url=%s err=%s tentativa=%d",
                url, exc, tentativa,
            )
        if tentativa < max_retries:
            time.sleep(2 ** tentativa)  # 1s, 2s, 4s
    return False, last_resposta
