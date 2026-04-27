from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.deps import get_db, require_auth
from app.models.empresa import Empresa
from app.models.enums import StatusEsqueleto
from app.models.esqueleto import Esqueleto
from app.schemas.empresa import EsqueletoDetail, EsqueletoUpdateRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/esqueletos", tags=["esqueletos"])


def _parse_uuid(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID inválido.")


def _serializar(s: Esqueleto, db: Session) -> EsqueletoDetail:
    empresa = db.get(Empresa, s.empresa_id)
    return EsqueletoDetail(
        id=str(s.id),
        empresa_id=str(s.empresa_id),
        empresa_nome=empresa.nome if empresa else None,
        versao=s.versao,
        status=s.status,
        fingerprint=s.fingerprint,
        fingerprints=list(s.fingerprints or []),
        estrutura=s.estrutura or {},
        exemplos_validados=s.exemplos_validados or [],
        taxa_sucesso=s.taxa_sucesso,
        total_extracoes=s.total_extracoes,
        criado_em=s.criado_em,
        atualizado_em=s.atualizado_em,
    )


@router.get("/{esqueleto_id}", response_model=EsqueletoDetail)
def get_esqueleto(
    esqueleto_id: str,
    auth: dict = Depends(require_auth),
    db: Session = Depends(get_db),
) -> EsqueletoDetail:
    s = db.get(Esqueleto, _parse_uuid(esqueleto_id))
    if s is None:
        raise HTTPException(status_code=404, detail="Esqueleto não encontrado.")
    return _serializar(s, db)


@router.patch("/{esqueleto_id}", response_model=EsqueletoDetail)
def patch_esqueleto(
    esqueleto_id: str,
    payload: EsqueletoUpdateRequest,
    auth: dict = Depends(require_auth),
    db: Session = Depends(get_db),
) -> EsqueletoDetail:
    s = db.get(Esqueleto, _parse_uuid(esqueleto_id))
    if s is None:
        raise HTTPException(status_code=404, detail="Esqueleto não encontrado.")

    if payload.estrutura is not None:
        s.estrutura = payload.estrutura
    if payload.exemplos_validados is not None:
        s.exemplos_validados = payload.exemplos_validados

    db.commit()
    db.refresh(s)
    return _serializar(s, db)


@router.post("/{esqueleto_id}/desativar", response_model=EsqueletoDetail)
def desativar(
    esqueleto_id: str,
    auth: dict = Depends(require_auth),
    db: Session = Depends(get_db),
) -> EsqueletoDetail:
    s = db.get(Esqueleto, _parse_uuid(esqueleto_id))
    if s is None:
        raise HTTPException(status_code=404, detail="Esqueleto não encontrado.")
    s.status = StatusEsqueleto.INATIVO.value
    db.commit()
    db.refresh(s)
    return _serializar(s, db)


@router.post("/{esqueleto_id}/reativar", response_model=EsqueletoDetail)
def reativar(
    esqueleto_id: str,
    auth: dict = Depends(require_auth),
    db: Session = Depends(get_db),
) -> EsqueletoDetail:
    s = db.get(Esqueleto, _parse_uuid(esqueleto_id))
    if s is None:
        raise HTTPException(status_code=404, detail="Esqueleto não encontrado.")

    # Se reativar, desativa as outras da mesma empresa para manter 1 ativa por empresa.
    outras = (
        db.query(Esqueleto)
        .filter(Esqueleto.empresa_id == s.empresa_id)
        .filter(Esqueleto.id != s.id)
        .filter(Esqueleto.status == StatusEsqueleto.ATIVO.value)
        .all()
    )
    for o in outras:
        o.status = StatusEsqueleto.INATIVO.value

    s.status = StatusEsqueleto.ATIVO.value
    db.commit()
    db.refresh(s)
    return _serializar(s, db)
