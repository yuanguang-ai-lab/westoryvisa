"""Compatibility alias for :mod:`backend.docling_client`."""
import sys
from backend import docling_client as _implementation
sys.modules[__name__] = _implementation
