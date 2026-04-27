"""
Testes de `identificar_empresa` com DB real (SQLite in-memory).
Mockam `gerar_fingerprint` e `extrair_texto_todo` para evitar dependência
de pdfplumber com PDFs sintéticos.
"""
from __future__ import annotations

from unittest.mock import patch

from app.models.empresa import Empresa, EmpresaCNPJ
from app.models.enums import StatusEsqueleto
from app.models.esqueleto import Esqueleto
from app.services.fingerprint import FingerprintInfo
from app.services.identificacao import identificar_empresa


def _fp(hash_: str) -> FingerprintInfo:
    return FingerprintInfo(
        hash=hash_,
        tokens=["entrada", "saida"],
        page_size=(595, 842),
        max_colunas=6,
        raw_canonical="canonical",
        versao="v2",
    )


def _patch_extracao(texto_completo: str, fp_hash: str):
    """Helper que monta os mocks para os dois pontos de entrada de PDF."""
    return (
        patch(
            "app.services.identificacao.extrair_texto_todo",
            return_value=[texto_completo],
        ),
        patch(
            "app.services.identificacao.gerar_fingerprint",
            return_value=_fp(fp_hash),
        ),
    )


def test_nenhum_match_retorna_nenhum(db_session):
    t1, t2 = _patch_extracao("texto sem cnpj", "deadbeef00000000")
    with t1, t2:
        r = identificar_empresa(b"pdf", db_session)
    assert r.empresa is None
    assert r.esqueleto is None
    assert r.match_type == "nenhum"


def test_match_por_fingerprint(db_session):
    empresa = Empresa(nome="ACME")
    db_session.add(empresa)
    db_session.flush()
    esq = Esqueleto(
        empresa_id=empresa.id,
        versao=1,
        status=StatusEsqueleto.ATIVO.value,
        fingerprint="aaaaaaaabbbbbbbb",
        estrutura={},
        exemplos_validados=[],
    )
    db_session.add(esq)
    db_session.commit()

    t1, t2 = _patch_extracao("texto sem cnpj", "aaaaaaaabbbbbbbb")
    with t1, t2:
        r = identificar_empresa(b"pdf", db_session)
    assert r.match_type == "fingerprint_somente"
    assert r.empresa.id == empresa.id
    assert r.esqueleto.id == esq.id


def test_match_exato_fingerprint_e_cnpj(db_session):
    empresa = Empresa(nome="ACME")
    db_session.add(empresa)
    db_session.flush()
    db_session.add(EmpresaCNPJ(empresa_id=empresa.id, cnpj="11222333000181"))
    esq = Esqueleto(
        empresa_id=empresa.id,
        versao=1,
        status=StatusEsqueleto.ATIVO.value,
        fingerprint="ccccdddd00000000",
        estrutura={},
        exemplos_validados=[],
    )
    db_session.add(esq)
    db_session.commit()

    t1, t2 = _patch_extracao(
        "Empresa ACME LTDA CNPJ 11.222.333/0001-81",
        "ccccdddd00000000",
    )
    with t1, t2:
        r = identificar_empresa(b"pdf", db_session)
    assert r.match_type == "exato"
    assert r.empresa.id == empresa.id
    assert r.esqueleto.id == esq.id
    assert r.cnpj_detectado == "11222333000181"


def test_cnpj_bate_mas_fingerprint_nao(db_session):
    """Cenário: empresa trocou de sistema de ponto (layout novo)."""
    empresa = Empresa(nome="ACME")
    db_session.add(empresa)
    db_session.flush()
    db_session.add(EmpresaCNPJ(empresa_id=empresa.id, cnpj="11222333000181"))
    # Esqueleto com fingerprint antigo
    esq_antigo = Esqueleto(
        empresa_id=empresa.id,
        versao=1,
        status=StatusEsqueleto.ATIVO.value,
        fingerprint="fingerprintantigo",
        estrutura={},
        exemplos_validados=[],
    )
    db_session.add(esq_antigo)
    db_session.commit()

    t1, t2 = _patch_extracao(
        "Empresa ACME CNPJ 11.222.333/0001-81",
        "fingerprintnovo00",  # diferente do cadastrado
    )
    with t1, t2:
        r = identificar_empresa(b"pdf", db_session)
    assert r.match_type == "cnpj_somente"
    assert r.empresa.id == empresa.id
    assert r.esqueleto is None


def test_esqueleto_inativo_nao_e_usado(db_session):
    empresa = Empresa(nome="ACME")
    db_session.add(empresa)
    db_session.flush()
    esq = Esqueleto(
        empresa_id=empresa.id,
        versao=1,
        status=StatusEsqueleto.INATIVO.value,
        fingerprint="zzzz0000zzzz0000",
        estrutura={},
        exemplos_validados=[],
    )
    db_session.add(esq)
    db_session.commit()

    t1, t2 = _patch_extracao("qualquer", "zzzz0000zzzz0000")
    with t1, t2:
        r = identificar_empresa(b"pdf", db_session)
    assert r.esqueleto is None
    assert r.match_type == "nenhum"


def test_match_por_fingerprint_em_lista_extra(db_session):
    """
    Match deve acontecer mesmo se o fingerprint NÃO for o principal,
    desde que esteja em `Esqueleto.fingerprints` (lista). Cobre o caso
    "operador anexou fingerprint à versão atual" (decisão 2b).
    """
    empresa = Empresa(nome="ACME")
    db_session.add(empresa)
    db_session.flush()
    db_session.add(EmpresaCNPJ(empresa_id=empresa.id, cnpj="11222333000181"))
    esq = Esqueleto(
        empresa_id=empresa.id,
        versao=1,
        status=StatusEsqueleto.ATIVO.value,
        fingerprint="principal00000000",
        # O fingerprint do PDF que vai chegar está apenas na lista
        # secundária — o "principal" é diferente.
        fingerprints=["principal00000000", "secundario1111111"],
        estrutura={},
        exemplos_validados=[],
    )
    db_session.add(esq)
    db_session.commit()

    t1, t2 = _patch_extracao(
        "Empresa ACME CNPJ 11.222.333/0001-81",
        "secundario1111111",
    )
    with t1, t2:
        r = identificar_empresa(b"pdf", db_session)
    assert r.esqueleto is not None
    assert r.esqueleto.id == esq.id
    # match_type vira "exato" porque CNPJ + esqueleto da mesma empresa.
    assert r.match_type == "exato"
