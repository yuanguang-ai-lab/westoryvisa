"""Compatibility alias for :mod:`backend.appointment_bridge`."""
import sys
from backend import appointment_bridge as _implementation
sys.modules[__name__] = _implementation
