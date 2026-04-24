"""
Classificação rápida: "este PDF é um cartão de ponto?"

Estratégia barata: se o texto contém um número suficiente de labels da
whitelist de termos estruturais, aceita. Rejeita contratos, notas fiscais,
extratos bancários, etc.

Uso caro (LLM) fica para quando a heurística rejeita mas o usuário insiste
(cadastro assistido pede validação). Essa decisão é tomada nas rotas.
"""
from __future__ import annotations

import logging

from app.services.fingerprint import extrair_tokens_estruturais
from app.utils.pdf import extrair_texto_todo

logger = logging.getLogger(__name__)

# Mínimo de tokens estruturais para considerar "parece cartão de ponto".
# Calibrado para baixo falso-positivo em documentos aleatórios e baixo
# falso-negativo em cartões reais (que costumam ter 15+ labels).
MIN_TOKENS_ESTRUTURAIS = 5


def parece_cartao_de_ponto(pdf_bytes: bytes) -> tuple[bool, list[str]]:
    """
    Retorna (parece, tokens_encontrados). Em PDFs escaneados sem OCR, os
    tokens estarão vazios e a função retorna False — o chamador deve
    considerar fallback via OCR antes de concluir.
    """
    textos_por_pagina = extrair_texto_todo(pdf_bytes)
    texto_completo = "\n".join(textos_por_pagina)
    tokens = extrair_tokens_estruturais(texto_completo)
    parece = len(tokens) >= MIN_TOKENS_ESTRUTURAIS
    if not parece:
        logger.info(
            "classificador_rejeitou tokens=%d min=%d",
            len(tokens),
            MIN_TOKENS_ESTRUTURAIS,
        )
    return parece, tokens
