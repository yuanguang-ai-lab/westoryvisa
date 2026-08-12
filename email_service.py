"""Compatibility alias for :mod:`backend.email_service`."""
import sys
from backend import email_service as _implementation
sys.modules[__name__] = _implementation
