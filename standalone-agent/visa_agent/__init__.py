"""Standalone visa document-recognition and computer-use agent."""

from .models import (
    AgentJob,
    BrowserObservation,
    ComputerAction,
    Evidence,
    ExtractedField,
    RecognitionResult,
)
from .recognition import DocumentRecognizer
from .orchestrator import AgentOrchestrator
from .providers import ProviderBundle, ProviderRegistry
from .adapters import register_builtin_providers
from .factory import build_service
from .workflow import ComputerUseAgent

__all__ = [
    "AgentJob",
    "AgentOrchestrator",
    "BrowserObservation",
    "build_service",
    "ComputerAction",
    "ComputerUseAgent",
    "DocumentRecognizer",
    "Evidence",
    "ExtractedField",
    "ProviderBundle",
    "ProviderRegistry",
    "register_builtin_providers",
    "RecognitionResult",
]
