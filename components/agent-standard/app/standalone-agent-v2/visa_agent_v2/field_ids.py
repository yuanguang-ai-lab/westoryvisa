"""Helpers for matching provider field IDs to stable semantic IDs."""

import re


_INSTANCE_HASH = re.compile(r"\.[0-9a-f]{12}$", flags=re.IGNORECASE)


def semantic_field_id(field_id):
    """Return the stable field ID without its provider instance hash.

    Production manifests append a twelve-character hexadecimal instance hash
    to every semantic ID.  Fixtures and older jobs may omit it, so callers
    must support both forms when applying page-specific safety rules.
    """
    normalized = str(field_id or "").strip().casefold()
    return _INSTANCE_HASH.sub("", normalized)


def semantic_field_id_endswith(field_id, suffixes):
    """Match one or more semantic suffixes across hashed and legacy IDs."""
    normalized = semantic_field_id(field_id)
    if isinstance(suffixes, str):
        suffixes = (suffixes,)
    return normalized.endswith(
        tuple(str(suffix or "").casefold() for suffix in suffixes)
    )
