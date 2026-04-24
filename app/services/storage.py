"""
Armazenamento transiente em memória para bytes de PDF e propostas durante
o ciclo de vida de um processamento. TTL de 1h, limpeza preguiçosa.

Em múltiplas réplicas, cada processo tem seu próprio store — por ora não é
problema (Railway default = 1 réplica). Para escalar horizontalmente,
trocar por Redis ou object storage.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

DEFAULT_TTL_SECONDS = 60 * 60  # 1h


@dataclass
class _PDFEntry:
    pdf_bytes: bytes
    filename: str
    expires_at: float


@dataclass
class _PropostaEntry:
    data: dict[str, Any]
    expires_at: float


class _Store:
    def __init__(self, ttl: int = DEFAULT_TTL_SECONDS) -> None:
        self._pdfs: dict[str, _PDFEntry] = {}
        self._propostas: dict[str, _PropostaEntry] = {}
        self._ttl = ttl
        self._lock = threading.Lock()

    # --- PDFs ---
    def put_pdf(self, key: str, pdf_bytes: bytes, filename: str) -> None:
        with self._lock:
            self._pdfs[key] = _PDFEntry(
                pdf_bytes=pdf_bytes,
                filename=filename,
                expires_at=time.time() + self._ttl,
            )

    def get_pdf(self, key: str) -> tuple[bytes, str] | None:
        self._gc()
        with self._lock:
            entry = self._pdfs.get(key)
            return (entry.pdf_bytes, entry.filename) if entry else None

    def remove_pdf(self, key: str) -> None:
        with self._lock:
            self._pdfs.pop(key, None)

    # --- Propostas ---
    def put_proposta(self, key: str, data: dict[str, Any]) -> None:
        with self._lock:
            self._propostas[key] = _PropostaEntry(
                data=data,
                expires_at=time.time() + self._ttl,
            )

    def get_proposta(self, key: str) -> dict[str, Any] | None:
        self._gc()
        with self._lock:
            entry = self._propostas.get(key)
            return entry.data if entry else None

    def remove_proposta(self, key: str) -> None:
        with self._lock:
            self._propostas.pop(key, None)

    # --- GC ---
    def _gc(self) -> None:
        now = time.time()
        with self._lock:
            for k in [k for k, e in self._pdfs.items() if e.expires_at < now]:
                self._pdfs.pop(k, None)
            for k in [k for k, e in self._propostas.items() if e.expires_at < now]:
                self._propostas.pop(k, None)

    def clear_all(self) -> None:
        with self._lock:
            self._pdfs.clear()
            self._propostas.clear()


_store = _Store()


def put_pdf(key: str, pdf_bytes: bytes, filename: str) -> None:
    _store.put_pdf(key, pdf_bytes, filename)


def get_pdf(key: str) -> tuple[bytes, str] | None:
    return _store.get_pdf(key)


def remove_pdf(key: str) -> None:
    _store.remove_pdf(key)


def put_proposta(key: str, data: dict[str, Any]) -> None:
    _store.put_proposta(key, data)


def get_proposta(key: str) -> dict[str, Any] | None:
    return _store.get_proposta(key)


def remove_proposta(key: str) -> None:
    _store.remove_proposta(key)


def clear_all_for_tests() -> None:
    _store.clear_all()
