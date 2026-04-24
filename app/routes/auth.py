from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.config import get_settings
from app.schemas.auth import LoginRequest, MeResponse, SimpleOk
from app.utils.rate_limit import login_limiter
from app.utils.security import (
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE_SECONDS,
    check_password,
    create_session_token,
    verify_session_token,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _client_ip(request: Request) -> str:
    # Railway/proxies injetam x-forwarded-for; primeiro valor é o cliente real.
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


@router.post("/login", response_model=SimpleOk)
def login(payload: LoginRequest, request: Request, response: Response) -> SimpleOk:
    ip = _client_ip(request)
    if not login_limiter.check_and_record(ip):
        logger.warning("rate_limit_hit ip=%s", ip)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Muitas tentativas. Tente novamente em 15 minutos.",
        )
    if not check_password(payload.password):
        logger.info("login_failed ip=%s", ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Senha incorreta.",
        )
    settings = get_settings()
    token = create_session_token()
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=settings.is_prod,
        samesite="lax",
        path="/",
    )
    login_limiter.reset(ip)
    logger.info("login_ok ip=%s", ip)
    return SimpleOk(ok=True)


@router.post("/logout", response_model=SimpleOk)
def logout(response: Response) -> SimpleOk:
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return SimpleOk(ok=True)


@router.get("/me", response_model=MeResponse)
def me(request: Request) -> MeResponse:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return MeResponse(authenticated=False)
    return MeResponse(authenticated=verify_session_token(token) is not None)
