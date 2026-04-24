"""
Cookies de sessão assinados com HMAC via itsdangerous e comparação de
senha em tempo constante. Não há banco de sessões: o próprio cookie é a
fonte da verdade, validado por assinatura e expiração.
"""
from __future__ import annotations

import hmac
import secrets
import time

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import get_settings

SESSION_COOKIE_NAME = "pontoextract_session"
SESSION_MAX_AGE_SECONDS = 8 * 60 * 60  # 8 horas
_SERIALIZER_SALT = "pontoextract.session.v1"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        secret_key=get_settings().SESSION_SECRET,
        salt=_SERIALIZER_SALT,
    )


def create_session_token() -> str:
    payload = {"sid": secrets.token_urlsafe(24), "iat": int(time.time())}
    return _serializer().dumps(payload)


def verify_session_token(token: str) -> dict | None:
    try:
        payload = _serializer().loads(token, max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(payload, dict) or "sid" not in payload:
        return None
    return payload


def check_password(provided: str) -> bool:
    expected = get_settings().ACCESS_PASSWORD
    return hmac.compare_digest(
        provided.encode("utf-8"),
        expected.encode("utf-8"),
    )
