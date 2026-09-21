"""Unit tests for JWT token manager."""

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import jwt
import pytest

from app.core.config import get_settings
from app.services.auth.token_manager import (
    TokenExpiredError,
    TokenInvalidError,
    TokenManager,
    TokenRevokedError,
    get_token_manager,
)


@pytest.fixture
def token_manager():
    """Create a fresh token manager instance for each test."""
    return TokenManager()


@pytest.fixture
def user_id():
    """Sample user ID for testing."""
    return "test-user-123"


class TestTokenCreation:
    """Tests for token creation functionality."""

    def test_create_token_returns_valid_token(self, token_manager, user_id):
        """Test that create_token returns a valid JWT token string."""
        token, expires_at = token_manager.create_token(user_id)

        assert isinstance(token, str)
        assert len(token) > 0
        assert isinstance(expires_at, datetime)

    def test_create_token_expiration_time(self, token_manager, user_id):
        """Test that token expiration is set to 24 hours from now."""
        before_creation = datetime.now(timezone.utc)
        token, expires_at = token_manager.create_token(user_id)
        after_creation = datetime.now(timezone.utc)

        settings = get_settings()
        expected_min = before_creation + timedelta(hours=settings.jwt_expiration_hours)
        expected_max = after_creation + timedelta(hours=settings.jwt_expiration_hours)

        assert expected_min <= expires_at <= expected_max

    def test_create_token_payload_contains_user_id(self, token_manager, user_id):
        """Test that created token contains the correct user ID in payload."""
        token, _ = token_manager.create_token(user_id)

        # Decode without verification to check payload
        payload = jwt.decode(token, options={"verify_signature": False})

        assert payload["sub"] == user_id
        assert "exp" in payload
        assert "iat" in payload


class TestTokenVerification:
    """Tests for token verification functionality."""

    def test_verify_token_returns_correct_data(self, token_manager, user_id):
        """Test that verify_token correctly decodes a valid token."""
        token, expected_expires_at = token_manager.create_token(user_id)

        token_data = token_manager.verify_token(token)

        assert token_data.user_id == user_id
        assert token_data.expires_at == expected_expires_at

    def test_verify_token_raises_on_expired_token(self, token_manager, user_id):
        """Test that verify_token raises TokenExpiredError for expired tokens."""
        # Create token with very short expiration
        with patch("app.services.auth.token_manager.get_settings") as mock_settings:
            mock_settings.return_value.jwt_expiration_hours = 0
            mock_settings.return_value.jwt_secret = get_settings().jwt_secret
            mock_settings.return_value.jwt_algorithm = get_settings().jwt_algorithm

            temp_manager = TokenManager()
            token, _ = temp_manager.create_token(user_id)

        # Wait for token to expire
        time.sleep(2)

        with pytest.raises(TokenExpiredError, match="Token has expired"):
            token_manager.verify_token(token)

    def test_verify_token_raises_on_invalid_token(self, token_manager):
        """Test that verify_token raises TokenInvalidError for malformed tokens."""
        invalid_token = "invalid.token.string"

        with pytest.raises(TokenInvalidError, match="Invalid token"):
            token_manager.verify_token(invalid_token)

    def test_verify_token_raises_on_tampered_token(self, token_manager, user_id):
        """Test that verify_token raises TokenInvalidError for tampered tokens."""
        token, _ = token_manager.create_token(user_id)

        # Tamper with the token
        tampered_token = token[:-5] + "xxxxx"

        with pytest.raises(TokenInvalidError):
            token_manager.verify_token(tampered_token)

    def test_verify_token_raises_on_wrong_secret(self, token_manager, user_id):
        """Test that tokens signed with different secret are rejected."""
        # Create token with different secret
        wrong_secret = "different-secret-key"
        payload = {
            "sub": user_id,
            "iat": int(datetime.now(timezone.utc).timestamp()),
            "exp": int(
                (datetime.now(timezone.utc) + timedelta(hours=24)).timestamp()
            ),
        }
        token = jwt.encode(payload, wrong_secret, algorithm="HS256")

        with pytest.raises(TokenInvalidError):
            token_manager.verify_token(token)


class TestTokenRevocation:
    """Tests for token revocation functionality."""

    def test_revoke_token_adds_to_blacklist(self, token_manager, user_id):
        """Test that revoke_token adds the token to blacklist."""
        token, _ = token_manager.create_token(user_id)

        # Token should be valid before revocation
        token_data = token_manager.verify_token(token)
        assert token_data.user_id == user_id

        # Revoke the token
        token_manager.revoke_token(token)

        # Token should now raise TokenRevokedError
        with pytest.raises(TokenRevokedError, match="Token has been revoked"):
            token_manager.verify_token(token)

    def test_revoke_token_on_invalid_token(self, token_manager):
        """Test that revoking an invalid token raises TokenInvalidError."""
        invalid_token = "invalid.token.string"

        with pytest.raises(TokenInvalidError, match="Cannot revoke token"):
            token_manager.revoke_token(invalid_token)

    def test_revoke_multiple_tokens(self, token_manager):
        """Test that multiple tokens can be revoked independently."""
        user1_id = "user-1"
        user2_id = "user-2"

        token1, _ = token_manager.create_token(user1_id)
        token2, _ = token_manager.create_token(user2_id)

        # Revoke only token1
        token_manager.revoke_token(token1)

        # token1 should be revoked
        with pytest.raises(TokenRevokedError):
            token_manager.verify_token(token1)

        # token2 should still be valid
        token_data = token_manager.verify_token(token2)
        assert token_data.user_id == user2_id


class TestTokenManagerSingleton:
    """Tests for get_token_manager dependency injection."""

    def test_get_token_manager_returns_singleton(self):
        """Test that get_token_manager returns the same instance."""
        manager1 = get_token_manager()
        manager2 = get_token_manager()

        assert manager1 is manager2

    def test_get_token_manager_returns_token_manager_instance(self):
        """Test that get_token_manager returns a TokenManager instance."""
        manager = get_token_manager()

        assert isinstance(manager, TokenManager)


class TestEdgeCases:
    """Tests for edge cases and error conditions."""

    def test_cleanup_expired_revoked_tokens(self, token_manager, user_id):
        """Test that expired tokens are cleaned up from revocation blacklist."""
        # Create token with very short expiration
        with patch("app.services.auth.token_manager.get_settings") as mock_settings:
            mock_settings.return_value.jwt_expiration_hours = 0
            mock_settings.return_value.jwt_secret = get_settings().jwt_secret
            mock_settings.return_value.jwt_algorithm = get_settings().jwt_algorithm

            temp_manager = TokenManager()
            token, _ = temp_manager.create_token(user_id)

        # Revoke the token
        token_manager.revoke_token(token)
        assert token in token_manager._revoked_tokens

        # Wait for token to expire
        time.sleep(2)

        # Create another token to trigger cleanup
        new_token, _ = token_manager.create_token(user_id)
        token_manager.revoke_token(new_token)

        # Expired token should be cleaned up from blacklist
        assert token not in token_manager._revoked_tokens

    def test_empty_user_id(self, token_manager):
        """Test token creation with empty user ID."""
        token, _ = token_manager.create_token("")

        token_data = token_manager.verify_token(token)
        assert token_data.user_id == ""

    def test_special_characters_in_user_id(self, token_manager):
        """Test token creation with special characters in user ID."""
        special_user_id = "user@example.com|123-456"
        token, _ = token_manager.create_token(special_user_id)

        token_data = token_manager.verify_token(token)
        assert token_data.user_id == special_user_id
