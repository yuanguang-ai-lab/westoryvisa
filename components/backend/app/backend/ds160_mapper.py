#!/usr/bin/env python3
import html as html_lib
import re
from datetime import datetime, timezone

from .ds160_value_validation import canonicalize_ds160_value
from .ds160_language import compact_romanize, structure_address, translate_ds160_value


FIELD_META = {
    "personal.surname": ("姓（Surname）", "基础信息", "high"),
    "personal.givenNames": ("名（Given Names）", "基础信息", "high"),
    "personal.nativeName": ("完整母语姓名", "基础信息", "medium"),
    "personal.dateOfBirth": ("出生日期", "基础信息", "high"),
    "personal.sex": ("性别", "基础信息", "medium"),
    "personal.birthCity": ("出生城市", "基础信息", "high"),
    "personal.birthRegion": ("出生省、州或地区", "基础信息", "medium"),
    "personal.birthCountry": ("出生国家或地区", "基础信息", "high"),
    "personal.placeOfBirth": ("出生地", "基础信息", "medium"),
    "personal.nationality": ("国籍", "基础信息", "high"),
    "personal.nationalId": ("本国身份证号码", "基础信息", "high"),
    "passport.number": ("护照号码", "护照信息", "high"),
    "passport.bookNumber": ("Passport Book Number", "护照信息", "medium"),
    "passport.type": ("护照类型", "护照信息", "medium"),
    "passport.issueDate": ("护照签发日期", "护照信息", "medium"),
    "passport.issuePlace": ("护照签发地", "护照信息", "medium"),
    "passport.issuingAuthority": ("护照签发机关", "护照信息", "medium"),
    "passport.expiration": ("护照有效期至", "护照信息", "medium"),
    "travel.arrivalDate": ("预计抵达美国日期", "旅行信息", "medium"),
    "travel.stayDuration": ("预计停留时间", "旅行信息", "low"),
    "travel.arrivalFlight": ("抵达航班", "旅行信息", "medium"),
    "travel.arrivalCity": ("抵达城市", "旅行信息", "medium"),
    "travel.departureDate": ("离开美国日期", "旅行信息", "medium"),
    "travel.departureFlight": ("离境航班", "旅行信息", "medium"),
    "travel.departureCity": ("离开城市", "旅行信息", "medium"),
    "travel.locations": ("计划访问地点", "旅行信息", "low"),
    "contact.usAddress": ("美国停留地址", "美国联系人", "medium"),
    "contact.homeStreet1": ("家庭地址 · 街道地址 1", "地址 / 电话 / 社交媒体", "medium"),
    "contact.homeStreet2": ("家庭地址 · 街道地址 2", "地址 / 电话 / 社交媒体", "low"),
    "contact.homeCity": ("家庭地址 · 城市", "地址 / 电话 / 社交媒体", "medium"),
    "contact.homeRegion": ("家庭地址 · 省、州或地区", "地址 / 电话 / 社交媒体", "medium"),
    "contact.homePostalCode": ("家庭地址 · 邮编", "地址 / 电话 / 社交媒体", "low"),
    "contact.homeCountry": ("家庭地址 · 国家或地区", "地址 / 电话 / 社交媒体", "medium"),
    "contact.surname": ("美国联系人姓", "美国联系人", "medium"),
    "contact.givenNames": ("美国联系人名", "美国联系人", "medium"),
    "contact.organizationName": ("美国联系人机构 / 学校", "美国联系人", "low"),
    "contact.phone": ("美国联系人电话", "美国联系人", "medium"),
    "contact.email": ("美国联系人邮箱", "美国联系人", "low"),
    "education.schoolName": ("学校名称", "SEVIS / 学生信息", "low"),
    "education.schoolAddress": ("学校地址", "SEVIS / 学生信息", "medium"),
    "education.programName": ("项目 / 专业名称", "SEVIS / 学生信息", "medium"),
    "education.sevisId": ("SEVIS ID", "SEVIS / 学生信息", "high"),
    "education.programNumber": ("J-1 Program Number", "SEVIS / 学生信息", "high"),
    "education.sponsorName": ("J-1 Sponsor 名称", "SEVIS / 学生信息", "medium"),
    "work.employerName": ("雇主名称", "工作 / 教育 / 培训", "low"),
    "work.employerAddress": ("雇主地址", "工作 / 教育 / 培训", "medium"),
    "work.employerPhone": ("雇主电话", "工作 / 教育 / 培训", "medium"),
    "work.startDate": ("入职 / 入学日期", "工作 / 教育 / 培训", "medium"),
    "work.title": ("职位 / 专业", "工作 / 教育 / 培训", "medium"),
    "work.monthlyIncome": ("月收入", "工作 / 教育 / 培训", "medium"),
    "work.duties": ("工作职责 / 学习内容", "工作 / 教育 / 培训", "low"),
    "history.previousVisaIssueDate": ("最近美国签证签发日期", "以往赴美记录", "high"),
    "history.previousVisaNumber": ("最近美国签证号码", "以往赴美记录", "high"),
    "history.previousVisaClass": ("最近美国签证类别", "以往赴美记录", "medium"),
}


SENSITIVE_FIELD_PREFIXES = (
    "security.", "history.refusal", "history.overstay", "history.criminal",
    "history.immigration", "history.removal",
)

CRITICAL_REVIEW_FIELD_IDS = {
    "personal.surname", "personal.givenNames", "personal.dateOfBirth",
    "passport.number", "passport.expiration", "travel.visaType",
    "travel.arrivalDate", "contact.usAddress", "education.sevisId",
    "education.programNumber", "history.previousVisaNumber",
}

MRZ_COUNTRY_NAMES = {
    "CHN": "CHINA",
    "HKG": "HONG KONG S.A.R.",
    "MAC": "MACAU S.A.R.",
    "TWN": "TAIWAN",
    "USA": "UNITED STATES OF AMERICA",
    "CAN": "CANADA",
    "GBR": "UNITED KINGDOM",
    "AUS": "AUSTRALIA",
    "SGP": "SINGAPORE",
    "JPN": "JAPAN",
    "KOR": "SOUTH KOREA",
}


def map_document(slot, filename, text, document_id, visa_type="", page_texts=None):
    pages = page_texts or [{"page": 1, "text": text}]
    candidates = []
    for page in pages:
        page_text = normalize_text(page.get("text"))
        if not page_text:
            continue
        page_candidates = map_page(slot, page_text, visa_type)
        for item in page_candidates:
            item["page"] = page.get("page") or 1
        candidates.extend(page_candidates)

    fallback_text = normalize_text(text)
    if fallback_text:
        fallback_candidates = map_page(slot, fallback_text, visa_type)
        for item in fallback_candidates:
            item.setdefault("page", 1)
        candidates.extend(fallback_candidates)

    if not candidates:
        return []

    output = []
    for candidate in candidates:
        field_id = candidate["id"]
        if field_id.startswith(SENSITIVE_FIELD_PREFIXES):
            continue
        label, section, risk = FIELD_META[field_id]
        prepared = translate_ds160_value(
            candidate["value"],
            field_id=field_id,
            context=label,
            preserve_native=field_id == "personal.nativeName",
        )
        output.append({
            "id": field_id,
            "label": label,
            "section": section,
            "value": prepared["value"],
            "originalValue": prepared.get("originalValue") or "",
            "translationProvider": prepared.get("provider") or "original",
            "sourceDocument": filename,
            "sourceDocumentId": document_id,
            "sourcePage": candidate.get("page") or 1,
            "evidence": candidate.get("evidence", ""),
            "confidence": round(candidate.get("confidence", 0.7), 2),
            "riskLevel": risk,
            "requiresUserConfirmation": (
                risk in {"high", "medium"} or prepared.get("reviewRequired", False)
            ),
            "confirmed": False,
            "editedByUser": False,
            "extractionMethod": candidate.get("method", "rule"),
        })
    return deduplicate(output)


def map_page(slot, text, visa_type):
    slot_lower = str(slot or "").lower()
    candidates = []
    if "身份证" in slot or "national id" in slot_lower or "identity card" in slot_lower:
        candidates.extend(map_national_id(text))
    if ("护照" in slot or "passport" in slot_lower) and "过往" not in slot:
        candidates.extend(map_passport(text))
    student_document = any(
        marker in slot_lower for marker in ("i-20", "ds-2019", "ds-7002", "enrollment")
    ) or any(marker in slot for marker in ("录取", "在读", "培训实习"))
    if is_student_visa(visa_type) and student_document:
        candidates.extend(map_student_document(text))
    if "旅行" in slot or "itinerary" in slot_lower:
        candidates.extend(map_travel(text))
    if any(marker in slot for marker in ("酒店", "邀请")) or any(
        marker in slot_lower for marker in ("hotel", "invitation")
    ):
        candidates.extend(map_us_address(text))
        candidates.extend(map_us_contact(text))
    if "在职" in slot or "employment" in slot_lower:
        candidates.extend(map_employment(text))
    if "过往美国签证" in slot or "previous u.s. visa" in slot_lower:
        candidates.extend(map_previous_visa(text))
    return candidates


def is_student_visa(visa_type):
    normalized = str(visa_type or "").strip().upper()
    return normalized.startswith(("F", "M", "J"))


def normalize_text(text):
    text = html_lib.unescape(str(text or ""))
    text = text.replace("\x00", " ").replace("\r\n", "\n").replace("\r", "\n")
    normalized_lines = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if cells and all(re.fullmatch(r"[-: ]+", cell or "-") for cell in cells):
                continue
            if len(cells) == 2 and all(cells):
                normalized_lines.append(f"{cells[0]}: {cells[1]}")
                continue
            if cells:
                normalized_lines.append(" | ".join(cell for cell in cells if cell))
                continue
        normalized_lines.append(raw_line)
    text = "\n".join(normalized_lines)
    text = re.sub(r"[\t\f\v]+", " ", text)
    text = re.sub(r"[ ]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def map_passport(text):
    results = []
    mrz = parse_td3_mrz(text)
    if mrz:
        evidence = " / ".join(mrz.pop("evidence"))
        checks = mrz.pop("checks")
        results.extend([
            candidate("personal.surname", mrz["surname"], 0.97, evidence, "mrz"),
            candidate("personal.givenNames", mrz["givenNames"], 0.97, evidence, "mrz"),
            candidate("personal.dateOfBirth", mrz["dateOfBirth"], 0.98 if checks["birth"] else 0.72, evidence, "mrz"),
            candidate("passport.number", mrz["passportNumber"], 0.99 if checks["passport"] else 0.72, evidence, "mrz"),
            candidate("passport.expiration", mrz["expirationDate"], 0.98 if checks["expiry"] else 0.72, evidence, "mrz"),
        ])
        if mrz.get("sex"):
            results.append(candidate("personal.sex", mrz["sex"], 0.96, evidence, "mrz"))
        if mrz.get("nationality"):
            results.append(candidate("personal.nationality", mrz["nationality"], 0.97, evidence, "mrz"))
        if mrz.get("passportType"):
            results.append(candidate("passport.type", mrz["passportType"], 0.96, evidence, "mrz"))

    passport_number = labelled_compact_value(text, [
        r"Passport\s*(?:No\.?|Number)", r"护照(?:号码|号)", r"Document\s*No\."
    ], r"[A-Z0-9][A-Z0-9\s-]{5,16}", r"[^A-Z0-9]")
    if not passport_number:
        # Chinese ordinary/service passport numbers are stable enough to use as
        # a lower-confidence fallback when OCR loses the bilingual label.
        standalone_number = re.search(
            r"(?<![A-Z0-9])((?:E[A-Z]?|[PSD]E?)\s*[- ]?(?:\d\s*){7,8})(?![A-Z0-9])",
            text,
            flags=re.IGNORECASE,
        )
        if standalone_number:
            normalized = re.sub(r"[^A-Z0-9]", "", standalone_number.group(1).upper())
            passport_number = (normalized, line_for_match(text, standalone_number))
    if passport_number and sum(character.isdigit() for character in passport_number[0]) >= 3:
        confidence = 0.9 if re.fullmatch(r"(?:E[A-Z]?|[PSD]E?)\d{7,8}", passport_number[0]) else 0.86
        results.append(candidate("passport.number", passport_number[0], confidence, passport_number[1], "label"))

    book_number = labelled_value(text, [
        r"Passport\s*Book\s*(?:No\.?|Number)", r"Inventory\s*Control\s*Number", r"护照簿编号",
    ], r"[A-Z0-9]{5,20}")
    if book_number:
        results.append(candidate("passport.bookNumber", book_number[0].upper(), 0.82, book_number[1], "label"))

    dob = labelled_date(text, [r"Date\s*of\s*Birth", r"出生日期", r"Birth\s*Date"])
    if dob:
        results.append(candidate("personal.dateOfBirth", dob[0], 0.86, dob[1], "label"))

    expiry = labelled_date(text, [
        r"Date\s*(?:of\s*)?(?:Expiry|Expiration)",
        r"(?:Expiry|Expiration)\s*Date",
        r"Valid\s*(?:Until|Through)",
        r"有效期至", r"有效期限", r"失效日期",
    ])
    if expiry:
        results.append(candidate("passport.expiration", expiry[0], 0.86, expiry[1], "label"))

    issue_date = labelled_date(text, [r"Date\s*of\s*Issue", r"Issue\s*Date", r"签发日期"])
    if issue_date:
        results.append(candidate("passport.issueDate", issue_date[0], 0.84, issue_date[1], "label"))
    if mrz:
        inferred_issue_dates = [
            item for item in passport_visible_dates(text)
            if mrz["dateOfBirth"] < item[0] < mrz["expirationDate"]
        ]
        if inferred_issue_dates:
            inferred_issue = max(inferred_issue_dates, key=lambda item: item[0])
            results.append(candidate(
                "passport.issueDate", inferred_issue[0], 0.82,
                inferred_issue[1], "passport_date_consistency",
            ))

    issue_place = labelled_document_line(
        text,
        [r"Place\s*of\s*Issue", r"Issuing\s*Place", r"签发地点"],
        [r"Place\s*of\s*Birth", r"出生地点", r"出生地(?!点)", r"Date\s*of\s*Issue", r"签发日期"],
    )
    if not issue_place:
        issue_place = bilingual_value_before_label(text, r"(?:Place\s*of\s*Issue|签发地点)")
    if issue_place:
        results.append(candidate("passport.issuePlace", issue_place[0], 0.78, issue_place[1], "label"))

    place_of_birth = labelled_document_line(
        text,
        [r"Place\s*of\s*Birth", r"出生地点", r"出生地(?!点)"],
        [r"Place\s*of\s*Issue", r"签发地点", r"Date\s*of\s*Birth", r"出生日期"],
    )
    if not place_of_birth:
        place_of_birth = bilingual_value_before_label(text, r"(?:Place\s*of\s*Birth|出生地点)")
    if place_of_birth:
        results.append(candidate("personal.placeOfBirth", place_of_birth[0], 0.8, place_of_birth[1], "label"))

    authority_match = re.search(
        r"National\s+Immigration\s+Administration\s*,?\s*P\.?R\.?C\.?",
        text,
        flags=re.IGNORECASE,
    )
    if authority_match:
        results.append(candidate(
            "passport.issuingAuthority", "NATIONAL IMMIGRATION ADMINISTRATION, PRC",
            0.96, line_for_match(text, authority_match), "authority_pattern",
        ))
    else:
        authority = labelled_document_line(
            text,
            [r"Issuing\s*Authority", r"Authority", r"签发机关"],
            [r"Place\s*of\s*Issue", r"签发地点", r"Bearer(?:'s)?\s*Signature", r"持照人签名"],
        )
        if authority and authority[0].upper() != "AUTHORITY":
            results.append(candidate(
                "passport.issuingAuthority", authority[0], 0.8, authority[1], "label"
            ))

    nationality = labelled_value(text, [r"Nationality", r"国籍"], r"[A-Z\u4e00-\u9fff][A-Z\u4e00-\u9fff ]{1,40}")
    if nationality:
        results.append(candidate(
            "personal.nationality", normalize_nationality(nationality[0]),
            0.82, nationality[1], "label",
        ))

    sex = labelled_value(text, [r"Sex", r"Gender", r"性别"], r"M|F|MALE|FEMALE|男|女")
    if sex:
        results.append(candidate("personal.sex", normalize_sex(sex[0]), 0.84, sex[1], "label"))

    surname = labelled_value(text, [r"Surname", r"姓"], r"[A-Z][A-Z '-]{1,48}")
    given = labelled_value(text, [r"Given\s*Names?", r"名"], r"[A-Z][A-Z '-]{1,64}")
    if not surname and not given:
        full_name = labelled_line(text, [r"姓名\s*/\s*Name", r"Full\s*Name"])
        if full_name:
            value = re.sub(r"[（(].*?[）)]", "", full_name[0]).strip()
            if "," in value:
                surname_value, given_value = [clean_name(part) for part in value.split(",", 1)]
            else:
                parts = clean_name(value).split()
                surname_value = parts[0] if len(parts) > 1 else ""
                given_value = " ".join(parts[1:]) if len(parts) > 1 else ""
            if surname_value:
                surname = (surname_value, full_name[1])
            if given_value:
                given = (given_value, full_name[1])
    if surname and plausible_passport_name(surname[0]):
        results.append(candidate("personal.surname", clean_name(surname[0]), 0.84, surname[1], "label"))
    if given and plausible_passport_name(given[0]):
        results.append(candidate("personal.givenNames", clean_name(given[0]), 0.84, given[1], "label"))

    native_name = labelled_line(text, [r"中文姓名", r"姓名\s*/\s*(?:Name|Chinese\s*Name)"])
    chinese_name = ""
    if native_name and contains_chinese(native_name[0]):
        chinese_name = "".join(re.findall(r"[\u3400-\u9fff·]+", native_name[0]))
        if mrz and not native_name_matches_mrz(
            chinese_name, mrz.get("surname"), mrz.get("givenNames")
        ):
            chinese_name = ""
    if not chinese_name and mrz:
        chinese_name = native_name_matching_mrz(
            text, mrz.get("surname"), mrz.get("givenNames")
        )
    if chinese_name:
        results.append(candidate(
            "personal.nativeName", chinese_name, 0.9 if mrz else 0.88,
            chinese_name if mrz else native_name[1],
            "mrz_name_match" if mrz else "label",
        ))
    return results


def map_national_id(text):
    """Map a PRC resident identity card without inferring facts not on the card."""
    results = []
    compact_text = re.sub(r"(?<=\d)[\s·•:：-]+(?=[0-9Xx])", "", text)
    id_match = re.search(r"(?<!\d)(\d{17}[0-9Xx])(?!\w)", compact_text)
    if id_match:
        number = id_match.group(1).upper()
        evidence = line_for_match(compact_text, id_match)
        checksum_valid = prc_id_checksum_valid(number)
        results.append(candidate(
            "personal.nationalId", number, 0.99 if checksum_valid else 0.82,
            evidence, "id_pattern",
        ))
        birth = number[6:14]
        try:
            birth_date = datetime.strptime(birth, "%Y%m%d").strftime("%Y-%m-%d")
            results.append(candidate(
                "personal.dateOfBirth", birth_date, 0.98 if checksum_valid else 0.78,
                "根据身份证号码中的出生日期整理", "id_derived",
            ))
        except ValueError:
            pass
        if number[16].isdigit():
            results.append(candidate(
                "personal.sex", "MALE" if int(number[16]) % 2 else "FEMALE",
                0.95 if checksum_valid else 0.78,
                "根据身份证号码校验位前一位整理", "id_derived",
            ))
        results.append(candidate("personal.nationality", "CHINA", 0.97, evidence, "id_document"))

    native_name = identity_card_name(text)
    if native_name:
        results.append(candidate(
            "personal.nativeName", native_name[0], 0.94, native_name[1], "label"
        ))

    birth_date = labelled_date(text, [r"出生(?:日期)?", r"Date\s*of\s*Birth"])
    if birth_date and not any(item["id"] == "personal.dateOfBirth" for item in results):
        results.append(candidate("personal.dateOfBirth", birth_date[0], 0.9, birth_date[1], "label"))

    sex = labelled_value(text, [r"性别", r"Sex"], r"男|女|M|F|MALE|FEMALE")
    if sex:
        # Prefer the value printed on the card while retaining the ID-number
        # derivation as independent supporting evidence.
        results.append(candidate("personal.sex", normalize_sex(sex[0]), 0.96, sex[1], "label"))

    address = identity_card_address(text)
    if address:
        structured = structure_address(address[0], "CHINA")
        for part, field_id in (
            ("line1", "contact.homeStreet1"),
            ("line2", "contact.homeStreet2"),
            ("city", "contact.homeCity"),
            ("region", "contact.homeRegion"),
            ("postalCode", "contact.homePostalCode"),
            ("country", "contact.homeCountry"),
        ):
            if structured.get(part):
                results.append(candidate(
                    field_id, structured[part], 0.88, address[1], "id_address"
                ))
    return results


def identity_card_ocr_lines(text):
    """Return compact OCR lines while discarding Markdown image artifacts."""
    lines = []
    for raw_line in str(text or "").splitlines():
        line = re.sub(r"^\s*#{1,6}\s*", "", raw_line).strip()
        if not line or re.fullmatch(r"<!--\s*image\s*-->", line, flags=re.IGNORECASE):
            continue
        line = re.sub(r"\s+", "", line)
        if line:
            lines.append(line)
    return lines


def identity_card_field_line(value):
    return bool(re.search(
        r"(?:姓名|性别|民族|出生|住址|家庭地址|"
        r"公民身份号码|身份证?号码|签发机关|有效期限|"
        r"DateofBirth|NationalID|IdentityCard)",
        str(value or ""),
        flags=re.IGNORECASE,
    ))


def plausible_identity_card_name(value):
    normalized = re.sub(r"\s+", "", str(value or "")).strip("/:|｜-")
    if not re.fullmatch(r"[\u3400-\u9fff·]{2,8}", normalized):
        return False
    forbidden = (
        "中华人民共和国", "居民身份证", "公民身份", "性别", "民族",
        "出生", "住址", "签发机关", "有效期限",
    )
    return not any(marker in normalized for marker in forbidden)


def identity_card_name(text):
    """Read a PRC ID name even when Docling returns the rows bottom-to-top."""
    lines = identity_card_ocr_lines(text)
    for index, line in enumerate(lines):
        label = re.search(r"姓名(?:/Name)?", line, flags=re.IGNORECASE)
        if not label:
            continue
        same_line = line[label.end():].strip("/:|｜-")
        if plausible_identity_card_name(same_line):
            return same_line, line
        for distance in (1, 2):
            for direction in (1, -1):
                candidate_index = index + distance * direction
                if not 0 <= candidate_index < len(lines):
                    continue
                candidate_value = lines[candidate_index]
                if identity_card_field_line(candidate_value):
                    continue
                if plausible_identity_card_name(candidate_value):
                    return candidate_value, f"{line}\n{candidate_value}"
    return None


def identity_card_address(text):
    """Reassemble a multiline PRC ID address in either OCR row direction."""
    lines = identity_card_ocr_lines(text)
    candidates = []
    for index, line in enumerate(lines):
        label = re.search(r"(?:住址|家庭地址)(?:/Address)?", line, flags=re.IGNORECASE)
        if not label:
            continue
        base = line[label.end():].strip("/:|｜-")
        for direction in (1, -1):
            fragments = []
            evidence = [line]
            for distance in range(1, 6):
                fragment_index = index + distance * direction
                if not 0 <= fragment_index < len(lines):
                    break
                fragment = lines[fragment_index]
                if identity_card_field_line(fragment):
                    break
                if not re.search(r"[\u3400-\u9fff0-9]", fragment):
                    break
                fragments.append(fragment.strip("/:|｜-"))
                evidence.append(fragment)
            value = f"{base}{''.join(fragments)}"
            if len(value) < 6:
                continue
            marker_score = sum(bool(re.search(pattern, value)) for pattern in (
                r"省|自治区|特别行政区", r"市|州|地区", r"区|县|旗",
                r"街道|镇|乡", r"大道|大街|公路|路|街|巷|弄",
                r"幢|栋|号楼|室|单元",
            ))
            score = marker_score * 20 + min(len(value), 80)
            candidates.append((score, value, "\n".join(evidence)))
    if not candidates:
        return None
    _, value, evidence = max(candidates, key=lambda item: item[0])
    return value, evidence


def prc_id_checksum_valid(number):
    normalized = str(number or "").strip().upper()
    if not re.fullmatch(r"\d{17}[0-9X]", normalized):
        return False
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    check_map = "10X98765432"
    expected = check_map[sum(int(value) * weight for value, weight in zip(normalized[:17], weights)) % 11]
    return normalized[-1] == expected


def contains_chinese(value):
    return bool(re.search(r"[\u3400-\u9fff]", str(value or "")))


def plausible_passport_name(value):
    normalized = clean_name(value)
    compact = re.sub(r"[^A-Z]", "", normalized)
    if len(compact) < 2 or not re.fullmatch(r"[A-Z][A-Z '-]{1,80}", normalized):
        return False
    forbidden = (
        "CHINA", "CHINESE", "NATIONAL", "NADONAL", "NATIONALITY", "GUOJI",
        "AUTHORITY", "PASSPORT", "COUNTRYCODE", "CASEOFNEED", "REPUBLIC",
        "DATEOF", "PLACEOF", "SURNAME", "GIVENNAME",
    )
    return not any(marker in compact for marker in forbidden)


def passport_visible_dates(text):
    output = []
    seen = set()
    pattern = re.compile(
        r"(?<!\d)(\d{1,2})\s+(?:\d{1,2}\s*月\s*/?\s*)?([A-Z0-9]{3,9})\s+((?:19|20)\d{2})(?!\d)",
        flags=re.IGNORECASE,
    )
    for match in pattern.finditer(text):
        month = match.group(2).upper().replace("0", "O")
        normalized = normalize_date(f"{match.group(1)} {month} {match.group(3)}")
        if normalized and normalized not in seen:
            output.append((normalized, line_for_match(text, match)))
            seen.add(normalized)
    return output


def bilingual_value_before_label(text, label_pattern):
    lines = text.splitlines()
    for index, line in enumerate(lines):
        label_match = re.search(label_pattern, line, flags=re.IGNORECASE)
        if not label_match:
            continue
        same_line = line[:label_match.start()]
        value_match = re.fullmatch(
            r"\s*[\u3400-\u9fff]{1,20}\s*/\s*([A-Z][A-Z .'-]{1,40})\s*",
            same_line,
            flags=re.IGNORECASE,
        )
        if value_match:
            return value_match.group(1).strip(), line.strip()
        for previous in range(index - 1, max(-1, index - 3), -1):
            value_match = re.fullmatch(
                r"\s*[\u3400-\u9fff]{1,20}\s*/\s*([A-Z][A-Z .'-]{1,40})\s*",
                lines[previous],
                flags=re.IGNORECASE,
            )
            if value_match:
                return value_match.group(1).strip(), lines[previous].strip()
    return None


def native_name_matching_mrz(text, surname, given_names):
    target = re.sub(r"[^A-Z]", "", f"{surname or ''}{given_names or ''}".upper())
    if not target:
        return ""
    excluded = {
        "中华人民共和国", "国家移民管理局", "持照人签名", "出生地点", "签发地点",
        "签发机关", "护照号码", "有效期至",
    }
    for value in re.findall(r"[\u3400-\u9fff·]{2,8}", text):
        if value in excluded:
            continue
        romanized = re.sub(r"[^A-Z]", "", compact_romanize(value).upper())
        if romanized == target:
            return value
    return ""


def native_name_matches_mrz(value, surname, given_names):
    target = re.sub(r"[^A-Z]", "", f"{surname or ''}{given_names or ''}".upper())
    romanized = re.sub(r"[^A-Z]", "", compact_romanize(value).upper())
    return bool(target and romanized == target)


def parse_td3_mrz(text):
    candidates = []

    def add_candidate(value):
        candidate_value = re.sub(r"[^A-Z0-9<]", "", value.upper())
        if candidate_value.startswith(("P0", "PO")) and len(candidate_value) >= 5:
            candidate_value = f"P<{candidate_value[2:]}"
        elif candidate_value.startswith("PCHN"):
            candidate_value = f"P<{candidate_value[1:]}"
        if len(candidate_value) >= 38 and candidate_value.startswith("P<"):
            candidates.append(candidate_value[:44].ljust(44, "<"))
        elif 38 <= len(candidate_value) <= 48:
            candidates.append(candidate_value[:44].ljust(44, "<"))

    for raw_line in text.splitlines():
        prepared = raw_line.upper().translate(str.maketrans({
            "《": "<", "〈": "<", "＜": "<", "‹": "<", "«": "<", "⟨": "<",
        }))
        tokens = re.findall(r"[A-Z0-9<]{38,48}", prepared)
        if tokens:
            for token in tokens:
                add_candidate(token)
        else:
            add_candidate(prepared)

    first = next((
        line for line in candidates
        if line.startswith("P<") and "<<" in line[5:]
    ), None)
    second = next((
        line for line in candidates
        if line is not first
        and re.fullmatch(r"[A-Z0-9<]{44}", line)
        and re.fullmatch(r"[A-Z<]{3}", line[10:13])
        and repair_mrz_digits(line[13:19]).isdigit()
        and repair_mrz_digits(line[21:27]).isdigit()
    ), None)
    if not first or not second:
        return None
    names = first[5:].split("<<", 1)
    surname = clean_name(names[0].replace("<", " "))
    given_names = clean_name((names[1] if len(names) > 1 else "").replace("<", " "))
    passport_number_raw = second[0:9]
    passport_number = passport_number_raw.replace("<", "").strip()
    date_of_birth_raw = repair_mrz_digits(second[13:19])
    expiration_date_raw = repair_mrz_digits(second[21:27])
    date_of_birth = mrz_date(date_of_birth_raw, expiry=False)
    expiration_date = mrz_date(expiration_date_raw, expiry=True)
    if not all([surname, given_names, passport_number, date_of_birth, expiration_date]):
        return None
    return {
        "surname": surname,
        "givenNames": given_names,
        "passportNumber": passport_number,
        "passportType": "REGULAR" if first[0:1] == "P" else first[0:1],
        "dateOfBirth": date_of_birth,
        "expirationDate": expiration_date,
        "nationality": normalize_nationality(second[10:13]),
        "sex": normalize_sex(second[20:21]),
        "checks": {
            "passport": mrz_check(passport_number_raw, repair_mrz_digits(second[9:10])),
            "birth": mrz_check(date_of_birth_raw, repair_mrz_digits(second[19:20])),
            "expiry": mrz_check(expiration_date_raw, repair_mrz_digits(second[27:28])),
        },
        "evidence": [first, second],
    }


def repair_mrz_digits(value):
    return str(value or "").upper().translate(str.maketrans({
        "O": "0", "Q": "0", "I": "1", "L": "1", "Z": "2",
        "S": "5", "G": "6", "B": "8",
    }))


def mrz_check(value, expected):
    if not expected.isdigit():
        return False
    weights = (7, 3, 1)

    def numeric(character):
        if character.isdigit():
            return int(character)
        if "A" <= character <= "Z":
            return ord(character) - ord("A") + 10
        return 0

    checksum = sum(numeric(character) * weights[index % 3] for index, character in enumerate(value))
    return checksum % 10 == int(expected)


def mrz_date(value, expiry=False):
    if not re.fullmatch(r"\d{6}", value):
        return ""
    year, month, day = int(value[:2]), int(value[2:4]), int(value[4:6])
    current_year = datetime.now(timezone.utc).year % 100
    full_year = (2000 + year) if expiry or year <= current_year else (1900 + year)
    try:
        return datetime(full_year, month, day).strftime("%Y-%m-%d")
    except ValueError:
        return ""


def map_student_document(text):
    results = []
    sevis = re.search(r"\bN\d{10}\b", text.upper())
    if sevis:
        results.append(candidate("education.sevisId", sevis.group(0), 0.96, line_for_match(text, sevis), "pattern"))

    school = labelled_line(text, [
        r"School\s*Name", r"Name\s*of\s*School", r"School\s*Official\s*Name",
        r"学校名称", r"院校名称",
    ])
    if school:
        results.append(candidate("education.schoolName", school[0], 0.84, school[1], "label"))

    address = labelled_line(text, [r"School\s*Address", r"学校地址", r"U\.S\.\s*Address"])
    if address:
        results.append(candidate("education.schoolAddress", address[0], 0.78, address[1], "label"))

    program = labelled_line(text, [
        r"Program\s*(?:Name|of\s*Study)", r"Major", r"Field\s*of\s*Study",
        r"项目名称", r"专业名称", r"专业",
    ])
    if program:
        results.append(candidate("education.programName", program[0], 0.78, program[1], "label"))

    program_number = labelled_value(text, [
        r"Program\s*(?:No\.?|Number)", r"Exchange\s*Visitor\s*Program\s*Number", r"项目编号",
    ], r"[A-Z]-\d{1,2}-\d{3,8}")
    if program_number:
        results.append(candidate("education.programNumber", program_number[0].upper(), 0.92, program_number[1], "label"))

    sponsor = labelled_line(text, [
        r"Program\s*Sponsor(?:'s)?\s*Name", r"Sponsor(?:'s)?\s*Name",
        r"Exchange\s*Visitor\s*Program\s*Sponsor", r"项目主办方", r"Sponsor\s*名称",
    ])
    if sponsor:
        results.append(candidate("education.sponsorName", sponsor[0], 0.82, sponsor[1], "label"))
    return results


def map_travel(text):
    results = []
    arrival = labelled_date(text, [
        r"Intended\s*(?:Date\s*of\s*)?Arrival", r"Arrival\s*Date", r"抵达(?:美国)?日期", r"预计抵达日期"
    ])
    if arrival:
        results.append(candidate("travel.arrivalDate", arrival[0], 0.82, arrival[1], "label"))

    departure = labelled_date(text, [
        r"(?:Intended\s*)?(?:Date\s*of\s*)?Departure", r"Departure\s*Date",
        r"离开美国日期", r"预计离境日期",
    ])
    if departure:
        results.append(candidate("travel.departureDate", departure[0], 0.82, departure[1], "label"))

    if not arrival or not departure:
        date_range = re.search(
            r"(\d{1,2}\s+[A-Za-z]{3,9}\s+(?:19|20)\d{2})\s*(?:-|–|—|to|至)\s*"
            r"(\d{1,2}\s+[A-Za-z]{3,9}\s+(?:19|20)\d{2})",
            text,
            flags=re.IGNORECASE,
        )
        if date_range:
            if not arrival:
                results.append(candidate(
                    "travel.arrivalDate", normalize_date(date_range.group(1)), 0.74,
                    line_for_match(text, date_range), "date_range",
                ))
            if not departure:
                results.append(candidate(
                    "travel.departureDate", normalize_date(date_range.group(2)), 0.74,
                    line_for_match(text, date_range), "date_range",
                ))

    travel_dates = {
        item["id"]: item for item in results
        if item["id"] in {"travel.arrivalDate", "travel.departureDate"}
    }
    if {"travel.arrivalDate", "travel.departureDate"}.issubset(travel_dates):
        try:
            arrival_value = datetime.fromisoformat(travel_dates["travel.arrivalDate"]["value"])
            departure_value = datetime.fromisoformat(travel_dates["travel.departureDate"]["value"])
            day_count = (departure_value - arrival_value).days
            if day_count > 0:
                results.append(candidate(
                    "travel.stayDuration",
                    f"{day_count} DAYS",
                    min(
                        travel_dates["travel.arrivalDate"]["confidence"],
                        travel_dates["travel.departureDate"]["confidence"],
                    ),
                    "根据行程单抵达和离境日期自动计算",
                    "derived_date_range",
                ))
        except ValueError:
            pass

    arrival_flight = labelled_value(text, [r"Arrival\s*Flight", r"Inbound\s*Flight", r"抵达航班"], r"[A-Z0-9]{2,4}\s?\d{1,4}")
    if arrival_flight:
        results.append(candidate("travel.arrivalFlight", arrival_flight[0].upper(), 0.8, arrival_flight[1], "label"))
    departure_flight = labelled_value(text, [r"Departure\s*Flight", r"Outbound\s*Flight", r"离境航班"], r"[A-Z0-9]{2,4}\s?\d{1,4}")
    if departure_flight:
        results.append(candidate("travel.departureFlight", departure_flight[0].upper(), 0.8, departure_flight[1], "label"))
    if not arrival_flight or not departure_flight:
        flights = list(re.finditer(
            r"\b(?!(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s?(?:19|20)\d{2}\b)"
            r"(?:[A-Z]{2,3}|[A-Z]\d|\d[A-Z])\s?\d{2,4}\b",
            text.upper(),
        ))
        if flights:
            if not arrival_flight:
                results.append(candidate(
                    "travel.arrivalFlight", flights[0].group(0).replace(" ", ""), 0.62,
                    line_for_match(text, flights[0]), "itinerary_pattern",
                ))
            if not departure_flight and len(flights) > 1:
                results.append(candidate(
                    "travel.departureFlight", flights[-1].group(0).replace(" ", ""), 0.62,
                    line_for_match(text, flights[-1]), "itinerary_pattern",
                ))

    arrival_city = labelled_line(text, [r"Arrival\s*(?:City|Airport)", r"Port\s*of\s*Arrival", r"抵达城市", r"入境城市"])
    if arrival_city:
        results.append(candidate("travel.arrivalCity", arrival_city[0], 0.76, arrival_city[1], "label"))
    departure_city = labelled_line(text, [r"Departure\s*(?:City|Airport)", r"Port\s*of\s*Departure", r"离开城市", r"离境城市"])
    if departure_city:
        results.append(candidate("travel.departureCity", departure_city[0], 0.76, departure_city[1], "label"))

    locations = labelled_line(text, [
        r"Locations?\s*(?:to\s*be\s*)?Visited", r"Places?\s*to\s*Visit",
        r"Destination(?:s)?", r"计划访问地点", r"目的地",
    ], max_length=220)
    if locations:
        results.append(candidate("travel.locations", locations[0], 0.7, locations[1], "label"))
    results.extend(map_us_address(text))
    return results


def map_us_address(text):
    address = labelled_line(text, [
        r"U\.S\.\s*(?:Street\s*)?Address", r"Address\s*in\s*the\s*U\.S\.",
        r"Hotel\s*Address", r"住宿地址", r"酒店地址", r"在美(?:停留)?地址",
    ], max_length=180)
    if not address:
        match = re.search(
            r"\b\d{1,6}\s+[A-Za-z0-9 .'-]{2,70},\s*[A-Za-z .'-]{2,40},\s*"
            r"[A-Z]{2}\s+\d{5}(?:-\d{4})?\b",
            text,
        )
        if match:
            address = (match.group(0).strip(), line_for_match(text, match))
    if not address:
        return []
    return [candidate("contact.usAddress", address[0], 0.76, address[1], "label")]


def map_us_contact(text):
    results = []
    organization = labelled_line(text, [
        r"U\.S\.\s*(?:Contact\s*)?(?:Organization|Company|School)",
        r"Inviting\s*(?:Organization|Company|School)", r"邀请单位", r"美国联系人机构",
    ])
    if organization:
        results.append(candidate("contact.organizationName", organization[0], 0.74, organization[1], "label"))

    contact_name = labelled_line(text, [
        r"U\.S\.\s*Contact\s*(?:Person|Name)", r"Contact\s*Person", r"联系人姓名", r"美国联系人",
    ])
    if contact_name:
        parts = clean_name(contact_name[0]).split()
        if len(parts) >= 2:
            results.append(candidate("contact.surname", parts[-1], 0.66, contact_name[1], "name_split"))
            results.append(candidate("contact.givenNames", " ".join(parts[:-1]), 0.66, contact_name[1], "name_split"))

    phone = labelled_value(text, [r"(?:U\.S\.\s*)?Contact\s*Phone", r"Telephone", r"联系电话", r"电话"], r"\+?[0-9() .-]{7,24}")
    if not phone:
        generic_phone = re.search(r"\+1\s*[0-9() .-]{7,24}", text)
        if generic_phone:
            phone = (generic_phone.group(0), line_for_match(text, generic_phone))
    if phone:
        results.append(candidate("contact.phone", normalize_phone(phone[0]), 0.78, phone[1], "label"))
    email = labelled_value(text, [r"(?:U\.S\.\s*)?Contact\s*E-?mail", r"E-?mail", r"邮箱"], r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}")
    if not email:
        generic_email = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text, flags=re.IGNORECASE)
        if generic_email:
            email = (generic_email.group(0), line_for_match(text, generic_email))
    if email:
        results.append(candidate("contact.email", email[0].lower(), 0.82, email[1], "label"))
    return results


def map_employment(text):
    results = []
    employer = labelled_line(text, [
        r"Employer(?:'s)?\s*Name", r"Company\s*Name", r"Name\s*of\s*Employer",
        r"雇主名称", r"公司名称", r"单位名称",
    ])
    if employer:
        results.append(candidate("work.employerName", employer[0], 0.8, employer[1], "label"))
    address = labelled_line(text, [
        r"Employer(?:'s)?\s*Address", r"Company\s*Address", r"Work\s*Address",
        r"雇主地址", r"公司地址", r"单位地址",
    ], max_length=180)
    if address:
        results.append(candidate("work.employerAddress", address[0], 0.76, address[1], "label"))

    phone = labelled_value(text, [
        r"Employer(?:'s)?\s*(?:Phone|Telephone)", r"Company\s*(?:Phone|Telephone)",
        r"单位电话", r"公司电话",
    ], r"\+?[0-9() .-]{7,24}")
    if phone:
        results.append(candidate("work.employerPhone", normalize_phone(phone[0]), 0.78, phone[1], "label"))

    start_date = labelled_date(text, [
        r"(?:Employment|Start|Hire)\s*Date", r"Date\s*Employed\s*From",
        r"入职日期", r"参加工作日期",
    ])
    if start_date:
        results.append(candidate("work.startDate", start_date[0], 0.8, start_date[1], "label"))

    title = labelled_line(text, [
        r"Job\s*Title", r"Position", r"Occupation", r"职位", r"职务",
    ])
    if title:
        results.append(candidate("work.title", title[0], 0.78, title[1], "label"))

    monthly_income = labelled_value(text, [
        r"Monthly\s*(?:Income|Salary)", r"月收入", r"月薪",
    ], r"(?:[A-Z]{3}|[$¥￥])?\s?[0-9][0-9,.]{1,18}")
    if monthly_income:
        results.append(candidate("work.monthlyIncome", monthly_income[0], 0.72, monthly_income[1], "label"))

    duties = labelled_line(text, [r"Duties", r"Job\s*Description", r"Responsibilities", r"工作职责", r"主要职责"], max_length=260)
    if duties:
        results.append(candidate("work.duties", duties[0], 0.68, duties[1], "label"))
    return results


def map_previous_visa(text):
    results = []
    issue_date = labelled_date(text, [r"Issue\s*Date", r"Date\s*of\s*Issue", r"签发日期"])
    if issue_date:
        results.append(candidate("history.previousVisaIssueDate", issue_date[0], 0.86, issue_date[1], "label"))

    visa_number = labelled_value(text, [
        r"Visa\s*(?:No\.?|Number)", r"签证号码", r"签证号",
    ], r"[A-Z0-9]{6,16}")
    if visa_number:
        results.append(candidate("history.previousVisaNumber", visa_number[0].upper(), 0.82, visa_number[1], "label"))

    visa_class = labelled_value(text, [r"Visa\s*Class", r"Class", r"签证类别"], r"[A-Z][A-Z0-9/-]{0,12}")
    if visa_class:
        results.append(candidate("history.previousVisaClass", visa_class[0].upper(), 0.82, visa_class[1], "label"))
    return results


def labelled_value(text, labels, value_pattern):
    for label in labels:
        match = re.search(
            rf"(?:{label})\s*(?:[:：#|｜]\s*)?({value_pattern})",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            return match.group(1).strip(), line_for_match(text, match)
    return None


def labelled_compact_value(text, labels, value_pattern, remove_pattern=r"\s+"):
    for label in labels:
        for match in re.finditer(
            rf"(?:{label})\s*(?:[:：#|｜]\s*)?({value_pattern})",
            text,
            flags=re.IGNORECASE,
        ):
            normalized = re.sub(remove_pattern, "", match.group(1).upper())
            if 6 <= len(normalized) <= 14 and re.fullmatch(r"[A-Z0-9]+", normalized):
                return normalized, line_for_match(text, match)
    return None


def labelled_line(text, labels, max_length=120):
    for label in labels:
        match = re.search(
            rf"(?:{label})\s*(?:[:：#|｜]\s*)?([^\n]{{2,{max_length}}})",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            value = re.split(r"\s{3,}|\|", match.group(1).strip())[0].strip(" :-")
            value = re.sub(r"^/?\s*[A-Za-z][A-Za-z .'-]{1,48}\s*[:：]\s*", "", value)
            if value:
                return value, line_for_match(text, match)
    return None


def labelled_multiline(text, labels, stop_labels, max_length=240):
    for label in labels:
        for match in re.finditer(label, text, flags=re.IGNORECASE):
            nearby = text[match.end():match.end() + max_length + 160]
            nearby = re.sub(r"^\s*(?:/\s*)?(?:Address|住址)\s*[:：]?\s*", "", nearby, flags=re.IGNORECASE)
            stop_at = len(nearby)
            for stop_label in stop_labels:
                stop = re.search(rf"(?:^|\n)\s*(?:{stop_label})", nearby, flags=re.IGNORECASE)
                if stop:
                    stop_at = min(stop_at, stop.start())
            value = re.sub(r"\s*\n\s*", "", nearby[:stop_at]).strip(" :：|-\n")[:max_length]
            if len(value) >= 2:
                evidence = text[match.start():match.end() + stop_at].strip()[:500]
                return value, evidence
    return None


def labelled_document_line(text, labels, stop_labels, max_length=160):
    """Read a bilingual document value without returning a neighboring label."""
    for label in labels:
        for match in re.finditer(label, text, flags=re.IGNORECASE):
            line_end = text.find("\n", match.end())
            if line_end < 0:
                line_end = len(text)
            nearby = text[match.end():min(line_end, match.end() + max_length)]
            nearby = nearby.lstrip(" /:：|｜-")
            stop_at = len(nearby)
            for stop_label in stop_labels:
                stop = re.search(stop_label, nearby, flags=re.IGNORECASE)
                if stop:
                    stop_at = min(stop_at, stop.start())
            value = nearby[:stop_at].strip(" /:：|｜-")
            if not value:
                continue
            # Chinese passports often print one place as 浙江/ZHEJIANG. Prefer
            # the official Latin rendering that is already present on the page.
            components = [item.strip() for item in value.split("/") if item.strip()]
            latin = next((
                item for item in components
                if re.search(r"[A-Za-z]", item)
                and not re.fullmatch(
                    r"(?:PLACE\s+OF\s+(?:ISSUE|BIRTH)|ISSUING\s+(?:PLACE|AUTHORITY))",
                    item,
                    flags=re.IGNORECASE,
                )
            ), "")
            cleaned = latin or value
            cleaned = re.split(r"\s{3,}|\|", cleaned)[0].strip(" /:：|｜-")
            if re.fullmatch(
                r"(?:PLACE\s+OF\s+(?:ISSUE|BIRTH)|ISSUING\s+(?:PLACE|AUTHORITY))",
                cleaned,
                flags=re.IGNORECASE,
            ):
                continue
            if cleaned:
                return cleaned, line_for_match(text, match)
    return None


def labelled_date(text, labels):
    date_pattern = (
        r"(?:19|20)\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?"
        r"|\d{1,2}[-/.]\d{1,2}[-/.](?:19|20)\d{2}"
        r"|\d{1,2}\s*[A-Za-z]{3,9}\s*(?:19|20)\d{2}"
        r"|(?:19|20)\d{2}\s*[A-Za-z]{3,9}\s*\d{1,2}"
    )
    found = labelled_value(text, labels, date_pattern)
    if found:
        normalized = normalize_date(found[0])
        if normalized:
            return normalized, found[1]

    # OCR and table parsers often leave bilingual labels or confidence marks
    # between the label and value. Search the same line and one following line
    # without allowing a distant, unrelated date to be captured.
    for label in labels:
        for label_match in re.finditer(label, text, flags=re.IGNORECASE):
            line_end = text.find("\n", label_match.end())
            if line_end < 0:
                line_end = len(text)
            next_line_end = text.find("\n", line_end + 1)
            if next_line_end < 0:
                next_line_end = len(text)
            search_end = min(next_line_end, label_match.end() + 180)
            nearby = text[label_match.end():search_end]
            date_match = re.search(date_pattern, nearby, flags=re.IGNORECASE)
            if not date_match:
                continue
            normalized = normalize_date(date_match.group(0))
            if normalized:
                absolute_end = label_match.end() + date_match.end()
                evidence_match = re.match(r".*", text[label_match.start():absolute_end])
                evidence = evidence_match.group(0).strip() if evidence_match else date_match.group(0)
                return normalized, evidence[:500]
    return None


def normalize_date(value):
    value = value.strip().replace("年", "-").replace("月", "-").replace("日", "")
    value = re.sub(r"^(\d{1,2})\s*([A-Za-z]{3,9})\s*((?:19|20)\d{2})$", r"\1 \2 \3", value)
    value = re.sub(r"^((?:19|20)\d{2})\s*([A-Za-z]{3,9})\s*(\d{1,2})$", r"\1 \2 \3", value)
    for fmt in (
        "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%m/%d/%Y", "%d/%m/%Y",
        "%d %b %Y", "%d %B %Y", "%Y %b %d", "%Y %B %d",
    ):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def candidate(field_id, value, confidence, evidence, method):
    return {
        "id": field_id,
        "value": str(value).strip(),
        "confidence": confidence,
        "evidence": str(evidence).strip()[:500],
        "method": method,
        "page": 1,
    }


def line_for_match(text, match):
    start = text.rfind("\n", 0, match.start()) + 1
    end = text.find("\n", match.end())
    if end < 0:
        end = len(text)
    return text[start:end].strip()[:500]


def clean_name(value):
    return re.sub(r"\s+", " ", value.replace("<", " ")).strip(" -'").upper()


def normalize_sex(value):
    normalized = str(value or "").strip().upper()
    if normalized in {"M", "MALE", "男"}:
        return "MALE"
    if normalized in {"F", "FEMALE", "女"}:
        return "FEMALE"
    return ""


def normalize_nationality(value):
    code = str(value or "").replace("<", "").strip().upper()
    return canonicalize_ds160_value(
        "personal.nationality", MRZ_COUNTRY_NAMES.get(code, code)
    )


def normalize_phone(value):
    normalized = re.sub(r"[^0-9+]", "", str(value or "").strip())
    if normalized.count("+") > 1 or ("+" in normalized and not normalized.startswith("+")):
        normalized = normalized.replace("+", "")
    return normalized


def deduplicate(fields):
    best = {}
    for field in fields:
        current = best.get(field["id"])
        if not current or field["confidence"] > current["confidence"]:
            best[field["id"]] = field
    return list(best.values())


def merge_extracted_fields(existing_fields, extracted_fields, visa_type=""):
    existing = {field.get("id"): dict(field) for field in existing_fields or []}
    issues = []
    conflicted_field_ids = set()
    grouped = {}
    for field in extracted_fields:
        grouped.setdefault(field["id"], []).append(field)

    for field_id, candidates in grouped.items():
        candidates.sort(key=lambda item: item.get("confidence", 0), reverse=True)
        winner = candidates[0]
        distinct_values = {normalize_compare(item.get("value")) for item in candidates if item.get("value")}
        if len(distinct_values) > 1:
            conflicted_field_ids.add(field_id)
            source_summary = "；".join(
                f"{item.get('sourceDocument') or '材料'}：{item.get('value')}"
                for item in candidates[:3]
            )
            issues.append({
                "id": f"ocr.conflict.{field_id}",
                "type": "conflict",
                "severity": "medium",
                "category": "跨材料冲突",
                "message": f"“{winner['label']}”在不同材料中不一致（{source_summary}），请核对原件后确认。",
                "requiresUserResolution": True,
                "resolved": False,
            })
        previous = existing.get(field_id, {})
        previous_value = str(previous.get("value") or "").strip()
        if previous_value and normalize_compare(previous_value) != normalize_compare(winner.get("value")):
            conflicted_field_ids.add(field_id)
            issues.append({
                "id": f"ocr.conflict.manual.{field_id}",
                "type": "conflict",
                "severity": "high" if winner.get("riskLevel") == "high" else "medium",
                "category": "跨材料冲突",
                "message": (
                    f"客户档案中的“{winner['label']}”为“{previous_value}”，"
                    f"但 {winner.get('sourceDocument') or '上传材料'} 识别为“{winner.get('value')}”。请人工确认。"
                ),
                "requiresUserResolution": True,
                "resolved": False,
            })
        if previous.get("editedByUser") or previous.get("confirmed"):
            continue
        existing[field_id] = {**previous, **winner}

    if "travel.visaType" in existing:
        existing["travel.visaType"]["value"] = visa_type
        existing["travel.visaType"]["sourceDocument"] = "客户档案"
        existing["travel.visaType"]["confidence"] = 1

    for field in existing.values():
        confidence = field.get("confidence")
        if field.get("value") and isinstance(confidence, (int, float)) and 0 < confidence < 0.8:
            issues.append({
                "id": f"ocr.low.{field.get('id')}",
                "type": "low-confidence",
                "severity": "medium" if field.get("riskLevel") == "high" else "low",
                "category": "低置信度信息",
                "message": f"“{field.get('label')}”的材料识别置信度为 {round(confidence * 100)}%，请对照原件核查。",
                "requiresUserResolution": False,
                "resolved": False,
            })

        if field.get("confirmed") or field.get("editedByUser"):
            field["autoVerified"] = False
            field["reviewReason"] = "顾问已确认" if field.get("confirmed") else "顾问已编辑"
            continue
        if not str(field.get("value") or "").strip():
            field["autoVerified"] = False
            field["reviewReason"] = "尚未识别到内容"
            continue
        threshold = {"high": 0.94, "medium": 0.86, "low": 0.80}.get(
            field.get("riskLevel"), 0.86
        )
        is_automatic = (
            field.get("id") not in CRITICAL_REVIEW_FIELD_IDS
            and field.get("id") not in conflicted_field_ids
            and isinstance(confidence, (int, float))
            and confidence >= threshold
        )
        field["autoVerified"] = is_automatic
        field["requiresUserConfirmation"] = not is_automatic
        if is_automatic:
            field["reviewReason"] = "来源清晰且通过格式与一致性校验"
        elif field.get("id") in conflicted_field_ids:
            field["reviewReason"] = "不同来源存在冲突"
        elif field.get("id") in CRITICAL_REVIEW_FIELD_IDS:
            field["reviewReason"] = "关键身份或签证字段"
        else:
            field["reviewReason"] = "识别置信度未达到自动校验阈值"

    required_ids = {
        "personal.surname", "personal.givenNames", "personal.dateOfBirth",
        "passport.number", "passport.expiration", "travel.visaType",
    }
    normalized_visa = str(visa_type or "").strip().upper()
    if normalized_visa.startswith(("F", "M")):
        required_ids.update({"education.schoolName", "education.sevisId"})
    if normalized_visa.startswith("J"):
        required_ids.update({"education.sevisId", "education.programNumber", "education.sponsorName"})
    for field_id in sorted(required_ids):
        field = existing.get(field_id)
        if field and field.get("value"):
            continue
        label = (field or {}).get("label") or FIELD_META.get(field_id, (field_id, "", ""))[0]
        issues.append({
            "id": f"ocr.missing.{field_id}",
            "type": "missing",
            "severity": "medium",
            "category": "缺失信息",
            "message": f"上传材料中尚未识别到“{label}”，请补充材料或由文案老师手动录入。",
            "requiresUserResolution": False,
            "resolved": False,
        })

    unique_issues = {item["id"]: item for item in issues}
    return list(existing.values()), list(unique_issues.values())


def normalize_compare(value):
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())
