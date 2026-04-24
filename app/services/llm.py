"""
Cliente OpenRouter. API compatível com OpenAI Chat Completions, então
usamos httpx direto (sem dep do SDK da OpenAI).

Dois modelos:
  - potente: Grok-4 / GPT-4o (cadastro assistido, visão)
  - barato: Grok-4-fast (fallback com esqueleto, few-shot)

Todas as chamadas passam por `LLMClient.chat` ou `LLMClient.chat_json`.
Testes fazem monkeypatch no cliente retornado por `get_llm_client()`.
"""
from __future__ import annotations

import base64
import json
import logging
from functools import lru_cache
from typing import Any, Iterable, Literal

import httpx

from app.config import get_settings
from app.utils.errors import LLMUnavailableError, PontoExtractError


class LLMImageUnsupportedError(PontoExtractError):
    """Modelo chamado não aceita input de imagem — retry só com texto pode ser tentado."""
    http_status = 422
    code = "llm_sem_visao"

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_TIMEOUT_SECONDS = 120
MAX_RETRIES = 2


class LLMClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = OPENROUTER_BASE_URL,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                # OpenRouter pede identificação opcional da app.
                "HTTP-Referer": "https://github.com/maru-garagem/analisepontov2",
                "X-Title": "PontoExtract v2",
            },
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int | None = None,
        temperature: float = 0.1,
        response_format: dict | None = None,
        extra: dict | None = None,
    ) -> dict:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if response_format is not None:
            payload["response_format"] = response_format
        if extra:
            payload.update(extra)

        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = self._client.post("/chat/completions", json=payload)
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as exc:
                # 4xx é "não adianta tentar de novo" (exceto 429 rate limit do provedor).
                if exc.response.status_code != 429 and 400 <= exc.response.status_code < 500:
                    body_txt = exc.response.text[:1000]
                    logger.error("llm_4xx status=%s body=%s", exc.response.status_code, body_txt)
                    # Caso especial: modelo não suporta imagem. Diferenciamos
                    # para que o chamador possa fazer retry sem a imagem.
                    if (
                        "image input" in body_txt.lower()
                        or "does not support image" in body_txt.lower()
                        or "image_url" in body_txt.lower()
                    ):
                        raise LLMImageUnsupportedError(body_txt) from exc
                    raise LLMUnavailableError(
                        f"LLM retornou {exc.response.status_code}: {body_txt[:200]}"
                    ) from exc
                last_exc = exc
                logger.warning("llm_retry attempt=%d status=%s", attempt, exc.response.status_code)
            except (httpx.RequestError, httpx.TimeoutException) as exc:
                last_exc = exc
                logger.warning("llm_network_retry attempt=%d err=%s", attempt, exc)

        raise LLMUnavailableError(f"LLM indisponível após {MAX_RETRIES + 1} tentativas: {last_exc}")

    def chat_json(
        self,
        model: str,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> dict:
        """
        Como chat(), mas pede resposta em JSON e retorna o dict parseado.
        O primeiro choice.message.content é parseado como JSON.
        """
        kwargs.setdefault("response_format", {"type": "json_object"})
        result = self.chat(model, messages, **kwargs)
        try:
            content = result["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            logger.error("llm_response_malformed result=%s", str(result)[:500])
            raise LLMUnavailableError("Resposta do LLM mal formada.") from exc
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            logger.error("llm_json_invalid content=%s", content[:500])
            raise LLMUnavailableError("LLM retornou JSON inválido.") from exc


def encode_image_base64(image_bytes: bytes, mime: str = "image/png") -> str:
    """Codifica imagem para data-URL embutível em mensagens de visão."""
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime};base64,{b64}"


def message_with_image(prompt: str, image_data_url: str | Iterable[str]) -> dict:
    """
    Constrói uma mensagem multi-modal compatível com OpenAI/OpenRouter.
    Aceita uma URL/data-URL ou um iterável de URLs.
    """
    images = [image_data_url] if isinstance(image_data_url, str) else list(image_data_url)
    content: list[dict] = [{"type": "text", "text": prompt}]
    for url in images:
        content.append({"type": "image_url", "image_url": {"url": url}})
    return {"role": "user", "content": content}


@lru_cache
def get_llm_client() -> LLMClient:
    settings = get_settings()
    return LLMClient(api_key=settings.OPENROUTER_API_KEY)


def reset_llm_client() -> None:
    """Útil em testes para injetar um client mockado."""
    get_llm_client.cache_clear()
