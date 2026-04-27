from __future__ import annotations

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.deps import get_db, require_auth
from app.models.empresa import Empresa
from app.models.enums import StatusProcessamento
from app.models.processamento import Processamento
from app.schemas.history import HistoryDetailResponse, HistoryItem, HistoryListResponse
from app.services.sweeper import cadastro_pode_ser_retomado, varrer_orfaos

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/history", tags=["history"])


def _parse_uuid(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID inválido.")


@router.get("", response_model=HistoryListResponse)
def list_history(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    empresa_id: str | None = None,
    status: str | None = None,
    data_inicio: datetime | None = None,
    data_fim: datetime | None = None,
    auth: dict = Depends(require_auth),
    db: Session = Depends(get_db),
) -> HistoryListResponse:
    # Limpa órfãos antes de listar — user vê estado consistente.
    varrer_orfaos(db)

    q = db.query(Processamento)
    if empresa_id:
        q = q.filter(Processamento.empresa_id == _parse_uuid(empresa_id))
    if status:
        q = q.filter(Processamento.status == status)
    if data_inicio:
        q = q.filter(Processamento.criado_em >= data_inicio)
    if data_fim:
        q = q.filter(Processamento.criado_em <= data_fim)

    total = q.count()
    rows = (
        q.order_by(Processamento.criado_em.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    empresa_ids = list({r.empresa_id for r in rows if r.empresa_id})
    empresas_map: dict[uuid.UUID, str] = {}
    if empresa_ids:
        for e in db.query(Empresa).filter(Empresa.id.in_(empresa_ids)).all():
            empresas_map[e.id] = e.nome

    itens = [
        HistoryItem(
            id=str(r.id),
            criado_em=r.criado_em,
            nome_arquivo_original=r.nome_arquivo_original,
            empresa_id=str(r.empresa_id) if r.empresa_id else None,
            empresa_nome=empresas_map.get(r.empresa_id),
            esqueleto_id=str(r.esqueleto_id) if r.esqueleto_id else None,
            metodo_usado=r.metodo_usado or "",
            status=r.status,
            score_conformidade=r.score_conformidade,
            tempo_processamento_ms=r.tempo_processamento_ms,
            id_processo=r.id_processo,
            id_documento=r.id_documento,
            pode_retomar=(
                r.status == StatusProcessamento.AGUARDANDO_CADASTRO.value
                and cadastro_pode_ser_retomado(str(r.id))
            ),
        )
        for r in rows
    ]
    return HistoryListResponse(itens=itens, total=total, limit=limit, offset=offset)


@router.delete("/{processamento_id}")
def delete_history_item(
    processamento_id: str,
    auth: dict = Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    """
    Remove um processamento (LGPD / retenção). Apagar um processamento
    NÃO apaga a empresa nem o esqueleto associados — esses são metadados
    de identificação do layout, não dados pessoais.
    """
    p = db.get(Processamento, _parse_uuid(processamento_id))
    if p is None:
        raise HTTPException(status_code=404, detail="Processamento não encontrado.")
    db.delete(p)
    db.commit()
    return {"ok": True}


@router.get("/{processamento_id}", response_model=HistoryDetailResponse)
def get_history_item(
    processamento_id: str,
    auth: dict = Depends(require_auth),
    db: Session = Depends(get_db),
) -> HistoryDetailResponse:
    p = db.get(Processamento, _parse_uuid(processamento_id))
    if p is None:
        raise HTTPException(status_code=404, detail="Processamento não encontrado.")

    empresa_nome = None
    if p.empresa_id:
        e = db.get(Empresa, p.empresa_id)
        empresa_nome = e.nome if e else None

    return HistoryDetailResponse(
        id=str(p.id),
        criado_em=p.criado_em,
        nome_arquivo_original=p.nome_arquivo_original,
        empresa_id=str(p.empresa_id) if p.empresa_id else None,
        empresa_nome=empresa_nome,
        esqueleto_id=str(p.esqueleto_id) if p.esqueleto_id else None,
        metodo_usado=p.metodo_usado or "",
        status=p.status,
        score_conformidade=p.score_conformidade,
        tempo_processamento_ms=p.tempo_processamento_ms,
        id_processo=p.id_processo,
        id_documento=p.id_documento,
        resultado_json=p.resultado_json,
        custo_estimado_usd=p.custo_estimado_usd,
        webhook_enviado=p.webhook_enviado,
        webhook_resposta=p.webhook_resposta,
    )
