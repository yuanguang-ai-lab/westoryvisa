"""Compatibility alias for :mod:`backend.mineru_client`."""
import sys

from backend import mineru_client as _implementation

sys.modules[__name__] = _implementation
