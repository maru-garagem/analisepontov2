"""
Sweeper: marca como `falhou` processamentos órfãos (stuck em
`em_processamento` ou `aguardando_cadastro` além do TTL do storage).

Resolve 3 cenários:
  1. Container reiniciou durante BackgroundTask (em_processamento orphan).
  2. Usuário abandonou o cadastro assistido (aguardando_cadastro > 1h).
  3. PDF ou proposta expiraram do storage em memória.

Roda no startup da app e lazy no listar de histórico.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.enums import MetodoExtracao, StatusProcessamento
from app.models.processamento import Processamento
from app.services import storage

logger = logging.getLogger(__name__)

LIMITE_EM_PROCESSAMENTO_MIN = 10  # > que qualquer extração razoável
LIMITE_AGUARDANDO_CADASTRO_MIN = 60  # igual ao TTL do storage


def varrer_orfaos(db: Session) -> int:
    """
    Marca como `falhou` processamentos stuck. Retorna quantidade afetada.
    """
    agora = datetime.now(timezone.utc)
    cutoff_em_processamento = agora - timedelta(minutes=LIMITE_EM_PROCESSAMENTO_MIN)
    cutoff_aguardando = agora - timedelta(minutes=LIMITE_AGUARDANDO_CADASTRO_MIN)

    afetados = 0

    orfaos_em_proc = (
        db.query(Processamento)
        .filter(Processamento.status == StatusProcessamento.EM_PROCESSAMENTO.value)
        .filter(Processamento.criado_em < cutoff_em_processamento)
        .all()
    )
    for p in orfaos_em_proc:
        p.status = StatusProcessamento.FALHOU.value
        p.metodo_usado = p.metodo_usado or MetodoExtracao.FALHOU.value
        p.resultado_json = {
            "erro": "orfao",
            "mensagem": "Processamento abandonado — provavelmente o container reiniciou durante o pipeline.",
        }
        storage.remove_pdf(str(p.id))
        storage.remove_proposta(str(p.id))
        storage.remove_metadata(str(p.id))
        afetados += 1

    orfaos_aguardando = (
        db.query(Processamento)
        .filter(Processamento.status == StatusProcessamento.AGUARDANDO_CADASTRO.value)
        .filter(Processamento.criado_em < cutoff_aguardando)
        .all()
    )
    for p in orfaos_aguardando:
        p.status = StatusProcessamento.FALHOU.value
        p.resultado_json = {
            "erro": "cadastro_nao_confirmado",
            "mensagem": (
                "Cadastro assistido não confirmado dentro do prazo de 1h — "
                "o PDF foi descartado. Reenvie o arquivo."
            ),
        }
        storage.remove_pdf(str(p.id))
        storage.remove_proposta(str(p.id))
        storage.remove_metadata(str(p.id))
        afetados += 1

    if afetados:
        db.commit()
        logger.info("sweeper_marcou_orfaos total=%d", afetados)
    return afetados


def cadastro_pode_ser_retomado(processamento_id: str) -> bool:
    """
    Só pode retomar cadastro se PDF e proposta ainda estão no storage.
    Chamado pelo frontend antes de redirecionar para /cadastro-assistido.html.
    """
    return (
        storage.get_pdf(processamento_id) is not None
        and storage.get_proposta(processamento_id) is not None
    )
