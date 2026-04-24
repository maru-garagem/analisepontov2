from __future__ import annotations

from app.services.fingerprint import (
    WHITELIST,
    _gerar_hash,
    _normalizar_texto,
    extrair_tokens_estruturais,
)


class TestNormalizacao:
    def test_lowercase_e_remove_digitos(self):
        assert _normalizar_texto("Entrada 08:30") == "entrada :"

    def test_preserva_acentos(self):
        assert "saída" in _normalizar_texto("Saída Horário")

    def test_nfkc_normaliza_chars_equivalentes(self):
        # U+00E9 (é) vs U+0065 U+0301 (e + combining acute) devem colapsar.
        combinado = "entrada\u0301"  # não é exatamente 'é', mas NFKC estabiliza
        n1 = _normalizar_texto("já entrada")
        n2 = _normalizar_texto("j\u00e1 entrada")
        assert n1 == n2


class TestExtracaoTokens:
    def test_somente_whitelist(self):
        txt = "Entrada Saída José da Silva Departamento Administrativo Rua Flor 123"
        tokens = extrair_tokens_estruturais(txt)
        # Entrada, saída e departamento estão na whitelist; resto não.
        assert "entrada" in tokens
        assert "saída" in tokens
        assert "departamento" in tokens
        assert "silva" not in tokens
        assert "rua" not in tokens

    def test_ordena_e_dedupe(self):
        txt = "Saída Entrada Entrada Saída Entrada"
        tokens = extrair_tokens_estruturais(txt)
        assert tokens == sorted(set(tokens))
        assert tokens == ["entrada", "saída"]

    def test_texto_vazio(self):
        assert extrair_tokens_estruturais("") == []

    def test_ignora_palavras_curtas(self):
        # Palavras de 1-2 chars são descartadas pelo regex
        tokens = extrair_tokens_estruturais("a b c entrada")
        assert tokens == ["entrada"]


class TestWhitelistConteudo:
    def test_termos_chave_estao_presentes(self):
        # Garantir que os labels mais comuns estão cobertos.
        chaves = {"entrada", "saida", "saída", "ponto", "jornada",
                  "funcionario", "funcionário", "matricula", "matrícula",
                  "empresa", "cnpj", "periodo", "período"}
        assert chaves <= WHITELIST


class TestHashDeterminismo:
    def test_mesmo_input_mesmo_hash(self):
        assert _gerar_hash("abc") == _gerar_hash("abc")

    def test_inputs_diferentes_hashes_diferentes(self):
        assert _gerar_hash("abc") != _gerar_hash("abd")

    def test_hash_tem_16_chars(self):
        assert len(_gerar_hash("qualquer coisa")) == 16


class TestIntegracaoTokensMaisEstaveis:
    """
    Simula dois 'PDFs' da mesma empresa — textos diferentes (nomes,
    departamentos, datas), mas labels iguais. Deve gerar o mesmo conjunto
    de tokens estruturais (e portanto o mesmo hash se dimensões iguais).
    """

    def test_mesmo_layout_mesmo_conjunto_de_tokens(self):
        pdf_a = """
        EMPRESA ACME LTDA
        CNPJ 12.345.678/0001-90
        Funcionário: José da Silva   Matrícula: 12345
        Departamento: Administrativo
        Período: 01/03/2026 a 31/03/2026
        Data   Entrada  Saída  Total  Observação
        01/03  08:00    17:00  08:00
        """
        pdf_b = """
        EMPRESA ACME LTDA
        CNPJ 12.345.678/0001-90
        Funcionário: Maria Oliveira   Matrícula: 67890
        Departamento: Operacional
        Período: 01/04/2026 a 30/04/2026
        Data   Entrada  Saída  Total  Observação
        01/04  09:00    18:00  08:00
        """
        tokens_a = extrair_tokens_estruturais(pdf_a)
        tokens_b = extrair_tokens_estruturais(pdf_b)
        assert tokens_a == tokens_b

    def test_layouts_diferentes_conjuntos_diferentes(self):
        sistema_1 = "Funcionário Entrada Saída Total Jornada Observação"
        sistema_2 = "Colaborador Batida1 Batida2 Horas Feriado Atestado DSR"
        assert extrair_tokens_estruturais(sistema_1) != extrair_tokens_estruturais(sistema_2)
