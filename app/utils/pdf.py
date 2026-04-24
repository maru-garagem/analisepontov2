"""
Helpers de leitura de PDFs usando pdfplumber. Todas as funções recebem
bytes (ou BytesIO) — nada toca disco. Validações rígidas e exceções de
domínio são levantadas para problemas comuns.
"""
from __future__ import annotations

import logging
from io import BytesIO
from typing import Any

import pdfplumber
from pypdf import PdfReader
from pypdf.errors import FileNotDecryptedError, PdfReadError

from app.utils.errors import PDFInvalidError, PDFPasswordProtectedError, PDFTooLargeError

logger = logging.getLogger(__name__)

_PDF_MAGIC = b"%PDF-"


def validar_pdf_bytes(pdf_bytes: bytes, max_pages: int | None = None) -> int:
    """
    Valida que o buffer é um PDF legível e não criptografado. Retorna o
    número de páginas. O limite de páginas é **opcional** (default sem
    limite) — cartões de ponto de empresas grandes podem ter centenas
    de páginas legitimamente.
    """
    if not pdf_bytes:
        raise PDFInvalidError("Arquivo vazio.")
    if not pdf_bytes.startswith(_PDF_MAGIC):
        raise PDFInvalidError("Arquivo não é um PDF válido (magic bytes ausentes).")

    try:
        reader = PdfReader(BytesIO(pdf_bytes))
    except PdfReadError as exc:
        raise PDFInvalidError(f"PDF corrompido: {exc}") from exc

    if reader.is_encrypted:
        # Tentativa comum: alguns PDFs são "encriptados" com senha vazia.
        try:
            if reader.decrypt("") == 0:
                raise PDFPasswordProtectedError("PDF protegido por senha.")
        except Exception as exc:
            raise PDFPasswordProtectedError("PDF protegido por senha.") from exc

    num_pages = len(reader.pages)
    if num_pages == 0:
        raise PDFInvalidError("PDF sem páginas.")
    if max_pages is not None and num_pages > max_pages:
        raise PDFTooLargeError(
            f"PDF com {num_pages} páginas excede o limite de {max_pages}."
        )
    return num_pages


def abrir_pdf(pdf_bytes: bytes) -> "pdfplumber.pdf.PDF":
    """
    Abre um PDF com pdfplumber. Deve ser usado como context manager:
        with abrir_pdf(bytes) as pdf:
            ...
    """
    try:
        return pdfplumber.open(BytesIO(pdf_bytes))
    except Exception as exc:
        raise PDFInvalidError(f"Falha ao abrir PDF com pdfplumber: {exc}") from exc


def extrair_texto_pagina(pdf_bytes: bytes, page_index: int = 0) -> str:
    with abrir_pdf(pdf_bytes) as pdf:
        if page_index >= len(pdf.pages):
            raise PDFInvalidError(f"Página {page_index} inexistente.")
        return pdf.pages[page_index].extract_text() or ""


def extrair_texto_todo(pdf_bytes: bytes) -> list[str]:
    with abrir_pdf(pdf_bytes) as pdf:
        return [p.extract_text() or "" for p in pdf.pages]


def extrair_tabelas_pagina(pdf_bytes: bytes, page_index: int = 0) -> list[list[list[str | None]]]:
    with abrir_pdf(pdf_bytes) as pdf:
        if page_index >= len(pdf.pages):
            return []
        return pdf.pages[page_index].extract_tables() or []


def dimensoes_pagina(pdf_bytes: bytes, page_index: int = 0) -> tuple[int, int]:
    """Retorna (largura, altura) arredondadas em pontos (1 pt = 1/72 pol)."""
    with abrir_pdf(pdf_bytes) as pdf:
        if page_index >= len(pdf.pages):
            raise PDFInvalidError(f"Página {page_index} inexistente.")
        page = pdf.pages[page_index]
        return round(page.width), round(page.height)


def metadata_pdf(pdf_bytes: bytes) -> dict[str, Any]:
    """Metadata do PDF (autor, criador, etc). Pode ajudar na identificação."""
    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        info = reader.metadata or {}
        return {str(k).lstrip("/").lower(): str(v) for k, v in info.items()}
    except Exception:
        return {}


def parece_pdf_escaneado(pdf_bytes: bytes, limite_chars_por_pagina: int = 50) -> bool:
    """
    Heurística: se as páginas têm muito pouco texto extraível, é provavelmente
    um PDF escaneado (só imagem) e precisa de OCR.
    """
    try:
        textos = extrair_texto_todo(pdf_bytes)
    except PDFInvalidError:
        return False
    if not textos:
        return True
    media_chars = sum(len(t) for t in textos) / len(textos)
    return media_chars < limite_chars_por_pagina
