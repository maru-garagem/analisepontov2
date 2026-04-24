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


def ocr_tabela_por_bbox(
    pdf_bytes: bytes,
    lang: str = DEFAULT_LANG,
    dpi: int = 300,
    gap_coluna_px: int = 30,
) -> list[list[list[str]]]:
    """
    OCR com reconstrução de tabelas a partir de bounding boxes (pytesseract
    image_to_data). Retorna, para cada página, uma lista de "linhas" — cada
    linha é uma lista de células (strings concatenadas das palavras próximas).

    Essa abordagem é mais robusta do que split por espaços em texto livre
    porque usa a posição real dos tokens na imagem para separar colunas.
    """
    images = rasterizar(pdf_bytes, dpi=dpi)
    paginas: list[list[list[str]]] = []

    for img in images:
        dados = pytesseract.image_to_data(
            img, lang=lang, output_type=pytesseract.Output.DICT
        )
        n = len(dados["text"])

        # Agrupa tokens por linha física (block/par/line do Tesseract).
        grupos: dict[tuple, list[dict]] = {}
        for i in range(n):
            texto = (dados["text"][i] or "").strip()
            if not texto:
                continue
            key = (
                dados["block_num"][i],
                dados["par_num"][i],
                dados["line_num"][i],
            )
            grupos.setdefault(key, []).append({
                "text": texto,
                "left": int(dados["left"][i]),
                "width": int(dados["width"][i]),
                "top": int(dados["top"][i]),
            })

        linhas_pagina: list[list[str]] = []
        # Ordena linhas por posição vertical (top médio)
        chaves_ordenadas = sorted(
            grupos.keys(),
            key=lambda k: sum(t["top"] for t in grupos[k]) / len(grupos[k]),
        )
        for k in chaves_ordenadas:
            tokens = sorted(grupos[k], key=lambda t: t["left"])
            # Agrupa tokens em células — palavras com gap > gap_coluna_px
            # viram coluna nova.
            celulas: list[list[str]] = []
            atual: list[str] = []
            ultimo_right: int | None = None
            for t in tokens:
                if atual and ultimo_right is not None and t["left"] - ultimo_right > gap_coluna_px:
                    celulas.append(atual)
                    atual = []
                atual.append(t["text"])
                ultimo_right = t["left"] + t["width"]
            if atual:
                celulas.append(atual)
            linhas_pagina.append([" ".join(c) for c in celulas])

        paginas.append(linhas_pagina)

    return paginas
