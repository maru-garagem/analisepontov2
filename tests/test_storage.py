from __future__ import annotations

import time

from app.services import storage


def test_put_e_get_pdf():
    storage.put_pdf("abc", b"%PDF-1.4 ...", "arquivo.pdf")
    r = storage.get_pdf("abc")
    assert r is not None
    pdf, nome = r
    assert pdf.startswith(b"%PDF-1.4")
    assert nome == "arquivo.pdf"


def test_get_pdf_inexistente():
    assert storage.get_pdf("nao-existe") is None


def test_remove_pdf():
    storage.put_pdf("x", b"d", "f.pdf")
    storage.remove_pdf("x")
    assert storage.get_pdf("x") is None


def test_put_e_get_proposta():
    storage.put_proposta("p1", {"foo": "bar"})
    assert storage.get_proposta("p1") == {"foo": "bar"}


def test_put_e_get_metadata():
    storage.put_metadata("m1", {"webhook_url": "https://x"})
    assert storage.get_metadata("m1") == {"webhook_url": "https://x"}


def test_get_metadata_inexistente_retorna_dict_vazio():
    assert storage.get_metadata("nunca") == {}


def test_clear_all_for_tests():
    storage.put_pdf("a", b"d", "f.pdf")
    storage.put_proposta("a", {"k": 1})
    storage.put_metadata("a", {"v": 2})
    storage.clear_all_for_tests()
    assert storage.get_pdf("a") is None
    assert storage.get_proposta("a") is None
    assert storage.get_metadata("a") == {}


def test_ttl_remove_entradas_expiradas(monkeypatch):
    """
    Força TTL curtíssimo para simular expiração. Usa o objeto _store
    interno que aceita TTL personalizável.
    """
    from app.services.storage import _Store
    s = _Store(ttl=0)
    s.put_pdf("exp", b"d", "f.pdf")
    time.sleep(0.01)
    assert s.get_pdf("exp") is None
