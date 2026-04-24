from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import patch

import httpx

from app.config import get_settings
from app.services.webhook import enviar_webhook


class _RespostaFake:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


def test_envia_e_recebe_200():
    with patch("httpx.post", return_value=_RespostaFake(200, "ok")) as m:
        sucesso, resp = enviar_webhook(
            "https://exemplo.com/hook",
            {"foo": "bar"},
            assinar=False,
        )
    assert sucesso is True
    assert "200" in resp
    m.assert_called_once()


def test_4xx_nao_retenta():
    with patch("httpx.post", return_value=_RespostaFake(404, "not found")) as m:
        sucesso, resp = enviar_webhook("https://x", {}, assinar=False)
    assert sucesso is False
    assert "404" in resp
    assert m.call_count == 1  # sem retry


def test_5xx_retenta_e_falha():
    with patch(
        "httpx.post",
        side_effect=[_RespostaFake(503), _RespostaFake(503), _RespostaFake(503), _RespostaFake(503)],
    ) as m, patch("time.sleep", return_value=None):
        sucesso, resp = enviar_webhook("https://x", {}, assinar=False, max_retries=3)
    assert sucesso is False
    assert m.call_count == 4  # 1 original + 3 retries


def test_erro_rede_retenta():
    with patch(
        "httpx.post",
        side_effect=[httpx.TimeoutException("timeout"), _RespostaFake(200)],
    ) as m, patch("time.sleep", return_value=None):
        sucesso, resp = enviar_webhook("https://x", {}, assinar=False, max_retries=3)
    assert sucesso is True
    assert m.call_count == 2


def test_assinatura_hmac_correta():
    captured_headers = {}

    def fake_post(url, content, headers, timeout):
        captured_headers.update(headers)
        return _RespostaFake(200, "ok")

    with patch("httpx.post", side_effect=fake_post):
        enviar_webhook("https://x", {"k": "v"}, assinar=True)

    assert "X-PontoExtract-Signature" in captured_headers
    sig = captured_headers["X-PontoExtract-Signature"]
    assert sig.startswith("sha256=")

    # Reconstrói a assinatura esperada
    expected_body = json.dumps({"k": "v"}, ensure_ascii=False).encode("utf-8")
    expected_sig = "sha256=" + hmac.new(
        get_settings().SESSION_SECRET.encode("utf-8"),
        expected_body,
        hashlib.sha256,
    ).hexdigest()
    assert hmac.compare_digest(sig, expected_sig)
