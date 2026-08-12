"""Compatibility alias for :mod:`backend.browser_use_bridge`."""
import sys
from backend import browser_use_bridge as _implementation
sys.modules[__name__] = _implementation
