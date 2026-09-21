import secrets
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

from app.core.config import Settings, get_settings
from app.services.upstox.client import UpstoxClient
from app.services.upstox.exceptions import UpstoxNotConfigured, UpstoxTokenExpired
from app.services.upstox.schemas import (
    StoredToken,
    UpstoxStatusResponse,
    UpstoxTokenResponse,
)
from app.services.upstox.storage import TokenStore

IST = ZoneInfo("Asia/Kolkata")
EXPIRY_HOUR = 3
EXPIRY_MINUTE = 30


def calculate_expiry(issued_at: datetime) -> datetime:
    """Upstox tokens always die at 3:30 AM IST, whenever they were issued.

    A token made at 8 PM Tuesday expires 3:30 AM Wednesday. One made at
    2:30 AM Wednesday expires 3:30 AM the same Wednesday. Returned in UTC.
    """
    ist_now = issued_at.astimezone(IST)
    expiry = ist_now.replace(
        hour=EXPIRY_HOUR, minute=EXPIRY_MINUTE, second=0, microsecond=0
    )
    if ist_now >= expiry:
        expiry += timedelta(days=1)
    return expiry.astimezone(timezone.utc)


class UpstoxAuthService:
    """Owns the login handshake and hands out a usable access token."""

    def __init__(self, client: UpstoxClient, store: TokenStore) -> None:
        self._client = client
        self._store = store
        self._pending_states: set[str] = set()

    def start_login(self) -> str:
        """Return the Upstox URL the user's browser must be sent to."""
        state = secrets.token_urlsafe(24)
        self._pending_states.add(state)
        return self._client.build_login_url(state)

    def verify_state(self, state: str | None) -> bool:
        """Consume a state value issued by start_login. Single use."""
        if state is None or state not in self._pending_states:
            return False
        self._pending_states.discard(state)
        return True

    async def complete_login(self, code: str) -> StoredToken:
        """Exchange the callback code for a token and persist it."""
        response = await self._client.exchange_code(code)
        token = self._to_stored_token(response)
        self._store.save(token)
        return token

    async def get_access_token(self) -> str:
        """The single entry point every other service should use.

        Raises UpstoxTokenExpired when a fresh browser login is needed.
        """
        token = self._store.load()
        if token is None:
            raise UpstoxTokenExpired(
                "No Upstox token stored. Log in at /auth/upstox/login."
            )
        if token.is_expired():
            raise UpstoxTokenExpired(
                f"Upstox token expired at {token.expires_at.isoformat()}. "
                "Log in again at /auth/upstox/login."
            )
        return token.access_token

    def get_status(self) -> UpstoxStatusResponse:
        token = self._store.load()
        if token is None or token.is_expired():
            return UpstoxStatusResponse(connected=False)
        return UpstoxStatusResponse(
            connected=True,
            expires_at=token.expires_at,
            user_id=token.user_id,
            user_name=token.user_name,
        )

    def logout(self) -> None:
        self._store.clear()

    @staticmethod
    def _to_stored_token(response: UpstoxTokenResponse) -> StoredToken:
        issued_at = datetime.now(timezone.utc)
        return StoredToken(
            access_token=response.access_token,
            issued_at=issued_at,
            expires_at=calculate_expiry(issued_at),
            user_id=response.user_id,
            user_name=response.user_name,
            email=response.email,
        )


def build_service(settings: Settings) -> UpstoxAuthService:
    if not settings.upstox_api_key or not settings.upstox_api_secret.get_secret_value():
        raise UpstoxNotConfigured(
            "UPSTOX_API_KEY and UPSTOX_API_SECRET must be set in .env"
        )
    client = UpstoxClient(
        api_key=settings.upstox_api_key,
        api_secret=settings.upstox_api_secret.get_secret_value(),
        redirect_uri=settings.upstox_redirect_uri,
        base_url=settings.upstox_base_url,
    )
    return UpstoxAuthService(client, TokenStore(settings.upstox_token_path))


@lru_cache
def get_upstox_auth_service() -> UpstoxAuthService:
    return build_service(get_settings())


async def get_access_token() -> str:
    """Internal helper — call this from any service that needs the token.

        from app.services.upstox import get_access_token
        token = await get_access_token()
    """
    return await get_upstox_auth_service().get_access_token()
