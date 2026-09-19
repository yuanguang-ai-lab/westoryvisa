#!/usr/bin/env python3
"""DS-160 value validation owned by the backend."""
"""Canonicalize values that must match a finite DS-160 control option."""

import re


COUNTRY_FIELD_IDS = {
    "application.consulateCountry",
    "personal.birthCountry",
    "personal.nationality",
    "contact.homeCountry",
    "passport.issuingAuthority",
    "passport.issueCountry",
}

STATE_FIELD_IDS = {
    "travel.usState",
    "contact.usState",
    "education.schoolState",
}

SELECT_FIELD_IDS = COUNTRY_FIELD_IDS | STATE_FIELD_IDS | {
    "personal.sex",
}

COUNTRY_ALIASES = {
    "CHN": "CHINA",
    "CHINESE": "CHINA",
    "PRC": "CHINA",
    "P.R.C.": "CHINA",
    "P.R. CHINA": "CHINA",
    "PEOPLE'S REPUBLIC OF CHINA": "CHINA",
    "PEOPLES REPUBLIC OF CHINA": "CHINA",
    "中国": "CHINA",
    "中华人民共和国": "CHINA",
    "HKG": "HONG KONG S.A.R.",
    "HONG KONG": "HONG KONG S.A.R.",
    "MAC": "MACAU S.A.R.",
    "MACAO": "MACAU S.A.R.",
    "TWN": "TAIWAN",
    "USA": "UNITED STATES OF AMERICA",
    "US": "UNITED STATES OF AMERICA",
    "U.S.": "UNITED STATES OF AMERICA",
    "UNITED STATES": "UNITED STATES OF AMERICA",
    "GBR": "UNITED KINGDOM",
    "UK": "UNITED KINGDOM",
    "CAN": "CANADA",
    "AUS": "AUSTRALIA",
    "SGP": "SINGAPORE",
    "JPN": "JAPAN",
    "KOR": "SOUTH KOREA",
}

STATE_ALIASES = {
    "CA": "CALIFORNIA",
    "DC": "DISTRICT OF COLUMBIA",
    "IL": "ILLINOIS",
    "MA": "MASSACHUSETTS",
    "NJ": "NEW JERSEY",
    "NY": "NEW YORK",
    "PA": "PENNSYLVANIA",
    "TX": "TEXAS",
    "VA": "VIRGINIA",
    "WA": "WASHINGTON",
}

PLACEHOLDER_PATTERN = re.compile(
    r"(?:^|\b)(?:DEMO|DUMMY|EXAMPLE|FAKE|MOCK|PLACEHOLDER|SAMPLE|TEST)(?:\b|$)",
    re.IGNORECASE,
)


def clean_ds160_value(value, limit=500):
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def canonicalize_ds160_value(field_id, value):
    cleaned = clean_ds160_value(value)
    if not cleaned:
        return ""
    normalized = cleaned.upper()
    if field_id in COUNTRY_FIELD_IDS:
        return COUNTRY_ALIASES.get(normalized, normalized)
    if field_id in STATE_FIELD_IDS:
        return STATE_ALIASES.get(normalized, normalized)
    if field_id == "personal.sex":
        return {
            "M": "MALE",
            "男": "MALE",
            "F": "FEMALE",
            "女": "FEMALE",
        }.get(normalized, normalized)
    return cleaned


def field_value_is_usable(field_id, value):
    canonical = canonicalize_ds160_value(field_id, value)
    if not canonical:
        return False
    if field_id not in SELECT_FIELD_IDS:
        return True
    if PLACEHOLDER_PATTERN.search(canonical):
        return False
    if canonical in {
        "1", "N/A", "NA", "NONE", "UNKNOWN", "DO NOT KNOW",
        "DOES NOT APPLY", "NOT APPLICABLE", "NATIONAL", "NATIONALITY",
    }:
        return False
    if field_id == "personal.sex":
        return canonical in {"MALE", "FEMALE"}
    return bool(re.fullmatch(r"[A-Z][A-Z .,'()&/\-]{1,79}", canonical))
