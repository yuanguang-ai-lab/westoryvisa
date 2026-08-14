"""Patchless V2 service composition."""

import time

from visa_agent.adapters import register_builtin_providers
from visa_agent.factory import build_service as build_legacy_service
from visa_agent.providers import ProviderNotConfigured, ProviderRegistry

from .browser import FastVisiblePlaywrightBrowser
from .gemini import FastGeminiComputerUseAdapter
from .safety import FastVisaFormSafetyPolicy
from .settings import load_v2_config
from .workflow import FastComputerUseAgent


def _register_v2_providers(registry):
    registry.register(
        "computer_use",
        "google",
        FastGeminiComputerUseAdapter,
    )
    registry.register(
        "computer_use",
        "gemini",
        FastGeminiComputerUseAdapter,
    )
    registry.register(
        "browser",
        "playwright",
        FastVisiblePlaywrightBrowser,
    )
    return register_builtin_providers(registry)


def build_fast_service(config=None, registry=None, checkpoint_store=None):
    """Build the existing API surface with the independent V2 runtime."""
    config = config or load_v2_config()
    provider_registry = _register_v2_providers(
        registry or ProviderRegistry()
    )
    service = build_legacy_service(
        config=config,
        registry=provider_registry,
        checkpoint_store=checkpoint_store,
    )
    config = service.config

    if not (
        config.computer_use_configured
        and config.browser.configured
    ):
        return service

    review = (
        provider_registry.create("review", config.review)
        if config.review.configured
        else None
    )

    def runtime_factory(job, startup_control=None):
        model = provider_registry.create(
            "computer_use",
            config.computer_use,
        )
        if model is None:
            raise ProviderNotConfigured(
                "Both computer-use and browser providers are required"
            )

        browser = None
        startup_error = None
        for attempt in range(2):
            browser = provider_registry.create("browser", config.browser)
            if browser is None:
                raise ProviderNotConfigured(
                    "Both computer-use and browser providers are required"
                )

            # These two modes are deliberately different. The workflow plans
            # from semantic controls first; the browser still renders visible
            # motion.
            set_execution_mode = getattr(
                browser,
                "set_execution_mode",
                None,
            )
            if callable(set_execution_mode):
                set_execution_mode("visual")

            set_profile_dir = getattr(browser, "set_profile_dir", None)
            if callable(set_profile_dir):
                set_profile_dir(
                    config.data_dir / "browser-profiles-v2" / job.id
                )

            publish_browser = getattr(
                startup_control,
                "publish_browser",
                None,
            )
            if callable(publish_browser):
                publish_browser(browser)

            set_visual_status = getattr(
                browser,
                "set_visual_status",
                None,
            )
            set_status_callback = getattr(
                model,
                "set_status_callback",
                None,
            )
            if (
                callable(set_status_callback)
                and callable(set_visual_status)
            ):
                set_status_callback(set_visual_status)
            if callable(set_visual_status):
                set_visual_status(
                    "paused",
                    "请在这个 DocFlow V2 专用窗口中恢复已有 DS-160",
                )

            start = getattr(browser, "start", None)
            try:
                if bool(
                    getattr(startup_control, "stop_requested", False)
                ):
                    raise TimeoutError(
                        "Browser startup was cancelled before launch"
                    )
                if callable(start):
                    start(job.start_url)
                if bool(
                    getattr(startup_control, "stop_requested", False)
                ):
                    raise TimeoutError(
                        "Browser startup completed after its lease expired"
                    )
                startup_error = None
                break
            except Exception as error:
                startup_error = error
                close = getattr(browser, "close", None)
                if callable(close):
                    close()
                if (
                    attempt == 0
                    and not bool(
                        getattr(
                            startup_control,
                            "stop_requested",
                            False,
                        )
                    )
                ):
                    # Chromium can briefly reject a new persistent context
                    # while the previous job-owned process is exiting. One
                    # bounded retry with the same private profile avoids
                    # manufacturing an orphan reviewed job or a UI-level 500.
                    time.sleep(0.4)
                    continue
                raise
        if startup_error is not None:
            raise startup_error

        return FastComputerUseAgent(
            model,
            browser,
            policy=FastVisaFormSafetyPolicy(),
            action_reviewer=review,
            use_model_verification=review is not None,
            execution_mode="hybrid",
        )

    runtime_factory._docflow_accepts_startup_control = True
    service.runtime_factory = runtime_factory
    return service
