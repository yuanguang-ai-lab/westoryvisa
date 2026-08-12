"""Compatibility alias for :mod:`backend.school_directory`."""
import sys
from backend import school_directory as _implementation
sys.modules[__name__] = _implementation
