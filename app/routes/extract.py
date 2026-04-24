"""
Endpoints de extração: upload, polling de status, proposta de cadastro,
confirmação e cancelamento.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, Response, UploadFile, status

from app.config import get_settings
from app.database import SessionLocal
from app.deps import require_auth, session_id_short
from app.models.empresa import Empresa, EmpresaCNPJ
from app.models.enums import StatusEsqueleto, StatusProcessamento
from app.models.esqueleto import Esqueleto
from app.models.processamento import Processamento
from app.schemas.extract import (
    ApiExtractExternalResponse,
    CadastroConfirmarRequest,
    CadastroPropostaResponse,
    ExtractStartResponse,
    ExtractStatusResponse,
)
from app.services import storage
from app.services.conformidade import atualizar_metricas_esqueleto, calcular_score
from app.services.extracao_esqueleto import aplicar_esqueleto
from app.services.identificacao import formatar_cnpj, normalizar_cnpj, validar_cnpj
from app.tasks.processamento import processar_em_background
from app.utils.rate_limit import upload_limiter
from app.utils.errors import (
    PDFInvalidError,
    PDFPasswordProtectedError,
    PDFTooLargeError,
)
from app.utils.pdf import validar_pdf_bytes

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/extract", tags=["extract"])


def _parse_uuid(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID inválido.")


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def _validar_webhook_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(
            status_code=400,
            detail="webhook_url deve ser URL absoluta com esquema http ou https.",
        )


@router.post("", response_model=ExtractStartResponse)
def iniciar_extracao(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    id_processo: str | None = Form(default=None),
    id_documento: str | None = Form(default=None),
    modelo_potente: str | None = Form(default=None),
    auth: dict = Depends(require_auth),
) -> ExtractStartResponse:
    settings = get_settings()

    ip = _client_ip(request)
    if not upload_limiter.check_and_record(ip):
        raise HTTPException(
            status_code=429,
            detail="Limite de uploads atingido. Aguarde e tente novamente.",
        )

    # Modelo só é efetivo se cair em cadastro assistido. Valida whitelist;
    # modelo fora da lista vira None (default do servidor).
    modelo_validado: str | None = None
    if modelo_potente and modelo_potente in settings.modelos_potentes_permitidos:
        modelo_validado = modelo_potente

    # Valida content-type e tamanho
    if file.content_type not in ("application/pdf", "application/octet-stream", None):
        # Alguns browsers enviam octet-stream; magic bytes vão confirmar.
        logger.info("upload_content_type_incomum tipo=%s", file.content_type)

    pdf_bytes = file.file.read()
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if len(pdf_bytes) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Arquivo maior que {settings.MAX_UPLOAD_SIZE_MB}MB.",
        )

    try:
        validar_pdf_bytes(pdf_bytes)
    except PDFPasswordProtectedError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except PDFTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc))
    except PDFInvalidError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Cria registro de Processamento
    db = SessionLocal()
    try:
        proc = Processamento(
            id=uuid.uuid4(),
            nome_arquivo_original=file.filename or "arquivo.pdf",
            metodo_usado="",
            status=StatusProcessamento.EM_PROCESSAMENTO.value,
            id_processo=id_processo,
            id_documento=id_documento,
            criado_por=session_id_short(auth),
        )
        db.add(proc)
        db.commit()
        db.refresh(proc)
        proc_id = proc.id
    finally:
        db.close()

    # Guarda bytes em memória e lança background task
    storage.put_pdf(str(proc_id), pdf_bytes, file.filename or "arquivo.pdf")
    if modelo_validado:
        storage.put_metadata(str(proc_id), {"modelo_potente": modelo_validado})
    background_tasks.add_task(processar_em_background, proc_id)

    return ExtractStartResponse(
        processing_id=str(proc_id),
        status=StatusProcessamento.EM_PROCESSAMENTO.value,
    )


@router.get("/modelos-disponiveis")
def modelos_disponiveis(auth: dict = Depends(require_auth)) -> dict[str, Any]:
    """
    Retorna catálogos de modelos:
      - `modelos` (potentes): usados no cadastro assistido (Vision).
      - `modelos_baratos`: usados no fallback IA em extrações futuras; salvos
        no esqueleto (estrutura.modelo_fallback) na hora da confirmação.
    """
    settings = get_settings()
    return {
        "modelos": settings.modelos_potentes_catalogo,
        "padrao": settings.OPENROUTER_MODEL_POTENTE,
        "modelos_baratos": settings.modelos_baratos_catalogo,
        "padrao_barato": settings.OPENROUTER_MODEL_BARATO,
    }


@router.get("/{processing_id}/status", response_model=ExtractStatusResponse)
def status_extracao(
    processing_id: str,
    auth: dict = Depends(require_auth),
) -> ExtractStatusResponse:
    pid = _parse_uuid(processing_id)
    db = SessionLocal()
    try:
        proc = db.get(Processamento, pid)
        if proc is None:
            raise HTTPException(status_code=404, detail="Processamento não encontrado.")

        empresa_nome: str | None = None
        if proc.empresa_id:
            empresa = db.get(Empresa, proc.empresa_id)
            empresa_nome = empresa.nome if empresa else None

        resultado = proc.resultado_json or {}
        # Se o resultado não veio pelo campo, mas temos proposta em storage
        # (aguardando_cadastro), avisamos no match_type.
        match_type = resultado.get("match_type") if isinstance(resultado, dict) else None

        return ExtractStatusResponse(
            processing_id=str(proc.id),
            status=proc.status,
            empresa_id=str(proc.empresa_id) if proc.empresa_id else None,
            empresa_nome=empresa_nome,
            esqueleto_id=str(proc.esqueleto_id) if proc.esqueleto_id else None,
            cnpj_detectado=resultado.get("cnpj_detectado") if isinstance(resultado, dict) else None,
            match_type=match_type,
            metodo_usado=proc.metodo_usado or None,
            score_conformidade=proc.score_conformidade,
            resultado_json=resultado if resultado else None,
            avisos=resultado.get("avisos", []) if isinstance(resultado, dict) else [],
            detalhe_erro=resultado.get("mensagem") if isinstance(resultado, dict) else None,
            tempo_processamento_ms=proc.tempo_processamento_ms,
        )
    finally:
        db.close()


@router.get("/{processing_id}/pdf")
def baixar_pdf(processing_id: str, auth: dict = Depends(require_auth)) -> Response:
    """Serve o PDF original para o frontend renderizar no PDF.js."""
    entrada = storage.get_pdf(processing_id)
    if entrada is None:
        raise HTTPException(status_code=404, detail="PDF não disponível (expirado ou concluído).")
    pdf_bytes, filename = entrada
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/{processing_id}/cadastro-proposta", response_model=CadastroPropostaResponse)
def cadastro_proposta(
    processing_id: str,
    auth: dict = Depends(require_auth),
) -> CadastroPropostaResponse:
    pid = _parse_uuid(processing_id)
    db = SessionLocal()
    try:
        proc = db.get(Processamento, pid)
        if proc is None:
            raise HTTPException(status_code=404, detail="Processamento não encontrado.")
        if proc.status != StatusProcessamento.AGUARDANDO_CADASTRO.value:
            raise HTTPException(
                status_code=409,
                detail=f"Processamento está em '{proc.status}', não aguardando cadastro.",
            )
    finally:
        db.close()

    payload = storage.get_proposta(processing_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Proposta expirou. Reenvie o PDF.")

    proposta = payload["proposta"]
    cnpj_detectado = payload.get("cnpj_detectado_no_pdf")
    return CadastroPropostaResponse(
        processing_id=processing_id,
        empresa_candidata_id=payload.get("empresa_candidata_id"),
        empresa_candidata_nome=payload.get("empresa_candidata_nome"),
        nome_empresa_sugerido=proposta.get("nome_empresa"),
        cnpjs_sugeridos=[formatar_cnpj(c) for c in proposta.get("cnpjs_sugeridos", [])],
        cnpj_detectado_no_pdf=formatar_cnpj(cnpj_detectado) if cnpj_detectado else None,
        fingerprint_hash=payload["fingerprint_hash"],
        nome_funcionario=proposta.get("nome_funcionario"),
        matricula=proposta.get("matricula"),
        periodo=proposta.get("periodo"),
        estrutura=proposta.get("estrutura") or {},
        amostra_linhas=proposta.get("amostra_linhas") or [],
        confianca=proposta.get("confianca"),
    )


@router.post("/{processing_id}/cadastro-confirmar", response_model=ExtractStatusResponse)
def cadastro_confirmar(
    processing_id: str,
    payload: CadastroConfirmarRequest,
    auth: dict = Depends(require_auth),
) -> ExtractStatusResponse:
    pid = _parse_uuid(processing_id)

    entrada_pdf = storage.get_pdf(processing_id)
    proposta_payload = storage.get_proposta(processing_id)
    if entrada_pdf is None or proposta_payload is None:
        raise HTTPException(status_code=404, detail="Sessão de cadastro expirou. Reenvie o PDF.")
    pdf_bytes, _ = entrada_pdf

    # Valida CNPJs de entrada (dígitos)
    cnpjs_normalizados: list[str] = []
    for c in payload.cnpjs:
        n = normalizar_cnpj(c)
        if n and validar_cnpj(n) and n not in cnpjs_normalizados:
            cnpjs_normalizados.append(n)

    db = SessionLocal()
    try:
        proc = db.get(Processamento, pid)
        if proc is None:
            raise HTTPException(status_code=404, detail="Processamento não encontrado.")

        # Empresa: usa existente (empresa_id) ou cria nova
        empresa: Empresa | None = None
        if payload.empresa_id:
            empresa = db.get(Empresa, _parse_uuid(payload.empresa_id))
            if empresa is None:
                raise HTTPException(status_code=404, detail="Empresa informada não existe.")
        else:
            empresa = Empresa(
                nome=payload.nome_empresa.strip(),
                criada_por=session_id_short(auth),
            )
            db.add(empresa)
            db.flush()

        # CNPJs novos, evitando duplicação
        for cnpj in cnpjs_normalizados:
            existe = db.query(EmpresaCNPJ).filter(EmpresaCNPJ.cnpj == cnpj).first()
            if existe is None:
                db.add(EmpresaCNPJ(empresa_id=empresa.id, cnpj=cnpj))
            elif existe.empresa_id != empresa.id:
                raise HTTPException(
                    status_code=409,
                    detail=f"CNPJ {formatar_cnpj(cnpj)} já vinculado a outra empresa.",
                )

        # Determina próxima versão de esqueleto da empresa
        versao_anterior = (
            db.query(Esqueleto)
            .filter(Esqueleto.empresa_id == empresa.id)
            .order_by(Esqueleto.versao.desc())
            .first()
        )
        proxima_versao = (versao_anterior.versao + 1) if versao_anterior else 1

        # Se existe versão ativa anterior, desativa (nova vira a ativa)
        if versao_anterior and versao_anterior.status == StatusEsqueleto.ATIVO.value:
            versao_anterior.status = StatusEsqueleto.INATIVO.value

        esqueleto = Esqueleto(
            empresa_id=empresa.id,
            versao=proxima_versao,
            status=StatusEsqueleto.ATIVO.value,
            fingerprint=proposta_payload["fingerprint_hash"],
            estrutura=payload.estrutura,
            exemplos_validados=payload.exemplos_validados,
            criado_por=session_id_short(auth),
        )
        db.add(esqueleto)
        db.flush()

        # Aplica o esqueleto recém-criado ao PDF atual e grava resultado
        resultado = aplicar_esqueleto(pdf_bytes, esqueleto)
        score = calcular_score(resultado, esqueleto)

        settings = get_settings()
        if score >= settings.SCORE_CONFORMIDADE_MIN:
            status_final = StatusProcessamento.SUCESSO.value
        elif score >= settings.SCORE_CONFORMIDADE_ALERTA:
            status_final = StatusProcessamento.SUCESSO_COM_AVISO.value
        else:
            status_final = StatusProcessamento.SUCESSO_COM_AVISO.value

        proc.empresa_id = empresa.id
        proc.esqueleto_id = esqueleto.id
        proc.status = status_final
        proc.metodo_usado = resultado.metodo_efetivo
        proc.score_conformidade = score
        proc.resultado_json = {
            "cabecalho": resultado.cabecalho,
            "linhas": resultado.linhas,
            "avisos": resultado.avisos,
            "empresa_nome": empresa.nome,
            "cnpj_detectado": proposta_payload.get("cnpj_detectado_no_pdf"),
            "fingerprint": proposta_payload["fingerprint_hash"],
            "match_type": "cadastro_assistido_confirmado",
        }
        proc.tempo_processamento_ms = (proc.tempo_processamento_ms or 0) + resultado.tempo_ms

        # Breakdown do score entra em resultado_json para debug
        from app.services.conformidade import breakdown_como_dict, calcular_score_detalhado
        breakdown = calcular_score_detalhado(resultado, esqueleto)
        resultado_json = dict(proc.resultado_json or {})
        resultado_json["score_breakdown"] = breakdown_como_dict(breakdown)
        proc.resultado_json = resultado_json

        db.commit()
        atualizar_metricas_esqueleto(db, esqueleto, score)

        # Limpa storage
        storage.remove_pdf(processing_id)
        storage.remove_proposta(processing_id)

        return ExtractStatusResponse(
            processing_id=processing_id,
            status=proc.status,
            empresa_id=str(empresa.id),
            empresa_nome=empresa.nome,
            esqueleto_id=str(esqueleto.id),
            metodo_usado=proc.metodo_usado,
            score_conformidade=proc.score_conformidade,
            resultado_json=proc.resultado_json,
            avisos=resultado.avisos,
            tempo_processamento_ms=proc.tempo_processamento_ms,
        )
    finally:
        db.close()


@router.post("-api", response_model=ApiExtractExternalResponse)
def extract_api_externa(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    id_processo: str | None = Form(default=None),
    id_documento: str | None = Form(default=None),
    webhook_url: str | None = Form(default=None),
    auth: dict = Depends(require_auth),
) -> ApiExtractExternalResponse:
    """
    Endpoint para integrações externas. Faz upload + processamento em
    background e dispara webhook quando concluir. O cliente recebe
    `processing_id` e pode (a) aguardar o webhook ou (b) fazer polling
    em /api/extract/{id}/status.
    """
    settings = get_settings()
    pdf_bytes = file.file.read()
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if len(pdf_bytes) > max_bytes:
        raise HTTPException(status_code=413, detail="Arquivo grande demais.")

    try:
        validar_pdf_bytes(pdf_bytes)
    except PDFPasswordProtectedError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except PDFTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc))
    except PDFInvalidError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    ip = _client_ip(request)
    if not upload_limiter.check_and_record(ip):
        raise HTTPException(
            status_code=429,
            detail="Limite de uploads atingido. Aguarde e tente novamente.",
        )

    effective_webhook = webhook_url or settings.DEFAULT_WEBHOOK_URL
    if not effective_webhook:
        raise HTTPException(
            status_code=400,
            detail="webhook_url obrigatório (no form ou DEFAULT_WEBHOOK_URL).",
        )
    _validar_webhook_url(effective_webhook)

    db = SessionLocal()
    try:
        proc = Processamento(
            id=uuid.uuid4(),
            nome_arquivo_original=file.filename or "arquivo.pdf",
            metodo_usado="",
            status=StatusProcessamento.EM_PROCESSAMENTO.value,
            id_processo=id_processo,
            id_documento=id_documento,
            criado_por=session_id_short(auth),
        )
        db.add(proc)
        db.commit()
        proc_id = proc.id
    finally:
        db.close()

    storage.put_pdf(str(proc_id), pdf_bytes, file.filename or "arquivo.pdf")
    storage.put_metadata(str(proc_id), {"webhook_url": effective_webhook})
    background_tasks.add_task(processar_em_background, proc_id)

    return ApiExtractExternalResponse(
        processing_id=str(proc_id),
        status=StatusProcessamento.EM_PROCESSAMENTO.value,
    )


@router.post("/{processing_id}/cadastro-cancelar")
def cadastro_cancelar(
    processing_id: str,
    auth: dict = Depends(require_auth),
) -> dict[str, bool]:
    pid = _parse_uuid(processing_id)
    db = SessionLocal()
    try:
        proc = db.get(Processamento, pid)
        if proc is None:
            raise HTTPException(status_code=404, detail="Processamento não encontrado.")
        proc.status = StatusProcessamento.FALHOU.value
        proc.resultado_json = {"erro": "cancelado", "mensagem": "Cadastro cancelado pelo usuário."}
        db.commit()
    finally:
        db.close()
    storage.remove_pdf(processing_id)
    storage.remove_proposta(processing_id)
    return {"ok": True}
