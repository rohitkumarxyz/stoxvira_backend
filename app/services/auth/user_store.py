"""In-memory user store for authentication."""

import secrets
from datetime import datetime, timedelta
from typing import Optional

import bcrypt

from app.services.auth.models import LoginResponse, SessionToken, User


class UserStore:
    """In-memory storage for users and session tokens.

    This class provides user registration, authentication, and session management
    using bcrypt for password hashing and in-memory dictionaries for storage.
    """

    def __init__(self, token_expiry_hours: int = 24):
        """Initialize the user store.

        Args:
            token_expiry_hours: Number of hours before a session token expires
        """
        self._users: dict[str, User] = {}  # user_id -> User
        self._users_by_username: dict[str, User] = {}  # username -> User
        self._sessions: dict[str, SessionToken] = {}  # token -> SessionToken
        self._token_expiry_hours = token_expiry_hours

    def create_user(self, username: str, email: str, password: str) -> User:
        """Create a new user with hashed password.

        Args:
            username: Unique username
            email: User email address
            password: Plain text password (will be hashed)

        Returns:
            Created User object

        Raises:
            ValueError: If username already exists
        """
        if username in self._users_by_username:
            raise ValueError(f"Username '{username}' already exists")

        # Generate unique user ID
        user_id = secrets.token_urlsafe(16)

        # Hash password using bcrypt
        hashed_password = self._hash_password(password)

        # Create user
        user = User(
            id=user_id, username=username, email=email, hashed_password=hashed_password
        )

        # Store user
        self._users[user_id] = user
        self._users_by_username[username] = user

        return user

    def verify_credentials(self, username: str, password: str) -> Optional[User]:
        """Verify user credentials.

        Args:
            username: Username to verify
            password: Plain text password to verify

        Returns:
            User object if credentials are valid, None otherwise
        """
        user = self._users_by_username.get(username)
        if not user:
            return None

        # Verify password using bcrypt
        if not self._verify_password(password, user.hashed_password):
            return None

        return user

    def create_session(self, user: User) -> LoginResponse:
        """Create a new session token for a user.

        Args:
            user: User to create session for

        Returns:
            LoginResponse with token and user information
        """
        # Generate secure random token
        token = secrets.token_urlsafe(32)

        # Calculate expiration time
        expires_at = datetime.utcnow() + timedelta(hours=self._token_expiry_hours)

        # Create session token
        session = SessionToken(
            token=token, user_id=user.id, expires_at=expires_at, is_active=True
        )

        # Store session
        self._sessions[token] = session

        # Return login response
        return LoginResponse(
            token=token,
            user={"id": user.id, "username": user.username, "email": user.email},
            expires_at=expires_at,
        )

    def get_session(self, token: str) -> Optional[SessionToken]:
        """Retrieve a session by token.

        Args:
            token: Session token

        Returns:
            SessionToken if found, None otherwise
        """
        return self._sessions.get(token)

    def validate_session(self, token: str) -> Optional[User]:
        """Validate a session token and return the associated user.

        Args:
            token: Session token to validate

        Returns:
            User object if session is valid, None otherwise
        """
        session = self.get_session(token)
        if not session or not session.is_valid():
            return None

        return self._users.get(session.user_id)

    def invalidate_session(self, token: str) -> bool:
        """Invalidate a session token.

        Args:
            token: Session token to invalidate

        Returns:
            True if session was invalidated, False if not found
        """
        session = self._sessions.get(token)
        if not session:
            return False

        session.is_active = False
        return True

    def get_user_by_id(self, user_id: str) -> Optional[User]:
        """Retrieve a user by ID.

        Args:
            user_id: User ID

        Returns:
            User object if found, None otherwise
        """
        return self._users.get(user_id)

    def get_user_by_username(self, username: str) -> Optional[User]:
        """Retrieve a user by username.

        Args:
            username: Username

        Returns:
            User object if found, None otherwise
        """
        return self._users_by_username.get(username)

    @staticmethod
    def _hash_password(password: str) -> str:
        """Hash a password using bcrypt.

        Args:
            password: Plain text password

        Returns:
            Hashed password string
        """
        salt = bcrypt.gensalt()
        hashed = bcrypt.hashpw(password.encode("utf-8"), salt)
        return hashed.decode("utf-8")

    @staticmethod
    def _verify_password(password: str, hashed_password: str) -> bool:
        """Verify a password against a hashed password.

        Args:
            password: Plain text password
            hashed_password: Hashed password to verify against

        Returns:
            True if password matches, False otherwise
        """
        return bcrypt.checkpw(password.encode("utf-8"), hashed_password.encode("utf-8"))
