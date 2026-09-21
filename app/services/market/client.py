from urllib.parse import quote

import httpx

from app.services.market.exceptions import MarketDataError

REQUEST_TIMEOUT = 15.0
MAX_KEYS_PER_CALL = 500


class MarketDataClient:
    """The only place that knows Upstox's market-data URLs."""

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    async def ltp(self, instrument_keys: list[str], access_token: str) -> dict:
        """Last traded price for each key."""
        if not instrument_keys:
            return {}
        if len(instrument_keys) > MAX_KEYS_PER_CALL:
            raise MarketDataError(
                f"Upstox accepts at most {MAX_KEYS_PER_CALL} instruments per call, "
                f"got {len(instrument_keys)}."
            )

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            try:
                response = await client.get(
                    f"{self._base_url}/market-quote/ltp",
                    params={"instrument_key": ",".join(instrument_keys)},
                    headers=headers,
                )
            except httpx.HTTPError as exc:
                raise MarketDataError(f"Could not reach Upstox: {exc}") from exc

        if response.status_code != httpx.codes.OK:
            raise MarketDataError(
                f"Upstox market-quote failed ({response.status_code}): "
                f"{_error_detail(response)}"
            )
        return response.json().get("data", {})

    async def historical_candles(
        self,
        instrument_key: str,
        unit: str,
        interval: str,
        to_date: str,
        from_date: str,
        access_token: str,
    ) -> list[list]:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }
        encoded_key = quote(instrument_key, safe="")
        url = (
            f"{self._base_url}/historical-candle/"
            f"{encoded_key}/{unit}/{interval}/{to_date}/{from_date}"
        )
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            try:
                response = await client.get(url, headers=headers)
            except httpx.HTTPError as exc:
                raise MarketDataError(f"Could not reach Upstox: {exc}") from exc

        if response.status_code != httpx.codes.OK:
            raise MarketDataError(
                f"Upstox historical-candle failed ({response.status_code}): "
                f"{_error_detail(response)}"
            )
        return response.json().get("data", {}).get("candles", [])


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:500]

    errors = body.get("errors") if isinstance(body, dict) else None
    if isinstance(errors, list) and errors:
        first = errors[0]
        if isinstance(first, dict):
            return str(first.get("message") or first)
    return str(body)[:500]
