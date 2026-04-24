"""
Enums compartilhados entre modelos ORM e schemas Pydantic. Armazenados
como VARCHAR no banco (não como ENUM nativo) para facilitar evolução
sem precisar de migrations só para adicionar valor.
"""
from __future__ import annotations

from enum import StrEnum


class StatusEsqueleto(StrEnum):
    ATIVO = "ativo"
    INATIVO = "inativo"
    EM_REVISAO = "em_revisao"


class MetodoExtracao(StrEnum):
    ESQUELETO_PLUMBER = "esqueleto_plumber"
    ESQUELETO_OCR = "esqueleto_ocr"
    ESQUELETO_IA_BARATA = "esqueleto_ia_barata"
    CADASTRO_ASSISTIDO = "cadastro_assistido"
    FALHOU = "falhou"


class StatusProcessamento(StrEnum):
    EM_PROCESSAMENTO = "em_processamento"
    AGUARDANDO_CADASTRO = "aguardando_cadastro"
    SUCESSO = "sucesso"
    SUCESSO_COM_AVISO = "sucesso_com_aviso"
    FALHOU = "falhou"
    NAO_CARTAO_PONTO = "nao_cartao_ponto"
