#!/usr/bin/env python3
"""Validate production environment files without printing secret values."""

import os
import re
import stat
import sys
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = Path(
    os.environ.get("DOCFLOW_DEPLOY_DIR", ROOT / "deploy")
).expanduser().resolve()
PLACEHOLDERS = ("replace-me", "replace-with", "example.com", "synthetic")


def read_env(path):
    values = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def private_file(path, errors):
    if not path.is_file():
        errors.append(f"missing {path.name}")
        return {}
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        errors.append(f"{path.name} permissions must be 600 or stricter")
    return read_env(path)


def require(values, keys, label, errors):
    for key in keys:
        value = values.get(key, "")
        if not value:
            errors.append(f"{label} missing {key}")
        elif any(marker in value.lower() for marker in PLACEHOLDERS):
            errors.append(f"{label} contains placeholder {key}")


def main():
    errors = []
    production = private_file(DEPLOY / "production.env", errors)
    postgres = private_file(DEPLOY / "postgres.env", errors)
    backend = private_file(DEPLOY / "backend.env", errors)
    agent = private_file(DEPLOY / "agent.env", errors)
    if errors and not all((production, postgres, backend, agent)):
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)

    require(production, ("DOCFLOW_DATABASE_URL", "DOCFLOW_RELEASE"), "production.env", errors)
    require(postgres, ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"), "postgres.env", errors)
    require(agent, ("GEMINI_API_KEY", "AGENT_CHECKPOINT_ENCRYPTION_KEY"), "agent.env", errors)

    database_url = urlparse(production.get("DOCFLOW_DATABASE_URL", ""))
    if database_url.scheme not in {"postgres", "postgresql"}:
        errors.append("DOCFLOW_DATABASE_URL must use PostgreSQL")
    if database_url.hostname != "postgres" or database_url.port != 5432:
        errors.append("DOCFLOW_DATABASE_URL must target postgres:5432")
    if database_url.username != postgres.get("POSTGRES_USER"):
        errors.append("PostgreSQL username does not match DOCFLOW_DATABASE_URL")
    if database_url.password != postgres.get("POSTGRES_PASSWORD"):
        errors.append("PostgreSQL password does not match DOCFLOW_DATABASE_URL")
    if database_url.path.lstrip("/") != postgres.get("POSTGRES_DB"):
        errors.append("PostgreSQL database name does not match DOCFLOW_DATABASE_URL")

    release = production.get("DOCFLOW_RELEASE", "")
    if release and not re.fullmatch(r"[A-Za-z0-9._-]{6,80}", release):
        errors.append("DOCFLOW_RELEASE contains unsafe characters")
    if production.get("DOCFLOW_BIND_ADDRESS", "127.0.0.1") != "127.0.0.1":
        errors.append("DOCFLOW_BIND_ADDRESS must be 127.0.0.1")
    if production.get("DOCFLOW_COOKIE_SECURE", "true").lower() != "true":
        errors.append("DOCFLOW_COOKIE_SECURE must be true")
    if backend.get("DOCFLOW_MEMBERSHIP_BYPASS", "false").lower() == "true":
        errors.append("DOCFLOW_MEMBERSHIP_BYPASS must not be true in production")
    if agent.get("AGENT_ALLOW_PLAINTEXT_CHECKPOINTS", "false").lower() != "false":
        errors.append("plaintext Agent checkpoints must be disabled")

    ocr_provider = (production.get("OCR_PROVIDER") or backend.get("OCR_PROVIDER") or "auto").lower()
    if ocr_provider == "mineru":
        require(backend, ("MINERU_API_TOKEN",), "backend.env", errors)
    verification = (
        backend.get("REGISTRATION_VERIFICATION")
        or backend.get("REGISTRATION_VERIFICATION_MODE")
        or "none"
    ).lower()
    if verification == "email":
        require(
            backend,
            ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "MAIL_FROM"),
            "backend.env",
            errors,
        )

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
    print("production environment preflight passed; secret values were not printed")


if __name__ == "__main__":
    main()
