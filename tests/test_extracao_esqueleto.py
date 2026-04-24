from __future__ import annotations

from app.services.extracao_esqueleto import (
    _parse_data,
    _parse_hora,
    _parse_numero,
    eh_linha_descartavel,
    eh_linha_header,
    extrair_campo_cabecalho,
    parse_celula,
    processar_linha,
)


class TestParseHora:
    def test_hh_mm(self):
        assert _parse_hora("08:30") == "08:30"

    def test_com_espaco(self):
        assert _parse_hora(" 08:30 ") == "08:30"

    def test_h_separator(self):
        assert _parse_hora("08h30") == "08:30"

    def test_um_digito_na_hora(self):
        assert _parse_hora("8:05") == "08:05"

    def test_invalida(self):
        assert _parse_hora("25:61") is None
        assert _parse_hora("xx:yy") is None
        assert _parse_hora("") is None
        assert _parse_hora(None) is None  # type: ignore[arg-type]


class TestParseData:
    def test_dd_mm_yyyy(self):
        assert _parse_data("01/03/2026") == "01/03/2026"

    def test_dd_mm_yy(self):
        assert _parse_data("01/03/26") == "01/03/2026"

    def test_dd_mm_com_ano_default(self):
        assert _parse_data("01/03", ano_default=2026) == "01/03/2026"

    def test_dd_mm_sem_ano_default(self):
        assert _parse_data("01/03") == "01/03"

    def test_invalida(self):
        assert _parse_data("abc") is None
        assert _parse_data("") is None


class TestParseNumero:
    def test_inteiro(self):
        assert _parse_numero("10") == 10.0

    def test_decimal_com_virgula(self):
        assert _parse_numero("8,5") == 8.5

    def test_com_separador_milhar(self):
        assert _parse_numero("1.234,56") == 1234.56

    def test_invalido(self):
        assert _parse_numero("abc") is None
        assert _parse_numero("") is None


class TestParseCelula:
    def test_hora_falha_preserva_texto(self):
        assert parse_celula("hora", "manutencao", {}) == "manutencao"

    def test_celula_vazia_usa_default(self):
        assert parse_celula("hora", "", {"celula_vazia_valor": None}) is None
        assert parse_celula("hora", "   ", {"celula_vazia_valor": "--"}) == "--"

    def test_texto_strip(self):
        assert parse_celula("texto", "  abc  ", {}) == "abc"


class TestCabecalho:
    def test_ancora_regex_captura_grupo(self):
        regra = {"tipo": "ancora_regex", "regex": r"(?i)funcion[aá]rio:\s+([^\n]+)"}
        texto = "Empresa X\nFuncionário: João da Silva\nOutro"
        assert extrair_campo_cabecalho(texto, regra) == "João da Silva"

    def test_ancora_sem_grupo_usa_match_inteiro(self):
        regra = {"tipo": "ancora_regex", "regex": r"[A-Z]{3,}"}
        texto = "xyz ABC def"
        assert extrair_campo_cabecalho(texto, regra) == "ABC"

    def test_ancora_nao_encontra(self):
        regra = {"tipo": "ancora_regex", "regex": r"inexistente"}
        assert extrair_campo_cabecalho("qualquer coisa", regra) is None

    def test_ancora_regex_invalida(self):
        regra = {"tipo": "ancora_regex", "regex": r"([unclosed"}
        assert extrair_campo_cabecalho("x", regra) is None

    def test_cnpj(self):
        regra = {"tipo": "regex_cnpj"}
        texto = "CNPJ 11.222.333/0001-81 válido"
        assert extrair_campo_cabecalho(texto, regra) == "11.222.333/0001-81"

    def test_cnpj_sem_match(self):
        regra = {"tipo": "regex_cnpj"}
        assert extrair_campo_cabecalho("sem cnpj aqui", regra) is None

    def test_literal(self):
        regra = {"tipo": "literal", "valor": "Empresa Fixa"}
        assert extrair_campo_cabecalho("qualquer", regra) == "Empresa Fixa"


class TestLinhasTabela:
    def test_header_detectado(self):
        linha = ["Data", "Entrada", "Saída", "Total"]
        assert eh_linha_header(linha, r"data.*entrada.*sa[ií]da")

    def test_header_nao_detectado(self):
        linha = ["01/03", "08:00", "17:00", "09:00"]
        assert not eh_linha_header(linha, r"data.*entrada.*sa[ií]da")

    def test_linha_vazia_descartada(self):
        assert eh_linha_descartavel(["", "", None], [])

    def test_linha_total_descartada(self):
        linha = ["Total geral", "", "", "200:00"]
        assert eh_linha_descartavel(linha, [r"^total"])

    def test_linha_normal_nao_descartada(self):
        linha = ["01/03", "08:00", "17:00", "09:00"]
        assert not eh_linha_descartavel(linha, [r"^total"])


class TestProcessarLinha:
    def test_mapeia_por_nome_e_tipo(self):
        colunas = [
            {"nome": "data", "tipo": "data"},
            {"nome": "entrada", "tipo": "hora"},
            {"nome": "saida", "tipo": "hora"},
            {"nome": "obs", "tipo": "texto"},
        ]
        parsing = {"ano_default": 2026, "celula_vazia_valor": None}
        linha = ["01/03", "08:00", "17:30", "  "]
        r = processar_linha(linha, colunas, parsing)
        assert r == {
            "data": "01/03/2026",
            "entrada": "08:00",
            "saida": "17:30",
            "obs": None,
        }

    def test_linha_curta_completa_com_nulo(self):
        colunas = [
            {"nome": "a", "tipo": "texto"},
            {"nome": "b", "tipo": "texto"},
        ]
        r = processar_linha(["só_a"], colunas, {"celula_vazia_valor": None})
        assert r == {"a": "só_a", "b": None}
