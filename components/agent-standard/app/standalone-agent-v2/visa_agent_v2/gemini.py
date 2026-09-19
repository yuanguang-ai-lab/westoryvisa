"""Bounded Gemini fallback used only for semantically unresolved fields."""

from visa_agent.adapters import GeminiComputerUseAdapter


class FastGeminiComputerUseAdapter(GeminiComputerUseAdapter):
    """Use a smaller request budget because V2 sends only fallback fields."""

    # Historical live calls have a p95 just under 27 seconds. A 20-second
    # cutoff rejected healthy slow responses and turned one rare fallback
    # into repeated retries. The semantic path still pays no model latency.
    PRIMARY_PLANNING_TIMEOUT_SECONDS = 30
    RECOVERY_PLANNING_TIMEOUT_SECONDS = 4
    PLANNING_RETRY_BACKOFF_SECONDS = 0.25
    PLANNING_TOTAL_BUDGET_SECONDS = (
        PRIMARY_PLANNING_TIMEOUT_SECONDS
        + RECOVERY_PLANNING_TIMEOUT_SECONDS
        + PLANNING_RETRY_BACKOFF_SECONDS
    )
