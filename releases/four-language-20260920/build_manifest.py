#!/usr/bin/env python3
"""Build the frontend-only release manifest without altering original provenance."""
import hashlib
import json
from pathlib import Path

BASE_COMMIT = "e553b7f7fed5ed09af0914e006e6660dce3e477e"
ALLOWLIST = (
    "app.js", "billing.js", "contact.html", "extension-account.js",
    "extension.html", "intake-i18n.js", "membership.html", "privacy.html",
    "product.html", "refund-policy.html", "styles.css", "terms.html",
    "workspace.html", "site-language.css", "site-language.js", "site-translations.js",
)
PREFIX = "components/frontend/www/"
NGINX = "components/frontend/nginx/default.conf"
ROOT = Path(__file__).resolve().parents[2]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generate(root=ROOT):
    provenance = json.loads((root / "PROVENANCE.json").read_text())
    originals = {item["path"]: item["sha256"] for item in provenance["files"]}
    if len(originals) != 366:
        raise ValueError("Unexpected original manifest count")
    replacements = {PREFIX + name for name in ALLOWLIST}
    changed = []
    for path, expected in originals.items():
        target = root / path
        if target.is_symlink() or not target.is_file():
            raise ValueError("Missing or symlinked source: " + path)
        if digest(target) != expected:
            if path not in replacements:
                raise ValueError("Unexpected source change: " + path)
            changed.append(path)
    original_static = {path[len(PREFIX):]: value for path, value in originals.items()
                       if path.startswith(PREFIX)}
    payload = {name: digest(root / PREFIX / name) for name in ALLOWLIST}
    expected_static = {**original_static, **payload}
    actual_paths = {str(path.relative_to(root / PREFIX)) for path in (root / PREFIX).rglob("*")
                    if path.is_file()}
    if any(path.is_symlink() for path in (root / PREFIX).rglob("*")):
        raise ValueError("Symlinks are prohibited in static source")
    if actual_paths != set(expected_static) or len(original_static) != 45 or len(expected_static) != 48:
        raise ValueError("Unexpected static file set")
    if len(changed) != 13 or len(set(payload) - set(original_static)) != 3:
        raise ValueError("Expected exactly 13 replacements and 3 additions")
    return {
        "schema": 1, "base_commit": BASE_COMMIT,
        "provenance_sha256": digest(root / "PROVENANCE.json"),
        "base_image_id": provenance["components"]["frontend"]["image_id"],
        "payload": payload, "original_static": original_static,
        "expected_static": expected_static, "nginx_sha256": originals[NGINX],
        "original_source_files": 366, "replacements": 13, "additions": 3,
    }


if __name__ == "__main__":
    manifest = generate()
    output = Path(__file__).with_name("manifest.json")
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": "manifest_verified", "static_files": 48,
                      "replacements": 13, "additions": 3}))
