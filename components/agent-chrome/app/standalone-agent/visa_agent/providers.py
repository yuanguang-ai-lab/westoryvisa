"""Provider contracts and a registry that keeps vendors out of domain logic."""

from dataclasses import dataclass
from typing import Callable, Dict, List, Protocol

from .models import BrowserObservation, ComputerAction, ExtractedField


class ProviderNotConfigured(RuntimeError):
    pass


class DocumentParser(Protocol):
    def parse(self, content: bytes, filename: str, media_type: str) -> str:
        ...


class OCRProvider(Protocol):
    def recognize(self, content: bytes, filename: str, media_type: str) -> str:
        ...


class ExtractionModel(Protocol):
    def extract(
        self, text: str, document_type: str, filename: str
    ) -> List[ExtractedField]:
        ...


class ReviewModel(Protocol):
    def review(
        self, fields: List[ExtractedField], document_type: str
    ) -> List[str]:
        ...

    def review_action(
        self,
        action: ComputerAction,
        before: BrowserObservation,
        after: BrowserObservation,
    ) -> bool:
        ...


class TranslationProvider(Protocol):
    def translate(self, text: str, source_language: str, target_language: str) -> str:
        ...

    def transliterate(
        self, text: str, source_language: str, target_script: str = "Latn"
    ) -> str:
        ...


class ComputerUseModel(Protocol):
    def propose_actions(
        self,
        observation: BrowserObservation,
        available_field_ids: List[str],
        completed_field_ids: List[str],
        page_field_ids: List[str],
    ) -> List[ComputerAction]:
        ...

    def propose_action(
        self,
        observation: BrowserObservation,
        available_field_ids: List[str],
        completed_field_ids: List[str],
    ) -> ComputerAction:
        ...

    def verify_action(
        self,
        action: ComputerAction,
        before: BrowserObservation,
        after: BrowserObservation,
    ) -> bool:
        ...


class BrowserDriver(Protocol):
    def observe(self) -> BrowserObservation:
        ...

    def execute(self, action: ComputerAction) -> None:
        ...


@dataclass
class ProviderBundle:
    document_parser: object = None
    ocr: object = None
    ocr_fallback: object = None
    extraction: object = None
    review: object = None
    translation: object = None
    computer_use: object = None
    browser: object = None


class ProviderRegistry:
    """Small dependency registry; adapters register by capability and name."""

    CAPABILITIES = {
        "document_parser",
        "ocr",
        "ocr_fallback",
        "extraction",
        "review",
        "translation",
        "computer_use",
        "browser",
    }

    def __init__(self):
        self._factories: Dict[str, Dict[str, Callable]] = {
            capability: {} for capability in self.CAPABILITIES
        }

    def register(self, capability, name, factory):
        if capability not in self.CAPABILITIES:
            raise ValueError(f"Unknown provider capability: {capability}")
        normalized = str(name).strip().lower()
        if not normalized:
            raise ValueError("Provider name is required")
        self._factories[capability][normalized] = factory

    def create(self, capability, config):
        name = config.provider.strip().lower()
        if not name:
            return None
        factory = self._factories.get(capability, {}).get(name)
        if factory is None:
            raise ProviderNotConfigured(
                f"{capability} provider is not registered: {config.provider}"
            )
        return factory(config)

    def has(self, capability, name):
        return (
            str(name).strip().lower()
            in self._factories.get(str(capability), {})
        )

    def build_bundle(self, config):
        return ProviderBundle(**{
            capability: self.create(capability, getattr(config, capability))
            for capability in self.CAPABILITIES
        })


class UnconfiguredDocumentParser:
    def parse(self, content, filename, media_type):
        raise ProviderNotConfigured(
            "Document parser is not configured; connect Docling, MinerU, or another adapter"
        )


class UnconfiguredOCRProvider:
    def recognize(self, content, filename, media_type):
        raise ProviderNotConfigured(
            "OCR provider is not configured; connect PaddleOCR, RapidOCR, or another adapter"
        )


class UnconfiguredExtractionModel:
    def extract(self, text, document_type, filename):
        raise ProviderNotConfigured(
            "Model API is not configured; rule-based extraction remains available"
        )


class UnconfiguredReviewModel:
    def review(self, fields, document_type):
        raise ProviderNotConfigured("Review model is not configured")


class UnconfiguredTranslationProvider:
    def translate(self, text, source_language, target_language):
        raise ProviderNotConfigured("Translation provider is not configured")

    def transliterate(self, text, source_language, target_script="Latn"):
        raise ProviderNotConfigured("Transliteration provider is not configured")


class UnconfiguredComputerUseModel:
    def propose_action(self, observation, available_field_ids, completed_field_ids):
        raise ProviderNotConfigured("Computer-use model is not configured")

    def verify_action(self, action, before, after):
        raise ProviderNotConfigured("Computer-use model is not configured")


class PlainTextOCRProvider:
    """Test/demo provider that treats supplied UTF-8 bytes as OCR output."""

    def recognize(self, content, filename, media_type):
        return content.decode("utf-8")


class FallbackOCRProvider:
    """Use the primary OCR first, then a parser/OCR fallback on failure or blank text."""

    def __init__(self, primary, fallback, minimum_text_length=3):
        self.primary = primary
        self.fallback = fallback
        self.minimum_text_length = max(1, int(minimum_text_length))

    def recognize(self, content, filename, media_type):
        primary_error = None
        try:
            text = self._call(self.primary, content, filename, media_type)
            if self._usable(text):
                return text
        except Exception as error:
            primary_error = error

        try:
            text = self._call(self.fallback, content, filename, media_type)
        except Exception:
            if primary_error is not None:
                raise primary_error
            raise
        if not isinstance(text, str):
            raise TypeError("Fallback document text provider returned a non-text result")
        return text

    def recognize_fallback(self, content, filename, media_type):
        """Run the secondary OCR explicitly for incomplete critical documents."""
        text = self._call(self.fallback, content, filename, media_type)
        if not isinstance(text, str):
            raise TypeError("Fallback document text provider returned a non-text result")
        return text

    def _usable(self, text):
        return isinstance(text, str) and len(text.strip()) >= self.minimum_text_length

    @staticmethod
    def _call(provider, content, filename, media_type):
        recognize = getattr(provider, "recognize", None)
        if callable(recognize):
            return recognize(content, filename, media_type)
        parse = getattr(provider, "parse", None)
        if callable(parse):
            return parse(content, filename, media_type)
        raise TypeError("Document text provider must implement recognize() or parse()")
