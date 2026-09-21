from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse

from app.services.upstox.schemas import UpstoxCallbackResponse, UpstoxStatusResponse
from app.services.upstox.service import UpstoxAuthService, get_upstox_auth_service

router = APIRouter(prefix="/auth/upstox", tags=["upstox-auth"])


@router.get("/login")
async def login(
    service: UpstoxAuthService = Depends(get_upstox_auth_service),
) -> RedirectResponse:
    """Send the browser to Upstox's login page."""
    return RedirectResponse(
        service.start_login(), status_code=status.HTTP_307_TEMPORARY_REDIRECT
    )


@router.get("/callback", response_model=UpstoxCallbackResponse)
async def callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    service: UpstoxAuthService = Depends(get_upstox_auth_service),
) -> UpstoxCallbackResponse:
    """Where Upstox drops the user after a successful login."""
    if not code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Upstox did not return a code.",
        )
    if not service.verify_state(state):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unknown or already-used state. Start again at /auth/upstox/login.",
        )

    token = await service.complete_login(code)
    return UpstoxCallbackResponse(
        status="connected",
        expires_at=token.expires_at,
        user_id=token.user_id,
        user_name=token.user_name,
    )


@router.get("/status", response_model=UpstoxStatusResponse)
async def connection_status(
    service: UpstoxAuthService = Depends(get_upstox_auth_service),
) -> UpstoxStatusResponse:
    return service.get_status()


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    service: UpstoxAuthService = Depends(get_upstox_auth_service),
) -> None:
    service.logout()
