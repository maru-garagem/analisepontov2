from __future__ import annotations

from types import SimpleNamespace

from app.services.conformidade import (
    _celula_bem_parseada,
    calcular_score,
    classificar_status_por_score,
)
from app.services.extracao_esqueleto import ResultadoExtracao


def _esqueleto_fake(estrutura: dict) -> SimpleNamespace:
    return SimpleNamespace(estrutura=estrutura, exemplos_validados=[])


def _resultado(cabecalho=None, linhas=None, avisos=None):
    return ResultadoExtracao(
        cabecalho=cabecalho or {},
        linhas=linhas or [],
        metodo_efetivo="esqueleto_plumber",
        tempo_ms=10,
        avisos=avisos or [],
    )


class TestCelulaBemParseada:
    def test_hora_valida(self):
        assert _celula_bem_parseada("hora", "08:30")

    def test_hora_invalida(self):
        assert not _celula_bem_parseada("hora", "xyz")

    def test_data_valida_dd_mm_yyyy(self):
        assert _celula_bem_parseada("data", "01/03/2026")

    def test_data_valida_dd_mm(self):
        assert _celula_bem_parseada("data", "01/03")

    def test_vazio_nao_invalida(self):
        assert _celula_bem_parseada("hora", None)
        assert _celula_bem_parseada("hora", "")

    def test_texto_sempre_ok(self):
        assert _celula_bem_parseada("texto", "qualquer")


class TestCalcularScore:
    def test_cabecalho_completo_linhas_com_horas_ok(self):
        esq = _esqueleto_fake({
            "cabecalho": {"empresa": {}, "cnpj": {}, "funcionario": {}},
            "tabela": {
                "colunas": [
                    {"nome": "entrada", "tipo": "hora"},
                    {"nome": "saida", "tipo": "hora"},
                ]
            },
        })
        res = _resultado(
            cabecalho={"empresa": "ACME", "cnpj": "123", "funcionario": "José"},
            linhas=[
                {"entrada": "08:00", "saida": "17:00"},
                {"entrada": "08:15", "saida": "17:30"},
            ],
        )
        score = calcular_score(res, esq)
        assert score == 1.0

    def test_cabecalho_parcial_e_celulas_invalidas(self):
        esq = _esqueleto_fake({
            "cabecalho": {"empresa": {}, "cnpj": {}},
            "tabela": {"colunas": [{"nome": "entrada", "tipo": "hora"}]},
        })
        res = _resultado(
            cabecalho={"empresa": "ACME", "cnpj": None},
            linhas=[
                {"entrada": "08:00"},   # ok
                {"entrada": "xxx"},     # inválido
            ],
        )
        score = calcular_score(res, esq)
        # Cabecalho 1/2 = 0.5, linhas 1.0, celulas_validas 1/2 = 0.5
        # Score = 0.3*0.5 + 0.3*1.0 + 0.4*0.5 = 0.65
        assert abs(score - 0.65) < 0.001

    def test_sem_linhas_zera_peso_de_linhas(self):
        esq = _esqueleto_fake({
            "cabecalho": {"a": {}, "b": {}},
            "tabela": {"colunas": []},
        })
        res = _resultado(cabecalho={"a": "x", "b": "y"}, linhas=[])
        # Cabecalho 1.0, linhas 0.0, celulas 1.0 (nenhuma coluna tipada)
        # Score = 0.3 + 0.0 + 0.4 = 0.7
        assert abs(calcular_score(res, esq) - 0.7) < 0.001

    def test_avisos_penalizam(self):
        esq = _esqueleto_fake({
            "cabecalho": {"a": {}},
            "tabela": {"colunas": []},
        })
        res_sem = _resultado(cabecalho={"a": "x"}, linhas=[{"x": 1}])
        res_com = _resultado(
            cabecalho={"a": "x"},
            linhas=[{"x": 1}],
            avisos=["w1", "w2"],
        )
        assert calcular_score(res_sem, esq) > calcular_score(res_com, esq)

    def test_score_limitado_entre_0_e_1(self):
        esq = _esqueleto_fake({
            "cabecalho": {"a": {}},
            "tabela": {"colunas": []},
        })
        res = _resultado(
            cabecalho={"a": None},
            linhas=[],
            avisos=["w"] * 10,  # penalidade máxima 0.2
        )
        score = calcular_score(res, esq)
        assert 0.0 <= score <= 1.0


class TestClassificarStatus:
    def test_acima_do_min(self):
        assert classificar_status_por_score(0.95) == "sucesso"

    def test_abaixo_do_min(self):
        assert classificar_status_por_score(0.60) == "sucesso_com_aviso"
