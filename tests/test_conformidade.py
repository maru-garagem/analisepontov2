from __future__ import annotations

from types import SimpleNamespace

from app.services.conformidade import (
    _celula_bem_parseada,
    calcular_score,
    calcular_score_detalhado,
    classificar_status_por_score,
)
from app.services.extracao_esqueleto import ResultadoExtracao


def _esqueleto_fake(estrutura: dict) -> SimpleNamespace:
    return SimpleNamespace(estrutura=estrutura, exemplos_validados=[], id="fake")


def _resultado(cabecalho=None, linhas=None, avisos=None):
    return ResultadoExtracao(
        cabecalho=cabecalho or {},
        linhas=linhas or [],
        metodo_efetivo="esqueleto_plumber",
        tempo_ms=10,
        avisos=avisos or [],
    )


class TestCelulaBemParseada:
    def test_hora_hh_mm(self):
        assert _celula_bem_parseada("hora", "08:30")

    def test_hora_com_segundos(self):
        assert _celula_bem_parseada("hora", "08:30:00")

    def test_hora_com_h_separator(self):
        assert _celula_bem_parseada("hora", "08h30")

    def test_hora_maiuscula_h(self):
        assert _celula_bem_parseada("hora", "08H30")

    def test_hora_invalida(self):
        assert not _celula_bem_parseada("hora", "xyz")

    def test_data_dd_mm_yyyy(self):
        assert _celula_bem_parseada("data", "01/03/2026")

    def test_data_dd_mm(self):
        assert _celula_bem_parseada("data", "01/03")

    def test_data_com_traco(self):
        assert _celula_bem_parseada("data", "01-03-2026")

    def test_vazio_nao_invalida(self):
        assert _celula_bem_parseada("hora", None)
        assert _celula_bem_parseada("hora", "")

    def test_texto_sempre_ok(self):
        assert _celula_bem_parseada("texto", "qualquer")


class TestCalcularScore:
    def test_extracao_perfeita_da_1(self):
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
        assert calcular_score(res, esq) == 1.0

    def test_horas_em_formato_h_nao_penalizam(self):
        """Formato 08h30 é legítimo e deve passar no validador permissivo."""
        esq = _esqueleto_fake({
            "cabecalho": {"empresa": {}},
            "tabela": {"colunas": [{"nome": "entrada", "tipo": "hora"}]},
        })
        res = _resultado(
            cabecalho={"empresa": "X"},
            linhas=[{"entrada": "08h30"}, {"entrada": "17h45"}],
        )
        assert calcular_score(res, esq) == 1.0

    def test_sem_colunas_tipadas_nao_penaliza(self):
        """
        Esqueleto cuja tabela é só texto: componente de células vale 1.0.
        Cabeçalho 100% + linhas presentes → 100% total.
        """
        esq = _esqueleto_fake({
            "cabecalho": {"a": {}},
            "tabela": {"colunas": [{"nome": "obs", "tipo": "texto"}]},
        })
        res = _resultado(
            cabecalho={"a": "x"},
            linhas=[{"obs": "normal"}, {"obs": "feriado"}],
        )
        assert calcular_score(res, esq) == 1.0

    def test_cabecalho_parcial(self):
        esq = _esqueleto_fake({
            "cabecalho": {"a": {}, "b": {}},
            "tabela": {"colunas": []},
        })
        res = _resultado(cabecalho={"a": "x", "b": None}, linhas=[{"z": 1}])
        # cabecalho 0.5, linhas 1.0, celulas 1.0 (sem tipadas)
        # 0.30*0.5 + 0.40*1 + 0.30*1 = 0.85
        score = calcular_score(res, esq)
        assert abs(score - 0.85) < 0.001

    def test_sem_linhas(self):
        esq = _esqueleto_fake({
            "cabecalho": {"a": {}},
            "tabela": {"colunas": []},
        })
        res = _resultado(cabecalho={"a": "x"}, linhas=[])
        # cab 1.0, linhas 0.0, cel 1.0 → 0.30 + 0 + 0.30 = 0.60
        assert abs(calcular_score(res, esq) - 0.60) < 0.001

    def test_avisos_penalizam_menos(self):
        esq = _esqueleto_fake({
            "cabecalho": {"a": {}},
            "tabela": {"colunas": []},
        })
        res = _resultado(cabecalho={"a": "x"}, linhas=[{"x": 1}], avisos=["a", "b"])
        # score base 1.0, penalidade 2*0.02 = 0.04 → 0.96
        assert abs(calcular_score(res, esq) - 0.96) < 0.001

    def test_penalidade_avisos_capped(self):
        esq = _esqueleto_fake({"cabecalho": {}, "tabela": {"colunas": []}})
        res = _resultado(linhas=[{"x": 1}], avisos=["w"] * 20)
        # penalidade capada em 0.10
        # cab 1 (0 campos → max(1,0)=1, preenchidos 0, mas 0/1 = 0; 0.30*0=0)
        # Espera, cabecalho vazio = 0 campos, preenchidos 0/max(1,0)=0/1=0.0
        # linhas 1.0, cel 1.0
        # 0.40 + 0.30*0 + 0.30 - 0.10 = 0.60
        assert abs(calcular_score(res, esq) - 0.60) < 0.001

    def test_score_limitado_entre_0_e_1(self):
        esq = _esqueleto_fake({"cabecalho": {"a": {}}, "tabela": {"colunas": []}})
        res = _resultado(cabecalho={"a": None}, linhas=[], avisos=["w"] * 50)
        score = calcular_score(res, esq)
        assert 0.0 <= score <= 1.0


class TestBreakdown:
    def test_retorna_componentes(self):
        esq = _esqueleto_fake({
            "cabecalho": {"a": {}, "b": {}},
            "tabela": {"colunas": [{"nome": "h", "tipo": "hora"}]},
        })
        res = _resultado(
            cabecalho={"a": "x", "b": None},
            linhas=[{"h": "08:00"}],
            avisos=["w"],
        )
        b = calcular_score_detalhado(res, esq)
        assert b.frac_cabecalho == 0.5
        assert b.tem_linhas == 1.0
        assert b.frac_celulas == 1.0
        assert b.tem_colunas_tipadas is True
        assert b.num_avisos == 1
        assert b.penalidade_avisos == 0.02


class TestClassificarStatus:
    def test_acima_do_min(self):
        assert classificar_status_por_score(0.95) == "sucesso"

    def test_abaixo_do_min(self):
        assert classificar_status_por_score(0.60) == "sucesso_com_aviso"
