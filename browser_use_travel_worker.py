#!/usr/bin/env python3
"""Compatibility launcher for the backend Browser Use worker."""

import sys

from backend.workers import browser_use_travel_worker as _implementation


if __name__ == "__main__":
    raise SystemExit(_implementation.main())
else:
    sys.modules[__name__] = _implementation
