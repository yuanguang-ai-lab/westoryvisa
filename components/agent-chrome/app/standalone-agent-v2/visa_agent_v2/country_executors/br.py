"""Brazil execution layer."""

from .base import InternationalComputerUseAgent


class BrazilComputerUseAgent(InternationalComputerUseAgent):
    COUNTRY_CODE = "BR"
    EXECUTOR_VERSION = "br-v1"
    SOURCE_LOCALES = ("pt-BR", "en-US")
    EXECUTION_RULES = (
        "preserve-multiple-surnames",
        "normalize-plus-55-ddd-phone",
        "map-uf-and-cep",
    )
