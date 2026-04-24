"""
Score de conformidade de uma extração + atualização de métricas do esqueleto
para detecção de drift. Versão inicial implementada aqui; métricas mais
sofisticadas vão ser refinadas na Fase 9.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.enums import StatusEsqueleto
from app.models.esqueleto import Esqueleto
from app.services.extracao_esqueleto import ResultadoExtracao

logger = logging.getLogger(__name__)


def calcular_score(resultado: ResultadoExtracao, esqueleto: Esqueleto) -> float:
    """
    Score de conformidade entre 0.0 e 1.0. Heurísticas:
      - Número de linhas extraídas vs. esperado (se conhecido).
      - Proporção de campos de cabeçalho preenchidos.
      - Ausência de avisos críticos.
    """
    estrutura = esqueleto.estrutura or {}
    cabecalho_spec = estrutura.get("cabecalho") or {}
    num_campos_cabecalho = max(1, len(cabecalho_spec))
    preenchidos = sum(1 for v in resultado.cabecalho.values() if v)
    frac_cabecalho = preenchidos / num_campos_cabecalho

    # Linhas: se houver exemplos_validados indicando quantidade típica, usamos
    # o mínimo entre (linhas atuais / linhas esperadas) e 1.0. Sem exemplos,
    # consideramos sucesso se houver pelo menos 1 linha.
    if resultado.linhas:
        frac_linhas = 1.0
    else:
        frac_linhas = 0.0

    # Avisos penalizam levemente
    penalidade_avisos = min(0.2, len(resultado.avisos) * 0.05)

    score = (0.4 * frac_cabecalho + 0.6 * frac_linhas) - penalidade_avisos
    return max(0.0, min(1.0, score))


def atualizar_metricas_esqueleto(db: Session, esqueleto: Esqueleto, score: float) -> None:
    """
    Atualiza taxa de sucesso (média móvel simples) e total_extracoes. Se a
    taxa cair abaixo do limite configurável, marca como em_revisao.
    """
    total_antigo = esqueleto.total_extracoes or 0
    taxa_antiga = esqueleto.taxa_sucesso or 0.0
    novo_total = total_antigo + 1
    nova_taxa = ((taxa_antiga * total_antigo) + score) / novo_total

    esqueleto.total_extracoes = novo_total
    esqueleto.taxa_sucesso = nova_taxa

    settings = get_settings()
    if (
        novo_total >= 5  # precisa de histórico mínimo para flagar
        and nova_taxa < settings.TAXA_SUCESSO_MIN_ESQUELETO
        and esqueleto.status == StatusEsqueleto.ATIVO.value
    ):
        logger.warning(
            "esqueleto_em_revisao id=%s taxa=%.2f total=%d",
            esqueleto.id, nova_taxa, novo_total,
        )
        esqueleto.status = StatusEsqueleto.EM_REVISAO.value

    db.commit()
