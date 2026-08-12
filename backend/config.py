"""Runtime configuration shared by the standalone backend server."""

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "docflow_ds160.sqlite3"

DEFAULT_ALLOWED_ORIGINS = (
    "http://127.0.0.1:4175",
    "http://localhost:4175",
    "http://127.0.0.1:5173",
    "http://localhost:5173",
)


def env_flag(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def bind_host():
    return os.environ.get("DOCFLOW_BACKEND_HOST", "127.0.0.1").strip() or "127.0.0.1"


def bind_port(default=4176):
    value = os.environ.get("DOCFLOW_BACKEND_PORT", "").strip()
    return int(value) if value else default


def allowed_origins():
    configured = os.environ.get("DOCFLOW_ALLOWED_ORIGINS", "")
    if not configured.strip():
        return set(DEFAULT_ALLOWED_ORIGINS)
    return {
        origin.strip().rstrip("/")
        for origin in configured.split(",")
        if origin.strip()
    }


def cookie_same_site():
    value = os.environ.get("DOCFLOW_COOKIE_SAMESITE", "Lax").strip().capitalize()
    if value not in {"Strict", "Lax", "None"}:
        raise ValueError("DOCFLOW_COOKIE_SAMESITE must be Strict, Lax, or None")
    return value


def cookie_secure():
    return env_flag("DOCFLOW_COOKIE_SECURE", default=False)


def cookie_domain():
    return os.environ.get("DOCFLOW_COOKIE_DOMAIN", "").strip()
