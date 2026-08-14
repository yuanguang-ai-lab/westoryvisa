"""V2 safety refinements for CEAC data-entry pages."""

from visa_agent.safety import VisaFormSafetyPolicy


class FastVisaFormSafetyPolicy(VisaFormSafetyPolicy):
    """Keep hard CEAC boundaries without confusing form data with login UI.

    The Address/Phone page can legitimately contain the word ``username`` in
    social-media guidance. Page classification and URL policy already reject
    retrieval/login pages before text inspection, so broad credential words
    must not turn a known formal DS-160 page into a false human checkpoint.
    """

    HUMAN_TEXT_PATTERNS = tuple(
        pattern
        for pattern in VisaFormSafetyPolicy.HUMAN_TEXT_PATTERNS
        if pattern not in {r"username", r"password"}
    )
