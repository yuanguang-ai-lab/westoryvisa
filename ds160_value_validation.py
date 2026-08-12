"""Compatibility alias for :mod:`backend.ds160_value_validation`."""
import sys
from backend import ds160_value_validation as _implementation
sys.modules[__name__] = _implementation
