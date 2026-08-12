#!/usr/bin/env python3
"""Build narrowly scoped Browser Use plans from a DocFlow case payload."""

import re

from .ds160_language import contains_cjk, structure_address, translate_ds160_value
from .ds160_value_validation import (
    canonicalize_ds160_value,
    field_value_is_usable,
)
from .school_directory import enrich_education_record


CEAC_START_URL = "https://ceac.state.gov/GenNIV/Default.aspx"

CEAC_TRAVEL_URL = (
    "https://ceac.state.gov/GenNIV/General/complete/"
    "complete_travel.aspx?node=Travel"
)

CEAC_PAGE_URLS = {
    "personal1": (
        "https://ceac.state.gov/GenNIV/General/complete/"
        "complete_personal.aspx?node=Personal1"
    ),
    "personal2": (
        "https://ceac.state.gov/GenNIV/General/complete/"
        "complete_personalcont.aspx?node=Personal2"
    ),
    "address_phone": (
        "https://ceac.state.gov/GenNIV/General/complete/"
        "complete_contact.aspx?node=AddressPhone"
    ),
    "passport": (
        "https://ceac.state.gov/GenNIV/General/complete/"
        "complete_pptvisa.aspx?node=PptVisa"
    ),
    "travel": CEAC_TRAVEL_URL,
    "travel_companions": (
        "https://ceac.state.gov/GenNIV/General/complete/"
        "complete_travelcompanions.aspx?node=TravelCompanions"
    ),
    "previous_us_travel": (
        "https://ceac.state.gov/GenNIV/General/complete/"
        "complete_previousustravel.aspx?node=PreviousUSTravel"
    ),
    "us_contact": (
        "https://ceac.state.gov/GenNIV/General/complete/"
        "complete_uscontact.aspx?node=USContact"
    ),
    "relatives": (
        "https://ceac.state.gov/GenNIV/General/complete/"
        "complete_family1.aspx?node=Relatives"
    ),
    "spouse": (
        "https://ceac.state.gov/GenNIV/General/complete/"
        "complete_family2.aspx?node=Spouse"
    ),
    "work_education1": (
        "https://ceac.state.gov/GenNIV/General/complete/"
        "complete_workeducation1.aspx?node=WorkEducation1"
    ),
    "work_education2": (
        "https://ceac.state.gov/GenNIV/General/complete/"
        "complete_workeducation2.aspx?node=WorkEducation2"
    ),
    "work_education3": (
        "https://ceac.state.gov/GenNIV/General/complete/"
        "complete_workeducation3.aspx?node=WorkEducation3"
    ),
}

CEAC_PAGE_META = {
    "personal1": ("Personal Information 1", ["node=personal1", "complete_personal.aspx"]),
    "personal2": ("Personal Information 2", ["node=personal2", "complete_personalcont.aspx"]),
    "address_phone": ("Address and Phone", ["node=addressphone", "complete_contact.aspx"]),
    "passport": ("Passport Information", ["node=pptvisa", "complete_pptvisa.aspx"]),
    "travel": ("Travel Information", ["node=travel", "complete_travel.aspx"]),
    "travel_companions": (
        "Travel Companions", ["node=travelcompanions", "complete_travelcompanions.aspx"]
    ),
    "previous_us_travel": (
        "Previous U.S. Travel", ["node=previousustravel", "complete_previousustravel.aspx"]
    ),
    "us_contact": ("U.S. Contact", ["node=uscontact", "complete_uscontact.aspx"]),
    "relatives": ("Family Information", ["node=relatives", "complete_family1.aspx"]),
    "spouse": ("Spouse Information", ["node=spouse", "complete_family2.aspx"]),
    "work_education1": (
        "Present Work / Education", ["node=workeducation1", "complete_workeducation1.aspx"]
    ),
    "work_education2": (
        "Previous Work / Education", ["node=workeducation2", "complete_workeducation2.aspx"]
    ),
    "work_education3": (
        "Additional Work / Education", ["node=workeducation3", "complete_workeducation3.aspx"]
    ),
    "sevis": ("SEVIS Information", ["node=sevis", "complete_sevis"]),
    "additional_contacts": (
        "Additional Point of Contact", ["node=additionalpointcontact", "additionalpointcontact"]
    ),
}

US_STATE_NAMES = {
    "AL": "ALABAMA", "AK": "ALASKA", "AZ": "ARIZONA", "AR": "ARKANSAS",
    "CA": "CALIFORNIA", "CO": "COLORADO", "CT": "CONNECTICUT",
    "DE": "DELAWARE", "DC": "DISTRICT OF COLUMBIA", "FL": "FLORIDA",
    "GA": "GEORGIA", "HI": "HAWAII", "ID": "IDAHO", "IL": "ILLINOIS",
    "IN": "INDIANA", "IA": "IOWA", "KS": "KANSAS", "KY": "KENTUCKY",
    "LA": "LOUISIANA", "ME": "MAINE", "MD": "MARYLAND",
    "MA": "MASSACHUSETTS", "MI": "MICHIGAN", "MN": "MINNESOTA",
    "MS": "MISSISSIPPI", "MO": "MISSOURI", "MT": "MONTANA",
    "NE": "NEBRASKA", "NV": "NEVADA", "NH": "NEW HAMPSHIRE",
    "NJ": "NEW JERSEY", "NM": "NEW MEXICO", "NY": "NEW YORK",
    "NC": "NORTH CAROLINA", "ND": "NORTH DAKOTA", "OH": "OHIO",
    "OK": "OKLAHOMA", "OR": "OREGON", "PA": "PENNSYLVANIA",
    "RI": "RHODE ISLAND", "SC": "SOUTH CAROLINA", "SD": "SOUTH DAKOTA",
    "TN": "TENNESSEE", "TX": "TEXAS", "UT": "UTAH", "VT": "VERMONT",
    "VA": "VIRGINIA", "WA": "WASHINGTON", "WV": "WEST VIRGINIA",
    "WI": "WISCONSIN", "WY": "WYOMING",
}


def _clean(value, limit=500):
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _field_map(payload):
    return {
        str(item.get("id") or ""): item
        for item in (payload.get("extractedFields") or [])
        if isinstance(item, dict)
    }


def _question_map(payload):
    return {
        str(item.get("id") or ""): item
        for item in (payload.get("branchQuestionnaire") or [])
        if isinstance(item, dict)
    }


def _field_value(fields, field_id):
    raw_value = (fields.get(field_id) or {}).get("value")
    prepared = translate_ds160_value(
        raw_value,
        field_id=field_id,
        context=(fields.get(field_id) or {}).get("label") or field_id,
        preserve_native=field_id == "personal.nativeName",
    )
    value = canonicalize_ds160_value(
        field_id, prepared.get("value")
    )
    return _clean(value) if field_value_is_usable(field_id, value) else ""


def _detail_value(question, detail_id, fields, field_id=None):
    detail = _clean((question.get("details") or {}).get(detail_id))
    if detail:
        prepared = translate_ds160_value(
            detail,
            field_id=field_id or f"{question.get('id') or 'question'}.{detail_id}",
            context=f"{question.get('englishLabel') or question.get('label') or question.get('id')} · {detail_id}",
        )
        return _clean(prepared.get("value"))
    return _field_value(fields, field_id) if field_id else ""


def _ceac_action_value(action_id, label, kind, value):
    cleaned = _clean(value)
    if not cleaned or action_id == "personal.nativeName":
        return cleaned, False
    if kind == "yes_no":
        answer_aliases = {"是": "yes", "有": "yes", "否": "no", "没有": "no"}
        return answer_aliases.get(cleaned, cleaned), False
    if kind not in {"text", "text_segments", "select_text"} or not contains_cjk(cleaned):
        return cleaned, False
    prepared = translate_ds160_value(
        cleaned,
        field_id=action_id,
        context=label or action_id,
    )
    translated = _clean(prepared.get("value"))
    return translated, not translated or contains_cjk(translated)


def _action(action_id, label, kind, value, **extra):
    prepared_value, translation_blocked = _ceac_action_value(
        action_id, label, kind, value
    )
    action = {
        "id": action_id,
        "label": label,
        "kind": kind,
        "value": prepared_value,
    }
    if translation_blocked:
        action["translationBlocked"] = True
    action.update(extra)
    return action


def _purpose_actions(visa_type, questions):
    normalized = str(visa_type or "").strip().upper()
    actions = []
    if normalized.startswith("F"):
        actions.extend([
            _action(
                "travel.purpose.primary", "Purpose of Trip to the U.S.",
                "select_text", "ACADEMIC OR LANGUAGE STUDENT (F)",
                labelTerms=["Purpose of Trip to the U.S."],
                optionTerms=["ACADEMIC OR LANGUAGE STUDENT", "(F)"],
                controlHints=["PurposeOfTrip", "PURPOSE_OF_TRIP"],
                causesRefresh=True,
            ),
            _action(
                "travel.purpose.secondary", "Specify visa class", "select_text",
                "STUDENT (F1)", labelTerms=["Specify"],
                optionTerms=["STUDENT", "F1"],
                controlHints=["OtherPurpose", "SPECIFY"], causesRefresh=True,
            ),
        ])
    elif normalized.startswith("J"):
        actions.extend([
            _action(
                "travel.purpose.primary", "Purpose of Trip to the U.S.",
                "select_text", "EXCHANGE VISITOR (J)",
                labelTerms=["Purpose of Trip to the U.S."],
                optionTerms=["EXCHANGE VISITOR", "(J)"], causesRefresh=True,
                controlHints=["PurposeOfTrip", "PURPOSE_OF_TRIP"],
            ),
            _action(
                "travel.purpose.secondary", "Specify visa class", "select_text",
                "EXCHANGE VISITOR (J1)", labelTerms=["Specify"],
                optionTerms=["EXCHANGE VISITOR", "J1"],
                controlHints=["OtherPurpose", "SPECIFY"], causesRefresh=True,
            ),
        ])
    elif normalized.startswith("B"):
        visit_purpose = (questions.get("travel.b_visit_purpose") or {}).get("answer")
        secondary = {
            "b1": ("BUSINESS/CONFERENCE VISITOR (B1)", ["BUSINESS", "B1"]),
            "b2_tourism": ("TOURISM/MEDICAL TREATMENT (B2)", ["TOURISM", "B2"]),
            "b2_medical": ("TOURISM/MEDICAL TREATMENT (B2)", ["MEDICAL", "B2"]),
            "b1b2": (
                "BUSINESS & TOURISM (TEMPORARY VISITOR) (B1/B2)",
                ["BUSINESS", "TOURISM", "B1/B2"],
            ),
        }.get(visit_purpose or "b1b2")
        actions.append(_action(
            "travel.purpose.primary", "Purpose of Trip to the U.S.",
            "select_text", "TEMP. BUSINESS PLEASURE VISITOR (B)",
            labelTerms=["Purpose of Trip to the U.S."],
            optionTerms=["BUSINESS", "PLEASURE", "(B)"],
            controlHints=["PurposeOfTrip", "PURPOSE_OF_TRIP"], causesRefresh=True,
        ))
        if secondary:
            actions.append(_action(
                "travel.purpose.secondary", "Specify visa class", "select_text",
                secondary[0], labelTerms=["Specify"],
                optionTerms=secondary[1],
                controlHints=["OtherPurpose", "SPECIFY"], causesRefresh=True,
            ))
    return actions


def parse_us_address(value):
    """Conservatively split a common `street, city, ST ZIP` U.S. address."""
    cleaned = _clean(value)
    if not cleaned:
        return {}
    parts = [part.strip() for part in cleaned.split(",") if part.strip()]
    line1, line2 = split_address_line1_first(cleaned)
    result = {"street1": line1}
    if line2:
        result["street2"] = line2
    if len(parts) < 3:
        return result
    region_match = re.fullmatch(
        r"([A-Za-z .'-]+?)\s+(\d{5}(?:-\d{4})?)", parts[-1]
    )
    if not region_match:
        return result
    state_raw = _clean(region_match.group(1), 60).upper()
    line1, line2 = split_address_line1_first(", ".join(parts[:-2]))
    result.update({
        "street1": line1,
        "city": parts[-2],
        "state": US_STATE_NAMES.get(state_raw, state_raw),
        "postalCode": region_match.group(2),
    })
    if line2:
        result["street2"] = line2
    return result


def split_address_line1_first(value, maximum=80):
    """Keep address line 1 populated; line 2 is overflow only."""
    cleaned = _clean(value, 500).strip(" ,")
    if len(cleaned) <= maximum:
        return cleaned, ""
    boundary = max(cleaned.rfind(",", 0, maximum + 1), cleaned.rfind(" ", 0, maximum + 1))
    if boundary < maximum // 2:
        boundary = maximum
    return cleaned[:boundary].strip(" ,"), cleaned[boundary:].strip(" ,")


def _duration_parts(value):
    match = re.fullmatch(
        r"\s*(\d+(?:\.\d+)?)\s*(DAY|DAYS|WEEK|WEEKS|MONTH|MONTHS|YEAR|YEARS)\s*",
        str(value or ""), re.IGNORECASE,
    )
    if not match:
        return None
    return {
        "amount": match.group(1),
        "unit": match.group(2).upper().rstrip("S"),
    }


def build_travel_actions(payload):
    """Return a page-scoped plan. It never contains Save or Next actions."""
    fields = _field_map(payload)
    questions = _question_map(payload)
    specific = questions.get("travel.specific_plans") or {}
    payer = questions.get("travel.payer") or {}
    payer_same = questions.get("travel.payer_address_same") or {}
    actions = _purpose_actions(payload.get("visaType"), questions)

    specific_answer = _clean(specific.get("answer")).lower()
    if specific_answer in {"yes", "no"}:
        actions.append(_action(
            "travel.specific_plans", "Have you made specific travel plans?",
            "yes_no", specific_answer,
            labelTerms=["Have you made specific travel plans"],
            controlHints=["SpecificTravel", "SPECIFIC_TRAVEL"],
            causesRefresh=True,
        ))

    detail_specs = (
        ("arrivalFlight", "travel.arrivalFlight", "Arrival Flight", ["Arrival Flight"]),
        ("arrivalCity", "travel.arrivalCity", "Arrival City", ["Arrival City"]),
        ("departureFlight", "travel.departureFlight", "Departure Flight", ["Departure Flight"]),
        ("departureCity", "travel.departureCity", "Departure City", ["Departure City"]),
    )
    arrival_date = _detail_value(specific, "arrivalDate", fields, "travel.arrivalDate")
    if arrival_date:
        actions.append(_action(
            "travel.arrivalDate", "Intended Date of Arrival", "date",
            arrival_date, labelTerms=["Intended Date of Arrival"],
            controlHints=["ARRIVAL", "ARRIVE"],
        ))
    for detail_id, field_id, label, terms in detail_specs[:2]:
        value = _detail_value(specific, detail_id, fields, field_id)
        if value:
            actions.append(_action(field_id, label, "text", value, labelTerms=terms))

    departure_date = _detail_value(
        specific, "departureDate", fields, "travel.departureDate"
    )
    if departure_date:
        actions.append(_action(
            "travel.departureDate", "Departure Date", "date", departure_date,
            labelTerms=["Departure Date", "Date of Departure"],
        ))
    for detail_id, field_id, label, terms in detail_specs[2:]:
        value = _detail_value(specific, detail_id, fields, field_id)
        if value:
            actions.append(_action(field_id, label, "text", value, labelTerms=terms))

    locations = _detail_value(specific, "locations", fields, "travel.locations")
    if locations:
        first_location = re.split(r"[;\n|]+", locations)[0].strip()
        actions.append(_action(
            "travel.locations", "Location(s) You Plan to Visit", "text",
            first_location, labelTerms=["Location", "Plan to Visit"],
        ))

    stay_length = _clean((specific.get("details") or {}).get("stayLength"))
    stay_unit = _clean((specific.get("details") or {}).get("stayUnit")).upper().rstrip("S")
    duration = (
        {"amount": stay_length, "unit": stay_unit}
        if stay_length and stay_unit
        else _duration_parts(
            _detail_value(specific, "stayDuration", fields, "travel.stayDuration")
        )
    )
    if duration:
        actions.append(_action(
            "travel.stayDuration", "Intended Length of Stay in U.S.",
            "duration", f"{duration['amount']} {duration['unit']}",
            labelTerms=["Intended Length of Stay"],
            controlHints=["LENGTH_OF_STAY", "LENGTH_STAY"], duration=duration,
        ))

    details = specific.get("details") or {}
    address = {
        "street1": _field_value(fields, "contact.usStreet1") or _clean(details.get("usStreet1")),
        "street2": _field_value(fields, "contact.usStreet2") or _clean(details.get("usStreet2")),
        "city": _field_value(fields, "contact.usCity") or _clean(details.get("usCity")),
        "state": _field_value(fields, "contact.usState") or _clean(details.get("usState")),
        "postalCode": _field_value(fields, "contact.usPostalCode") or _clean(details.get("usPostalCode")),
    }
    if not address["street1"]:
        address = parse_us_address(
            _detail_value(specific, "usAddress", fields, "contact.usAddress")
        )
    address_specs = (
        ("street1", "travel.usStreet1", "Street Address (Line 1)", "text", ["Street Address", "Line 1"], ["ADDR_US_LINE1", "US_STREET_ADDR1"]),
        ("street2", "travel.usStreet2", "Street Address (Line 2)", "text", ["Street Address", "Line 2"], ["ADDR_US_LINE2", "US_STREET_ADDR2"]),
        ("city", "travel.usCity", "City", "text", ["Address Where You Will Stay", "City"], ["ADDR_US_CITY", "US_CITY"]),
        ("state", "travel.usState", "State", "select_text", ["Address Where You Will Stay", "State"], ["ADDR_US_STATE", "US_STATE"]),
        ("postalCode", "travel.usPostalCode", "ZIP Code", "text", ["ZIP Code"], ["ADDR_US_POSTAL", "US_ZIP"]),
    )
    for key, action_id, label, kind, terms, hints in address_specs:
        value = address.get(key)
        if not value:
            continue
        extra = {"optionTerms": [value]} if kind == "select_text" else {}
        actions.append(_action(
            action_id, label, kind, value, labelTerms=terms,
            controlHints=hints, **extra
        ))

    payer_answer = _clean(payer.get("answer")).lower()
    payer_options = {
        "self": ["SELF"],
        "other_person": ["OTHER PERSON"],
        "present_employer": ["PRESENT EMPLOYER"],
        "us_employer": ["EMPLOYER IN THE U.S."],
        "other_organization": ["OTHER COMPANY", "ORGANIZATION"],
    }
    if payer_answer in payer_options:
        actions.append(_action(
            "travel.payer", "Person/Entity Paying for Your Trip", "select_text",
            payer_answer, labelTerms=["Person/Entity Paying for Your Trip"],
            optionTerms=payer_options[payer_answer],
            controlHints=["Payer", "PAYING_FOR_TRIP"], causesRefresh=True,
        ))

    payer_detail_specs = (
        ("surname", "travel.payerSurname", "Surnames of Person Paying for Trip", "text", ["Surnames", "Paying for Trip"]),
        ("givenNames", "travel.payerGivenNames", "Given Names of Person Paying for Trip", "text", ["Given Names", "Paying for Trip"]),
        ("phone", "travel.payerPhone", "Telephone Number", "text", ["Telephone Number"]),
        ("email", "travel.payerEmail", "Email Address", "text", ["Email Address"]),
        ("relationship", "travel.payerRelationship", "Relationship to You", "select_text", ["Relationship to You"]),
        ("organization", "travel.payerOrganization", "Organization Name", "text", ["Organization Name"]),
        ("address", "travel.payerAddress", "Paying party address", "text", ["Address of the party paying"]),
    )
    payer_relationship_options = {
        # CEAC does not consistently expose a dedicated guardian relationship.
        # Keep the richer intake value, but select its supported catch-all option.
        "LEGAL GUARDIAN": ["OTHER"],
    }
    if payer_answer and payer_answer != "self":
        for detail_id, action_id, label, kind, terms in payer_detail_specs:
            value = _clean((payer.get("details") or {}).get(detail_id))
            if value:
                extra = (
                    {"optionTerms": payer_relationship_options.get(value.upper(), [value])}
                    if kind == "select_text" else {}
                )
                actions.append(_action(action_id, label, kind, value, labelTerms=terms, **extra))
    same_answer = _clean(payer_same.get("answer")).lower()
    if payer_answer == "other_person" and same_answer in {"yes", "no"}:
        actions.append(_action(
            "travel.payerAddressSame", "Is the paying party address the same?",
            "yes_no", same_answer,
            labelTerms=["address of the party paying", "same as your Home"],
            causesRefresh=True,
        ))

    return {
        "version": 1,
        "page": "travel",
        "targetUrl": CEAC_TRAVEL_URL,
        "actions": actions,
        "clickSave": False,
        "clickNext": False,
    }


QUESTION_PAGE_KEYS = {
    "personal.other_names": "personal1",
    "personal.telecode": "personal1",
    "personal.marital_status": "personal1",
    "personal.current_spouse": "spouse",
    "personal.deceased_spouse": "spouse",
    "personal.former_spouses": "spouse",
    "personal.marital_other": "spouse",
    "personal.other_nationalities": "personal2",
    "personal.permanent_resident_other_country": "personal2",
    "companions.has_companions": "travel_companions",
    "companions.is_group": "travel_companions",
    "us_history.visited": "previous_us_travel",
    "us_history.drivers_license": "previous_us_travel",
    "us_history.previous_visa": "previous_us_travel",
    "us_history.visa_lost_stolen": "previous_us_travel",
    "us_history.visa_cancelled": "previous_us_travel",
    "us_history.refusal_or_admission": "previous_us_travel",
    "us_history.immigrant_petition": "previous_us_travel",
    "contact.mailing_same_as_home": "address_phone",
    "contact.other_phones": "address_phone",
    "contact.other_emails": "address_phone",
    "contact.social_media": "address_phone",
    "contact.other_platforms": "address_phone",
    "passport.type": "passport",
    "passport.lost_stolen": "passport",
    "family.father_known": "relatives",
    "family.father_in_us": "relatives",
    "family.mother_known": "relatives",
    "family.mother_in_us": "relatives",
    "family.immediate_relatives_us": "relatives",
    "family.other_relatives_us": "relatives",
    "work.primary_occupation": "work_education1",
    "work.previously_employed": "work_education2",
    "work.education_secondary_or_above": "work_education2",
    "additional.clan_tribe": "work_education3",
    "additional.languages": "work_education3",
    "additional.countries_visited": "work_education3",
    "additional.organizations": "work_education3",
    "additional.specialized_skills": "work_education3",
    "additional.military_service": "work_education3",
    "additional.paramilitary": "work_education3",
    "j.intends_to_study": "sevis",
}

QUESTION_LABEL_TERMS = {
    "personal.marital_status": ["Marital Status"],
    "us_history.drivers_license": ["U.S. Driver's License"],
    "us_history.visa_lost_stolen": ["U.S. Visa ever been lost or stolen"],
    "us_history.visa_cancelled": ["U.S. Visa ever been cancelled or revoked"],
    "contact.mailing_same_as_home": ["Mailing Address", "same as your Home Address"],
    "contact.other_phones": ["other telephone numbers", "last five years"],
    "contact.other_emails": ["other email addresses", "last five years"],
    "contact.social_media": ["social media", "last five years"],
    "contact.other_platforms": ["other websites or applications", "content"],
    "passport.type": ["Passport/Travel Document Type"],
    "passport.lost_stolen": ["lost a passport", "stolen"],
    "family.father_in_us": ["Is your father in the U.S."],
    "family.mother_in_us": ["Is your mother in the U.S."],
    "family.immediate_relatives_us": ["immediate relatives", "United States"],
    "family.other_relatives_us": ["other relatives", "United States"],
    "work.previously_employed": ["Were you previously employed"],
    "work.education_secondary_or_above": ["attended any educational institutions", "secondary level"],
    "additional.clan_tribe": ["belong to a clan or tribe"],
    "additional.countries_visited": ["traveled to any countries", "last five years"],
    "additional.organizations": ["belonged to", "professional", "charitable organization"],
}

# CEAC is an ASP.NET application. The generated prefix can change, but the
# semantic suffixes below are stable enough to disambiguate nearby controls.
QUESTION_CONTROL_HINTS = {
    "personal.other_names": ["OtherNames", "OTHER_NAMES"],
    "personal.telecode": ["TelecodeQuestion", "TELECODE"],
    "personal.marital_status": ["APP_MARITAL_STATUS"],
    "personal.other_nationalities": ["OTH_NATL"],
    "personal.permanent_resident_other_country": ["OTH_RES", "PERM_RES"],
    "companions.has_companions": ["OTHER_PERSONS_TRAVELING", "TRAVEL_COMPANION"],
    "companions.is_group": ["GROUP_TRAVEL", "TRAVEL_GROUP"],
    "us_history.visited": ["PREV_US_TRAVEL_IND"],
    "us_history.drivers_license": ["PREV_US_DRIVER_LIC_IND", "DRIVER_LIC"],
    "us_history.previous_visa": ["PREV_VISA_IND"],
    "us_history.visa_lost_stolen": ["PREV_VISA_LOST_IND", "VISA_LOST"],
    "us_history.visa_cancelled": ["PREV_VISA_CANCELLED_IND", "VISA_CANCELLED"],
    "us_history.refusal_or_admission": ["PREV_VISA_REFUSED_IND", "VISA_REFUSED"],
    "us_history.immigrant_petition": ["IV_PETITION_IND"],
    "contact.mailing_same_as_home": ["MAILING_ADDR_SAME", "MAILING_ADDRESS"],
    "contact.other_phones": ["OTHER_PHONE", "ADDL_PHONE"],
    "contact.other_emails": ["OTHER_EMAIL", "ADDL_EMAIL"],
    "contact.social_media": ["SOCIAL_MEDIA", "SOCIALMED"],
    "contact.other_platforms": ["OTHER_SOCIAL", "OTHER_PLATFORM"],
    "passport.type": ["PPT_TYPE", "PASSPORT_TYPE"],
    "passport.lost_stolen": ["LOST_PPT", "LOST_PASSPORT"],
    "family.father_in_us": ["FATHER_US", "FATHER_IN_US"],
    "family.mother_in_us": ["MOTHER_US", "MOTHER_IN_US"],
    "family.immediate_relatives_us": ["IMMED_RELATIVE", "IMMEDIATE_REL"],
    "family.other_relatives_us": ["OTHER_RELATIVE", "OTH_REL"],
    "work.primary_occupation": ["PRIMARY_OCCUPATION"],
    "work.previously_employed": ["PREV_EMPLOYED", "PREVIOUS_EMPLOYMENT"],
    "work.education_secondary_or_above": ["EDUCATION", "PREV_EDUCATION"],
    "additional.clan_tribe": ["CLAN_TRIBE"],
    "additional.countries_visited": ["COUNTRIES_VISITED"],
    "additional.organizations": ["ORGANIZATION", "ORG_BELONG"],
}

FIELD_CONTROL_HINTS = {
    "personal.surname": ["APP_SURNAME"],
    "personal.givenNames": ["APP_GIVEN_NAME"],
    "personal.nativeName": ["APP_FULL_NAME_NATIVE", "FULL_NAME_NATIVE"],
    "personal.sex": ["APP_GENDER"],
    "personal.dateOfBirth": ["DOB"],
    "personal.birthCity": ["APP_POB_CITY", "POB_CITY"],
    # Legacy OCR records used one composite place-of-birth field. CEAC exposes
    # separate city, region, and country controls, so the legacy value is only
    # safe as the city fallback.
    "personal.placeOfBirth": ["APP_POB_CITY", "POB_CITY"],
    "personal.birthRegion": ["APP_POB_ST_PROVINCE", "POB_ST_PROVINCE"],
    "personal.birthCountry": ["APP_POB_CNTRY", "POB_CNTRY"],
    "personal.nationality": ["APP_NATL"],
    "personal.nationalId": ["APP_NATIONAL_ID", "NATIONAL_ID"],
    "passport.number": ["PPT_NUM", "PASSPORT_NUMBER"],
    "passport.issueDate": ["PPT_ISSUED", "ISSUANCE_DATE"],
    "passport.expiration": ["PPT_EXPIRE", "EXPIRATION_DATE"],
}

SKIPPED_GENERIC_QUESTIONS = {
    "personal.has_ssn",
    "personal.has_us_tax_id",
    "travel.specific_plans",
    "travel.b_visit_purpose",
    "travel.j1_category",
    "travel.payer",
    "travel.payer_address_same",
    "passport.has_book_number",
    "us_contact.knows_person",
}


def _choice_terms(question, answer):
    choice = next(
        (
            item for item in (question.get("choices") or [])
            if str(item.get("value") or "") == answer
        ),
        None,
    ) or {}
    label = _clean(choice.get("label"), 160)
    terms = [_clean(answer.replace("_", " "), 80).upper()]
    if "/" in label:
        label = label.rsplit("/", 1)[-1].strip()
    ascii_label = " ".join(re.findall(r"[A-Za-z0-9][A-Za-z0-9 .&'()-]*", label))
    if ascii_label:
        terms.insert(0, ascii_label.upper())
    return list(dict.fromkeys(term for term in terms if term))


def _simple_question_action(question):
    answer = _clean(question.get("answer"), 100).lower()
    if not answer or answer == "unknown" or question.get("sensitive"):
        return None
    answer_type = question.get("answerType")
    if answer_type not in {"yes_no", "select"}:
        return None
    if answer_type == "yes_no" and answer not in {"yes", "no"}:
        return None
    label_terms = QUESTION_LABEL_TERMS.get(question.get("id")) or [
        question.get("englishLabel") or question.get("label") or question.get("id")
    ]
    extra = {
        "labelTerms": [term for term in label_terms if term],
        "controlHints": QUESTION_CONTROL_HINTS.get(question.get("id"), []),
    }
    kind = "yes_no"
    if answer_type == "select":
        kind = "select_text"
        extra["optionTerms"] = _choice_terms(question, answer)
    return _action(
        question.get("id"),
        question.get("englishLabel") or question.get("label") or question.get("id"),
        kind,
        answer,
        causesRefresh=True,
        **extra,
    )


def _field_action(fields, field_id, label, kind, terms, *, option_terms=None):
    value = _field_value(fields, field_id)
    if not value:
        return None
    extra = {
        "labelTerms": terms,
        "controlHints": FIELD_CONTROL_HINTS.get(field_id, []),
    }
    if kind == "text" and value.upper() in {
        "DOES NOT APPLY", "DO NOT KNOW", "NOT APPLICABLE", "N/A",
    }:
        return _action(
            field_id, label, "does_not_apply", "true",
            checkboxTerms=["DOES NOT APPLY", "DO NOT KNOW", "NOT APPLICABLE"],
            **extra,
        )
    if kind == "select_text":
        extra["optionTerms"] = option_terms or [value]
    return _action(field_id, label, kind, value, **extra)


def _question_detail(question, detail_id, fields=None, field_id=None):
    return _detail_value(question, detail_id, fields or {}, field_id)


def _binary_detail_action(
    action_id, label, value, label_terms, control_hints, occurrence=None,
):
    answer = _clean(value).lower()
    if answer not in {"yes", "no"}:
        return None
    extra = {"occurrence": occurrence} if occurrence is not None else {}
    return _action(
        action_id, label, "yes_no", answer,
        labelTerms=label_terms, controlHints=control_hints, causesRefresh=True,
        **extra,
    )


def _append(actions, action):
    if not action:
        return
    if any(existing.get("id") == action.get("id") for existing in actions):
        return
    actions.append(action)


def _personal_page_actions(fields, questions):
    personal1 = []
    for spec in (
        ("personal.surname", "Surnames", "text", ["Surnames"]),
        ("personal.givenNames", "Given Names", "text", ["Given Names"]),
        ("personal.nativeName", "Full Name in Native Alphabet", "text", ["Full Name in Native Alphabet"]),
        ("personal.sex", "Sex", "select_text", ["Sex"]),
        ("personal.dateOfBirth", "Date of Birth", "date", ["Date of Birth"]),
        ("personal.birthCity", "City of Birth", "text", ["City of Birth"]),
        ("personal.birthRegion", "State/Province of Birth", "text", ["State/Province of Birth"]),
        ("personal.birthCountry", "Country/Region of Birth", "select_text", ["Country/Region of Birth"]),
    ):
        _append(personal1, _field_action(fields, *spec))
    if not _field_value(fields, "personal.birthCity"):
        _append(personal1, _field_action(
            fields, "personal.placeOfBirth", "City of Birth", "text",
            ["City", "Date and Place of Birth"]
        ))

    other_names = questions.get("personal.other_names") or {}
    if other_names.get("answer") == "yes":
        for index, record in enumerate(other_names.get("records") or []):
            _append(personal1, _repeat_action(
                f"personal.other_names.ensure.{index + 1}", "Add Another Name",
                index + 1, ["Other Names", "Add Another"],
                ["OTHER_NAMES", "ADD"], ["Other Surnames Used"],
            ))
            for key, label, hints in (
                ("surname", "Other Surnames Used", ["OTHER_SURNAME"]),
                ("givenNames", "Other Given Names Used", ["OTHER_GIVEN_NAME"]),
            ):
                value = _clean(record.get(key))
                if value:
                    _append(personal1, _action(
                        f"personal.other_names.{index}.{key}", label, "text", value,
                        labelTerms=[label], controlHints=hints, occurrence=index,
                    ))

    telecode = questions.get("personal.telecode") or {}
    if telecode.get("answer") == "yes":
        for key, label, hints in (
            ("surnameTelecode", "Surnames Telecode", ["TELECODE_SURNAME"]),
            ("givenNamesTelecode", "Given Names Telecode", ["TELECODE_GIVEN_NAME"]),
        ):
            value = _question_detail(telecode, key)
            if value:
                _append(personal1, _action(
                    f"personal.telecode.{key}", label, "text", value,
                    labelTerms=[label], controlHints=hints,
                ))

    personal2 = []
    for spec in (
        ("personal.nationality", "Nationality", "select_text", ["Nationality"]),
        ("personal.nationalId", "National Identification Number", "text", ["National Identification Number"]),
    ):
        _append(personal2, _field_action(fields, *spec))

    ssn = questions.get("personal.has_ssn") or {}
    if ssn.get("answer") == "no":
        _append(personal2, _action(
            "personal.ssn.does_not_apply", "Social Security Number", "does_not_apply", "true",
            labelTerms=["Social Security Number"], controlHints=["APP_SSN", "SSN"],
        ))
    elif ssn.get("answer") == "yes" and _question_detail(ssn, "number"):
        _append(personal2, _action(
            "personal.ssn", "Social Security Number", "text_segments",
            _question_detail(ssn, "number"), labelTerms=["Social Security Number"],
            controlHints=["APP_SSN", "SSN"],
        ))

    tax_id = questions.get("personal.has_us_tax_id") or {}
    if tax_id.get("answer") == "no":
        _append(personal2, _action(
            "personal.tax_id.does_not_apply", "U.S. Taxpayer ID Number", "does_not_apply", "true",
            labelTerms=["U.S. Taxpayer ID Number", "Taxpayer ID"],
            controlHints=["APP_TAX_ID", "TAX_ID"],
        ))
    elif tax_id.get("answer") == "yes" and _question_detail(tax_id, "number"):
        _append(personal2, _action(
            "personal.tax_id", "U.S. Taxpayer ID Number", "text",
            _question_detail(tax_id, "number"), labelTerms=["U.S. Taxpayer ID Number", "Taxpayer ID"],
            controlHints=["APP_TAX_ID", "TAX_ID"],
        ))

    other_nationalities = questions.get("personal.other_nationalities") or {}
    if other_nationalities.get("answer") == "yes":
        for index, record in enumerate(other_nationalities.get("records") or []):
            _append(personal2, _repeat_action(
                f"personal.other_nationalities.ensure.{index + 1}",
                "Add Another Nationality", index + 1,
                ["Other Nationality", "Add Another"],
                ["OTH_NATL", "ADD"], ["Other Country/Region of Nationality"],
            ))
            country = _clean(record.get("country"))
            if country:
                _append(personal2, _action(
                    f"personal.other_nationalities.{index}.country",
                    "Other Country/Region of Nationality", "select_text", country,
                    labelTerms=["Other Country/Region of Nationality"],
                    optionTerms=[country], controlHints=["OTH_NATL", "OTHER_NATIONALITY"],
                    occurrence=index,
                ))
            held_passport = _clean(record.get("heldPassport")).lower()
            _append(personal2, _binary_detail_action(
                f"personal.other_nationalities.{index}.heldPassport",
                "Do you hold a passport for the other nationality?", held_passport,
                ["passport", "other nationality"], ["OTH_NATL_PPT", "OTHER_PASSPORT"],
                occurrence=index,
            ))
            passport_number = _clean(record.get("passportNumber"))
            if passport_number:
                _append(personal2, _action(
                    f"personal.other_nationalities.{index}.passportNumber",
                    "Other Passport Number", "text", passport_number,
                    labelTerms=["Passport Number"],
                    controlHints=["OTH_NATL_PPT_NUM", "OTHER_PASSPORT_NUMBER"],
                    occurrence=index,
                ))

    permanent_residence = questions.get("personal.permanent_resident_other_country") or {}
    if permanent_residence.get("answer") == "yes":
        for index, record in enumerate(permanent_residence.get("records") or []):
            _append(personal2, _repeat_action(
                f"personal.permanent_resident_other_country.ensure.{index + 1}",
                "Add Another Permanent Residence", index + 1,
                ["Permanent Residence", "Add Another"],
                ["PERM_RES", "ADD"], ["Country/Region of Permanent Residence"],
            ))
            country = _clean(record.get("country"))
            if country:
                _append(personal2, _action(
                    f"personal.permanent_resident_other_country.{index}.country",
                    "Country/Region of Permanent Residence", "select_text", country,
                    labelTerms=["Country/Region of Permanent Residence"],
                    optionTerms=[country], controlHints=["PERM_RES", "OTH_RES"],
                    occurrence=index,
                ))
    return personal1, personal2


def _travel_companion_actions(questions):
    actions = []
    group = questions.get("companions.is_group") or {}
    if group.get("answer") == "yes":
        group_name = _question_detail(group, "groupName")
        if group_name:
            _append(actions, _action(
                "companions.groupName", "Group Name", "text", group_name,
                labelTerms=["Group Name"], controlHints=["GROUP_NAME"],
            ))

    people = questions.get("companions.people") or {}
    if people.get("visible") is not False:
        for index, record in enumerate(people.get("records") or []):
            _append(actions, _repeat_action(
                f"companions.people.ensure.{index + 1}", "Add Another Traveler",
                index + 1, ["Persons traveling with you", "Add Another"],
                ["TRAVEL_COMPANION", "ADD"],
                ["Surnames of Person Traveling With You"],
            ))
            for key, label, kind, hints in (
                ("surname", "Surnames of Person Traveling With You", "text", ["TRAVEL_COMPANION_SURNAME"]),
                ("givenNames", "Given Names of Person Traveling With You", "text", ["TRAVEL_COMPANION_GIVEN_NAME"]),
                ("relationship", "Relationship with Person", "select_text", ["TRAVEL_COMPANION_RELATIONSHIP"]),
            ):
                value = _clean(record.get(key))
                if not value:
                    continue
                extra = {"optionTerms": [value]} if kind == "select_text" else {}
                _append(actions, _action(
                    f"companions.people.{index}.{key}", label, kind, value,
                    labelTerms=[label], controlHints=hints, occurrence=index, **extra,
                ))
    return actions


def _previous_us_actions(questions):
    actions = []
    visited = questions.get("us_history.visited") or {}
    if visited.get("answer") == "yes":
        for index, record in enumerate(visited.get("records") or []):
            _append(actions, _repeat_action(
                f"us_history.visited.ensure.{index + 1}", "Add Another U.S. Visit",
                index + 1, ["last five U.S. visits", "Add Another"],
                ["PREV_US_VISIT", "ADD"], ["Date Arrived"],
            ))
            arrival_date = _clean(record.get("arrivalDate"))
            if arrival_date:
                _append(actions, _action(
                    f"us_history.visited.{index}.arrivalDate", "Date Arrived", "date",
                    arrival_date, labelTerms=["Date Arrived"],
                    controlHints=["PREV_US_VISIT", "ARRIVAL"], occurrence=index,
                ))
            stay_length = _clean(record.get("stayLength"))
            stay_unit = _clean(record.get("stayUnit")).upper().rstrip("S")
            if stay_length and stay_unit:
                _append(actions, _action(
                    f"us_history.visited.{index}.duration", "Length of Stay", "duration",
                    f"{stay_length} {stay_unit}", labelTerms=["Length of Stay"],
                    controlHints=["PREV_US_VISIT", "LENGTH_STAY"], occurrence=index,
                    duration={"amount": stay_length, "unit": stay_unit},
                ))

    drivers = questions.get("us_history.drivers_license") or {}
    if drivers.get("answer") == "yes":
        for index, record in enumerate(drivers.get("records") or []):
            _append(actions, _repeat_action(
                f"us_history.drivers_license.ensure.{index + 1}",
                "Add Another Driver's License", index + 1,
                ["Driver's License", "Add Another"],
                ["DRIVER_LIC", "ADD"], ["Driver's License Number"],
            ))
            number = _clean(record.get("number"))
            state = _clean(record.get("state"))
            if number:
                _append(actions, _action(
                    f"us_history.drivers_license.{index}.number", "Driver's License Number",
                    "text", number, labelTerms=["Driver's License Number"],
                    controlHints=["DRIVER_LIC_NUM"], occurrence=index,
                ))
            if state:
                _append(actions, _action(
                    f"us_history.drivers_license.{index}.state", "State of Driver's License",
                    "select_text", state, labelTerms=["State of Driver's License"],
                    optionTerms=[state], controlHints=["DRIVER_LIC_STATE"], occurrence=index,
                ))

    previous_visa = questions.get("us_history.previous_visa") or {}
    if previous_visa.get("answer") == "yes":
        issue_date = _question_detail(previous_visa, "issueDate")
        if issue_date:
            _append(actions, _action(
                "us_history.previous_visa.issueDate", "Date Last Visa Was Issued", "date",
                issue_date, labelTerms=["Date Last Visa Was Issued"],
                controlHints=["PREV_VISA_ISSUED", "VISA_ISSUE_DATE"],
            ))
        visa_number = _question_detail(previous_visa, "visaNumber")
        if visa_number.upper() in {"DO NOT KNOW", "DOES NOT APPLY", "UNKNOWN"}:
            _append(actions, _action(
                "us_history.previous_visa.visaNumberUnknown", "Visa Number",
                "does_not_apply", "true", labelTerms=["Visa Number"],
                checkboxTerms=["DO NOT KNOW"], controlHints=["PREV_VISA", "FOIL_NUMBER"],
            ))
        elif visa_number:
            _append(actions, _action(
                "us_history.previous_visa.visaNumber", "Visa Number", "text", visa_number,
                labelTerms=["Visa Number"], controlHints=["PREV_VISA", "FOIL_NUMBER"],
            ))
        for key, label, terms, hints in (
            ("sameClass", "Are you applying for the same type of visa?", ["same type of visa"], ["PREV_VISA_SAME_TYPE_IND"]),
            ("sameLocation", "Are you applying in the same country or location?", ["same country or location"], ["PREV_VISA_SAME_CNTRY_IND"]),
            ("tenPrinted", "Have you been ten-printed?", ["ten-printed"], ["PREV_VISA_TEN_PRINT_IND"]),
        ):
            _append(actions, _binary_detail_action(
                f"us_history.previous_visa.{key}", label,
                _question_detail(previous_visa, key), terms, hints,
            ))
    return actions


def _address_phone_actions(fields):
    actions = []
    specs = (
        ("contact.homeStreet1", "Home Address Line 1", "text", ["Home Address", "Street Address (Line 1)"]),
        ("contact.homeStreet2", "Home Address Line 2", "text", ["Home Address", "Street Address (Line 2)"]),
        ("contact.homeCity", "Home City", "text", ["Home Address", "City"]),
        ("contact.homeRegion", "Home State/Province", "text", ["Home Address", "State/Province"]),
        ("contact.homePostalCode", "Home Postal Zone/ZIP Code", "text", ["Home Address", "Postal Zone", "ZIP Code"]),
        ("contact.homeCountry", "Home Country/Region", "select_text", ["Home Address", "Country/Region"]),
        ("contact.primaryPhone", "Primary Phone Number", "text", ["Primary Phone Number"]),
        ("contact.secondaryPhone", "Secondary Phone Number", "text", ["Secondary Phone Number"]),
        ("contact.workPhone", "Work Phone Number", "text", ["Work Phone Number"]),
        ("contact.email", "Email Address", "text", ["Email Address"]),
    )
    for spec in specs:
        _append(actions, _field_action(fields, *spec))
    if not _field_value(fields, "contact.homeStreet1"):
        _append(actions, _field_action(
            fields, "contact.homeAddress", "Home Address Line 1", "text",
            ["Home Address", "Street Address (Line 1)"]
        ))
    return actions


def _mailing_address_actions(questions):
    question = questions.get("contact.mailing_same_as_home") or {}
    if question.get("answer") != "no":
        return []
    raw_address = _clean(
        (question.get("originalDetails") or {}).get("mailingAddress")
        or (question.get("details") or {}).get("mailingAddress")
    )
    if not raw_address:
        return []
    structured = structure_address(raw_address, "CHINA")
    record = {
        "address": structured.get("line1") or raw_address,
        "city": structured.get("city"),
        "region": structured.get("region"),
        "postalCode": structured.get("postalCode"),
        "country": structured.get("country") or "CHINA",
    }
    return _address_record_actions(
        "contact.mailing", record, 0, "Mailing Address", ["MAILING_ADDR"]
    )


def _passport_actions(fields, questions):
    actions = []
    specs = (
        ("passport.number", "Passport/Travel Document Number", "text", ["Passport/Travel Document Number"]),
        ("passport.issuingAuthority", "Country/Authority that Issued Passport", "select_text", ["Country/Authority that Issued Passport"]),
        ("passport.issueCity", "City Where Issued", "text", ["City Where Issued"]),
        ("passport.issueRegion", "State/Province Where Issued", "text", ["State/Province Where Issued"]),
        ("passport.issueCountry", "Country/Region Where Issued", "select_text", ["Country/Region Where Issued"]),
        ("passport.issueDate", "Issuance Date", "date", ["Issuance Date"]),
        ("passport.expiration", "Expiration Date", "date", ["Expiration Date"]),
    )
    for spec in specs:
        _append(actions, _field_action(fields, *spec))
    book = questions.get("passport.has_book_number") or {}
    if book.get("answer") == "no":
        _append(actions, _action(
            "passport.book_number.does_not_apply", "Passport Book Number", "does_not_apply", "true",
            labelTerms=["Passport Book Number"],
        ))
    elif book.get("answer") == "yes" and _question_detail(book, "bookNumber", fields, "passport.bookNumber"):
        _append(actions, _action(
            "passport.bookNumber", "Passport Book Number", "text",
            _question_detail(book, "bookNumber", fields, "passport.bookNumber"),
            labelTerms=["Passport Book Number"],
        ))

    lost = questions.get("passport.lost_stolen") or {}
    if lost.get("answer") == "yes":
        for index, record in enumerate(lost.get("records") or []):
            _append(actions, _repeat_action(
                f"passport.lost_stolen.ensure.{index + 1}",
                "Add Another Lost or Stolen Passport", index + 1,
                ["Lost or Stolen Passport", "Add Another"],
                ["LOST_PPT", "ADD"], ["Passport/Travel Document Number"],
            ))
            passport_number = _clean(record.get("passportNumber"))
            if passport_number.upper() in {"DO NOT KNOW", "DOES NOT APPLY", "UNKNOWN"}:
                _append(actions, _action(
                    f"passport.lost_stolen.{index}.numberUnknown",
                    "Lost Passport Number", "does_not_apply", "true",
                    labelTerms=["Passport/Travel Document Number"],
                    checkboxTerms=["DO NOT KNOW"], controlHints=["LOST_PPT", "PPT_NUM"],
                    occurrence=index,
                ))
            elif passport_number:
                _append(actions, _action(
                    f"passport.lost_stolen.{index}.passportNumber",
                    "Lost Passport Number", "text", passport_number,
                    labelTerms=["Passport/Travel Document Number"],
                    controlHints=["LOST_PPT", "PPT_NUM"], occurrence=index,
                ))
            for key, label, kind, hints in (
                ("issuingCountry", "Country/Authority that Issued Passport", "select_text", ["LOST_PPT", "COUNTRY"]),
                ("explanation", "Explain", "text", ["LOST_PPT", "EXPLAIN"]),
            ):
                value = _clean(record.get(key))
                if not value:
                    continue
                extra = {"optionTerms": [value]} if kind == "select_text" else {}
                _append(actions, _action(
                    f"passport.lost_stolen.{index}.{key}", label, kind, value,
                    labelTerms=[label], controlHints=hints, occurrence=index, **extra,
                ))
    return actions


def _us_contact_actions(fields, questions):
    actions = []
    question = questions.get("us_contact.knows_person") or {}
    details = question.get("details") or {}
    mode = question.get("answer")
    if mode == "organization":
        _append(actions, _action(
            "us_contact.person.does_not_know", "Contact Person", "does_not_apply", "true",
            labelTerms=["Contact Person", "Surnames"], checkboxTerms=["DO NOT KNOW"],
        ))
    for detail_id, field_id, label, kind, terms in (
        ("surname", "contact.surname", "Contact Surnames", "text", ["Contact Person", "Surnames"]),
        ("givenNames", "contact.givenNames", "Contact Given Names", "text", ["Contact Person", "Given Names"]),
        ("organization", "contact.organizationName", "Organization Name", "text", ["Organization Name"]),
        ("relationship", None, "Relationship to You", "select_text", ["Relationship to You"]),
        ("phone", "contact.phone", "Phone Number", "text", ["Phone Number"]),
        ("email", "contact.usEmail", "Email Address", "text", ["Email Address"]),
    ):
        value = _question_detail(question, detail_id, fields, field_id)
        if not value:
            continue
        extra = {"optionTerms": [value]} if kind == "select_text" else {}
        _append(actions, _action(
            f"us_contact.{detail_id}", label, kind, value, labelTerms=terms, **extra
        ))
    address = parse_us_address(_question_detail(question, "address", fields, "contact.usAddress"))
    for key, label, kind, terms in (
        ("street1", "U.S. Contact Address Line 1", "text", ["Address", "Street Address (Line 1)"]),
        ("city", "U.S. Contact City", "text", ["Address", "City"]),
        ("state", "U.S. Contact State", "select_text", ["Address", "State"]),
        ("postalCode", "U.S. Contact ZIP Code", "text", ["Address", "ZIP Code"]),
    ):
        value = address.get(key)
        if not value:
            continue
        extra = {"optionTerms": [value]} if kind == "select_text" else {}
        _append(actions, _action(
            f"us_contact.address.{key}", label, kind, value, labelTerms=terms, **extra
        ))
    return actions


def _work_actions(fields, questions):
    actions = []
    question = questions.get("work.primary_occupation") or {}
    for detail_id, field_id, label, kind, terms in (
        ("organization", "work.employerName", "Present Employer or School Name", "text", ["Present Employer or School Name"]),
        ("phone", "work.employerPhone", "Work Phone Number", "text", ["Phone Number"]),
        ("startDate", "work.startDate", "Start Date", "date", ["Start Date"]),
        ("monthlyIncome", "work.monthlyIncome", "Monthly Income", "text", ["Monthly Income"]),
        ("duties", "work.duties", "Briefly Describe your Duties", "text", ["Briefly Describe your Duties"]),
        ("explanation", None, "Explain", "text", ["Explain"]),
    ):
        value = _question_detail(question, detail_id, fields, field_id)
        if value:
            _append(actions, _action(
                f"work.{detail_id}", label, kind, value, labelTerms=terms
            ))
    occupation = _clean(question.get("answer")).lower()
    school_level = _clean((question.get("details") or {}).get("schoolLevel")).lower()
    if occupation == "student" and school_level == "secondary":
        title_value = ""
    else:
        title_value = (
            _question_detail(question, "courseOfStudy", fields, "education.programName")
            if occupation == "student"
            else _question_detail(question, "jobTitle", fields, "work.title")
        ) or _question_detail(question, "titleOrMajor", fields, "work.title")
    if title_value:
        label = "Course of Study" if occupation == "student" else "Job Title"
        _append(actions, _action(
            "work.courseOfStudy" if occupation == "student" else "work.jobTitle",
            label, "text", title_value, labelTerms=[label, "Job Title", "Course of Study"],
        ))
    raw_address = _clean(
        (question.get("originalDetails") or {}).get("address")
        or (question.get("details") or {}).get("address")
    ) or _field_value(fields, "work.employerAddress")
    if raw_address:
        structured = structure_address(raw_address, "CHINA")
        for action in _address_record_actions(
            "work.present.address", {
                "address": structured.get("line1") or raw_address,
                "city": structured.get("city"),
                "region": structured.get("region"),
                "postalCode": structured.get("postalCode"),
                "country": structured.get("country") or "CHINA",
            }, 0, "Present Employer or School", ["PRESENT", "EMPLOYER"]
        ):
            _append(actions, action)
    return actions


def _sevis_actions(fields):
    actions = []
    for spec in (
        ("education.sevisId", "SEVIS ID", "text", ["SEVIS ID"]),
        ("education.schoolName", "Name of School", "text", ["Name of School"]),
        ("education.programName", "Course of Study", "text", ["Course of Study"]),
        ("education.programNumber", "Program Number", "text", ["Program Number"]),
        ("education.sponsorName", "Program Sponsor", "text", ["Program Sponsor", "Sponsor Name"]),
        ("education.schoolStreet1", "School Address Line 1", "text", ["School Address", "Street Address (Line 1)"]),
        ("education.schoolStreet2", "School Address Line 2", "text", ["School Address", "Street Address (Line 2)"]),
        ("education.schoolCity", "School City", "text", ["School Address", "City"]),
        ("education.schoolState", "School State", "select_text", ["School Address", "State"]),
        ("education.schoolPostalCode", "School ZIP Code", "text", ["School Address", "ZIP Code"]),
    ):
        _append(actions, _field_action(fields, *spec))
    if not _field_value(fields, "education.schoolStreet1"):
        _append(actions, _field_action(
            fields, "education.schoolAddress", "School Address Line 1", "text",
            ["School Address", "Street Address (Line 1)"]
        ))
    return actions


def _repeat_action(
    action_id, label, count, label_terms, control_hints, record_label_terms=None,
):
    if count <= 1:
        return None
    return _action(
        action_id, label, "ensure_repeater", str(count),
        expectedCount=count,
        labelTerms=label_terms,
        recordLabelTerms=record_label_terms or label_terms,
        controlHints=control_hints,
        causesRefresh=True,
    )


def _family_actions(questions):
    actions = []
    for role, label, hints in (
        ("father", "Father", ["FATHER"]),
        ("mother", "Mother", ["MOTHER"]),
    ):
        known = questions.get(f"family.{role}_known") or {}
        for key, field_label, kind, field_hints in (
            ("surname", f"{label}'s Surnames", "text", [*hints, "SURNAME"]),
            ("givenNames", f"{label}'s Given Names", "text", [*hints, "GIVEN_NAME"]),
            ("dateOfBirth", f"{label}'s Date of Birth", "date", [*hints, "DOB"]),
        ):
            value = _question_detail(known, key)
            if not value:
                continue
            if key == "dateOfBirth" and value.upper() in {
                "DOES NOT APPLY", "DO NOT KNOW", "UNKNOWN",
            }:
                _append(actions, _action(
                    f"family.{role}.{key}.unknown", field_label,
                    "does_not_apply", "true", labelTerms=[field_label],
                    checkboxTerms=["DO NOT KNOW"], controlHints=field_hints,
                ))
            else:
                _append(actions, _action(
                    f"family.{role}.{key}", field_label, kind, value,
                    labelTerms=[field_label], controlHints=field_hints,
                ))

        in_us = questions.get(f"family.{role}_in_us") or {}
        if in_us.get("answer") == "yes":
            status = _question_detail(in_us, "status")
            if status:
                _append(actions, _action(
                    f"family.{role}.usStatus", f"{label}'s Status in the U.S.",
                    "select_text", status,
                    labelTerms=[f"{label}'s Status in the U.S.", "Status"],
                    optionTerms=[status], controlHints=[*hints, "US_STATUS"],
                ))

    relatives = questions.get("family.immediate_relatives_us") or {}
    if relatives.get("answer") == "yes":
        for index, record in enumerate(relatives.get("records") or []):
            _append(actions, _repeat_action(
                f"family.relatives.ensure.{index + 1}", "Add Another Relative",
                index + 1, ["Immediate Relatives", "Add Another"],
                ["IMMED_RELATIVE", "ADD"], ["Relative Surnames"],
            ))
            for key, field_label, kind, field_hints in (
                ("surname", "Relative Surnames", "text", ["RELATIVE", "SURNAME"]),
                ("givenNames", "Relative Given Names", "text", ["RELATIVE", "GIVEN_NAME"]),
                ("relationship", "Relationship to You", "select_text", ["RELATIVE", "RELATIONSHIP"]),
                ("usStatus", "Relative's Status", "select_text", ["RELATIVE", "STATUS"]),
            ):
                value = _clean(record.get(key))
                if not value:
                    continue
                extra = {"optionTerms": [value]} if kind == "select_text" else {}
                _append(actions, _action(
                    f"family.relatives.{index}.{key}", field_label, kind, value,
                    labelTerms=[field_label], controlHints=field_hints,
                    occurrence=index, **extra,
                ))
    return actions


def _spouse_actions(questions):
    actions = []
    for question_id, prefix, person_label in (
        ("personal.current_spouse", "spouse.current", "Spouse"),
        ("personal.deceased_spouse", "spouse.deceased", "Deceased Spouse"),
    ):
        question = questions.get(question_id) or {}
        for key, label, kind, hints in (
            ("surname", f"{person_label}'s Surnames", "text", ["SPOUSE", "SURNAME"]),
            ("givenNames", f"{person_label}'s Given Names", "text", ["SPOUSE", "GIVEN_NAME"]),
            ("dateOfBirth", f"{person_label}'s Date of Birth", "date", ["SPOUSE", "DOB"]),
            ("nationality", f"{person_label}'s Country/Region of Origin", "select_text", ["SPOUSE", "NATIONALITY"]),
            ("birthCity", f"{person_label}'s City of Birth", "text", ["SPOUSE", "BIRTH_CITY"]),
            ("birthCountry", f"{person_label}'s Country/Region of Birth", "select_text", ["SPOUSE", "BIRTH_COUNTRY"]),
        ):
            value = _question_detail(question, key)
            if not value:
                continue
            extra = {"optionTerms": [value]} if kind == "select_text" else {}
            _append(actions, _action(
                f"{prefix}.{key}", label, kind, value,
                labelTerms=[label, label.replace(f"{person_label}'s ", "")],
                controlHints=hints, **extra,
            ))

        if question_id != "personal.current_spouse":
            continue
        raw_address_type = _clean((question.get("details") or {}).get("addressType"))
        address_type_aliases = {
            "同家庭地址": ["SAME AS HOME ADDRESS"],
            "同邮寄地址": ["SAME AS MAILING ADDRESS"],
            "同美国联系人地址": ["SAME AS U.S. CONTACT ADDRESS"],
            "不知道": ["DO NOT KNOW"],
            "其他": ["OTHER"],
        }
        if raw_address_type:
            option_terms = address_type_aliases.get(raw_address_type, [raw_address_type])
            _append(actions, _action(
                "spouse.current.addressType", "Spouse's Address", "select_text",
                raw_address_type, labelTerms=["Spouse's Address"],
                optionTerms=option_terms, controlHints=["SPOUSE", "ADDRESS_TYPE"],
                causesRefresh=True,
            ))
        raw_address = _clean(
            (question.get("originalDetails") or {}).get("address")
            or (question.get("details") or {}).get("address")
        )
        if raw_address:
            structured = structure_address(raw_address, "CHINA")
            for action in _address_record_actions(
                "spouse.current.address", {
                    "address": structured.get("line1") or raw_address,
                    "city": structured.get("city"),
                    "region": structured.get("region"),
                    "postalCode": structured.get("postalCode"),
                    "country": structured.get("country") or "CHINA",
                }, 0, "Spouse", ["SPOUSE", "ADDRESS"]
            ):
                _append(actions, action)

    former = questions.get("personal.former_spouses") or {}
    for index, record in enumerate(former.get("records") or []):
        _append(actions, _repeat_action(
            f"spouse.former.ensure.{index + 1}", "Add Another Former Spouse",
            index + 1, ["Former Spouse", "Add Another"],
            ["FORMER_SPOUSE", "ADD"], ["Former Spouse's Surnames"],
        ))
        for key, label, kind, hints in (
            ("surname", "Former Spouse's Surnames", "text", ["FORMER_SPOUSE", "SURNAME"]),
            ("givenNames", "Former Spouse's Given Names", "text", ["FORMER_SPOUSE", "GIVEN_NAME"]),
            ("dateOfBirth", "Former Spouse's Date of Birth", "date", ["FORMER_SPOUSE", "DOB"]),
            ("nationality", "Former Spouse's Nationality", "select_text", ["FORMER_SPOUSE", "NATIONALITY"]),
            ("birthPlace", "Former Spouse's Place of Birth", "text", ["FORMER_SPOUSE", "BIRTH_PLACE"]),
            ("marriageDate", "Date of Marriage", "date", ["FORMER_SPOUSE", "MARRIAGE_DATE"]),
            ("marriageCountry", "Country/Region of Marriage", "select_text", ["FORMER_SPOUSE", "MARRIAGE_COUNTRY"]),
            ("endDate", "Date Marriage Ended", "date", ["FORMER_SPOUSE", "END_DATE"]),
            ("endCountry", "Country/Region Where Marriage Ended", "select_text", ["FORMER_SPOUSE", "END_COUNTRY"]),
            ("endExplanation", "Explain How the Marriage Ended", "text", ["FORMER_SPOUSE", "EXPLAIN"]),
        ):
            value = _clean(record.get(key))
            if not value:
                continue
            extra = {"optionTerms": [value]} if kind == "select_text" else {}
            _append(actions, _action(
                f"spouse.former.{index}.{key}", label, kind, value,
                labelTerms=[label], controlHints=hints, occurrence=index, **extra,
            ))

    marital_other = questions.get("personal.marital_other") or {}
    explanation = _question_detail(marital_other, "explanation")
    if explanation:
        _append(actions, _action(
            "spouse.maritalOther", "Explain Other Marital Status", "text",
            explanation, labelTerms=["Explain", "Other Marital Status"],
            controlHints=["MARITAL", "EXPLAIN"],
        ))
    return actions


def _contact_history_actions(questions):
    actions = []
    record_specs = {
        "contact.other_phones": (
            ("phone", "Other Telephone Number", "text", ["OTHER_PHONE", "PHONE"]),
        ),
        "contact.other_emails": (
            ("email", "Other Email Address", "text", ["OTHER_EMAIL", "EMAIL"]),
        ),
        "contact.social_media": (
            ("platform", "Social Media Provider/Platform", "select_text", ["SOCIAL_MEDIA", "PROVIDER"]),
            ("handle", "Social Media Identifier", "text", ["SOCIAL_MEDIA", "IDENTIFIER"]),
        ),
        "contact.other_platforms": (
            ("platform", "Other Social Media Platform", "text", ["OTHER_PLATFORM", "PLATFORM"]),
            ("handle", "Other Social Media Identifier", "text", ["OTHER_PLATFORM", "IDENTIFIER"]),
        ),
    }
    for question_id, specs in record_specs.items():
        question = questions.get(question_id) or {}
        if question.get("answer") != "yes":
            continue
        for index, record in enumerate(question.get("records") or []):
            _append(actions, _repeat_action(
                f"{question_id}.ensure.{index + 1}", "Add Another",
                index + 1, QUESTION_LABEL_TERMS.get(question_id, ["Add Another"]),
                QUESTION_CONTROL_HINTS.get(question_id, []), [specs[0][1]],
            ))
            for key, label, kind, hints in specs:
                value = _clean(record.get(key))
                if not value:
                    continue
                extra = {"optionTerms": [value]} if kind == "select_text" else {}
                _append(actions, _action(
                    f"{question_id}.{index}.{key}", label, kind, value,
                    labelTerms=[label], controlHints=hints,
                    occurrence=index, **extra,
                ))
    return actions


def _address_record_actions(prefix, record, index, label_prefix, hints):
    actions = []
    address = {
        "line1": _clean(record.get("address")),
        "city": _clean(record.get("city")),
        "region": _clean(record.get("region")),
        "postalCode": _clean(record.get("postalCode")),
        "country": _clean(record.get("country")),
    }
    if address["line1"] and not all(address.get(key) for key in ("city", "region")):
        parsed = structure_address(address["line1"], address.get("country") or "CHINA")
        for key in address:
            if not address[key] and parsed.get(key):
                address[key] = parsed[key]
    for key, label, kind, control_hints in (
        ("line1", f"{label_prefix} Street Address (Line 1)", "text", [*hints, "ADDR_LN1", "STREET"]),
        ("city", f"{label_prefix} City", "text", [*hints, "CITY"]),
        ("region", f"{label_prefix} State/Province", "text", [*hints, "STATE", "PROVINCE"]),
        ("postalCode", f"{label_prefix} Postal Zone/ZIP Code", "text", [*hints, "POSTAL", "ZIP"]),
        ("country", f"{label_prefix} Country/Region", "select_text", [*hints, "COUNTRY"]),
    ):
        value = address.get(key)
        if not value:
            continue
        extra = {"optionTerms": [value]} if kind == "select_text" else {}
        _append(actions, _action(
            f"{prefix}.{index}.{key}", label, kind, value,
            labelTerms=[label, label.replace(f"{label_prefix} ", "")],
            controlHints=control_hints, occurrence=index, **extra,
        ))
    return actions


def _work_history_actions(questions):
    actions = []
    employment = questions.get("work.previously_employed") or {}
    if employment.get("answer") == "yes":
        for index, record in enumerate(employment.get("records") or []):
            _append(actions, _repeat_action(
                f"work.previous.ensure.{index + 1}", "Add Another Employer",
                index + 1, ["Previous Employer", "Add Another"],
                ["PREV_EMPLOYER", "ADD"], ["Employer Name"],
            ))
            for key, label, kind, hints in (
                ("employer", "Employer Name", "text", ["PREV_EMPLOYER", "NAME"]),
                ("phone", "Employer Phone Number", "text", ["PREV_EMPLOYER", "PHONE"]),
                ("title", "Job Title", "text", ["PREV_EMPLOYER", "JOB_TITLE"]),
                ("supervisor", "Supervisor's Name", "text", ["PREV_EMPLOYER", "SUPERVISOR"]),
                ("startDate", "Employment Date From", "date", ["PREV_EMPLOYER", "START"]),
                ("endDate", "Employment Date To", "date", ["PREV_EMPLOYER", "END"]),
                ("duties", "Briefly Describe your Duties", "text", ["PREV_EMPLOYER", "DUTIES"]),
            ):
                value = _clean(record.get(key))
                if value:
                    _append(actions, _action(
                        f"work.previous.{index}.{key}", label, kind, value,
                        labelTerms=[label], controlHints=hints, occurrence=index,
                    ))
            for action in _address_record_actions(
                "work.previous.address", record, index, "Employer", ["PREV_EMPLOYER"]
            ):
                _append(actions, action)

    education = questions.get("work.education_secondary_or_above") or {}
    if education.get("answer") == "yes":
        original_records = education.get("originalRecords") or []
        for index, source_record in enumerate(education.get("records") or []):
            record = dict(source_record)
            original_record = (
                original_records[index] if index < len(original_records) else {}
            )
            record, _ = enrich_education_record(record, original_record)
            original_school = _clean(original_record.get("school"))
            school_value = _clean(record.get("school"))
            unresolved_legacy_pinyin = bool(
                record.get("schoolLookupStatus") == "unresolved"
                and contains_cjk(original_school)
                and re.search(
                    r"\b(?:SHI|XUE|XIAO|DI|GAO|JI|ZHONG|DA)\b",
                    school_value,
                    flags=re.IGNORECASE,
                )
            )
            _append(actions, _repeat_action(
                f"work.education.ensure.{index + 1}", "Add Another Institution",
                index + 1, ["Educational Institution", "Add Another"],
                ["EDUCATION", "ADD"], ["Name of Institution"],
            ))
            for key, label, kind, hints in (
                ("school", "Name of Institution", "text", ["EDUCATION", "SCHOOL_NAME"]),
                ("course", "Course of Study", "text", ["EDUCATION", "COURSE"]),
                ("startDate", "Date of Attendance From", "date", ["SCHOOLFROM", "EDUC_INST_FROM_DTE"]),
                ("endDate", "Date of Attendance To", "date", ["SCHOOLTO", "EDUC_INST_TO_DTE"]),
            ):
                value = _clean(record.get(key))
                if key == "school" and unresolved_legacy_pinyin:
                    continue
                if value:
                    _append(actions, _action(
                        f"work.education.{index}.{key}", label, kind, value,
                        labelTerms=[label], controlHints=hints, occurrence=index,
                    ))
            for action in _address_record_actions(
                "work.education", record, index, "School", ["EDUCATION"]
            ):
                _append(actions, action)
    return actions


def _additional_history_actions(questions):
    actions = []
    for question_id, key, label, kind, hints in (
        ("additional.languages", "language", "Language Name", "text", ["LANGUAGE"]),
        ("additional.countries_visited", "country", "Country/Region Visited", "select_text", ["COUNTRY_VISITED"]),
        ("additional.organizations", "name", "Organization Name", "text", ["ORGANIZATION_NAME"]),
    ):
        question = questions.get(question_id) or {}
        if question.get("answerType") != "records" and question.get("answer") != "yes":
            continue
        for index, record in enumerate(question.get("records") or []):
            _append(actions, _repeat_action(
                f"{question_id}.ensure.{index + 1}", "Add Another",
                index + 1, QUESTION_LABEL_TERMS.get(question_id, [label]), hints,
                [label],
            ))
            value = _clean(record.get(key))
            if not value:
                continue
            extra = {"optionTerms": [value]} if kind == "select_text" else {}
            _append(actions, _action(
                f"{question_id}.{index}.{key}", label, kind, value,
                labelTerms=[label], controlHints=hints,
                occurrence=index, **extra,
            ))
    return actions


def _action_priority(page_key, action_id):
    rules = {
        "personal1": [
            ("personal.other_names", 20),
            ("personal.telecode", 40),
            ("personal.marital_status", 60),
        ],
        "personal2": [
            ("personal.other_nationalities", 20),
            ("personal.permanent_resident_other_country", 40),
        ],
        "travel": [
            ("travel.purpose", 10),
            ("travel.specific_plans", 20),
            ("travel.payerAddressSame", 50),
            ("travel.payer", 40),
        ],
        "travel_companions": [
            ("companions.has_companions", 10),
            ("companions.is_group", 20),
            ("companions.groupName", 30),
            ("companions.people", 30),
        ],
        "previous_us_travel": [
            ("us_history.visited", 10),
            ("us_history.drivers_license", 30),
            ("us_history.previous_visa", 50),
            ("us_history.visa_lost_stolen", 70),
            ("us_history.visa_cancelled", 80),
        ],
        "address_phone": [
            ("contact.mailing_same_as_home", 10),
            ("contact.mailing", 11),
            ("contact.other_phones", 20),
            ("contact.other_emails", 30),
            ("contact.social_media", 40),
            ("contact.other_platforms", 50),
        ],
        "relatives": [
            ("family.father", 10),
            ("family.father_in_us", 20),
            ("family.mother", 30),
            ("family.mother_in_us", 40),
            ("family.immediate_relatives_us", 50),
            ("family.relatives", 60),
            ("family.other_relatives_us", 70),
        ],
        "spouse": [
            ("spouse.current.addressType", 10),
            ("spouse.current", 20),
            ("spouse.deceased", 20),
            ("spouse.former", 30),
            ("spouse.maritalOther", 40),
        ],
        "passport": [
            ("passport.type", 10),
            ("passport.book_number", 20),
            ("passport.bookNumber", 20),
            ("passport.lost_stolen", 40),
        ],
        "work_education1": [
            ("work.primary_occupation", 10),
            ("work", 20),
        ],
        "work_education2": [
            ("work.previously_employed", 10),
            ("work.previous", 20),
            ("work.education_secondary_or_above", 40),
            ("work.education", 50),
        ],
        "work_education3": [
            ("additional.clan_tribe", 10),
            ("additional.languages", 20),
            ("additional.countries_visited", 40),
            ("additional.organizations", 60),
        ],
    }
    for prefix, priority in rules.get(page_key, []):
        if action_id == prefix:
            return priority
        if action_id.startswith(f"{prefix}."):
            return priority + 1
    return 30 if page_key == "travel" else 0


def build_browser_workflow(payload):
    """Build a deterministic multi-page CEAC plan from confirmed case data.

    Sensitive answers are intentionally excluded. Page navigation is permitted
    only after every listed action on that page is found and visibly verified.
    """
    fields = _field_map(payload)
    questions = _question_map(payload)
    pages = {key: [] for key in CEAC_PAGE_META}

    pages["personal1"], pages["personal2"] = _personal_page_actions(fields, questions)
    pages["address_phone"].extend(_address_phone_actions(fields))
    pages["address_phone"].extend(_mailing_address_actions(questions))
    pages["address_phone"].extend(_contact_history_actions(questions))
    pages["passport"].extend(_passport_actions(fields, questions))
    pages["travel"].extend(build_travel_actions(payload).get("actions") or [])
    pages["travel_companions"].extend(_travel_companion_actions(questions))
    pages["previous_us_travel"].extend(_previous_us_actions(questions))
    pages["us_contact"].extend(_us_contact_actions(fields, questions))
    pages["relatives"].extend(_family_actions(questions))
    pages["spouse"].extend(_spouse_actions(questions))
    pages["work_education1"].extend(_work_actions(fields, questions))
    pages["work_education2"].extend(_work_history_actions(questions))
    pages["work_education3"].extend(_additional_history_actions(questions))
    pages["sevis"].extend(_sevis_actions(fields))

    manual_review_pages = set()
    skipped_sensitive = []
    translation_blocked_fields = []
    for question in questions.values():
        page_key = QUESTION_PAGE_KEYS.get(question.get("id"))
        if not page_key:
            continue
        if question.get("sensitive"):
            skipped_sensitive.append(question.get("id"))
            manual_review_pages.add(page_key)
            continue
        if question.get("id") in SKIPPED_GENERIC_QUESTIONS:
            continue
        _append(pages[page_key], _simple_question_action(question))

    for page_key, actions in pages.items():
        usable_actions = []
        for action in actions:
            if action.pop("translationBlocked", False):
                translation_blocked_fields.append({
                    "page": page_key,
                    "id": action.get("id") or "",
                    "label": action.get("label") or "",
                })
                manual_review_pages.add(page_key)
                continue
            usable_actions.append(action)
        pages[page_key] = usable_actions
        actions = usable_actions
        actions.sort(key=lambda item: _action_priority(page_key, item.get("id") or ""))

    page_plans = []
    for page_key, actions in pages.items():
        if not actions:
            continue
        label, patterns = CEAC_PAGE_META[page_key]
        manual_review = page_key in manual_review_pages
        page_translation_blocked = any(
            item.get("page") == page_key for item in translation_blocked_fields
        )
        page_plans.append({
            "key": page_key,
            "label": label,
            "targetUrl": CEAC_PAGE_URLS.get(page_key, ""),
            "urlPatterns": patterns,
            "actions": actions,
            "allowNext": not manual_review,
            "manualReview": manual_review,
            "stopReason": (
                "本页有中文内容无法安全转换为 CEAC 英文格式，已暂停自动进入下一页。"
                if page_translation_blocked
                else "本页包含必须由顾问确认的敏感问题，已填写非敏感字段并暂停。"
                if manual_review else ""
            ),
        })

    return {
        "version": 3,
        "page": "workflow",
        "targetUrl": CEAC_START_URL,
        "pages": page_plans,
        "totalFields": sum(len(page["actions"]) for page in page_plans),
        "skippedSensitiveQuestions": skipped_sensitive,
        "translationBlockedFields": translation_blocked_fields,
        "autoNext": True,
        "clickSave": False,
        "clickNext": True,
    }
