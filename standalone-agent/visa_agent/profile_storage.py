"""Validated deletion for one job-owned private browser profile."""

import os
import shutil
import tempfile
from pathlib import Path


def profile_path_is_broad(path, *, follow_symlinks=True):
    """Reject filesystem roots, shared roots, and process-owned roots."""
    try:
        requested = Path(path).expanduser()
        candidate = (
            requested.resolve()
            if follow_symlinks
            else Path(os.path.abspath(str(requested)))
        )
        home = Path.home().resolve()
        working = Path.cwd().resolve()
        temporary = Path(tempfile.gettempdir()).resolve()
    except OSError:
        return True
    protected = {
        Path("/").resolve(),
        home,
        working,
        temporary,
    }
    if candidate in protected:
        return True
    if any(candidate in root.parents for root in protected):
        return True
    if candidate.parent in {home, temporary}:
        return True
    return len(candidate.parts) < 4


def purge_private_profile_path(path, *, required_parent=None):
    """Delete exactly one validated profile without following symlinks.

    ``required_parent`` lets restart recovery prove that a reconstructed path
    is the direct child ``data_dir/browser-profiles/<job-id>``.  A failed
    validation or deletion returns ``False`` so callers can retry later.
    """
    if path is None:
        return True
    try:
        lexical = Path(
            os.path.abspath(str(Path(path).expanduser()))
        )
        if profile_path_is_broad(
            lexical,
            follow_symlinks=False,
        ):
            return False
        if required_parent is not None:
            parent = Path(
                os.path.abspath(
                    str(Path(required_parent).expanduser())
                )
            )
            if lexical.parent != parent:
                return False
            if parent.is_symlink():
                return False
            # macOS exposes system aliases such as /var -> /private/var.
            # Canonicalize trusted ancestors while still rejecting a direct
            # replacement of the owned browser-profiles directory itself.
            canonical_parent = parent.resolve()
            if profile_path_is_broad(canonical_parent):
                return False
            lexical = canonical_parent / lexical.name
        if lexical.is_symlink():
            lexical.unlink()
            return True
        if profile_path_is_broad(lexical):
            return False
        resolved = lexical.resolve()
        if resolved != lexical or profile_path_is_broad(resolved):
            return False
        if not lexical.exists():
            return True
        if not lexical.is_dir():
            return False
        # CPython uses fd-relative traversal where supported; if the leaf is
        # replaced by a symlink during the check, rmtree refuses it and a later
        # retry unlinks only that directory entry.
        shutil.rmtree(lexical)
        return True
    except OSError:
        return False
