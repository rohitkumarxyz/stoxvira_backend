from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.core.config import get_settings
from app.services.market.exceptions import InstrumentsUnavailable, MarketDataError
from app.services.market.feed import get_market_feed
from app.services.upstox.exceptions import (
    UpstoxAuthError,
    UpstoxNotConfigured,
    UpstoxTokenExpired,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Close the Upstox websocket on shutdown.

    Upstox counts open sockets per user, so leaking one on every restart would
    lock us out after a couple of reloads.
    """
    yield
    await get_market_feed().stop()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        debug=settings.debug,
        lifespan=lifespan,
    )
    app.include_router(api_router, prefix=settings.api_prefix)
    register_exception_handlers(app)
    return app


def register_exception_handlers(app: FastAPI) -> None:
    """Map integration errors to HTTP responses once, for every route.

    Any future endpoint that calls get_access_token() gets a clean 401
    instead of a 500 when the token has expired.
    """

    @app.exception_handler(UpstoxTokenExpired)
    async def _token_expired(request: Request, exc: UpstoxTokenExpired) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": str(exc)}
        )

    @app.exception_handler(UpstoxNotConfigured)
    async def _not_configured(
        request: Request, exc: UpstoxNotConfigured
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": str(exc)},
        )

    @app.exception_handler(UpstoxAuthError)
    async def _auth_error(request: Request, exc: UpstoxAuthError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY, content={"detail": str(exc)}
        )

    @app.exception_handler(MarketDataError)
    async def _market_error(request: Request, exc: MarketDataError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY, content={"detail": str(exc)}
        )

    @app.exception_handler(InstrumentsUnavailable)
    async def _instruments_error(
        request: Request, exc: InstrumentsUnavailable
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": str(exc)},
        )


app = create_app()
