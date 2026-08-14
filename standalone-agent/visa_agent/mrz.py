"""Rule-based ICAO TD3 passport MRZ parser with check-digit validation."""

import re
from datetime import datetime, timezone


WEIGHTS = (7, 3, 1)
CHAR_VALUES = {
    **{str(number): number for number in range(10)},
    **{chr(code): code - 55 for code in range(ord("A"), ord("Z") + 1)},
    "<": 0,
}
COUNTRY_NAMES = {
    "CHN": "CHINA",
    "USA": "UNITED STATES OF AMERICA",
    "GBR": "UNITED KINGDOM",
    "CAN": "CANADA",
    "AUS": "AUSTRALIA",
    "JPN": "JAPAN",
    "KOR": "SOUTH KOREA",
    "SGP": "SINGAPORE",
    "UTO": "UTOPIA",
}


def check_digit(value):
    total = sum(
        CHAR_VALUES.get(character, 0) * WEIGHTS[index % 3]
        for index, character in enumerate(value)
    )
    return str(total % 10)


def _date(value, expiry=False):
    if not re.fullmatch(r"\d{6}", value):
        return ""
    year = int(value[:2])
    current_year = datetime.now(timezone.utc).year % 100
    century = 2000 if expiry or year <= current_year else 1900
    try:
        return datetime(
            century + year, int(value[2:4]), int(value[4:6])
        ).date().isoformat()
    except ValueError:
        return ""


def _clean_name(value):
    return re.sub(r"\s+", " ", value.replace("<", " ")).strip()


def find_td3_lines(text):
    candidates = []
    for raw_line in str(text or "").upper().splitlines():
        normalized = re.sub(r"[^A-Z0-9<]", "", raw_line)
        if len(normalized) >= 44:
            candidates.append(normalized[:44])
    for index in range(len(candidates) - 1):
        if candidates[index].startswith("P<") and len(candidates[index + 1]) == 44:
            return candidates[index], candidates[index + 1]
    return None


def parse_td3(text):
    lines = find_td3_lines(text)
    if not lines:
        return None
    line1, line2 = lines
    names = line1[5:].split("<<", 1)
    surname = _clean_name(names[0])
    given_names = _clean_name(names[1] if len(names) > 1 else "")
    passport_number_raw = line2[0:9]
    nationality_code = line2[10:13]
    birth_raw = line2[13:19]
    expiry_raw = line2[21:27]
    passport_number = passport_number_raw.replace("<", "")
    result = {
        "surname": surname,
        "givenNames": given_names,
        "passportNumber": passport_number,
        "nationality": COUNTRY_NAMES.get(nationality_code, nationality_code),
        "dateOfBirth": _date(birth_raw),
        "sex": {"M": "MALE", "F": "FEMALE", "<": "UNSPECIFIED"}.get(
            line2[20], line2[20]
        ),
        "expirationDate": _date(expiry_raw, expiry=True),
        "checks": {
            "passport": check_digit(passport_number_raw) == line2[9],
            "birth": check_digit(birth_raw) == line2[19],
            "expiry": check_digit(expiry_raw) == line2[27],
        },
        "evidence": [line1, line2],
    }
    return result
