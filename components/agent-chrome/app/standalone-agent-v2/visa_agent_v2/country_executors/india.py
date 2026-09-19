"""India execution layer."""

from .base import InternationalComputerUseAgent


class IndiaComputerUseAgent(InternationalComputerUseAgent):
    COUNTRY_CODE = "IN"
    EXECUTOR_VERSION = "in-v1"
    SOURCE_LOCALES = ("en-IN", "hi-IN")
    EXECUTION_RULES = (
        "preserve-passport-name-order",
        "normalize-plus-91-phone",
        "map-state-and-pin-code",
    )
