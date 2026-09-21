"""Data models for authentication service."""

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class User(BaseModel):
    """User model representing a registered user."""

    id: str = Field(..., description="Unique user identifier")
    username: str = Field(..., min_length=3, max_length=50, description="Username")
    email: EmailStr = Field(..., description="User email address")
    hashed_password: str = Field(..., description="Bcrypt hashed password")

    class Config:
        from_attributes = True


class LoginRequest(BaseModel):
    """Request model for user login."""

    username: str = Field(..., min_length=3, max_length=50, description="Username")
    password: str = Field(..., min_length=6, description="User password")


class LoginResponse(BaseModel):
    """Response model for successful login."""

    token: str = Field(..., description="Session token")
    user: dict = Field(..., description="User information (id, username, email)")
    expires_at: datetime = Field(..., description="Token expiration timestamp")


class SessionToken(BaseModel):
    """Session token model for user sessions."""

    token: str = Field(..., description="Unique session token")
    user_id: str = Field(..., description="Associated user ID")
    created_at: datetime = Field(
        default_factory=datetime.utcnow, description="Token creation timestamp"
    )
    expires_at: datetime = Field(..., description="Token expiration timestamp")
    is_active: bool = Field(default=True, description="Token active status")

    def is_expired(self) -> bool:
        """Check if the token has expired."""
        return datetime.utcnow() > self.expires_at

    def is_valid(self) -> bool:
        """Check if the token is valid (active and not expired)."""
        return self.is_active and not self.is_expired()
