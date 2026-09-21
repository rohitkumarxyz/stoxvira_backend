from urllib.parse import urlencode

import httpx

from app.services.upstox.exceptions import UpstoxAuthError
from app.services.upstox.schemas import UpstoxTokenResponse

REQUEST_TIMEOUT = 15.0


class UpstoxClient:
    """The only place that knows Upstox's URLs and wire format."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        redirect_uri: str,
        base_url: str,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._redirect_uri = redirect_uri
        self._base_url = base_url.rstrip("/")

    def build_login_url(self, state: str) -> str:
        params = {
            "response_type": "code",
            "client_id": self._api_key,
            "redirect_uri": self._redirect_uri,
            "state": state,
        }
        return f"{self._base_url}/login/authorization/dialog?{urlencode(params)}"

    async def exchange_code(self, code: str) -> UpstoxTokenResponse:
        """Swap the one-time code from the callback for an access token."""
        payload = {
            "code": code,
            "client_id": self._api_key,
            "client_secret": self._api_secret,
            "redirect_uri": self._redirect_uri,
            "grant_type": "authorization_code",
        }
        headers = {
            "accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        }

        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            try:
                response = await client.post(
                    f"{self._base_url}/login/authorization/token",
                    data=payload,
                    headers=headers,
                )
            except httpx.HTTPError as exc:
                raise UpstoxAuthError(f"Could not reach Upstox: {exc}") from exc

        if response.status_code != httpx.codes.OK:
            raise UpstoxAuthError(
                f"Upstox rejected the token request ({response.status_code}): "
                f"{_error_detail(response)}"
            )

        return UpstoxTokenResponse.model_validate(response.json())


def _error_detail(response: httpx.Response) -> str:
    """Pull Upstox's own error message out, falling back to the raw body."""
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
