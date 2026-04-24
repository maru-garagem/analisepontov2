"""
OCR de PDFs escaneados via Tesseract. Fluxo:
1. pdf2image renderiza páginas como PIL.Image (Poppler sob o capô).
2. pytesseract processa cada imagem retornando texto.
"""
from __future__ import annotations

import logging
from typing import List

from pdf2image import convert_from_bytes
from PIL import Image
import pytesseract

logger = logging.getLogger(__name__)

DEFAULT_LANG = "por"
DEFAULT_DPI = 200


def rasterizar(pdf_bytes: bytes, dpi: int = DEFAULT_DPI, first_page: int | None = None,
               last_page: int | None = None) -> List[Image.Image]:
    """Converte páginas do PDF em PIL.Image via Poppler."""
    return convert_from_bytes(
        pdf_bytes,
        dpi=dpi,
        first_page=first_page,
        last_page=last_page,
    )


def ocr_imagem(img: Image.Image, lang: str = DEFAULT_LANG) -> str:
    return pytesseract.image_to_string(img, lang=lang)


def ocr_pagina(pdf_bytes: bytes, page_index: int = 0, lang: str = DEFAULT_LANG,
               dpi: int = DEFAULT_DPI) -> str:
    images = rasterizar(pdf_bytes, dpi=dpi, first_page=page_index + 1, last_page=page_index + 1)
    if not images:
        return ""
    return ocr_imagem(images[0], lang=lang)


def ocr_todo(pdf_bytes: bytes, lang: str = DEFAULT_LANG, dpi: int = DEFAULT_DPI) -> List[str]:
    images = rasterizar(pdf_bytes, dpi=dpi)
    return [ocr_imagem(img, lang=lang) for img in images]
