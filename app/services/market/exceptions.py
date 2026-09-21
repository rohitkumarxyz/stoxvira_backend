from app.services.upstox.exceptions import UpstoxError


class MarketDataError(UpstoxError):
    """Upstox refused or could not serve a market data request."""


class InstrumentsUnavailable(UpstoxError):
    """The instrument master could not be downloaded or read."""
