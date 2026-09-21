import pytest

from app.services.upstox.client import UpstoxClient
from app.services.upstox.service import UpstoxAuthService
from app.services.upstox.storage import TokenStore

BASE_URL = "https://api.upstox.com/v2"


@pytest.fixture
def store(tmp_path) -> TokenStore:
    return TokenStore(tmp_path / "upstox.json")


@pytest.fixture
def client() -> UpstoxClient:
    return UpstoxClient(
        api_key="test-key",
        api_secret="test-secret",
        redirect_uri="http://127.0.0.1:8000/auth/upstox/callback",
        base_url=BASE_URL,
    )


@pytest.fixture
def service(client: UpstoxClient, store: TokenStore) -> UpstoxAuthService:
    return UpstoxAuthService(client, store)
