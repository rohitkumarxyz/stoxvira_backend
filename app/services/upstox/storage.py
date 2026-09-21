import json
import os
from pathlib import Path

from app.services.upstox.schemas import StoredToken


class TokenStore:
    """Persists the Upstox token as a JSON file.

    The only place that knows the token lives on disk. Swap this class for a
    Redis or database version and nothing else has to change.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> StoredToken | None:
        if not self._path.exists():
            return None
        try:
            raw = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        try:
            return StoredToken.model_validate(raw)
        except ValueError:
            return None

    def save(self, token: StoredToken) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(token.model_dump_json(indent=2))
        os.chmod(tmp, 0o600)
        tmp.replace(self._path)

    def clear(self) -> None:
        self._path.unlink(missing_ok=True)
