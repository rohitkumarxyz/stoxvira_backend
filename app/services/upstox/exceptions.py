class UpstoxError(Exception):
    """Base for every Upstox integration failure."""


class UpstoxAuthError(UpstoxError):
    """Upstox rejected the login or the token exchange."""


class UpstoxTokenExpired(UpstoxError):
    """No usable access token is stored — a fresh login is required."""


class UpstoxNotConfigured(UpstoxError):
    """API key / secret / redirect URI are missing from the environment."""
