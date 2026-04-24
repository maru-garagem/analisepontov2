"""
Exceções de domínio. Capturadas pelos exception handlers (Fase 12) e
traduzidas em respostas HTTP apropriadas.
"""
from __future__ import annotations


class PontoExtractError(Exception):
    """Base de todos os erros de domínio."""

    http_status: int = 500
    code: str = "erro_generico"


class PDFInvalidError(PontoExtractError):
    """PDF corrompido, não-PDF, vazio ou ilegível."""

    http_status = 400
    code = "pdf_invalido"


class PDFPasswordProtectedError(PontoExtractError):
    """PDF protegido por senha — não suportado."""

    http_status = 400
    code = "pdf_protegido"


class PDFTooLargeError(PontoExtractError):
    """PDF excede limite de tamanho ou de páginas."""

    http_status = 413
    code = "pdf_grande_demais"


class NotACardPontoError(PontoExtractError):
    """Documento enviado não é um cartão de ponto."""

    http_status = 422
    code = "nao_cartao_ponto"


class EmpresaNotFoundError(PontoExtractError):
    http_status = 404
    code = "empresa_nao_encontrada"


class EsqueletoNotFoundError(PontoExtractError):
    http_status = 404
    code = "esqueleto_nao_encontrado"


class ProcessamentoNotFoundError(PontoExtractError):
    http_status = 404
    code = "processamento_nao_encontrado"


class LLMUnavailableError(PontoExtractError):
    """Falha ao chamar LLM — timeout, rede, provedor fora do ar."""

    http_status = 503
    code = "llm_indisponivel"


class FingerprintError(PontoExtractError):
    """Falha ao gerar fingerprint (PDF sem texto legível, etc)."""

    http_status = 422
    code = "fingerprint_falhou"
