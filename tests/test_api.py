import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.main import app
from app.services.upstox.service import UpstoxAuthService, get_upstox_auth_service
from tests.conftest import BASE_URL
from tests.test_upstox_auth_service import UPSTOX_SUCCESS

TOKEN_URL = f"{BASE_URL}/login/authorization/token"


@pytest.fixture
def api(service: UpstoxAuthService):
    """A TestClient wired to a throwaway service, so tests never touch real config."""
    app.dependency_overrides[get_upstox_auth_service] = lambda: service
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_health(api: TestClient):
    response = api.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_login_redirects_to_upstox(api: TestClient):
    response = api.get("/auth/upstox/login", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"].startswith(
        f"{BASE_URL}/login/authorization/dialog"
    )


def test_callback_without_code_is_a_400(api: TestClient):
    response = api.get("/auth/upstox/callback")

    assert response.status_code == 400
    assert "did not return a code" in response.json()["detail"]


def test_callback_with_unknown_state_is_a_400(api: TestClient):
    response = api.get("/auth/upstox/callback", params={"code": "x", "state": "forged"})

    assert response.status_code == 400
    assert "Unknown or already-used state" in response.json()["detail"]


@respx.mock
def test_full_login_round_trip(api: TestClient):
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=UPSTOX_SUCCESS))

    redirect = api.get("/auth/upstox/login", follow_redirects=False)
    state = httpx.URL(redirect.headers["location"]).params["state"]

    response = api.get(
        "/auth/upstox/callback", params={"code": "one-time-code", "state": state}
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "connected"
    assert response.json()["user_id"] == "ABC123"

    status = api.get("/auth/upstox/status").json()
    assert status["connected"] is True
    assert "the-access-token" not in api.get("/auth/upstox/status").text


@respx.mock
def test_upstox_rejection_surfaces_as_502(api: TestClient):
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(400, json={"errors": [{"message": "bad code"}]})
    )

    redirect = api.get("/auth/upstox/login", follow_redirects=False)
    state = httpx.URL(redirect.headers["location"]).params["state"]

    response = api.get(
        "/auth/upstox/callback", params={"code": "stale", "state": state}
    )

    assert response.status_code == 502
    assert "bad code" in response.json()["detail"]


def test_status_is_disconnected_before_any_login(api: TestClient):
    response = api.get("/auth/upstox/status")

    assert response.status_code == 200
    assert response.json()["connected"] is False
