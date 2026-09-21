from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Stoxvira Backend"
    environment: str = "development"
    debug: bool = True
    version: str = "0.1.0"
    api_prefix: str = ""

    # JWT Authentication
    jwt_secret: SecretStr = SecretStr("your-secret-key-change-in-production")
    jwt_algorithm: str = "HS256"
    jwt_expiration_hours: int = 24

    # Upstox. Values live in .env — never commit them.
    upstox_api_key: str = ""
    upstox_api_secret: SecretStr = SecretStr("")
    upstox_redirect_uri: str = "http://127.0.0.1:8000/auth/upstox/callback"
    upstox_base_url: str = "https://api.upstox.com/v2"
    upstox_token_path: Path = Path(".tokens/upstox.json")

    # Market data. v3 is used because only v3 returns the previous close,
    # which the percentage change is measured against.
    upstox_market_base_url: str = "https://api.upstox.com/v3"
    upstox_instruments_url: str = (
        "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"
    )
    instruments_cache_path: Path = Path(".cache/upstox-instruments.json.gz")
    instruments_max_age_seconds: int = 12 * 60 * 60
    quote_cache_seconds: int = 10

    # AI analysis. The key lives only in the backend .env.
    groq_api_key: SecretStr = SecretStr("")
    groq_model: str = "openai/gpt-oss-120b"
    groq_base_url: str = "https://api.groq.com/openai/v1"

    # Live feed. Upstox permits only a couple of concurrent sockets per user,
    # so the app opens exactly one and fans it out.
    upstox_feed_url: str = "wss://api.upstox.com/v3/feed/market-data-feed"
    upstox_feed_mode: str = "ltpc"


@lru_cache
def get_settings() -> Settings:
    return Settings()
