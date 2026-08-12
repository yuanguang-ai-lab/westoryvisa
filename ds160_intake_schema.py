"""Compatibility alias for :mod:`backend.ds160_intake_schema`."""
import sys
from backend import ds160_intake_schema as _implementation
sys.modules[__name__] = _implementation
