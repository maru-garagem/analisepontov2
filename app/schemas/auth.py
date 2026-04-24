from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=512)


class SimpleOk(BaseModel):
    ok: bool


class MeResponse(BaseModel):
    authenticated: bool
