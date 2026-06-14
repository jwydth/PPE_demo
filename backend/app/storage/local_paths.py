from pathlib import Path

from app.core.config import BACKEND_DIR, settings


def _resolve_backend_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = BACKEND_DIR / path
    return path.resolve()


SNAPSHOT_DIR = _resolve_backend_path(settings.SNAPSHOT_DIR)


def ensure_snapshot_dir() -> Path:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    return SNAPSHOT_DIR
