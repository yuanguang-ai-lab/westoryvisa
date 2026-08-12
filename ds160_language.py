"""Compatibility alias for :mod:`backend.ds160_language`."""
import sys
from backend import ds160_language as _implementation
sys.modules[__name__] = _implementation
