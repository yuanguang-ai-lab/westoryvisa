#!/usr/bin/env python3
"""Cached school-location lookup for DS-160 education records."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from difflib import SequenceMatcher
from pathlib import Path
from urllib import error as url_error
from urllib import parse as url_parse
from urllib import request as url_request

from .ds160_language import (
    compact_romanize,
    contains_cjk,
    normalize_ceac_text,
    structure_address,
    translate_ds160_value,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_PATH = ROOT / "data" / "school_directory_cache.json"
DEFAULT_VERIFIED_PATH = ROOT / "school_directory_verified.json"
RESOLVER_SCHEMA_VERSION = "school-entity-v2"
_CACHE_LOCK = threading.Lock()
_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST_AT = 0.0

_EDUCATION_TYPES = {
    "school", "college", "university", "kindergarten", "academy",
}
_EDUCATION_WORDS = (
    "school", "college", "university", "institute", "academy",
    "polytechnic", "education", "学校", "大学", "学院", "中学", "高中",
    "小学", "幼儿园", "职业技术",
)
_EDUCATION_INSTANCE_IDS = {
    # Broad Wikidata classes. Descriptions and labels are checked as well because
    # individual schools often use a more specific subclass.
    "Q2385804",  # educational institution
    "Q3918",     # university
    "Q38723",    # higher education institution
    "Q875538",   # public university
    "Q15936437", # research university
}


def _clean(value, limit=500):
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _cache_path():
    configured = os.environ.get("SCHOOL_LOOKUP_CACHE", "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_CACHE_PATH


def _verified_path():
    configured = os.environ.get("SCHOOL_VERIFIED_DIRECTORY", "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_VERIFIED_PATH


def _cache_key(name, location_hint=""):
    identity = "|".join(part for part in (_clean(name), _clean(location_hint)) if part)
    return re.sub(r"[^\w\u3400-\u9fff|]+", "", identity.casefold())


def _verified_school(name):
    """Resolve an institution against locally reviewed, source-linked records."""
    wanted = _cache_key(name)
    if not wanted:
        return None
    try:
        payload = json.loads(_verified_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    for raw_record in payload.get("schools") or []:
        if not isinstance(raw_record, dict):
            continue
        aliases = list(raw_record.get("aliases") or [])
        aliases.extend((raw_record.get("school"), raw_record.get("officialEnglishName")))
        if wanted not in {_cache_key(alias) for alias in aliases if alias}:
            continue
        result = {
            key: _clean(value, 1600)
            for key, value in raw_record.items()
            if key != "aliases" and value is not None
        }
        result["provider"] = "verified_local_directory"
        result["resolvedAt"] = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
        )
        return result
    return None


def _read_cache():
    path = _cache_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _write_cache(cache):
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def _cached(name, location_hint=""):
    with _CACHE_LOCK:
        cache = _read_cache()
        item = cache.get(_cache_key(name, location_hint))
        if not item and not location_hint:
            item = cache.get(_cache_key(name))
    if (
        not isinstance(item, dict)
        or not item.get("school")
        or item.get("resolverSchemaVersion") != RESOLVER_SCHEMA_VERSION
    ):
        return None
    return dict(item)


def _store(name, item, location_hint=""):
    item = dict(item)
    item["resolverSchemaVersion"] = RESOLVER_SCHEMA_VERSION
    with _CACHE_LOCK:
        cache = _read_cache()
        cache[_cache_key(name, location_hint)] = item
        _write_cache(cache)


def _provider_enabled():
    return os.environ.get("SCHOOL_LOOKUP_PROVIDER", "off").strip().lower() in {
        "auto", "nominatim", "openstreetmap", "osm", "wikidata",
    }


def _wait_for_rate_limit():
    global _LAST_REQUEST_AT
    with _REQUEST_LOCK:
        elapsed = time.monotonic() - _LAST_REQUEST_AT
        if elapsed < 1.05:
            time.sleep(1.05 - elapsed)
        _LAST_REQUEST_AT = time.monotonic()


def _entity_key(value):
    return re.sub(r"[^a-z0-9\u3400-\u9fff]+", "", _clean(value).casefold())


def _candidate_names(candidate):
    named = candidate.get("namedetails") or {}
    values = [_clean(value, 500) for value in named.values() if value]
    display_head = _clean(candidate.get("display_name"), 1500).split(",", 1)[0]
    if display_head:
        values.append(display_head)
    return list(dict.fromkeys(value for value in values if value))


def _name_similarity(left, right):
    left_key = _entity_key(left)
    right_key = _entity_key(right)
    if not left_key or not right_key:
        return 0.0
    if left_key == right_key:
        return 1.0
    if left_key in right_key or right_key in left_key:
        return 0.9
    return SequenceMatcher(None, left_key, right_key).ratio()


def _is_education_candidate(candidate):
    category = _clean(candidate.get("category"), 80).casefold()
    candidate_type = _clean(candidate.get("type"), 80).casefold()
    extras = candidate.get("extratags") or {}
    tagged_type = " ".join(
        _clean(extras.get(key), 120).casefold()
        for key in ("amenity", "building", "office", "education")
    )
    if candidate_type in _EDUCATION_TYPES:
        return True
    if any(token in tagged_type.split() for token in _EDUCATION_TYPES):
        return True
    if category == "education":
        return True
    return False


def _candidate_score(candidate, school_name, location_hint=""):
    if not _is_education_candidate(candidate):
        return -100.0
    similarities = [
        _name_similarity(school_name, candidate_name)
        for candidate_name in _candidate_names(candidate)
    ]
    best_similarity = max(similarities or [0.0])
    score = 25.0 + best_similarity * 60.0
    address = candidate.get("address") or {}
    if contains_cjk(school_name) and str(address.get("country_code") or "").lower() == "cn":
        score += 3.0
    if _clean((candidate.get("extratags") or {}).get("wikidata"), 40):
        score += 3.0
    if location_hint:
        location_similarity = _name_similarity(
            location_hint, _clean(candidate.get("display_name"), 1500)
        )
        score += location_similarity * 8.0
    score += min(max(float(candidate.get("importance") or 0), 0.0), 1.0) * 4.0
    return score


def _select_nominatim_candidate(candidates, school_name, location_hint=""):
    ranked = sorted(
        (
            (item, _candidate_score(item, school_name, location_hint))
            for item in candidates
            if isinstance(item, dict)
        ),
        key=lambda pair: pair[1],
        reverse=True,
    )
    if not ranked or ranked[0][1] < 68:
        return None, 0.0
    top, top_score = ranked[0]
    top_similarity = max(
        (_name_similarity(school_name, value) for value in _candidate_names(top)),
        default=0.0,
    )
    if len(ranked) > 1 and top_score - ranked[1][1] < 4 and top_similarity < 0.98:
        return None, 0.0
    return top, min(top_score / 100.0, 0.99)


def _first(address, *keys):
    for key in keys:
        value = _clean(address.get(key))
        if value:
            return value
    return ""


def _english_place(value, suffix_pattern=""):
    cleaned = _clean(value, 300)
    if not cleaned or not contains_cjk(cleaned):
        return cleaned
    if suffix_pattern:
        cleaned = re.sub(suffix_pattern, "", cleaned).strip() or cleaned
    return compact_romanize(cleaned)


def _address_fields(candidate):
    address = candidate.get("address") or {}
    road = _first(address, "road", "pedestrian", "residential")
    house_number = _first(address, "house_number")
    district = _first(address, "city_district", "district", "county")
    locality = _first(address, "neighbourhood", "quarter", "suburb")
    city = _first(address, "city", "town", "municipality")
    region = _first(address, "state", "province")
    postal_code = _first(address, "postcode")
    country_code = _first(address, "country_code").upper()
    country = "CHINA" if country_code == "CN" else _first(address, "country")
    raw_components = (road, district, locality, city, region)

    if any(contains_cjk(value) for value in raw_components if value):
        raw_street = f"{road}{house_number}号" if road and house_number else road
        full_address = "".join(
            part for part in (region, city, district, locality, raw_street, postal_code)
            if part
        )
        structured = structure_address(full_address, country or "CHINA")
        street = structured.get("line1") or ""
        line2 = structured.get("line2") or ""
        city = structured.get("city") or _english_place(city, r"市$")
        region = structured.get("region") or _english_place(
            region, r"(?:省|自治区|特别行政区)$"
        )
        postal_code = structured.get("postalCode") or postal_code
        country = structured.get("country") or country or "CHINA"
    else:
        street = " ".join(part for part in (house_number, road) if part)
        if locality and locality.casefold() not in street.casefold():
            street = ", ".join(part for part in (street, locality) if part)
        if district and district.casefold() not in street.casefold():
            street = ", ".join(part for part in (street, district) if part)
        line2 = ""

    return {
        "address": _clean(street, 500),
        "addressLine2": _clean(line2, 500),
        "city": _clean(city, 160),
        "region": _clean(region, 160),
        "postalCode": _clean(postal_code, 40),
        "country": _clean(country, 160),
    }


def _source_url(candidate):
    osm_type = _clean(candidate.get("osm_type"), 20).lower()
    osm_id = _clean(candidate.get("osm_id"), 40)
    if osm_type in {"node", "way", "relation"} and osm_id:
        return f"https://www.openstreetmap.org/{osm_type}/{osm_id}"
    return ""


def _format_candidate(candidate, school_name, confidence=0.0):
    named = candidate.get("namedetails") or {}
    english_name = _first(named, "name:en", "official_name:en", "short_name:en")
    if contains_cjk(english_name):
        english_name = ""
    result = {
        "school": _clean(school_name),
        "officialEnglishName": english_name,
        "displayName": _clean(candidate.get("display_name"), 1600),
        "provider": "nominatim_openstreetmap",
        "attribution": "OpenStreetMap contributors",
        "confidence": round(confidence, 3),
        "latitude": _clean(candidate.get("lat"), 40),
        "longitude": _clean(candidate.get("lon"), 40),
        "wikidataId": _clean((candidate.get("extratags") or {}).get("wikidata"), 40),
        "sourceUrl": _source_url(candidate),
        "resolvedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    result.update(_address_fields(candidate))
    return result


def _http_json(endpoint, parameters, *, nominatim=False, timeout=7):
    if nominatim:
        _wait_for_rate_limit()
    query = url_parse.urlencode(parameters)
    request = url_request.Request(
        f"{endpoint}?{query}",
        headers={
            "Accept": "application/json",
            "Accept-Language": "en,zh-CN;q=0.8",
            "User-Agent": os.environ.get(
                "SCHOOL_LOOKUP_USER_AGENT",
                "DocFlow-DS160/0.2 local-school-resolver",
            ),
        },
        method="GET",
    )
    try:
        with url_request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, TypeError, url_error.URLError, TimeoutError):
        return None


def _nominatim_endpoint(kind):
    search = os.environ.get(
        "SCHOOL_LOOKUP_URL", "https://nominatim.openstreetmap.org/search"
    ).strip()
    if kind == "search":
        return search
    configured = os.environ.get("SCHOOL_REVERSE_URL", "").strip()
    if configured:
        return configured
    return re.sub(r"/search/?$", "/reverse", search)


def _search_nominatim(school_name, location_hint=""):
    query_text = " ".join(part for part in (school_name, location_hint) if part)
    parameters = {
        "q": query_text,
        "format": "jsonv2",
        "addressdetails": "1",
        "namedetails": "1",
        "extratags": "1",
        "limit": "8",
        "accept-language": "en,zh-CN",
    }
    country_codes = os.environ.get("SCHOOL_LOOKUP_COUNTRYCODES", "").strip()
    if country_codes:
        parameters["countrycodes"] = country_codes
    payload = _http_json(
        _nominatim_endpoint("search"),
        parameters,
        nominatim=True,
    )
    return payload if isinstance(payload, list) else []


def _reverse_nominatim(latitude, longitude):
    if not latitude or not longitude:
        return None
    payload = _http_json(
        _nominatim_endpoint("reverse"),
        {
            "lat": latitude,
            "lon": longitude,
            "format": "jsonv2",
            "addressdetails": "1",
            "namedetails": "1",
            "extratags": "1",
            "zoom": "18",
            "accept-language": "en,zh-CN",
        },
        nominatim=True,
    )
    return payload if isinstance(payload, dict) else None


def _wikidata_label(entity, language):
    return _clean(((entity.get("labels") or {}).get(language) or {}).get("value"), 500)


def _wikidata_description(entity, language):
    return _clean(
        ((entity.get("descriptions") or {}).get(language) or {}).get("value"),
        800,
    )


def _claim_entity_ids(entity, property_id):
    values = []
    for claim in (entity.get("claims") or {}).get(property_id) or []:
        raw = (((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value"))
        if isinstance(raw, dict) and raw.get("id"):
            values.append(str(raw["id"]))
    return values


def _claim_coordinates(entity):
    for claim in (entity.get("claims") or {}).get("P625") or []:
        raw = (((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value"))
        if isinstance(raw, dict) and raw.get("latitude") is not None and raw.get("longitude") is not None:
            return str(raw["latitude"]), str(raw["longitude"])
    return "", ""


def _wikidata_education_text(*values):
    combined = " ".join(_clean(value, 1000).casefold() for value in values if value)
    return any(word in combined for word in _EDUCATION_WORDS)


def _wikidata_score(entity, school_name, search_item=None):
    search_item = search_item or {}
    labels = [
        _wikidata_label(entity, "zh"),
        _wikidata_label(entity, "en"),
        _clean(search_item.get("label"), 500),
    ]
    descriptions = [
        _wikidata_description(entity, "zh"),
        _wikidata_description(entity, "en"),
        _clean(search_item.get("description"), 800),
    ]
    instance_ids = set(_claim_entity_ids(entity, "P31"))
    is_education = bool(instance_ids & _EDUCATION_INSTANCE_IDS) or _wikidata_education_text(
        *labels, *descriptions
    )
    if not is_education:
        return -100.0
    similarity = max((_name_similarity(school_name, label) for label in labels), default=0.0)
    return 30.0 + similarity * 65.0


def _get_wikidata_entities(entity_ids):
    ids = [entity_id for entity_id in entity_ids if re.fullmatch(r"Q\d+", entity_id or "")]
    if not ids:
        return {}
    endpoint = os.environ.get(
        "SCHOOL_WIKIDATA_URL", "https://www.wikidata.org/w/api.php"
    ).strip()
    payload = _http_json(
        endpoint,
        {
            "action": "wbgetentities",
            "ids": "|".join(ids[:8]),
            "props": "labels|descriptions|claims",
            "languages": "zh|en",
            "languagefallback": "1",
            "format": "json",
        },
    )
    entities = (payload or {}).get("entities") if isinstance(payload, dict) else {}
    return entities if isinstance(entities, dict) else {}


def _resolve_wikidata(school_name, preferred_id=""):
    search_items = []
    if not re.fullmatch(r"Q\d+", preferred_id or ""):
        endpoint = os.environ.get(
            "SCHOOL_WIKIDATA_URL", "https://www.wikidata.org/w/api.php"
        ).strip()
        payload = _http_json(
            endpoint,
            {
                "action": "wbsearchentities",
                "search": school_name,
                "language": "zh" if contains_cjk(school_name) else "en",
                "uselang": "en",
                "type": "item",
                "limit": "8",
                "format": "json",
            },
        )
        search_items = (payload or {}).get("search") if isinstance(payload, dict) else []
        if not isinstance(search_items, list):
            search_items = []
    ids = [preferred_id] if preferred_id else [
        _clean(item.get("id"), 40) for item in search_items if isinstance(item, dict)
    ]
    entities = _get_wikidata_entities(ids)
    ranked = []
    search_by_id = {
        _clean(item.get("id"), 40): item
        for item in search_items
        if isinstance(item, dict)
    }
    for entity_id, entity in entities.items():
        if not isinstance(entity, dict):
            continue
        score = _wikidata_score(entity, school_name, search_by_id.get(entity_id))
        ranked.append((entity_id, entity, score))
    ranked.sort(key=lambda item: item[2], reverse=True)
    if not ranked or ranked[0][2] < 70:
        return None
    entity_id, entity, score = ranked[0]
    if len(ranked) > 1 and score - ranked[1][2] < 4:
        top_similarity = _name_similarity(school_name, _wikidata_label(entity, "zh"))
        if top_similarity < 0.98:
            return None
    latitude, longitude = _claim_coordinates(entity)
    english_name = _wikidata_label(entity, "en")
    if contains_cjk(english_name):
        english_name = ""
    return {
        "school": _clean(school_name),
        "officialEnglishName": english_name,
        "wikidataId": entity_id,
        "latitude": latitude,
        "longitude": longitude,
        "provider": "wikidata",
        "confidence": round(min(score / 100.0, 0.99), 3),
        "sourceUrl": f"https://www.wikidata.org/wiki/{entity_id}",
        "resolvedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


_CHINESE_ORDINALS = {
    "一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
    "六": "6", "七": "7", "八": "8", "九": "9", "十": "10",
}


def _derived_school_name(school_name):
    match = re.fullmatch(
        r"(.+?)(?:市)?第([一二三四五六七八九十]|\d+)(高级中学|中学|小学)",
        _clean(school_name, 240),
    )
    if not match:
        return ""
    place, ordinal, school_type = match.groups()
    ordinal = _CHINESE_ORDINALS.get(ordinal, ordinal)
    translated_type = {
        "高级中学": "SENIOR HIGH SCHOOL",
        "中学": "MIDDLE SCHOOL",
        "小学": "PRIMARY SCHOOL",
    }[school_type]
    return normalize_ceac_text(
        f"{compact_romanize(place)} NO. {ordinal} {translated_type}", 240
    )


def _translated_school_name(school_name):
    translated = translate_ds160_value(
        school_name,
        field_id="education.schoolName",
        context="Official English name of a Chinese educational institution",
    )
    value = _clean(translated.get("value"), 240)
    if value and not contains_cjk(value):
        return value, translated.get("provider") or "translation", bool(
            translated.get("reviewRequired")
        )
    derived = _derived_school_name(school_name)
    return (derived, "institution_name_grammar", True) if derived else ("", "", True)


def _merge_address(result, candidate):
    if not isinstance(candidate, dict):
        return result
    for key, value in _address_fields(candidate).items():
        if value and not result.get(key):
            result[key] = value
    if not result.get("latitude"):
        result["latitude"] = _clean(candidate.get("lat"), 40)
    if not result.get("longitude"):
        result["longitude"] = _clean(candidate.get("lon"), 40)
    return result


def _finalize_resolution(school_name, osm_result=None, wiki_result=None):
    result = dict(osm_result or wiki_result or {})
    if not result:
        return None
    sources = []
    for item in (osm_result, wiki_result):
        if not item:
            continue
        source_url = item.get("sourceUrl")
        if source_url and source_url not in sources:
            sources.append(source_url)
    if wiki_result and wiki_result.get("officialEnglishName"):
        result["officialEnglishName"] = wiki_result["officialEnglishName"]
        result["nameProvider"] = "wikidata"
    if wiki_result:
        result["wikidataId"] = wiki_result.get("wikidataId") or result.get("wikidataId")
        result["latitude"] = result.get("latitude") or wiki_result.get("latitude")
        result["longitude"] = result.get("longitude") or wiki_result.get("longitude")
    if not result.get("officialEnglishName"):
        translated, provider, needs_review = _translated_school_name(school_name)
        result["officialEnglishName"] = translated
        result["nameProvider"] = provider
        result["nameReviewRequired"] = needs_review
    else:
        result["nameProvider"] = result.get("nameProvider") or "openstreetmap"
        result["nameReviewRequired"] = False
    result["school"] = school_name
    result["sources"] = sources
    result["sourceUrl"] = sources[0] if sources else result.get("sourceUrl", "")
    confidences = [
        float(item.get("confidence") or 0)
        for item in (osm_result, wiki_result)
        if item
    ]
    result["confidence"] = round(max(confidences or [0.0]), 3)
    required = ("officialEnglishName", "city", "region", "country")
    complete = all(result.get(key) for key in required)
    result["status"] = "resolved" if complete else "partial"
    result["reviewRequired"] = bool(
        result.get("nameReviewRequired")
        or result["confidence"] < 0.78
        or not complete
        or not result.get("address")
        or not result.get("postalCode")
    )
    providers = [
        item.get("provider") for item in (osm_result, wiki_result) if item and item.get("provider")
    ]
    result["provider"] = "+".join(dict.fromkeys(providers)) or "school_resolver"
    return result


def lookup_school(name, location_hint=""):
    """Resolve an institution and structured address from public entity data.

    Only the institution name and optional location hint leave the server. Client,
    passport, contact and application data are never sent to lookup providers.
    """
    school_name = _clean(name, 240)
    if len(school_name) < 3:
        return None
    verified = _verified_school(school_name)
    if verified:
        return verified
    hint = _clean(location_hint, 300)
    cached = _cached(school_name, hint)
    if cached:
        return cached
    if not _provider_enabled():
        return None

    candidates = _search_nominatim(school_name)
    selected, osm_confidence = _select_nominatim_candidate(
        candidates, school_name, hint
    )
    if not selected and hint:
        selected, osm_confidence = _select_nominatim_candidate(
            _search_nominatim(school_name, hint), school_name, hint
        )
    osm_result = _format_candidate(selected, school_name, osm_confidence) if selected else None
    preferred_wikidata = _clean((osm_result or {}).get("wikidataId"), 40)
    needs_wikidata = not osm_result or not osm_result.get("officialEnglishName")
    wiki_result = _resolve_wikidata(school_name, preferred_wikidata) if needs_wikidata else None

    coordinates = osm_result or wiki_result or {}
    needs_reverse = bool(coordinates) and any(
        not (osm_result or {}).get(key)
        for key in ("address", "city", "region", "postalCode", "country")
    )
    reverse = None
    if needs_reverse:
        reverse = _reverse_nominatim(
            coordinates.get("latitude"), coordinates.get("longitude")
        )
    if osm_result:
        _merge_address(osm_result, reverse)
    elif wiki_result and reverse:
        _merge_address(wiki_result, reverse)

    result = _finalize_resolution(school_name, osm_result, wiki_result)
    if not result:
        translated, provider, needs_review = _translated_school_name(school_name)
        if translated:
            result = {
                "school": school_name,
                "officialEnglishName": translated,
                "provider": provider or "translation_only",
                "nameProvider": provider or "translation_only",
                "confidence": 0.55,
                "status": "partial",
                "reviewRequired": True,
                "nameReviewRequired": needs_review,
                "sources": [],
                "sourceUrl": "",
                "resolvedAt": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                ),
            }
    if result:
        result["resolverSchemaVersion"] = RESOLVER_SCHEMA_VERSION
        _store(school_name, result, hint)
    return result


def enrich_education_record(record, original_record=None):
    """Resolve one education record without discarding dates or course details."""
    if not isinstance(record, dict):
        return record, False
    original = original_record if isinstance(original_record, dict) else {}
    complete = all(record.get(key) for key in ("address", "city", "region", "country"))
    has_source_name = bool(_clean(original.get("school")))
    if complete and not has_source_name and record.get("schoolLookupProvider"):
        return record, False

    candidates = []
    for value in (original.get("school"), record.get("originalSchool"), record.get("school")):
        cleaned = _clean(value, 240)
        if cleaned and cleaned not in candidates:
            candidates.append(cleaned)
    location_hint = " ".join(
        dict.fromkeys(
            _clean(value, 300)
            for value in (
                original.get("city"), original.get("region"),
                original.get("country"), record.get("city"),
                record.get("region"), record.get("country"),
            )
            if _clean(value, 300)
        )
    )
    result = None
    for name in candidates:
        result = lookup_school(name, location_hint)
        if result:
            break
    if not result:
        if candidates:
            record["schoolLookupStatus"] = "unresolved"
            record["schoolLookupOriginalName"] = candidates[0]
        return record, False

    authoritative = result.get("provider") == "verified_local_directory"
    official_name = result.get("officialEnglishName")
    changed = False
    if official_name and not contains_cjk(official_name):
        record["school"] = _clean(official_name, 240).upper()
        record["officialEnglishName"] = record["school"]
        changed = True
    for key in ("address", "city", "region", "postalCode", "country"):
        value = result.get(key)
        if value and not contains_cjk(value) and (authoritative or not record.get(key)):
            record[key] = _clean(value, 500).upper()
            changed = True
    record["schoolLookupProvider"] = result.get("provider")
    record["schoolLookupStatus"] = result.get("status") or "resolved"
    record["schoolLookupConfidence"] = result.get("confidence")
    record["schoolLookupReviewRequired"] = bool(result.get("reviewRequired"))
    record["schoolLookupOriginalName"] = candidates[0] if candidates else ""
    if result.get("sourceUrl"):
        record["schoolLookupSource"] = result.get("sourceUrl")
    if result.get("sources"):
        record["schoolLookupSources"] = list(result.get("sources") or [])
    if result.get("latitude") and result.get("longitude"):
        record["schoolCoordinates"] = {
            "latitude": result.get("latitude"),
            "longitude": result.get("longitude"),
        }
    if result.get("postalCodeConfidence"):
        record["postalCodeConfidence"] = result.get("postalCodeConfidence")
    return record, changed


def enrich_questionnaire_education(questionnaire):
    """Upgrade education records already stored in a complete questionnaire."""
    questions = questionnaire if isinstance(questionnaire, list) else []
    resolved = 0
    for question in questions:
        if not isinstance(question, dict) or question.get("id") != "work.education_secondary_or_above":
            continue
        original_records = question.get("originalRecords") or []
        records = question.get("records") or []
        for index, record in enumerate(records):
            original = original_records[index] if index < len(original_records) else {}
            _, changed = enrich_education_record(record, original)
            resolved += int(changed)
    return questions, resolved


def enrich_education_updates(questionnaire_updates):
    updates = questionnaire_updates if isinstance(questionnaire_updates, dict) else {}
    education = updates.get("work.education_secondary_or_above") or {}
    records = education.get("records") or []
    original_records = education.get("originalRecords") or []
    resolved = 0
    for index, record in enumerate(records):
        original = original_records[index] if index < len(original_records) else {}
        _, changed = enrich_education_record(record, original)
        resolved += int(changed)
    return updates, resolved
