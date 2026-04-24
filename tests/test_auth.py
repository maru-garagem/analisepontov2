from __future__ import annotations

from fastapi.testclient import TestClient

from main import app


def test_health_no_auth_required():
    client = TestClient(app)
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_login_wrong_password(test_password):
    client = TestClient(app)
    r = client.post("/api/auth/login", json={"password": "errada"})
    assert r.status_code == 401


def test_login_correct_password_sets_cookie(test_password):
    client = TestClient(app)
    r = client.post("/api/auth/login", json={"password": test_password})
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert client.cookies.get("pontoextract_session")


def test_me_without_cookie_returns_unauthenticated():
    client = TestClient(app)
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json() == {"authenticated": False}


def test_me_with_valid_cookie(test_password):
    client = TestClient(app)
    client.post("/api/auth/login", json={"password": test_password})
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json() == {"authenticated": True}


def test_logout_clears_cookie(test_password):
    client = TestClient(app)
    client.post("/api/auth/login", json={"password": test_password})
    r = client.post("/api/auth/logout")
    assert r.status_code == 200
    # Após logout, /me deve reportar não autenticado
    me = client.get("/api/auth/me")
    assert me.json() == {"authenticated": False}


def test_protected_api_route_rejects_without_cookie():
    # Usa uma rota /api/* inexistente: middleware deve bloquear antes do router.
    client = TestClient(app)
    r = client.get("/api/empresas")  # router não existe ainda, mas middleware roda primeiro
    assert r.status_code == 401
    assert r.json() == {"detail": "Não autenticado."}


def test_protected_api_route_accepts_with_cookie(test_password):
    client = TestClient(app)
    client.post("/api/auth/login", json={"password": test_password})
    r = client.get("/api/empresas")
    # Middleware passa; router não existe, então 404. O importante é NÃO ser 401.
    assert r.status_code != 401


def test_rate_limit_blocks_after_five_failed_attempts(test_password):
    client = TestClient(app)
    for _ in range(5):
        r = client.post("/api/auth/login", json={"password": "errada"})
        assert r.status_code == 401
    r6 = client.post("/api/auth/login", json={"password": "errada"})
    assert r6.status_code == 429


def test_rate_limit_resets_after_successful_login(test_password):
    client = TestClient(app)
    # 4 tentativas erradas, depois uma certa — limiter reseta
    for _ in range(4):
        client.post("/api/auth/login", json={"password": "errada"})
    ok = client.post("/api/auth/login", json={"password": test_password})
    assert ok.status_code == 200
    # Agora deveria ter 5 novas chances
    for _ in range(5):
        r = client.post("/api/auth/login", json={"password": "errada"})
        assert r.status_code == 401


def test_login_validates_payload():
    client = TestClient(app)
    r = client.post("/api/auth/login", json={})
    assert r.status_code == 422  # password ausente
