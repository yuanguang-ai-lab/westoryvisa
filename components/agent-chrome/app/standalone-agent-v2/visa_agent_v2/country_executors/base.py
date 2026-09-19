"""Shared contract for international executors.

China intentionally does not inherit this class.  International versions may
reuse the stable V2 browser primitives, while country behavior is added only
inside the matching executor module.
"""

from ..workflow import FastComputerUseAgent


class InternationalComputerUseAgent(FastComputerUseAgent):
    COUNTRY_CODE = ""
    EXECUTOR_VERSION = ""
    SOURCE_LOCALES = ()
    EXECUTION_RULES = ()

    @classmethod
    def executor_metadata(cls):
        return {
            "countryCode": cls.COUNTRY_CODE,
            "executorVersion": cls.EXECUTOR_VERSION,
            "sourceLocales": list(cls.SOURCE_LOCALES),
            "executionRules": list(cls.EXECUTION_RULES),
        }
