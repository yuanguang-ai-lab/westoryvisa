"""Composition helpers for built-in and deployment-specific provider adapters."""

from .adapters import register_builtin_providers
from .config import load_config
from .providers import (
    ProviderNotConfigured,
    ProviderRegistry,
    FallbackOCRProvider,
    UnconfiguredExtractionModel,
    UnconfiguredOCRProvider,
)
from .recognition import DocumentRecognizer
from .service import AgentService
from .workflow import ComputerUseAgent


def build_service(config=None, registry=None, checkpoint_store=None):
    """Build the isolated service from capability-specific registered adapters.

    Browser and computer-use adapters are created per job so browser sessions
    are never shared across customers.
    """
    config = config or load_config()
    registry = register_builtin_providers(registry or ProviderRegistry())
    parser = registry.create("document_parser", config.document_parser)
    ocr = (
        registry.create("ocr", config.ocr)
        if config.ocr.configured else UnconfiguredOCRProvider()
    )
    if config.ocr_fallback.configured:
        fallback = registry.create("ocr_fallback", config.ocr_fallback)
        ocr = FallbackOCRProvider(ocr, fallback)
    extraction = (
        registry.create("extraction", config.extraction)
        if config.extraction.configured else UnconfiguredExtractionModel()
    )
    review = (
        registry.create("review", config.review)
        if config.review.configured else None
    )
    translation = (
        registry.create("translation", config.translation)
        if config.translation.configured else None
    )
    recognizer = DocumentRecognizer(
        ocr,
        extraction,
        document_parser=parser,
        review_model=review,
    )

    runtime_factory = None
    if config.computer_use_configured and config.browser.configured:
        def runtime_factory(_job, startup_control=None):
            model = registry.create("computer_use", config.computer_use)
            browser = registry.create("browser", config.browser)
            if model is None or browser is None:
                raise ProviderNotConfigured(
                    "Both computer-use and browser providers are required"
                )
            execution_mode = (
                config.computer_use_execution.strip().lower()
                or "visual"
            )
            set_execution_mode = getattr(
                browser, "set_execution_mode", None
            )
            if callable(set_execution_mode):
                # Set visual mode before opening the browser so the manual
                # retrieval window is visibly marked from its first page.
                set_execution_mode(execution_mode)
            set_profile_dir = getattr(browser, "set_profile_dir", None)
            if callable(set_profile_dir):
                # A profile belongs to exactly one reviewed job.  It retains
                # the CEAC cookie/tab across an Agent restart, but is never
                # shared with another applicant and is purged at terminal
                # completion/cancellation by AgentService.
                set_profile_dir(
                    config.data_dir / "browser-profiles" / _job.id
                )
            publish_browser = getattr(
                startup_control,
                "publish_browser",
                None,
            )
            if callable(publish_browser):
                # Publish only after the exact per-job profile is configured.
                # A bounded startup timeout can now safely terminate this one
                # Chrome process without touching the user's normal browser.
                publish_browser(browser)
            set_visual_status = getattr(browser, "set_visual_status", None)
            set_status_callback = getattr(
                model, "set_status_callback", None
            )
            if callable(set_status_callback) and callable(set_visual_status):
                set_status_callback(set_visual_status)
            if callable(set_visual_status):
                set_visual_status(
                    "paused",
                    "请在这个 DocFlow Agent 专用窗口中恢复已有 DS-160",
                )
            start = getattr(browser, "start", None)
            if callable(start):
                try:
                    if bool(
                        getattr(startup_control, "stop_requested", False)
                    ):
                        raise TimeoutError(
                            "Browser startup was cancelled before launch"
                        )
                    start(_job.start_url)
                    if bool(
                        getattr(startup_control, "stop_requested", False)
                    ):
                        raise TimeoutError(
                            "Browser startup completed after its lease expired"
                        )
                except Exception:
                    close = getattr(browser, "close", None)
                    if callable(close):
                        close()
                    raise
            return ComputerUseAgent(
                model,
                browser,
                action_reviewer=review,
                use_model_verification=review is not None,
                execution_mode=execution_mode,
            )
        runtime_factory._docflow_accepts_startup_control = True

    return AgentService(
        config=config,
        checkpoint_store=checkpoint_store,
        runtime_factory=runtime_factory,
        recognizer=recognizer,
        translation_provider=translation,
    )
