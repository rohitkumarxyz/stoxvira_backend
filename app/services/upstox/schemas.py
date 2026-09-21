from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict


class UpstoxTokenResponse(BaseModel):
    """What Upstox sends back from the token exchange.

    Only the fields we care about are declared; Upstox may add more.
    """

    model_config = ConfigDict(extra="ignore")

    access_token: str
    extended_token: str | None = None
    email: str | None = None
    user_id: str | None = None
    user_name: str | None = None
    broker: str | None = None
    is_active: bool | None = None


class StoredToken(BaseModel):
    """What we persist on disk."""

    access_token: str
    issued_at: datetime
    expires_at: datetime
    user_id: str | None = None
    user_name: str | None = None
    email: str | None = None

    def is_expired(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        return now >= self.expires_at


class UpstoxStatusResponse(BaseModel):
    """Safe-to-expose connection state. Never carries the token itself."""

    connected: bool
    expires_at: datetime | None = None
    user_id: str | None = None
    user_name: str | None = None
    login_url_path: str = "/auth/upstox/login"


class UpstoxCallbackResponse(BaseModel):
    status: str
    expires_at: datetime
    user_id: str | None = None
    user_name: str | None = None
