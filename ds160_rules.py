"""Compatibility alias for :mod:`backend.ds160_rules`."""
import sys
from backend import ds160_rules as _implementation
sys.modules[__name__] = _implementation
