"""Compatibility alias for :mod:`backend.ds160_mapper`."""
import sys
from backend import ds160_mapper as _implementation
sys.modules[__name__] = _implementation
