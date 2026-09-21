"""JWT token manager for authentication."""

from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

import jwt
from pydantic import BaseModel

from app.core.config import get_settings


class TokenPayload(BaseModel):
    """JWT token payload structure."""

    sub: str  # Subject (user_id)
    exp: int  # Expiration timestamp
    iat: int  # Issued at timestamp


class TokenData(BaseModel):
    """Decoded token data."""

    user_id: str
    expires_at: datetime


class TokenError(Exception):
    """Base exception for token-related errors."""

    pass


class TokenExpiredError(TokenError):
    """Token has expired."""

    pass


class TokenInvalidError(TokenError):
    """Token is invalid or malformed."""

    pass


class TokenRevokedError(TokenError):
    """Token has been revoked."""

    pass


class TokenManager:
    """JWT-based token manager for user authentication.

    Handles token creation, verification, and revocation using JWT with HS256 algorithm.
    Maintains an in-memory blacklist for revoked tokens.
    """

    def __init__(self):
        """Initialize the token manager."""
        self._settings = get_settings()
        self._revoked_tokens: Dict[str, datetime] = {}

    def create_token(self, user_id: str) -> tuple[str, datetime]:
        """Create a new JWT token for a user.

        Args:
            user_id: The unique identifier of the user.

        Returns:
            A tuple of (token_string, expiration_datetime).
        """
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=self._settings.jwt_expiration_hours)

        payload = {
            "sub": user_id,
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
        }

        token = jwt.encode(
            payload,
            self._settings.jwt_secret.get_secret_value(),
            algorithm=self._settings.jwt_algorithm,
        )

        return token, expires_at

    def verify_token(self, token: str) -> TokenData:
        """Verify and decode a JWT token.

        Args:
            token: The JWT token string to verify.

        Returns:
            TokenData containing user_id and expiration timestamp.

        Raises:
            TokenExpiredError: If the token has expired.
            TokenInvalidError: If the token is malformed or invalid.
            TokenRevokedError: If the token has been revoked.
        """
        # Check if token is in revocation blacklist
        if token in self._revoked_tokens:
            raise TokenRevokedError("Token has been revoked")

        try:
            payload = jwt.decode(
                token,
                self._settings.jwt_secret.get_secret_value(),
                algorithms=[self._settings.jwt_algorithm],
            )

            user_id = payload.get("sub")
            exp_timestamp = payload.get("exp")

            if not user_id or not exp_timestamp:
                raise TokenInvalidError("Token payload is missing required fields")

            expires_at = datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)

            return TokenData(user_id=user_id, expires_at=expires_at)

        except jwt.ExpiredSignatureError:
            raise TokenExpiredError("Token has expired")
        except jwt.InvalidTokenError as e:
            raise TokenInvalidError(f"Invalid token: {str(e)}")
        except Exception as e:
            raise TokenInvalidError(f"Token verification failed: {str(e)}")

    def revoke_token(self, token: str) -> None:
        """Revoke a token by adding it to the blacklist.

        Args:
            token: The JWT token string to revoke.

        Raises:
            TokenInvalidError: If the token cannot be decoded.
        """
        try:
            # Decode without verification to get expiration time
            payload = jwt.decode(
                token,
                options={"verify_signature": False},
            )
            exp_timestamp = payload.get("exp")

            if exp_timestamp:
                expires_at = datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)
                self._revoked_tokens[token] = expires_at
                # Clean up expired tokens from blacklist
                self._cleanup_revoked_tokens()
            else:
                raise TokenInvalidError("Token has no expiration timestamp")

        except Exception as e:
            raise TokenInvalidError(f"Cannot revoke token: {str(e)}")

    def _cleanup_revoked_tokens(self) -> None:
        """Remove expired tokens from the revocation blacklist."""
        now = datetime.now(timezone.utc)
        expired_tokens = [
            token
            for token, exp_time in self._revoked_tokens.items()
            if exp_time < now
        ]
        for token in expired_tokens:
            del self._revoked_tokens[token]


# Global token manager instance
_token_manager: Optional[TokenManager] = None


def get_token_manager() -> TokenManager:
    """Get the global token manager instance (dependency injection).

    Returns:
        The TokenManager singleton instance.
    """
    global _token_manager
    if _token_manager is None:
        _token_manager = TokenManager()
    return _token_manager
