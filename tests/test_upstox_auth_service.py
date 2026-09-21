from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from app.services.upstox.exceptions import UpstoxAuthError, UpstoxTokenExpired
from app.services.upstox.schemas import StoredToken
from app.services.upstox.service import UpstoxAuthService
from tests.conftest import BASE_URL

TOKEN_URL = f"{BASE_URL}/login/authorization/token"

UPSTOX_SUCCESS = {
    "email": "trader@example.com",
    "user_id": "ABC123",
    "user_name": "Test Trader",
    "broker": "UPSTOX",
    "is_active": True,
    "access_token": "the-access-token",
    "extended_token": "the-extended-token",
}


def test_login_url_carries_our_credentials(service: UpstoxAuthService):
    url = service.start_login()
    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    assert parsed.path.endswith("/login/authorization/dialog")
    assert params["client_id"] == ["test-key"]
    assert params["response_type"] == ["code"]
    assert params["redirect_uri"] == ["http://127.0.0.1:8000/auth/upstox/callback"]
    assert params["state"][0]


def test_state_is_accepted_once_then_rejected(service: UpstoxAuthService):
    state = parse_qs(urlparse(service.start_login()).query)["state"][0]

    assert service.verify_state(state) is True
    assert service.verify_state(state) is False


def test_unknown_state_is_rejected(service: UpstoxAuthService):
    assert service.verify_state("never-issued") is False
    assert service.verify_state(None) is False


@respx.mock
async def test_complete_login_stores_a_usable_token(service: UpstoxAuthService):
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json=UPSTOX_SUCCESS)
    )

    token = await service.complete_login("one-time-code")

    assert route.called
    sent = dict(parse_qs(route.calls.last.request.content.decode()))
    assert sent["code"] == ["one-time-code"]
    assert sent["grant_type"] == ["authorization_code"]
    assert sent["client_secret"] == ["test-secret"]

    assert token.access_token == "the-access-token"
    assert token.user_id == "ABC123"
    assert await service.get_access_token() == "the-access-token"


@respx.mock
async def test_token_survives_a_new_service_instance(service, client, store):
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=UPSTOX_SUCCESS))
    await service.complete_login("one-time-code")

    fresh = UpstoxAuthService(client, store)
    assert await fresh.get_access_token() == "the-access-token"


@respx.mock
async def test_upstox_error_becomes_auth_error(service: UpstoxAuthService):
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            400, json={"errors": [{"message": "Invalid auth code"}]}
        )
    )

    with pytest.raises(UpstoxAuthError, match="Invalid auth code"):
        await service.complete_login("stale-code")


@respx.mock
async def test_network_failure_becomes_auth_error(service: UpstoxAuthService):
    respx.post(TOKEN_URL).mock(side_effect=httpx.ConnectError("boom"))

    with pytest.raises(UpstoxAuthError, match="Could not reach Upstox"):
        await service.complete_login("any-code")


async def test_get_access_token_raises_when_nothing_stored(service: UpstoxAuthService):
    with pytest.raises(UpstoxTokenExpired, match="No Upstox token stored"):
        await service.get_access_token()


async def test_get_access_token_raises_when_expired(service, store):
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    store.save(
        StoredToken(
            access_token="stale",
            issued_at=past - timedelta(hours=8),
            expires_at=past,
        )
    )

    with pytest.raises(UpstoxTokenExpired, match="expired"):
        await service.get_access_token()


def test_status_reports_disconnected_when_expired(service, store):
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    store.save(
        StoredToken(access_token="stale", issued_at=past, expires_at=past)
    )

    assert service.get_status().connected is False


@respx.mock
async def test_status_reports_connected_without_leaking_the_token(service):
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=UPSTOX_SUCCESS))
    await service.complete_login("one-time-code")

    status = service.get_status()
    assert status.connected is True
    assert status.user_id == "ABC123"
    assert "the-access-token" not in status.model_dump_json()


@respx.mock
async def test_logout_clears_the_token(service):
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=UPSTOX_SUCCESS))
    await service.complete_login("one-time-code")

    service.logout()

    assert service.get_status().connected is False


def test_corrupt_token_file_is_treated_as_missing(store):
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("this is not json")

    assert store.load() is None
