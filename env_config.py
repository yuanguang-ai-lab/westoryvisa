"""Compatibility alias for :mod:`backend.env_config`."""
import sys
from backend import env_config as _implementation
sys.modules[__name__] = _implementation
