#!/usr/bin/env python3
import os
import re
import shlex
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
GITIGNORE_PATH = ROOT / ".gitignore"
BLOCK_START = "# DocFlow mail settings (managed by 配置邮箱验证.command)"
BLOCK_END = "# End DocFlow mail settings"


def ensure_env_is_ignored():
    existing = GITIGNORE_PATH.read_text(encoding="utf-8") if GITIGNORE_PATH.exists() else ""
    patterns = {
        line.strip()
        for line in existing.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    if ".env" not in patterns:
        separator = "" if not existing or existing.endswith("\n") else "\n"
        with GITIGNORE_PATH.open("a", encoding="utf-8") as target:
            target.write(f"{separator}.env\n")


def load_env_file(path=ENV_PATH):
    values = {}
    if not Path(path).exists():
        return values
    assignment = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        match = assignment.match(line)
        if not match:
            continue
        raw = match.group(2).strip()
        try:
            parsed = shlex.split(raw, posix=True)
            values[match.group(1)] = parsed[0] if parsed else ""
        except ValueError:
            continue
    return values


def update_env_file(updates, remove_keys=(), path=ENV_PATH):
    path = Path(path)
    ensure_env_is_ignored()
    managed_keys = set(updates) | set(remove_keys)
    assignment = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
    current = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    preserved = []
    managed_values = {}
    inside_managed_block = False
    for line in current:
        if line.strip() == BLOCK_START:
            inside_managed_block = True
            continue
        if line.strip() == BLOCK_END:
            inside_managed_block = False
            continue
        if inside_managed_block:
            match = assignment.match(line)
            if match:
                raw = line.split("=", 1)[1].strip()
                try:
                    parsed = shlex.split(raw, posix=True)
                    managed_values[match.group(1)] = parsed[0] if parsed else ""
                except ValueError:
                    pass
            continue
        match = assignment.match(line)
        if match and match.group(1) in managed_keys:
            continue
        preserved.append(line)

    for key in remove_keys:
        managed_values.pop(key, None)
    managed_values.update({key: str(value) for key, value in updates.items()})

    while preserved and not preserved[-1].strip():
        preserved.pop()
    if preserved:
        preserved.append("")
    preserved.append(BLOCK_START)
    for key, value in managed_values.items():
        preserved.append(f"export {key}={shlex.quote(str(value))}")
    preserved.append(BLOCK_END)
    content = "\n".join(preserved) + "\n"

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
