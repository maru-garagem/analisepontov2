from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.deps import get_db, require_auth
from app.models.empresa import Empresa, EmpresaCNPJ
from app.models.esqueleto import Esqueleto
from app.schemas.empresa import (
    EmpresaDetail,
    EmpresaListItem,
    EmpresaListResponse,
    EmpresaUpdateRequest,
    EsqueletoListItem,
)
from app.services.identificacao import formatar_cnpj, normalizar_cnpj, validar_cnpj

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/empresas", tags=["empresas"])


def _parse_uuid(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID inválido.")


@router.get("", response_model=EmpresaListResponse)
def list_empresas(
    q: str | None = None,
    auth: dict = Depends(require_auth),
    db: Session = Depends(get_db),
) -> EmpresaListResponse:
    query = db.query(Empresa)
    if q:
        query = query.filter(Empresa.nome.ilike(f"%{q}%"))
    empresas = query.order_by(Empresa.nome.asc()).all()

    itens: list[EmpresaListItem] = []
    for e in empresas:
        esqueletos = e.esqueletos or []
        taxas = [s.taxa_sucesso for s in esqueletos if s.total_extracoes > 0]
        taxa_media = sum(taxas) / len(taxas) if taxas else None
        itens.append(
            EmpresaListItem(
                id=str(e.id),
                nome=e.nome,
                cnpjs=[formatar_cnpj(c.cnpj) for c in (e.cnpjs or [])],
                total_esqueletos=len(esqueletos),
                taxa_sucesso_media=taxa_media,
                criada_em=e.criada_em,
            )
        )
    return EmpresaListResponse(itens=itens, total=len(itens))


@router.get("/{empresa_id}", response_model=EmpresaDetail)
def get_empresa(
    empresa_id: str,
    auth: dict = Depends(require_auth),
    db: Session = Depends(get_db),
) -> EmpresaDetail:
    e = db.get(Empresa, _parse_uuid(empresa_id))
    if e is None:
        raise HTTPException(status_code=404, detail="Empresa não encontrada.")

    esqueletos_ordenados = sorted(
        e.esqueletos or [], key=lambda s: (s.versao or 0), reverse=True
    )
    return EmpresaDetail(
        id=str(e.id),
        nome=e.nome,
        cnpjs=[formatar_cnpj(c.cnpj) for c in (e.cnpjs or [])],
        criada_em=e.criada_em,
        atualizada_em=e.atualizada_em,
        esqueletos=[
            EsqueletoListItem(
                id=str(s.id),
                versao=s.versao,
                status=s.status,
                fingerprint=s.fingerprint,
                fingerprints=list(s.fingerprints or []),
                taxa_sucesso=s.taxa_sucesso,
                total_extracoes=s.total_extracoes,
                criado_em=s.criado_em,
            )
            for s in esqueletos_ordenados
        ],
    )


@router.patch("/{empresa_id}", response_model=EmpresaDetail)
def patch_empresa(
    empresa_id: str,
    payload: EmpresaUpdateRequest,
    auth: dict = Depends(require_auth),
    db: Session = Depends(get_db),
) -> EmpresaDetail:
    e = db.get(Empresa, _parse_uuid(empresa_id))
    if e is None:
        raise HTTPException(status_code=404, detail="Empresa não encontrada.")

    if payload.nome is not None:
        e.nome = payload.nome.strip()

    cnpjs_atuais = {c.cnpj: c for c in (e.cnpjs or [])}

    for raw in payload.cnpjs_adicionar:
        n = normalizar_cnpj(raw)
        if not n or not validar_cnpj(n):
            raise HTTPException(status_code=400, detail=f"CNPJ inválido: {raw}")
        if n in cnpjs_atuais:
            continue
        existente = db.query(EmpresaCNPJ).filter(EmpresaCNPJ.cnpj == n).first()
        if existente is not None and existente.empresa_id != e.id:
            raise HTTPException(
                status_code=409,
                detail=f"CNPJ {formatar_cnpj(n)} já vinculado a outra empresa.",
            )
        db.add(EmpresaCNPJ(empresa_id=e.id, cnpj=n))

    for raw in payload.cnpjs_remover:
        n = normalizar_cnpj(raw)
        entry = cnpjs_atuais.get(n)
        if entry is not None:
            db.delete(entry)

    db.commit()
    db.refresh(e)
    return get_empresa(empresa_id, auth=auth, db=db)  # reutiliza formatação
