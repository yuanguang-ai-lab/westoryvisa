"""Environment-backed, per-capability provider configuration."""

import os
from dataclasses import dataclass, field
from pathlib import Path


COMPUTER_USE_API_KEY_PROVIDERS = frozenset({
    "gemini",
    "google",
    "openrouter",
})


def _truthy(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _environment_with_dotenv():
    env = dict(os.environ)
    path = Path(__file__).resolve().parent.parent / ".env"
    if not path.is_file():
        return env
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return env
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if not re_full_env_key(key) or key in env:
            continue
        value = value.strip()
        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {"'", '"'}
        ):
            value = value[1:-1]
        env[key] = value
    return env


def re_full_env_key(value):
    return bool(value) and value.replace("_", "").isalnum() and value[0].isalpha()


@dataclass(frozen=True)
class ProviderConfig:
    provider: str = ""
    model: str = ""
    api_base_url: str = ""
    api_key: str = ""
    version: str = ""

    @property
    def configured(self):
        # Local adapters may not need a URL, key, or model name.
        return bool(self.provider)

    def public_summary(self, *, require_api_key=False):
        credential_required = bool(require_api_key and self.provider)
        credential_configured = bool(self.api_key)
        configured = bool(
            self.configured
            and (
                not credential_required
                or credential_configured
            )
        )
        summary = {
            "configured": configured,
            "provider": self.provider,
            "model": self.model,
            "version": self.version,
        }
        if credential_required:
            # Only readiness is exposed. The key itself, its length, and any
            # identifying prefix remain private.
            summary["credentialRequired"] = True
            summary["credentialConfigured"] = credential_configured
            if not credential_configured:
                summary["configurationIssue"] = "api_key_missing"
        return summary


@dataclass(frozen=True)
class AgentConfig:
    document_parser: ProviderConfig = field(default_factory=ProviderConfig)
    ocr: ProviderConfig = field(default_factory=ProviderConfig)
    ocr_fallback: ProviderConfig = field(default_factory=ProviderConfig)
    extraction: ProviderConfig = field(default_factory=ProviderConfig)
    review: ProviderConfig = field(default_factory=ProviderConfig)
    translation: ProviderConfig = field(default_factory=ProviderConfig)
    computer_use: ProviderConfig = field(default_factory=ProviderConfig)
    browser: ProviderConfig = field(default_factory=ProviderConfig)
    host: str = "127.0.0.1"
    port: int = 8765
    data_dir: Path = Path("agent-data")
    checkpoint_encryption_key: str = ""
    allow_plaintext_checkpoints: bool = True
    checkpoint_retention_days: int = 7
    integration_mode: str = "isolated"
    computer_use_execution: str = ""
    # Chrome normally opens in a few seconds.  Keep startup separate from the
    # longer workflow inactivity lease so a wedged launch cannot leave the UI
    # silently waiting for the historical 120-second worker default.
    browser_startup_timeout_seconds: float = 30.0

    @property
    def model_configured(self):
        """Compatibility flag from the 0.1 service."""
        return self.extraction.configured or self.computer_use_configured

    @property
    def computer_use_requires_api_key(self):
        return (
            str(self.computer_use.provider or "").strip().casefold()
            in COMPUTER_USE_API_KEY_PROVIDERS
        )

    @property
    def computer_use_configured(self):
        if not self.computer_use.configured:
            return False
        if self.computer_use_requires_api_key:
            return bool(self.computer_use.api_key)
        return True

    def provider_public_summary(self, name, settings):
        return settings.public_summary(
            require_api_key=(
                name == "computerUse"
                and self.computer_use_requires_api_key
            )
        )

    @property
    def ocr_configured(self):
        return self.ocr.configured

    @property
    def browser_configured(self):
        return self.browser.configured

    @property
    def providers(self):
        return {
            "documentParser": self.document_parser,
            "ocr": self.ocr,
            "ocrFallback": self.ocr_fallback,
            "extraction": self.extraction,
            "review": self.review,
            "translation": self.translation,
            "computerUse": self.computer_use,
            "browser": self.browser,
        }


def _first_value(env, *keys):
    for key in keys:
        if key and str(env.get(key, "")).strip():
            return str(env[key]).strip()
    return ""


def _provider(env, prefix, legacy=None, shared_api_key=""):
    legacy = legacy or {}
    shared_api_keys = (
        (shared_api_key,)
        if isinstance(shared_api_key, str)
        else tuple(shared_api_key)
    )
    return ProviderConfig(
        provider=_first_value(
            env, f"{prefix}_PROVIDER", legacy.get("provider")
        ),
        model=_first_value(env, f"{prefix}_MODEL", legacy.get("model")),
        api_base_url=_first_value(
            env, f"{prefix}_API_BASE_URL", legacy.get("api_base_url")
        ).rstrip("/"),
        api_key=_first_value(
            env,
            f"{prefix}_API_KEY",
            *shared_api_keys,
            legacy.get("api_key"),
        ),
        version=_first_value(env, f"{prefix}_VERSION"),
    )


def load_config(environ=None):
    env = _environment_with_dotenv() if environ is None else environ
    legacy_model = {
        "provider": "MODEL_PROVIDER",
        "model": "MODEL_NAME",
        "api_base_url": "MODEL_API_BASE_URL",
        "api_key": "MODEL_API_KEY",
    }
    return AgentConfig(
        document_parser=_provider(
            env, "DOCUMENT_PARSER", shared_api_key="MINERU_API_TOKEN"
        ),
        ocr=_provider(
            env, "OCR", shared_api_key="MINERU_API_TOKEN"
        ),
        ocr_fallback=_provider(
            env, "OCR_FALLBACK", shared_api_key="PADDLEOCR_ACCESS_TOKEN"
        ),
        extraction=_provider(
            env, "EXTRACTION", legacy_model, "DEEPSEEK_API_KEY"
        ),
        review=_provider(
            env, "REVIEW", shared_api_key="DEEPSEEK_API_KEY"
        ),
        translation=_provider(
            env, "TRANSLATION", shared_api_key="DEEPSEEK_API_KEY"
        ),
        computer_use=_provider(
            env,
            "COMPUTER_USE",
            legacy_model,
            ("OPENROUTER_API_KEY", "GEMINI_API_KEY"),
        ),
        browser=_provider(env, "BROWSER"),
        host=env.get("AGENT_HOST", "127.0.0.1").strip() or "127.0.0.1",
        port=int(env.get("AGENT_PORT", "8765")),
        data_dir=Path(env.get("AGENT_DATA_DIR", "agent-data")),
        checkpoint_encryption_key=env.get(
            "AGENT_CHECKPOINT_ENCRYPTION_KEY", ""
        ).strip(),
        # Environment-loaded services are secure by default. Direct
        # AgentConfig() remains convenient for isolated unit tests.
        allow_plaintext_checkpoints=_truthy(
            env.get("AGENT_ALLOW_PLAINTEXT_CHECKPOINTS"), False
        ),
        checkpoint_retention_days=max(
            1, int(env.get("AGENT_CHECKPOINT_RETENTION_DAYS", "7"))
        ),
        integration_mode=(
            env.get("AGENT_INTEGRATION_MODE", "isolated").strip()
            or "isolated"
        ),
        computer_use_execution=env.get(
            "AGENT_COMPUTER_USE_EXECUTION", ""
        ).strip(),
        browser_startup_timeout_seconds=max(
            5.0,
            min(
                60.0,
                float(
                    env.get(
                        "AGENT_BROWSER_STARTUP_TIMEOUT_SECONDS",
                        "30",
                    )
                ),
            ),
        ),
    )
