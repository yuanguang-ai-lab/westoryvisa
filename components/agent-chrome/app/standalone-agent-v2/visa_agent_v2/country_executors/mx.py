"""Mexico execution layer."""

from .base import InternationalComputerUseAgent


class MexicoComputerUseAgent(InternationalComputerUseAgent):
    COUNTRY_CODE = "MX"
    EXECUTOR_VERSION = "mx-v1"
    SOURCE_LOCALES = ("es-MX", "en-US")
    EXECUTION_RULES = (
        "preserve-two-surnames",
        "normalize-plus-52-phone",
        "map-state-and-postal-code",
    )
