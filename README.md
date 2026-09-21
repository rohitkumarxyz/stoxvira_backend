# Stoxvira Backend

FastAPI service with Upstox OAuth login.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env      # then fill in UPSTOX_API_KEY and UPSTOX_API_SECRET
```

## Run

```bash
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

- Health check: http://127.0.0.1:8000/health
- Swagger docs: http://127.0.0.1:8000/docs

Run on port 8000 unless you also change `UPSTOX_REDIRECT_URI` and the redirect URL
registered in the Upstox developer console. The two must match exactly.

## Tests

```bash
pytest
```

Upstox is never called for real — HTTP is faked with `respx`.

## Upstox authentication

Upstox issues **no refresh token**, and every access token expires at **3:30 AM IST**
regardless of when it was created. So a token cannot be renewed silently — a browser
login is needed once per trading day.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/auth/upstox/login` | Redirects to the Upstox login page |
| `GET` | `/auth/upstox/callback` | Where Upstox returns with `?code=` — token is stored here |
| `GET` | `/auth/upstox/status` | Whether a valid token exists, and when it expires |
| `POST` | `/auth/upstox/logout` | Deletes the stored token |

To connect, open http://127.0.0.1:8000/auth/upstox/login in a browser and log in.
The token is written to `.tokens/upstox.json` (gitignored, mode 600) and survives
restarts.

### Using the token from other code

Never read the token file directly. Use:

```python
from app.services.upstox import get_access_token

token = await get_access_token()
```

It returns the stored token, or raises `UpstoxTokenExpired` when a fresh login is
needed. Any endpoint that lets that exception propagate returns `401` with an
instruction to re-login — the handler is registered in `app/main.py`.

## Market data

`GET /market/quotes?symbols=INFY,SBIN,NIFTY 50`

Accepts equity trading symbols and index names in any case or spacing
(`nifty50`, `NIFTY 50`, `Nifty 50` all work). Returns numbers, not formatted
strings — the UI decides how to show a rupee sign.

```json
{
  "quotes": [
    {
      "symbol": "INFY",
      "name": "INFOSYS LIMITED",
      "instrument_key": "NSE_EQ|INE009A01021",
      "last_price": 1051.4,
      "prev_close": 1058.6,
      "change": -7.2,
      "change_percent": -0.68,
      "up": false
    }
  ],
  "unresolved": ["TATAMOTORS"]
}
```

Symbols that do not map to an Upstox instrument land in `unresolved` instead
of failing the whole request.

### Live stream

`GET /market/stream?symbols=INFY,NIFTY 50` — server-sent events, one message
per price change.

```
event: meta
data: {"subscribed": 2, "unresolved": []}

event: quotes
data: {"quotes": [{"symbol": "INFY", "last_price": 1051.4, ...}]}

: ping
```

Each `quotes` payload holds the same shape `/market/quotes` returns, so a
client can merge both without special-casing either.

**Upstox allows only about two concurrent websocket connections per user.**
So the app opens exactly one, held by `MarketFeed`, and fans it out to every
SSE listener. Never open a socket per request or per browser — that breaks at
the third visitor. The connection closes on application shutdown, otherwise a
few restarts would exhaust the allowance.

Frames arrive Protobuf-encoded; see `app/services/market/proto/README.md` for
regenerating the decoder.

Notes:

- Prices only move 09:15–15:30 IST on trading days. Outside that the stream
  connects, sends the last close, then goes quiet apart from a heartbeat every
  15 seconds. That is not a broken feed.
- Quotes are cached in memory for 10 seconds, so many visitors polling the
  same tape make one Upstox call, not one each.
- Symbol lookup uses Upstox's instrument master, cached at
  `.cache/upstox-instruments.json.gz` and re-downloaded when older than 12
  hours. First request after a cold start downloads ~3 MB.
- `USD/INR` has no spot instrument on Upstox — only dated futures. It cannot
  be quoted like an index.

## Layout

```
app/
  main.py                      app factory, exception handlers
  core/config.py               settings loaded from .env
  api/router.py                collects every route module
  api/routes/health.py         GET /health
  api/routes/upstox_auth.py    the Upstox auth endpoints
  api/routes/market.py         GET /market/quotes and /market/stream
  services/upstox/
    client.py                  the only file that knows Upstox's auth URLs
    storage.py                 the only file that knows the token is on disk
    service.py                 login handshake, expiry rules, get_access_token()
    schemas.py                 pydantic models
    exceptions.py              UpstoxAuthError, UpstoxTokenExpired, ...
  services/market/
    client.py                  the only file that knows the market-quote URLs
    instruments.py             symbol -> Upstox instrument key lookup
    feed.py                    the single Upstox websocket, fanned out
    service.py                 quote normalising and the 10s cache
    schemas.py                 Quote, QuotesResponse
    exceptions.py              MarketDataError, InstrumentsUnavailable
    proto/                     Upstox feed schema + generated decoder
```

Adding a new endpoint: create `app/api/routes/<name>.py` with its own `APIRouter`,
then include it in `app/api/router.py`.
# stoxvira_backend
