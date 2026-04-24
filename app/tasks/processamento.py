"""
BackgroundTask que executa o pipeline de extração: classificação →
identificação → extração via esqueleto OU proposta de cadastro assistido.

Atualiza incrementalmente o Processamento correspondente no banco. O
frontend faz polling em /api/extract/{id}/status.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.empresa import Empresa
from app.models.enums import MetodoExtracao, StatusProcessamento
from app.models.processamento import Processamento
from app.services import storage
from app.services.cadastro_assistido import gerar_proposta
from app.services.classificador import parece_cartao_de_ponto
from app.services.extracao_esqueleto import aplicar_esqueleto
from app.services.identificacao import formatar_cnpj, identificar_empresa
from app.utils.errors import NotACardPontoError, PontoExtractError

logger = logging.getLogger(__name__)


def _atualizar(db: Session, processamento_id: uuid.UUID, **campos: Any) -> None:
    db.query(Processamento).filter(Processamento.id == processamento_id).update(campos)
    db.commit()


def processar_em_background(processamento_id: uuid.UUID) -> None:
    """
    Entry point do BackgroundTask. Nunca recebe `db` da request — cria sua
    própria Session porque roda fora do ciclo da request.
    """
    inicio = time.monotonic()
    db = SessionLocal()
    try:
        _executar_pipeline(db, processamento_id, inicio)
    except Exception as exc:  # pragma: no cover
        logger.exception("pipeline_crashou processamento_id=%s", processamento_id)
        try:
            _atualizar(
                db,
                processamento_id,
                status=StatusProcessamento.FALHOU.value,
                metodo_usado=MetodoExtracao.FALHOU.value,
                resultado_json={"erro": "falha_interna", "mensagem": str(exc)[:500]},
                tempo_processamento_ms=int((time.monotonic() - inicio) * 1000),
            )
        except Exception:
            logger.exception("falha_ao_marcar_falha")
    finally:
        db.close()


def _executar_pipeline(db: Session, processamento_id: uuid.UUID, inicio: float) -> None:
    # Busca processamento
    proc = db.get(Processamento, processamento_id)
    if proc is None:
        logger.error("processamento_nao_encontrado id=%s", processamento_id)
        return

    # Pega bytes do storage
    entrada = storage.get_pdf(str(processamento_id))
    if entrada is None:
        _atualizar(
            db, processamento_id,
            status=StatusProcessamento.FALHOU.value,
            metodo_usado=MetodoExtracao.FALHOU.value,
            resultado_json={"erro": "pdf_expirado"},
            tempo_processamento_ms=int((time.monotonic() - inicio) * 1000),
        )
        return
    pdf_bytes, _filename = entrada

    # 1. Classificação rápida
    try:
        parece, _tokens = parece_cartao_de_ponto(pdf_bytes)
    except PontoExtractError as exc:
        _atualizar(
            db, processamento_id,
            status=StatusProcessamento.FALHOU.value,
            metodo_usado=MetodoExtracao.FALHOU.value,
            resultado_json={"erro": exc.code, "mensagem": str(exc)},
            tempo_processamento_ms=int((time.monotonic() - inicio) * 1000),
        )
        storage.remove_pdf(str(processamento_id))
        return

    if not parece:
        _atualizar(
            db, processamento_id,
            status=StatusProcessamento.NAO_CARTAO_PONTO.value,
            metodo_usado=MetodoExtracao.FALHOU.value,
            resultado_json={"erro": "nao_cartao_ponto"},
            tempo_processamento_ms=int((time.monotonic() - inicio) * 1000),
        )
        storage.remove_pdf(str(processamento_id))
        return

    # 2. Identificação
    try:
        ident = identificar_empresa(pdf_bytes, db)
    except PontoExtractError as exc:
        _atualizar(
            db, processamento_id,
            status=StatusProcessamento.FALHOU.value,
            metodo_usado=MetodoExtracao.FALHOU.value,
            resultado_json={"erro": exc.code, "mensagem": str(exc)},
            tempo_processamento_ms=int((time.monotonic() - inicio) * 1000),
        )
        storage.remove_pdf(str(processamento_id))
        return

    proc.empresa_id = ident.empresa.id if ident.empresa else None
    proc.esqueleto_id = ident.esqueleto.id if ident.esqueleto else None
    db.commit()

    # 3. Decide caminho
    if ident.esqueleto is not None:
        _fluxo_rapido(db, processamento_id, pdf_bytes, ident, inicio)
    else:
        _fluxo_cadastro_assistido(db, processamento_id, pdf_bytes, ident, inicio)


def _fluxo_rapido(db: Session, processamento_id, pdf_bytes, ident, inicio) -> None:
    from app.services.conformidade import calcular_score  # import lazy (Fase 9)

    try:
        resultado = aplicar_esqueleto(pdf_bytes, ident.esqueleto)
    except PontoExtractError as exc:
        _atualizar(
            db, processamento_id,
            status=StatusProcessamento.FALHOU.value,
            metodo_usado=MetodoExtracao.FALHOU.value,
            resultado_json={"erro": exc.code, "mensagem": str(exc)},
            tempo_processamento_ms=int((time.monotonic() - inicio) * 1000),
        )
        storage.remove_pdf(str(processamento_id))
        return

    score = calcular_score(resultado, ident.esqueleto)
    from app.config import get_settings
    settings = get_settings()
    if score >= settings.SCORE_CONFORMIDADE_MIN:
        status_final = StatusProcessamento.SUCESSO.value
    elif score >= settings.SCORE_CONFORMIDADE_ALERTA:
        status_final = StatusProcessamento.SUCESSO_COM_AVISO.value
    else:
        status_final = StatusProcessamento.SUCESSO_COM_AVISO.value

    _atualizar(
        db, processamento_id,
        status=status_final,
        metodo_usado=resultado.metodo_efetivo,
        score_conformidade=score,
        resultado_json={
            "cabecalho": resultado.cabecalho,
            "linhas": resultado.linhas,
            "avisos": resultado.avisos,
            "match_type": ident.match_type,
            "empresa_nome": ident.empresa.nome if ident.empresa else None,
            "cnpj_detectado": formatar_cnpj(ident.cnpj_detectado) if ident.cnpj_detectado else None,
            "fingerprint": ident.fingerprint.hash,
        },
        tempo_processamento_ms=int((time.monotonic() - inicio) * 1000),
    )

    # Atualiza métricas do esqueleto (Fase 9 refina isso)
    from app.services.conformidade import atualizar_metricas_esqueleto
    atualizar_metricas_esqueleto(db, ident.esqueleto, score)

    storage.remove_pdf(str(processamento_id))


def _fluxo_cadastro_assistido(db: Session, processamento_id, pdf_bytes, ident, inicio) -> None:
    try:
        proposta = gerar_proposta(pdf_bytes)
    except NotACardPontoError:
        _atualizar(
            db, processamento_id,
            status=StatusProcessamento.NAO_CARTAO_PONTO.value,
            metodo_usado=MetodoExtracao.FALHOU.value,
            resultado_json={"erro": "nao_cartao_ponto"},
            tempo_processamento_ms=int((time.monotonic() - inicio) * 1000),
        )
        storage.remove_pdf(str(processamento_id))
        return
    except PontoExtractError as exc:
        _atualizar(
            db, processamento_id,
            status=StatusProcessamento.FALHOU.value,
            metodo_usado=MetodoExtracao.FALHOU.value,
            resultado_json={"erro": exc.code, "mensagem": str(exc)},
            tempo_processamento_ms=int((time.monotonic() - inicio) * 1000),
        )
        storage.remove_pdf(str(processamento_id))
        return

    # Salva proposta em storage (o PDF segue em storage até confirmar/cancelar)
    proposta_payload = {
        "proposta": proposta.to_dict(),
        "fingerprint_hash": ident.fingerprint.hash,
        "cnpj_detectado_no_pdf": ident.cnpj_detectado,
        "empresa_candidata_id": str(ident.empresa.id) if ident.empresa else None,
        "empresa_candidata_nome": ident.empresa.nome if ident.empresa else None,
        "match_type": ident.match_type,
    }
    storage.put_proposta(str(processamento_id), proposta_payload)

    _atualizar(
        db, processamento_id,
        status=StatusProcessamento.AGUARDANDO_CADASTRO.value,
        metodo_usado=MetodoExtracao.CADASTRO_ASSISTIDO.value,
        custo_estimado_usd=proposta.custo_estimado_usd,
        tempo_processamento_ms=int((time.monotonic() - inicio) * 1000),
    )
