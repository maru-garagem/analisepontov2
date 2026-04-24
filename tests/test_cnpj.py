from __future__ import annotations

from app.services.identificacao import (
    extrair_cnpjs,
    formatar_cnpj,
    normalizar_cnpj,
    validar_cnpj,
)

# CNPJs conhecidamente válidos (exemplos de domínio público — Receita Federal).
CNPJ_VALIDO_1 = "11.222.333/0001-81"
CNPJ_VALIDO_1_DIGITS = "11222333000181"
CNPJ_VALIDO_2 = "34.028.316/0001-03"  # Caixa Econômica Federal (domínio público)
CNPJ_VALIDO_2_DIGITS = "34028316000103"

CNPJ_INVALIDO = "11.222.333/0001-82"  # DV errado
CNPJ_REPETIDO = "11.111.111/1111-11"
CNPJ_CURTO = "1234"


class TestValidarCnpj:
    def test_cnpj_valido_formatado(self):
        assert validar_cnpj(CNPJ_VALIDO_1)

    def test_cnpj_valido_so_digitos(self):
        assert validar_cnpj(CNPJ_VALIDO_1_DIGITS)

    def test_dv_errado(self):
        assert not validar_cnpj(CNPJ_INVALIDO)

    def test_todos_repetidos(self):
        assert not validar_cnpj(CNPJ_REPETIDO)

    def test_tamanho_errado(self):
        assert not validar_cnpj(CNPJ_CURTO)

    def test_string_vazia(self):
        assert not validar_cnpj("")


class TestExtrairCnpjs:
    def test_cnpj_formatado_no_meio_do_texto(self):
        texto = f"Empresa X Ltda, CNPJ: {CNPJ_VALIDO_1}, Rua Y, 123."
        assert extrair_cnpjs(texto) == [CNPJ_VALIDO_1_DIGITS]

    def test_cnpj_cru_14_digitos(self):
        texto = f"CNPJ {CNPJ_VALIDO_1_DIGITS} outros dados 9999"
        assert extrair_cnpjs(texto) == [CNPJ_VALIDO_1_DIGITS]

    def test_multiplos_cnpjs_unicos(self):
        texto = f"{CNPJ_VALIDO_1} e {CNPJ_VALIDO_2}"
        resultado = extrair_cnpjs(texto)
        assert set(resultado) == {CNPJ_VALIDO_1_DIGITS, CNPJ_VALIDO_2_DIGITS}

    def test_cnpj_invalido_ignorado(self):
        texto = f"Inválido: {CNPJ_INVALIDO} | Válido: {CNPJ_VALIDO_1}"
        assert extrair_cnpjs(texto) == [CNPJ_VALIDO_1_DIGITS]

    def test_texto_sem_cnpj(self):
        assert extrair_cnpjs("nada aqui") == []

    def test_dedupe_mesmo_cnpj_formas_diferentes(self):
        texto = f"{CNPJ_VALIDO_1} {CNPJ_VALIDO_1_DIGITS}"
        assert extrair_cnpjs(texto) == [CNPJ_VALIDO_1_DIGITS]


class TestFormatarENormalizar:
    def test_formatar_de_digitos(self):
        assert formatar_cnpj(CNPJ_VALIDO_1_DIGITS) == CNPJ_VALIDO_1

    def test_formatar_preserva_invalido_curto(self):
        assert formatar_cnpj("123") == "123"

    def test_normalizar_tira_pontuacao(self):
        assert normalizar_cnpj(CNPJ_VALIDO_1) == CNPJ_VALIDO_1_DIGITS
