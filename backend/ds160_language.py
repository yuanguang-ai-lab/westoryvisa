#!/usr/bin/env python3
"""DS-160 text normalization, local translation and consultant-note parsing."""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import threading
import time
import unicodedata
from difflib import SequenceMatcher
from urllib import error as url_error
from urllib import request as url_request


DOES_NOT_APPLY = "DOES NOT APPLY"
LANGUAGE_SCHEMA_VERSION = "ceac-english-v5-postal-addresses"
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_TRANSLATION_CACHE = {}
_OLLAMA_LOCK = threading.Lock()
_OLLAMA_DISABLED_UNTIL = 0.0
_LIBRETRANSLATE_LOCK = threading.Lock()
_LIBRETRANSLATE_DISABLED_UNTIL = 0.0
_LIBRETRANSLATE_LAST_SUCCESS = 0.0

_EXACT_TRANSLATIONS = {
    "中国": "CHINA",
    "中华人民共和国": "CHINA",
    "美国": "UNITED STATES OF AMERICA",
    "男": "MALE",
    "女": "FEMALE",
    "学生": "STUDENT",
    "无": DOES_NOT_APPLY,
    "不适用": DOES_NOT_APPLY,
    "没有": DOES_NOT_APPLY,
    "旅游": "TOURISM",
    "商务": "BUSINESS",
    "探亲": "VISITING RELATIVES",
    "访友": "VISITING FRIENDS",
    "旅游和探亲": "TOURISM AND VISITING RELATIVES",
    "参加商务会议": "ATTEND BUSINESS MEETINGS",
    "参加学术会议": "ATTEND AN ACADEMIC CONFERENCE",
    "赴美学习": "STUDY IN THE UNITED STATES",
    "中文": "MANDARIN CHINESE",
    "汉语": "MANDARIN CHINESE",
    "普通话": "MANDARIN CHINESE",
    "粤语": "CANTONESE",
    "英语": "ENGLISH",
    "英文": "ENGLISH",
    "日语": "JAPANESE",
    "韩语": "KOREAN",
    "日本": "JAPAN",
    "韩国": "SOUTH KOREA",
    "英国": "UNITED KINGDOM",
    "法国": "FRANCE",
    "德国": "GERMANY",
    "加拿大": "CANADA",
    "澳大利亚": "AUSTRALIA",
    "新加坡": "SINGAPORE",
    "泰国": "THAILAND",
    "马来西亚": "MALAYSIA",
    "北京": "BEIJING",
    "北京市": "BEIJING",
    "上海": "SHANGHAI",
    "上海市": "SHANGHAI",
    "天津": "TIANJIN",
    "天津市": "TIANJIN",
    "重庆": "CHONGQING",
    "重庆市": "CHONGQING",
    "河北": "HEBEI",
    "河北省": "HEBEI",
    "山西": "SHANXI",
    "山西省": "SHANXI",
    "辽宁": "LIAONING",
    "辽宁省": "LIAONING",
    "吉林": "JILIN",
    "吉林省": "JILIN",
    "黑龙江": "HEILONGJIANG",
    "黑龙江省": "HEILONGJIANG",
    "江苏": "JIANGSU",
    "江苏省": "JIANGSU",
    "浙江": "ZHEJIANG",
    "浙江省": "ZHEJIANG",
    "安徽": "ANHUI",
    "安徽省": "ANHUI",
    "福建": "FUJIAN",
    "福建省": "FUJIAN",
    "江西": "JIANGXI",
    "江西省": "JIANGXI",
    "山东": "SHANDONG",
    "山东省": "SHANDONG",
    "河南": "HENAN",
    "河南省": "HENAN",
    "湖北": "HUBEI",
    "湖北省": "HUBEI",
    "湖南": "HUNAN",
    "湖南省": "HUNAN",
    "广东": "GUANGDONG",
    "广东省": "GUANGDONG",
    "海南": "HAINAN",
    "海南省": "HAINAN",
    "四川": "SICHUAN",
    "四川省": "SICHUAN",
    "贵州": "GUIZHOU",
    "贵州省": "GUIZHOU",
    "云南": "YUNNAN",
    "云南省": "YUNNAN",
    "陕西": "SHAANXI",
    "陕西省": "SHAANXI",
    "甘肃": "GANSU",
    "甘肃省": "GANSU",
    "青海": "QINGHAI",
    "青海省": "QINGHAI",
    "内蒙古": "INNER MONGOLIA",
    "内蒙古自治区": "INNER MONGOLIA",
    "广西": "GUANGXI",
    "广西壮族自治区": "GUANGXI",
    "西藏": "TIBET",
    "西藏自治区": "TIBET",
    "宁夏": "NINGXIA",
    "宁夏回族自治区": "NINGXIA",
    "新疆": "XINJIANG",
    "新疆维吾尔自治区": "XINJIANG",
    "香港": "HONG KONG",
    "香港特别行政区": "HONG KONG",
    "澳门": "MACAU",
    "澳门特别行政区": "MACAU",
    "青岛": "QINGDAO",
    "青岛市": "QINGDAO",
    "营口": "YINGKOU",
    "营口市": "YINGKOU",
    "锦州": "JINZHOU",
    "锦州市": "JINZHOU",
    "老边": "LAOBIAN",
    "老边区": "LAOBIAN DISTRICT",
    "锦绣": "JINXIU",
    "昆明": "KUNMING",
    "深圳": "SHENZHEN",
    "深圳市": "SHENZHEN",
    "营口市第一高级中学": "YINGKOU SENIOR HIGH SCHOOL",
    "营口市高级中学": "YINGKOU SENIOR HIGH SCHOOL",
    "辽宁理工学院": "LIAONING INSTITUTE OF SCIENCE AND ENGINEERING",
}

_PRESERVE_NATIVE_FIELD_IDS = {"personal.nativeName"}
_INTERNAL_DISPLAY_FIELD_IDS = {"travel.visaType"}
_TRANSLITERATION_FIELD_PARTS = (
    "surname", "givennames", "birthcity", "birthregion", "placeofbirth",
    "issuecity", "issueregion", "arrivalcity", "departurecity",
    "homecity", "homeregion", "uscity",
)

_DS160_GLOSSARY = (
    ("计算机科学", " COMPUTER SCIENCE "),
    ("产品经理", " PRODUCT MANAGER "),
    ("有限责任公司", " COMPANY LIMITED "),
    ("股份有限公司", " COMPANY LIMITED "),
    ("信息技术", " INFORMATION TECHNOLOGY "),
    ("计算机", " COMPUTER "),
    ("科学", " SCIENCE "),
    ("国际贸易", " INTERNATIONAL TRADE "),
    ("客户服务", " CUSTOMER SERVICE "),
    ("档案管理", " RECORDS MANAGEMENT "),
    ("项目管理", " PROJECT MANAGEMENT "),
    ("市场营销", " MARKETING "),
    ("软件开发", " SOFTWARE DEVELOPMENT "),
    ("产品设计", " PRODUCT DESIGN "),
    ("产品", " PRODUCT "),
    ("数据分析", " DATA ANALYSIS "),
    ("财务管理", " FINANCIAL MANAGEMENT "),
    ("教育培训", " EDUCATION AND TRAINING "),
    ("有限公司", " COMPANY LIMITED "),
    ("中等专业学校", " SECONDARY VOCATIONAL SCHOOL "),
    ("职业技术学院", " VOCATIONAL AND TECHNICAL COLLEGE "),
    ("师范大学", " NORMAL UNIVERSITY "),
    ("理工大学", " UNIVERSITY OF TECHNOLOGY "),
    ("外国语大学", " UNIVERSITY OF FOREIGN LANGUAGES "),
    ("研究生院", " GRADUATE SCHOOL "),
    ("研究院", " RESEARCH INSTITUTE "),
    ("研究所", " RESEARCH INSTITUTE "),
    ("大学", " UNIVERSITY "),
    ("学院", " COLLEGE "),
    ("高中", " HIGH SCHOOL "),
    ("中学", " MIDDLE SCHOOL "),
    ("小学", " PRIMARY SCHOOL "),
    ("学校", " SCHOOL "),
    ("医院", " HOSPITAL "),
    ("银行", " BANK "),
    ("科技", " TECHNOLOGY "),
    ("海洋", " OCEAN "),
    ("航空", " AVIATION "),
    ("电子", " ELECTRONICS "),
    ("机械", " MECHANICAL "),
    ("工程", " ENGINEERING "),
    ("国际", " INTERNATIONAL "),
    ("贸易", " TRADE "),
    ("教育", " EDUCATION "),
    ("培训", " TRAINING "),
    ("负责", " RESPONSIBLE FOR "),
    ("学生", " STUDENT "),
    ("客户", " CUSTOMER "),
    ("档案", " RECORDS "),
    ("资料", " DOCUMENTS "),
    ("信息", " INFORMATION "),
    ("整理", " ORGANIZING "),
    ("核对", " VERIFYING "),
    ("申请", " APPLICATION "),
    ("管理", " MANAGEMENT "),
    ("销售", " SALES "),
    ("市场", " MARKETING "),
    ("运营", " OPERATIONS "),
    ("开发", " DEVELOPMENT "),
    ("设计", " DESIGN "),
    ("维护", " MAINTENANCE "),
    ("教学", " TEACHING "),
    ("研究", " RESEARCH "),
    ("财务", " FINANCE "),
    ("会计", " ACCOUNTING "),
    ("经理", " MANAGER "),
    ("工程师", " ENGINEER "),
    ("教师", " TEACHER "),
    ("顾问", " CONSULTANT "),
    ("助理", " ASSISTANT "),
    ("和", " AND "),
    ("中国", " CHINA "),
    ("青岛", " QINGDAO "),
    ("北京", " BEIJING "),
    ("上海", " SHANGHAI "),
)

_FIELD_LABELS = {
    "personal.surname": ("护照英文姓", "基础信息", "high"),
    "personal.givenNames": ("护照英文名", "基础信息", "high"),
    "personal.nativeName": ("完整母语姓名", "基础信息", "medium"),
    "personal.sex": ("性别", "基础信息", "high"),
    "personal.dateOfBirth": ("出生日期", "基础信息", "high"),
    "personal.birthCity": ("出生城市", "基础信息", "high"),
    "personal.birthRegion": ("出生省、州或地区", "基础信息", "medium"),
    "personal.birthCountry": ("出生国家或地区", "基础信息", "high"),
    "personal.nationality": ("当前国籍", "基础信息", "high"),
    "personal.nationalId": ("本国身份证号码", "基础信息", "high"),
    "passport.number": ("护照号码", "护照信息", "high"),
    "passport.issuingAuthority": ("护照签发国家或机构", "护照信息", "medium"),
    "passport.issueCity": ("护照签发城市", "护照信息", "medium"),
    "passport.issueRegion": ("护照签发省、州或地区", "护照信息", "medium"),
    "passport.issueCountry": ("护照签发国家或地区", "护照信息", "medium"),
    "passport.issueDate": ("护照签发日期", "护照信息", "high"),
    "passport.expiration": ("护照到期日期", "护照信息", "high"),
    "travel.purposeSummary": ("本次赴美真实目的", "旅行信息", "medium"),
    "travel.arrivalDate": ("预计抵达美国日期", "旅行信息", "medium"),
    "travel.stayDuration": ("预计停留时间", "旅行信息", "medium"),
    "contact.homeStreet1": ("家庭地址 · 街道地址 1", "地址 / 电话 / 社交媒体", "medium"),
    "contact.homeStreet2": ("家庭地址 · 街道地址 2", "地址 / 电话 / 社交媒体", "low"),
    "contact.homeCity": ("家庭地址 · 城市", "地址 / 电话 / 社交媒体", "medium"),
    "contact.homeRegion": ("家庭地址 · 省、州或地区", "地址 / 电话 / 社交媒体", "medium"),
    "contact.homePostalCode": ("家庭地址 · 邮编", "地址 / 电话 / 社交媒体", "low"),
    "contact.homeCountry": ("家庭地址 · 国家或地区", "地址 / 电话 / 社交媒体", "medium"),
    "contact.primaryPhone": ("主要电话号码", "地址 / 电话 / 社交媒体", "medium"),
    "contact.secondaryPhone": ("次要电话号码", "地址 / 电话 / 社交媒体", "low"),
    "contact.workPhone": ("工作电话号码", "地址 / 电话 / 社交媒体", "low"),
    "contact.email": ("当前常用 Email", "地址 / 电话 / 社交媒体", "medium"),
    "contact.usAddress": ("在美停留地址", "美国联系人", "medium"),
    "contact.usStreet1": ("在美停留地址 · 街道地址 1", "旅行信息", "medium"),
    "contact.usStreet2": ("在美停留地址 · 街道地址 2", "旅行信息", "low"),
    "contact.usCity": ("在美停留地址 · 城市", "旅行信息", "medium"),
    "contact.usState": ("在美停留地址 · 州", "旅行信息", "medium"),
    "contact.usPostalCode": ("在美停留地址 · ZIP Code", "旅行信息", "low"),
    "contact.surname": ("美国联系人姓", "美国联系人", "medium"),
    "contact.givenNames": ("美国联系人名", "美国联系人", "medium"),
    "contact.organizationName": ("美国联系人机构 / 学校", "美国联系人", "medium"),
    "contact.phone": ("美国联系人电话", "美国联系人", "medium"),
    "contact.usEmail": ("美国联系人邮箱", "美国联系人", "low"),
    "education.schoolName": ("学校名称", "工作 / 教育 / 培训", "medium"),
    "education.schoolAddress": ("学校地址", "工作 / 教育 / 培训", "medium"),
    "education.schoolPhone": ("学校电话", "工作 / 教育 / 培训", "low"),
    "education.programName": ("课程或专业名称", "SEVIS / 学生信息", "medium"),
    "education.sevisId": ("SEVIS ID", "SEVIS / 学生信息", "high"),
    "education.programNumber": ("DS-2019 Program Number", "SEVIS / 学生信息", "high"),
    "education.sponsorName": ("J-1 Sponsor 名称", "SEVIS / 学生信息", "medium"),
    "work.employerName": ("雇主名称", "工作 / 教育 / 培训", "medium"),
    "work.employerAddress": ("雇主地址", "工作 / 教育 / 培训", "medium"),
    "work.employerPhone": ("雇主电话", "工作 / 教育 / 培训", "low"),
    "work.startDate": ("入职或入学日期", "工作 / 教育 / 培训", "medium"),
    "work.title": ("职位或专业", "工作 / 教育 / 培训", "medium"),
    "work.monthlyIncome": ("月收入", "工作 / 教育 / 培训", "medium"),
    "work.duties": ("工作职责或学习内容", "工作 / 教育 / 培训", "medium"),
    "history.previousVisaNumber": ("最近美国签证号码", "以往赴美记录", "high"),
    "history.previousVisaIssueDate": ("最近美国签证签发日期", "以往赴美记录", "high"),
}

_CONSULTANT_FIELD_LABELS = (
    r"申请人姓名|客户姓名|中文姓名|母语姓名|护照英文姓名|护照英文姓|护照英文名|"
    r"英文姓|英文名|Surname|Given Names?|姓氏|"
    r"性别|出生日期|生日|出生城市|出生省份|出生省|出生国家|出生地|国籍|"
    r"身份证(?:号|号码)?|公民身份号码|护照(?:号|号码)?|护照签发日期|签发日期|"
    r"护照有效期|护照到期日期|有效期至|签发城市|签发省份|签发国家|签发地|"
    r"家庭住址|家庭地址|现住址|住址|在美(?:停留)?地址|美国地址|酒店地址|"
    r"主要电话|手机(?:号|号码)?|联系电话|次要电话|备用电话|工作电话|邮箱|Email|E-mail|"
    r"美国联系人姓|美国联系人名|美国联系人|美国联系机构|美国联系人电话|美国联系人邮箱|"
    r"学校名称|当前学校|学校地址|学校电话|专业名称|专业|课程|SEVIS(?:\s*ID)?|"
    r"Program Number|项目编号|Sponsor|项目赞助方|公司名称|公司|雇主名称|雇主|"
    r"公司地址|雇主地址|公司电话|雇主电话|职位|入职日期|月收入|工作职责|"
    r"预计抵达(?:美国)?日期|预计到达(?:美国)?日期|预计停留时间|赴美目的|旅行目的|"
    r"美国签证号码|旧签证号码|美国签证签发日期|邮编|邮政编码"
)


def clean_text(value, limit=8000):
    return re.sub(r"[\t\r ]+", " ", str(value or "")).strip()[:limit]


def normalize_does_not_apply(value):
    cleaned = clean_text(value)
    return DOES_NOT_APPLY if cleaned.upper() == "D" else cleaned


def contains_cjk(value):
    return bool(_CJK_RE.search(str(value or "")))


def normalize_ceac_text(value, limit=4000):
    """Return text accepted by CEAC's restricted Roman-character inputs."""
    cleaned = clean_text(value, limit)
    replacements = {
        "&": " AND ", "#": " NO. ", "，": ",", "。": ".", "？": "?",
        "！": ".", "：": ",", "；": ",", "、": ",", "（": " ", "）": " ",
        "“": "'", "”": "'", "‘": "'", "’": "'", "—": "-", "–": "-",
        "／": "-", "/": "-", "\n": " ", "\r": " ",
    }
    for source, target in replacements.items():
        cleaned = cleaned.replace(source, target)
    decomposed = unicodedata.normalize("NFKD", cleaned)
    cleaned = "".join(char for char in decomposed if not unicodedata.combining(char))
    cleaned = cleaned.upper()
    cleaned = re.sub(r"[^A-Z0-9$?.,'\- ]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.-")
    cleaned = re.sub(r"\s+([,.'?-])", r"\1", cleaned)
    return cleaned[:limit]


def _should_transliterate(field_id):
    normalized = str(field_id or "").replace("_", "").lower()
    return any(part in normalized for part in _TRANSLITERATION_FIELD_PARTS)


def _place_name_source(value, field_id):
    normalized_id = str(field_id or "").lower()
    if not any(part in normalized_id for part in ("city", "region", "province", "state")):
        return value
    return re.sub(
        r"(?:壮族自治区|回族自治区|维吾尔自治区|自治区|特别行政区|自治州|地区|省|市|盟)$",
        "",
        str(value or "").strip(),
    ) or value


def _romanize_macos(value):
    if platform.system() != "Darwin" or not contains_cjk(value):
        return ""
    source = json.dumps(str(value), ensure_ascii=False)
    script = f"""
ObjC.import('Foundation')
let value = $.NSMutableString.alloc.initWithString({source})
$.CFStringTransform(value, null, $.kCFStringTransformToLatin, false)
$.CFStringTransform(value, null, $.kCFStringTransformStripDiacritics, false)
ObjC.unwrap(value)
"""
    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-l", "JavaScript"],
            input=script,
            text=True,
            capture_output=True,
            timeout=4,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return re.sub(r"\s+", " ", result.stdout).strip().upper()


def romanize(value):
    cleaned = clean_text(value, 1000)
    if not contains_cjk(cleaned):
        return cleaned.upper()
    try:
        from pypinyin import lazy_pinyin  # Optional, pure-Python enhancement.

        return " ".join(lazy_pinyin(cleaned)).upper()
    except (ImportError, RuntimeError):
        return _romanize_macos(cleaned) or cleaned


def compact_romanize(value):
    """Romanize one proper-name component without pinyin syllable gaps."""
    return re.sub(r"\s+", "", romanize(value)).upper()


def _translation_provider():
    return os.environ.get("DS160_TRANSLATION_PROVIDER", "auto").strip().lower()


def _libretranslate_endpoint():
    return os.environ.get(
        "LIBRETRANSLATE_URL", "http://127.0.0.1:5000"
    ).strip().rstrip("/")


def _libretranslate_translation(value, _context=""):
    """Translate through a self-hosted LibreTranslate server.

    The browser never sees this endpoint or its optional API key. A short
    circuit breaker keeps an unavailable local service from slowing every
    field in a document batch.
    """
    global _LIBRETRANSLATE_DISABLED_UNTIL, _LIBRETRANSLATE_LAST_SUCCESS
    provider = _translation_provider()
    if provider not in {"auto", "libre", "libretranslate"}:
        return ""
    with _LIBRETRANSLATE_LOCK:
        if time.monotonic() < _LIBRETRANSLATE_DISABLED_UNTIL:
            return ""
    endpoint = _libretranslate_endpoint()
    if not endpoint:
        return ""
    payload = {
        "q": str(value),
        "source": "zh",
        "target": "en",
        "format": "text",
    }
    api_key = os.environ.get("LIBRETRANSLATE_API_KEY", "").strip()
    if api_key:
        payload["api_key"] = api_key
    try:
        req = url_request.Request(
            f"{endpoint}/translate",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        with url_request.urlopen(req, timeout=5) as response:
            body = json.loads(response.read().decode("utf-8"))
        translated = clean_text(
            body.get("translatedText") if isinstance(body, dict) else "", 8000
        )
        if not translated or contains_cjk(translated):
            raise ValueError("LibreTranslate returned an unusable translation")
        with _LIBRETRANSLATE_LOCK:
            _LIBRETRANSLATE_LAST_SUCCESS = time.monotonic()
            _LIBRETRANSLATE_DISABLED_UNTIL = 0.0
        return translated
    except (OSError, ValueError, KeyError, TypeError, url_error.URLError, TimeoutError):
        with _LIBRETRANSLATE_LOCK:
            _LIBRETRANSLATE_DISABLED_UNTIL = time.monotonic() + 120
            _LIBRETRANSLATE_LAST_SUCCESS = 0.0
        return ""


def translation_service_status():
    """Return non-secret translation availability for the local health API."""
    global _LIBRETRANSLATE_DISABLED_UNTIL, _LIBRETRANSLATE_LAST_SUCCESS
    provider = _translation_provider()
    result = {
        "provider": provider,
        "libreTranslate": False,
        "ollamaFallback": provider in {"auto", "ollama"},
    }
    if provider in {"off", "none", "disabled"}:
        return result
    if provider not in {"auto", "libre", "libretranslate"}:
        return result
    with _LIBRETRANSLATE_LOCK:
        if (
            _LIBRETRANSLATE_LAST_SUCCESS
            and time.monotonic() - _LIBRETRANSLATE_LAST_SUCCESS < 600
        ):
            result["libreTranslate"] = True
            return result
    try:
        req = url_request.Request(
            f"{_libretranslate_endpoint()}/languages",
            headers={"Accept": "application/json"},
            method="GET",
        )
        with url_request.urlopen(req, timeout=3.0) as response:
            languages = json.loads(response.read().decode("utf-8"))
        codes = {
            str(item.get("code") or "") for item in languages if isinstance(item, dict)
        }
        has_chinese = any(
            code.lower() == "zh"
            or code.lower().startswith("zh-")
            or code.lower().startswith("zh_")
            for code in codes
        )
        result["libreTranslate"] = "en" in codes and has_chinese
        if result["libreTranslate"]:
            with _LIBRETRANSLATE_LOCK:
                _LIBRETRANSLATE_LAST_SUCCESS = time.monotonic()
                _LIBRETRANSLATE_DISABLED_UNTIL = 0.0
    except (OSError, ValueError, TypeError, url_error.URLError, TimeoutError):
        pass
    return result


def _ollama_translation(value, context):
    global _OLLAMA_DISABLED_UNTIL
    provider = _translation_provider()
    if provider not in {"auto", "ollama"}:
        return ""
    with _OLLAMA_LOCK:
        if time.monotonic() < _OLLAMA_DISABLED_UNTIL:
            return ""
    endpoint = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    model = os.environ.get("OLLAMA_MODEL", "qwen2.5:3b").strip()
    prompt = (
        "Translate the following Chinese text into concise factual English suitable for a "
        "U.S. DS-160 form. Preserve names as Hanyu Pinyin, preserve all numbers, dates and "
        "identifiers, do not add facts, and return JSON only as {\"translation\":\"...\"}. "
        f"Field context: {context or 'DS-160 free text'}. Text: {value}"
    )
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0},
    }).encode("utf-8")
    try:
        req = url_request.Request(
            f"{endpoint}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with url_request.urlopen(req, timeout=8) as response:
            body = json.loads(response.read().decode("utf-8"))
        result = body.get("response") or ""
        decoded = json.loads(result) if isinstance(result, str) else result
        translated = clean_text(
            decoded.get("translation") if isinstance(decoded, dict) else "", 8000
        )
        return translated if translated and not contains_cjk(translated) else ""
    except (OSError, ValueError, KeyError, TypeError, url_error.URLError, TimeoutError):
        with _OLLAMA_LOCK:
            _OLLAMA_DISABLED_UNTIL = time.monotonic() + 300
        return ""


def _evidence_key(value):
    return re.sub(r"[\s,，;；:：。.!！?？()（）\[\]【】]+", "", str(value or "")).upper()


def _ollama_consultant_extraction(value, evidence_scope=""):
    """Ask the local model for evidence-backed fields missed by deterministic parsing."""
    global _OLLAMA_DISABLED_UNTIL
    provider = os.environ.get(
        "DS160_TEXT_ANALYSIS_PROVIDER",
        os.environ.get("DS160_TRANSLATION_PROVIDER", "auto"),
    ).strip().lower()
    if provider in {"off", "none", "disabled", "rules"}:
        return [], "rules_only"
    with _OLLAMA_LOCK:
        if time.monotonic() < _OLLAMA_DISABLED_UNTIL:
            return [], "ollama_unavailable"
    endpoint = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    model = os.environ.get("OLLAMA_MODEL", "qwen2.5:3b").strip()
    allowed_ids = sorted(_FIELD_LABELS)
    qa_instruction = (
        "The note contains numbered questions followed by answers. Treat only answer text as "
        "facts; never extract words from a question as a value. "
        if evidence_scope else ""
    )
    prompt = (
        "Extract only facts explicitly stated in the following consultant note for a U.S. "
        "DS-160 draft. Never infer missing facts. Return JSON only as "
        "{\"fields\":[{\"fieldId\":\"...\",\"value\":\"exact original value\"," 
        "\"englishValue\":\"concise CEAC-safe English\",\"evidence\":\"exact quote from note\"}]}. "
        "Evidence must be copied exactly from the note. Preserve every identifier, date and "
        "number. For Chinese names use Hanyu Pinyin in englishValue. "
        f"{qa_instruction}Use only these field IDs: "
        f"{json.dumps(allowed_ids, ensure_ascii=False)}. Note: {value}"
    )
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0, "num_predict": 2500},
    }).encode("utf-8")
    try:
        req = url_request.Request(
            f"{endpoint}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with url_request.urlopen(req, timeout=18) as response:
            body = json.loads(response.read().decode("utf-8"))
        decoded = json.loads(body.get("response") or "{}")
        source_key = _evidence_key(value)
        answer_key = _evidence_key(evidence_scope or value)
        output = []
        for item in decoded.get("fields") or []:
            if not isinstance(item, dict):
                continue
            field_id = clean_text(item.get("fieldId"), 120)
            raw_value = clean_text(item.get("value"), 1200)
            english_value = clean_text(item.get("englishValue"), 1200)
            evidence = clean_text(item.get("evidence"), 1200)
            if field_id not in _FIELD_LABELS or not raw_value or not evidence:
                continue
            evidence_key = _evidence_key(evidence)
            raw_key = _evidence_key(raw_value)
            if not evidence_key or evidence_key not in source_key:
                continue
            if raw_key and raw_key not in evidence_key and raw_key not in source_key:
                continue
            if raw_key and raw_key not in answer_key:
                continue
            if contains_cjk(english_value):
                english_value = ""
            output.append({
                "fieldId": field_id,
                "value": raw_value,
                "englishValue": english_value,
                "evidence": evidence,
            })
        return output, "ollama_structured" if output else "ollama_no_extra_fields"
    except (OSError, ValueError, KeyError, TypeError, url_error.URLError, TimeoutError):
        with _OLLAMA_LOCK:
            _OLLAMA_DISABLED_UNTIL = time.monotonic() + 300
        return [], "ollama_unavailable"


def _fallback_translation(value, field_id=""):
    cleaned = clean_text(value)
    if cleaned in _EXACT_TRANSLATIONS:
        return _EXACT_TRANSLATIONS[cleaned], "local_dictionary", False
    translated = cleaned
    phrase_map = (
        ("旅游", "TOURISM"), ("探亲", "VISITING RELATIVES"),
        ("访友", "VISITING FRIENDS"), ("商务会议", "BUSINESS MEETINGS"),
        ("学术会议", "ACADEMIC CONFERENCE"), ("学习", "STUDY"),
        ("父母", "PARENT"), ("监护人", "LEGAL GUARDIAN"),
    ) + _DS160_GLOSSARY
    glossary_used = False
    for chinese, english in sorted(phrase_map, key=lambda item: len(item[0]), reverse=True):
        if chinese in translated:
            translated = translated.replace(chinese, f" {english} ")
            glossary_used = True
    if contains_cjk(translated):
        normalized_id = str(field_id or "").replace("_", "").lower()
        if any(token in normalized_id for token in ("school", "institution")):
            # A school name is an entity, not a sentence to transliterate word by
            # word. Unknown institutions must be resolved by the school directory
            # or a real translation provider before they can reach CEAC.
            return "", "translation_required", True
        translated = romanize(translated)
        provider = "local_glossary_transliteration" if glossary_used else "local_transliteration"
        return translated, provider, True
    provider = "local_glossary" if glossary_used else "local_dictionary"
    return re.sub(r"\s+", " ", translated).strip(), provider, glossary_used


def translate_ds160_value(
    value, field_id="", context="", preserve_native=False, allow_model=True
):
    original = normalize_does_not_apply(value)
    if not original or original == DOES_NOT_APPLY:
        return {"value": original, "originalValue": "", "provider": "normalizer", "reviewRequired": False}
    if (
        preserve_native
        or field_id in _PRESERVE_NATIVE_FIELD_IDS
        or field_id in _INTERNAL_DISPLAY_FIELD_IDS
        or not contains_cjk(original)
    ):
        return {"value": original, "originalValue": "", "provider": "original", "reviewRequired": False}
    cache_key = (original, field_id, context)
    if cache_key in _TRANSLATION_CACHE:
        return dict(_TRANSLATION_CACHE[cache_key])
    exact = _EXACT_TRANSLATIONS.get(original)
    if exact:
        result = {
            "value": normalize_ceac_text(exact),
            "originalValue": original,
            "provider": "local_dictionary",
            "reviewRequired": False,
        }
        _TRANSLATION_CACHE[cache_key] = result
        return dict(result)
    if _should_transliterate(field_id):
        translated = romanize(_place_name_source(original, field_id))
        provider = "local_transliteration"
        review_required = True
    else:
        translated = ""
        provider = ""
        if allow_model:
            translated = _libretranslate_translation(original, context or field_id)
            if translated:
                provider = "libretranslate"
            else:
                translated = _ollama_translation(original, context or field_id)
                if translated:
                    provider = "ollama"
        review_required = False
    if translated:
        result = {
            "value": normalize_ceac_text(translated),
            "originalValue": original,
            "provider": provider,
            "reviewRequired": review_required,
        }
    else:
        fallback, provider, review_required = _fallback_translation(original, field_id)
        result = {
            "value": normalize_ceac_text(fallback),
            "originalValue": original,
            "provider": provider,
            "reviewRequired": review_required or contains_cjk(fallback),
        }
    if not result["value"]:
        result["reviewRequired"] = True
    if not result.get("reviewRequired"):
        _TRANSLATION_CACHE[cache_key] = result
    return dict(result)


def _strip_suffix(value, suffixes):
    cleaned = clean_text(value, 240)
    for suffix in sorted(suffixes, key=len, reverse=True):
        if cleaned.endswith(suffix):
            return cleaned[:-len(suffix)]
    return cleaned


def _split_line1_first(value, maximum=80):
    cleaned = re.sub(r"\s+", " ", clean_text(value, 500)).strip(" ,")
    if len(cleaned) <= maximum:
        return cleaned, ""
    boundary = max(cleaned.rfind(",", 0, maximum + 1), cleaned.rfind(" ", 0, maximum + 1))
    if boundary < maximum // 2:
        boundary = maximum
    return cleaned[:boundary].strip(" ,"), cleaned[boundary:].strip(" ,")


def _address_name(value):
    """Romanize a Chinese address entity without translating its meaning."""
    return normalize_ceac_text(compact_romanize(clean_text(value, 160)))


def _road_name(value):
    cleaned = clean_text(value, 160)
    directions = {
        "东": "EAST", "西": "WEST", "南": "SOUTH", "北": "NORTH", "中": "MIDDLE",
    }
    direction = directions.get(cleaned[-1:])
    if direction:
        cleaned = cleaned[:-1]
    name = _address_name(cleaned)
    return " ".join(item for item in (name, direction) if item)


def _property_name(value):
    cleaned = clean_text(value, 180)
    suffixes = (
        ("小区", "COMMUNITY"), ("大厦", "BUILDING"),
        ("公寓", "APARTMENT"), ("花园", "GARDEN"),
        ("广场", "PLAZA"), ("中心", "CENTER"),
    )
    for suffix, english in suffixes:
        if cleaned.endswith(suffix) and len(cleaned) > len(suffix):
            return " ".join(item for item in (_address_name(cleaned[:-len(suffix)]), english) if item)
    return _address_name(cleaned)


def _chinese_unit_digits(value):
    output = str(value or "")
    numbers = {
        "一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
        "六": "6", "七": "7", "八": "8", "九": "9",
    }
    for chinese, number in numbers.items():
        output = re.sub(rf"{chinese}(?=单元|号楼|栋|幢|楼|层|室|号)", number, output)
    return output


def structure_address(value, country_hint=""):
    """Split an address while keeping line 1 populated and line 2 optional."""
    original = clean_text(value, 1000)
    if not original:
        return {}
    postcode_match = re.search(r"(?<!\d)(\d{5}(?:-\d{4})?|\d{6})(?!\d)", original)
    postcode = postcode_match.group(1) if postcode_match else ""
    if not contains_cjk(original):
        without_postcode = original
        parts = [part.strip() for part in without_postcode.split(",") if part.strip()]
        state = ""
        city = ""
        street = original
        if len(parts) >= 3:
            tail = re.sub(rf"\b{re.escape(postcode)}\b", "", parts[-1]).strip() if postcode else parts[-1]
            state = tail
            city = parts[-2]
            street = ", ".join(parts[:-2])
        line1, line2 = _split_line1_first(street)
        return {
            "line1": line1, "line2": line2, "city": city, "region": state,
            "postalCode": postcode, "country": country_hint,
        }

    remaining = original
    if postcode:
        remaining = remaining.replace(postcode, " ")
    remaining = re.sub(r"(?:中华人民共和国|中国)", " ", remaining)
    region = ""
    city = ""
    district = ""
    region_match = re.search(r"^\s*(.{2,18}?)(省|自治区|特别行政区)", remaining)
    if region_match:
        region_source = region_match.group(1)
        region = (
            _EXACT_TRANSLATIONS.get(region_match.group(0).strip())
            or _EXACT_TRANSLATIONS.get(region_source)
            or _address_name(region_source)
        )
        remaining = remaining[region_match.end():]

    # A Chinese address can contain a prefecture-level city followed by a
    # county-level city (for example 温州市乐清市). DS-160 needs the most local
    # city, so consume the full chain and retain the last city name.
    while True:
        city_match = re.search(r"^\s*(.{1,18}?)(自治州|地区|盟|市)", remaining)
        if not city_match:
            break
        city_source = city_match.group(1)
        city = (
            _EXACT_TRANSLATIONS.get(city_match.group(0).strip())
            or _EXACT_TRANSLATIONS.get(city_source)
            or _address_name(city_source)
        )
        remaining = remaining[city_match.end():]
    district_match = (
        re.search(r"^\s*(.{1,18}?)(新区|区|县|旗)", remaining)
        if region or city else None
    )
    if district_match:
        district_source = district_match.group(1)
        district = _EXACT_TRANSLATIONS.get(
            f"{district_source}{district_match.group(2)}"
        ) or f"{_address_name(district_source)} DISTRICT"
        remaining = remaining[district_match.end():]
    if not region and city in {"BEIJING", "SHANGHAI", "TIANJIN", "CHONGQING"}:
        region = city
    street = clean_text(remaining, 500).strip(" ,，;；")

    subdistrict = ""
    subdistrict_match = re.match(r"^\s*(.{1,24}?)(街道|镇|乡)", street)
    if subdistrict_match:
        admin_types = {"街道": "SUBDISTRICT", "镇": "TOWN", "乡": "TOWNSHIP"}
        subdistrict = " ".join((
            _address_name(subdistrict_match.group(1)),
            admin_types[subdistrict_match.group(2)],
        )).strip()
        street = street[subdistrict_match.end():].strip()

    road = ""
    house_number = ""
    road_match = re.match(r"^\s*(.{1,32}?)(大道|大街|公路|路|街|巷|弄)", street)
    if road_match:
        road_types = {
            "大道": "AVENUE", "大街": "AVENUE", "公路": "ROAD",
            "路": "ROAD", "街": "STREET", "巷": "LANE", "弄": "LANE",
        }
        road = " ".join((
            _road_name(road_match.group(1)), road_types[road_match.group(2)]
        )).strip()
        street = street[road_match.end():].strip()
        house_match = re.match(r"^(\d+)\s*(?:号)?", street)
        if house_match:
            house_number = house_match.group(1)
            street = street[house_match.end():].strip()

    unit_source = _chinese_unit_digits(street)
    unit_matches = {
        "building": re.search(r"(\d+)\s*(?:号楼|栋|幢)", unit_source),
        "unit": re.search(r"(\d+)\s*单元", unit_source),
        "floor": re.search(r"(\d+)\s*(?:楼|层)", unit_source),
        "room": re.search(r"(\d+[A-Za-z]?)\s*(?:室|号)?\s*$", unit_source),
    }
    starts = [match.start() for match in unit_matches.values() if match]
    property_source = unit_source[:min(starts)].strip(" ,，") if starts else unit_source.strip(" ,，")
    property_value = _property_name(property_source) if property_source else ""
    unit_parts = []
    for key, label in (("room", "ROOM"), ("floor", "FLOOR"), ("unit", "UNIT"), ("building", "BUILDING")):
        match = unit_matches[key]
        if match:
            value = match.group(1).upper()
            item = f"{label} {value}"
            if item not in unit_parts:
                unit_parts.append(item)
    if property_value:
        unit_parts.append(property_value)

    road_value = " ".join(item for item in (
        f"NO. {house_number}" if house_number else "", road,
    ) if item)
    location_parts = [item for item in (road_value, subdistrict, district) if item]
    primary = ", ".join(unit_parts)
    secondary = ", ".join(location_parts)
    if not primary:
        primary, secondary = secondary, ""
    if not primary:
        primary = normalize_ceac_text(romanize(original))
    line1, overflow = _split_line1_first(primary)
    line2_source = ", ".join(item for item in (overflow, secondary) if item)
    line2, _unused = _split_line1_first(line2_source)
    return {
        "line1": line1,
        "line2": line2,
        "city": city,
        "region": region,
        "postalCode": postcode,
        "country": "CHINA" if country_hint.upper() != "UNITED STATES OF AMERICA" else country_hint,
    }


def _labelled_value(text, labels, limit=500):
    labels_expression = "|".join(labels)
    match = re.search(
        rf"(?:{labels_expression})\s*[:：]?\s*(.+?)(?="
        rf"(?:[,，;；。.!！?？\n]\s*|\s{{2,}})(?:{_CONSULTANT_FIELD_LABELS})\s*[:：]?|$)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return ""
    return clean_text(match.group(1), limit).strip(" ,，;；")


def _first_clause(value, limit=240):
    return clean_text(
        re.split(r"[,，;；。.!！?？\n]", str(value or ""), maxsplit=1)[0], limit
    ).strip(" :：")


def _normalized_date(value):
    cleaned = clean_text(value, 100)
    patterns = (
        r"(?<!\d)(\d{4})\s*[年./-]\s*(\d{1,2})\s*[月./-]\s*(\d{1,2})\s*日?",
        r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)",
    )
    for pattern in patterns:
        match = re.search(pattern, cleaned)
        if not match:
            continue
        year, month, day = (int(part) for part in match.groups())
        if 1900 <= year <= 2200 and 1 <= month <= 12 and 1 <= day <= 31:
            return f"{year:04d}-{month:02d}-{day:02d}"
    return ""


def _normalized_duration(value):
    cleaned = _first_clause(value, 100)
    match = re.search(
        r"(?<!\d)(\d+(?:\.\d+)?)\s*(天|日|周|星期|个月|月|年|DAYS?|WEEKS?|MONTHS?|YEARS?)",
        cleaned,
        flags=re.IGNORECASE,
    )
    if not match:
        return cleaned
    number, unit = match.groups()
    unit_map = {
        "天": "DAYS", "日": "DAYS", "周": "WEEKS", "星期": "WEEKS",
        "个月": "MONTHS", "月": "MONTHS", "年": "YEARS",
    }
    normalized_unit = unit_map.get(unit, unit.upper())
    if not normalized_unit.endswith("S"):
        normalized_unit += "S"
    return f"{number} {normalized_unit}"


_INLINE_QA_PROMPT_PATTERNS = (
    r"是否已婚.*?家庭住址",
    r"是否已有赴美旅行计划.*时长[?？]?",
    r"谁会支付本次出行.*?还是公司[?？]?",
    r"是否曾被拒签.*解释一下",
    r"是否有美国(?:驾照|驾驶执照)[?？]?",
    r"曾经去美国.*时长[?？]?",
    r"办过美签移民吗[?？]?",
    r"(?:现在|当前)家庭住址",
    r"邮箱",
    r"社(?:媒|交媒体)账号.*?说几个就行",
    r"电话[，,、和及 ]*备用电话",
    r"5年内用过其他电话号吗",
    r"5年内用过其他邮箱吗",
    r"曾经丢过护照吗",
    r"在美国的联系人或组织.*?联系方式",
    r"父亲和母亲的全名.*?是否在美国",
    r"是否有亲戚在美国",
    r"曾经就职公司.*?联系方式",
    r"公司的地址.*?工作职责",
    r"高中及以上学校.*?开始和毕业时间",
    r"语言数量及名称",
    r"有没有其他特殊情况",
)


def _split_inline_numbered_item(value):
    """Separate a known intake prompt from an answer written on the same line."""
    text = clean_text(value, 6200)
    if not text:
        return "", ""
    explicit = re.match(
        r"^(.*?)(?:\s*(?:答(?:案)?|A)\s*[:：]\s*|\s*(?:[—–]{2,}|-{2,})\s*)(.+)$",
        text,
        flags=re.IGNORECASE,
    )
    if explicit:
        return (
            clean_text(explicit.group(1), 1200).rstrip(" :：,，;；.。?？"),
            clean_text(explicit.group(2), 5000).lstrip(" :：,，;；.。?？—–-"),
        )
    for pattern in _INLINE_QA_PROMPT_PATTERNS:
        match = re.match(rf"^({pattern})(.*)$", text, flags=re.IGNORECASE)
        if not match:
            continue
        remainder = clean_text(match.group(2), 5000).lstrip(
            " :：,，;；.。?？—–-"
        )
        return clean_text(match.group(1), 1200).rstrip(), remainder
    question_mark = re.match(r"^(.+?[?？])\s*(.+)$", text, flags=re.DOTALL)
    if question_mark:
        return (
            clean_text(question_mark.group(1), 1200).rstrip(),
            clean_text(question_mark.group(2), 5000).lstrip(
                " :：,，;；.。?？—–-"
            ),
        )
    return text, ""


def _extract_numbered_qa(value):
    """Split numbered intake text even when every item is pasted on one line."""
    text = str(value or "")
    markers = list(re.finditer(
        r"(?im)(?:^|(?<=[\s；;。.!！?？]))"
        r"(?:问题\s*|Q\s*)?"
        r"(?:[（(]\s*(\d{1,3})\s*[)）]|(\d{1,3})\s*[.．、):：])\s*",
        text,
    ))
    if len(markers) < 2:
        return []

    # A stray decimal or date fragment should not turn ordinary prose into a
    # questionnaire. Real intake lists are monotonically numbered, although
    # they may start at a later number when only part of a template is pasted.
    filtered = []
    previous_number = None
    for marker in markers:
        number = int(marker.group(1) or marker.group(2))
        if previous_number is not None and number <= previous_number:
            continue
        filtered.append((marker, number))
        previous_number = number
    if len(filtered) < 2:
        return []

    pairs = []
    for index, (marker, number) in enumerate(filtered):
        segment_end = (
            filtered[index + 1][0].start()
            if index + 1 < len(filtered) else len(text)
        )
        segment = text[marker.end():segment_end].strip()
        lines = [line.strip() for line in segment.splitlines() if line.strip()]
        first_line = lines[0] if lines else segment
        question, inline_answer = _split_inline_numbered_item(first_line)
        trailing_answer = "\n".join(lines[1:]).strip()
        trailing_answer = re.sub(
            r"^(?:答(?:案)?|A)\s*[:：]\s*", "", trailing_answer,
            flags=re.IGNORECASE,
        )
        answer = "\n".join(part for part in (inline_answer, trailing_answer) if part)
        pairs.append({
            "number": number,
            "question": question,
            "answer": clean_text(answer, 5000),
            "mappedQuestionIds": [],
            "mappedFieldIds": [],
        })
    return pairs


def _mark_qa_mapping(pair, key, *values):
    current = pair.setdefault(key, [])
    for value in values:
        if value and value not in current:
            current.append(value)


def _normalized_prompt(value):
    text = str(value or "").upper()
    replacements = (
        ("驾驶执照", "驾照"), ("亲属", "亲戚"), ("申请人", ""),
        ("客户", ""), ("您的", ""), ("你的", ""), ("您", ""),
        ("现在或过去是否", "是否曾"), ("现在或过去", "曾"),
        ("拥有或曾拥有", "曾有"), ("持有或曾持有", "曾有"),
        ("是否拥有", "是否有"),
        ("有没有", "是否有"), ("是否曾经", "是否曾"),
    )
    for source, target in replacements:
        text = text.replace(source, target)
    return re.sub(r"[^A-Z0-9\u3400-\u9fff]+", "", text)


def _prompt_similarity(source, candidate):
    left = _normalized_prompt(source)
    right = _normalized_prompt(candidate)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    shorter, longer = sorted((left, right), key=len)
    containment = len(shorter) >= 6 and shorter in longer
    ratio = SequenceMatcher(None, left, right).ratio()
    left_pairs = {left[index:index + 2] for index in range(max(0, len(left) - 1))}
    right_pairs = {right[index:index + 2] for index in range(max(0, len(right) - 1))}
    overlap = (
        len(left_pairs & right_pairs) / max(1, len(left_pairs | right_pairs))
    )
    return max(0.96 if containment else 0.0, ratio, overlap)


def _schema_questionnaire_updates(pairs, updates):
    """Match unhandled explicit Yes/No answers to the maintained DS-160 schema."""
    try:
        from .ds160_rules import QUESTIONS
    except ImportError:  # pragma: no cover - compatibility when run as a script
        from backend.ds160_rules import QUESTIONS

    matched = 0
    definitions = [
        item for item in QUESTIONS if item.get("answerType") == "yes_no"
    ]
    for pair in pairs:
        if pair.get("mappedQuestionIds"):
            continue
        answer = pair.get("answer") or ""
        normalized_answer = (
            "no" if _answer_is_no(answer)
            else "yes" if _answer_is_yes(answer)
            else ""
        )
        if not normalized_answer:
            continue
        scores = sorted([
            (
                max(
                    _prompt_similarity(pair.get("question"), item.get("label")),
                    _prompt_similarity(pair.get("question"), item.get("englishLabel")),
                ),
                item,
            )
            for item in definitions
        ], key=lambda candidate: candidate[0])
        best_score, best = scores[-1]
        second_score = scores[-2][0] if len(scores) > 1 else 0.0
        if best_score < 0.82 or best_score - second_score < 0.06:
            continue
        question_id = best.get("id")
        if question_id in updates:
            continue
        updates[question_id] = {
            "answer": normalized_answer,
            "answerEvidence": clean_text(answer, 1200),
            "answerConfidence": round(min(0.96, best_score), 3),
        }
        _mark_qa_mapping(pair, "mappedQuestionIds", question_id)
        matched += 1
    return matched


def _answer_is_no(value):
    return bool(re.match(
        r"^\s*(?:否|无|没有|从未|不曾|NO\b|NONE\b|N/A\b)",
        str(value or ""),
        flags=re.IGNORECASE,
    ))


def _answer_is_yes(value):
    return bool(re.match(
        r"^\s*(?:是|有|YES\b)", str(value or ""), flags=re.IGNORECASE
    ))


def _extract_phone_values(value):
    matches = re.findall(
        r"(?<!\d)(?:\+?86[\s-]*)?1[3-9](?:[\s-]*\d){9}(?!\d)",
        str(value or ""),
    )
    phones = []
    for match in matches:
        digits = re.sub(r"\D", "", match)
        normalized = f"+{digits}" if digits.startswith("86") else digits
        if normalized not in phones:
            phones.append(normalized)
    return phones


def _split_chinese_person_name(value):
    name = re.sub(r"[^\u3400-\u9fff·]", "", str(value or ""))
    if len(name) < 2:
        return "", ""
    compound_surnames = (
        "欧阳", "司马", "上官", "诸葛", "东方", "皇甫", "尉迟", "公孙",
        "慕容", "司徒", "司空", "夏侯", "南宫", "令狐", "长孙",
    )
    surname = next((item for item in compound_surnames if name.startswith(item)), name[0])
    return surname, name[len(surname):]


def _qa_current_employment(value):
    """Extract only facts explicitly present in a current-employment answer."""
    answer = str(value or "")
    first_line = next((line.strip() for line in answer.splitlines() if line.strip()), "")
    phones = _extract_phone_values(answer)
    organization = _first_clause(
        _labelled_value(answer, [r"公司名称", r"雇主名称", r"单位名称"], 240), 240
    )
    labelled_address = _labelled_value(
        answer, [r"公司地址", r"雇主地址", r"单位地址"], 1000
    )
    address_source = labelled_address or first_line
    address_boundaries = []
    for pattern in (
        r"(?<!\d)(?:\+?86[\s-]*)?1[3-9](?:[\s-]*\d){9}(?!\d)",
        r"(?<!\d)\d{4}\s*[年./-]\s*\d{1,2}\s*[月./-]\s*\d{1,2}\s*日?",
        r"(?:开始时间|入职时间|入职日期|职位|职务|工作职责|职责|月收入)\s*[:：]?",
    ):
        match = re.search(pattern, address_source, flags=re.IGNORECASE)
        if match:
            address_boundaries.append(match.start())
    if address_boundaries:
        address_source = address_source[:min(address_boundaries)]
    address = re.sub(
        r"^(?:公司地址|雇主地址|单位地址)\s*[:：]?\s*", "", address_source
    ).strip(" ,，;；")
    start_value = _labelled_value(
        answer, [r"开始时间", r"入职时间", r"入职日期"], 100
    )
    start_date = _normalized_date(start_value) or _normalized_date(answer)
    title = _first_clause(_labelled_value(answer, [r"职位", r"职务"], 160), 160)
    if not title and start_date:
        date_match = re.search(
            r"(?<!\d)\d{4}\s*[年./-]\s*\d{1,2}\s*[月./-]\s*\d{1,2}\s*日?",
            answer,
        )
        if date_match:
            title_candidate = _first_clause(
                re.sub(
                    r"^(?:职位|职务)\s*[:：]?\s*", "", answer[date_match.end():].strip()
                ),
                160,
            )
            if (
                title_candidate
                and len(title_candidate) <= 40
                and not re.search(r"\d|省|市|区|县|路|街|大厦|小区", title_candidate)
            ):
                title = title_candidate
    duties = _first_clause(
        _labelled_value(answer, [r"工作职责", r"职责"], 1000), 1000
    )
    income = _first_clause(_labelled_value(answer, [r"月收入"], 120), 120)
    details = {}
    if organization:
        details["organization"] = organization
    if address:
        details["address"] = address
    if phones:
        details["phone"] = phones[0]
    if start_date:
        details["startDate"] = start_date
    if title:
        details["jobTitle"] = title
    if duties:
        details["duties"] = duties
    if income:
        details["monthlyIncome"] = income

    occupation = ""
    occupation_source = f"{title} {duties}".strip()
    occupation_rules = (
        (r"财务|会计|审计|金融|银行|商务|销售|运营|市场|经理", "business"),
        (r"计算机|软件|程序|开发|网络|数据", "computer_science"),
        (r"工程|工程师", "engineering"),
        (r"教师|教学|教育", "education"),
        (r"医生|护士|医疗|药师|健康", "medical_health"),
        (r"研究|科研", "research"),
        (r"政府|公务员", "government"),
        (r"法律|律师|法务", "legal"),
        (r"军人|军官|军事", "military"),
    )
    for pattern, value in occupation_rules:
        if re.search(pattern, occupation_source, flags=re.IGNORECASE):
            occupation = value
            break
    return {"details": details, "occupation": occupation}


def _qa_fact_text(pairs):
    """Create a narrow fact stream from answers that map safely to scalar fields."""
    facts = []
    for pair in pairs:
        question = re.sub(r"\s+", "", pair.get("question") or "")
        answer = pair.get("answer") or ""
        if not answer:
            continue
        if "现在家庭住址" in question or "当前家庭住址" in question:
            if not _answer_is_no(answer):
                facts.append(f"家庭地址：{_first_clause(answer, 1000)}")
                _mark_qa_mapping(
                    pair, "mappedFieldIds",
                    "contact.homeStreet1", "contact.homeStreet2",
                    "contact.homeCity", "contact.homeRegion",
                    "contact.homePostalCode", "contact.homeCountry",
                )
            continue
        if "邮箱" in question and "其他" not in question and "5年" not in question:
            email = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", answer, re.IGNORECASE)
            if email:
                facts.append(f"邮箱：{email.group(0)}")
                _mark_qa_mapping(pair, "mappedFieldIds", "contact.email")
            continue
        if "电话" in question and "备用电话" in question and "其他" not in question and "5年" not in question:
            phones = _extract_phone_values(answer)
            if phones:
                facts.append(f"主要电话：{phones[0]}")
                _mark_qa_mapping(pair, "mappedFieldIds", "contact.primaryPhone")
            if len(phones) > 1:
                facts.append(f"备用电话：{phones[1]}")
                _mark_qa_mapping(pair, "mappedFieldIds", "contact.secondaryPhone")
            continue
        if (
            "公司的地址" in question
            and "开始时间" in question
            and "曾经就职" not in question
        ):
            employment = _qa_current_employment(answer)["details"]
            if employment.get("organization"):
                facts.append(f"公司名称：{employment['organization']}")
                _mark_qa_mapping(pair, "mappedFieldIds", "work.employerName")
            if employment.get("address"):
                facts.append(f"公司地址：{employment['address']}")
                _mark_qa_mapping(pair, "mappedFieldIds", "work.employerAddress")
            if employment.get("phone"):
                facts.append(f"公司电话：{employment['phone']}")
                _mark_qa_mapping(pair, "mappedFieldIds", "work.employerPhone")
            if employment.get("startDate"):
                facts.append(f"入职日期：{employment['startDate']}")
                _mark_qa_mapping(pair, "mappedFieldIds", "work.startDate")
            if employment.get("jobTitle"):
                facts.append(f"职位：{employment['jobTitle']}")
                _mark_qa_mapping(pair, "mappedFieldIds", "work.title")
            if employment.get("duties"):
                facts.append(f"工作职责：{employment['duties']}")
                _mark_qa_mapping(pair, "mappedFieldIds", "work.duties")
            if employment.get("monthlyIncome"):
                facts.append(f"月收入：{employment['monthlyIncome']}")
                _mark_qa_mapping(pair, "mappedFieldIds", "work.monthlyIncome")
    return "；".join(facts)


def _qa_education_records(value):
    records = []
    date_pattern = re.compile(
        r"(?<!\d)(\d{4})\s*[年./-]\s*(\d{1,2})\s*[月./-]\s*(\d{1,2})\s*日?"
    )
    raw_lines = re.sub(
        r"\s+(?=(?:初中|高中|中学|中专|职校|技校|大学|本科|大专|硕士|博士|研究生)\s*[:：])",
        "\n",
        str(value or ""),
    ).splitlines()
    for line in raw_lines:
        line = line.strip(" ,，;；")
        if not line:
            continue
        date_matches = list(date_pattern.finditer(line))
        year_range = re.search(
            r"(?<!\d)(?:19|20)\d{2}\s*[年./-]?\s*[—–~-]\s*(?:19|20)\d{2}(?!\d)",
            line,
        )
        temporal_positions = [match.start() for match in date_matches]
        if year_range:
            temporal_positions.append(year_range.start())
        prefix = line[:min(temporal_positions)].strip() if temporal_positions else line
        level = ""
        level_match = re.match(
            r"^(初中|高中|中学|中专|职校|技校|大学|本科|大专|硕士|博士|研究生)\s*[:：]\s*",
            prefix,
        )
        if level_match:
            level_label = level_match.group(1)
            prefix = prefix[level_match.end():].strip()
            if level_label in {"初中", "高中", "中学"}:
                level = "secondary"
            elif level_label in {"中专", "职校", "技校"}:
                level = "vocational"
            elif level_label in {"大学", "本科", "大专"}:
                level = "college"
            else:
                level = "postgraduate"
        course_match = re.search(r"专业\s*[:：]\s*(.+)$", prefix)
        course = course_match.group(1).strip() if course_match else ""
        school_part = prefix[:course_match.start()].strip() if course_match else prefix
        school = school_part
        if not course_match:
            school_course_match = re.match(
                r"^(.+?(?:高级中学|中学|大学|学院|学校))\s+(.+)$", school_part
            )
            if school_course_match:
                school = school_course_match.group(1).strip()
                course = school_course_match.group(2).strip()
        if not school:
            continue
        if not level:
            if re.search(r"高级中学|中学|高中", school):
                level = "secondary"
            elif re.search(r"中专|职业学校|技工学校", school):
                level = "vocational"
            elif re.search(r"大学|学院", school):
                level = "college"
        record = {"level": level or "other", "school": school}
        if course and record["level"] != "secondary":
            record["course"] = course
        dates = [_normalized_date(match.group(0)) for match in date_matches[:2]]
        if dates and dates[0]:
            record["startDate"] = dates[0]
        if len(dates) > 1 and dates[1]:
            record["endDate"] = dates[1]
        records.append(record)
    return records


def _qa_questionnaire_updates(pairs):
    updates = {}
    active_pair = None
    current_employment = next((
        _qa_current_employment(pair.get("answer") or "")
        for pair in pairs
        if "公司的地址" in re.sub(r"\s+", "", pair.get("question") or "")
        and "开始时间" in re.sub(r"\s+", "", pair.get("question") or "")
        and "曾经就职" not in re.sub(r"\s+", "", pair.get("question") or "")
    ), {"details": {}, "occupation": ""})

    def put(
        question_id, *, answer=None, details=None, records=None, evidence="",
        confidence=0.99,
    ):
        current = updates.setdefault(question_id, {})
        if answer is not None:
            current["answer"] = answer
        if details:
            current.setdefault("details", {}).update(details)
        if records:
            current["records"] = records
        current["answerEvidence"] = clean_text(evidence, 1200)
        current["answerConfidence"] = confidence
        if active_pair is not None:
            _mark_qa_mapping(active_pair, "mappedQuestionIds", question_id)

    for pair in pairs:
        active_pair = pair
        question = re.sub(r"\s+", "", pair.get("question") or "")
        answer = pair.get("answer") or ""
        if not answer:
            continue

        if "是否已婚" in question or "婚姻状况" in question:
            marital_map = (
                ("未婚", "single"), ("已婚", "married"), ("离婚", "divorced"),
                ("丧偶", "widowed"), ("法律分居", "legally_separated"),
            )
            selected = next((value for label, value in marital_map if label in answer), "")
            if not selected and _answer_is_no(answer):
                selected = "single"
            if selected:
                put("personal.marital_status", answer=selected, evidence=answer)
        elif "旅行计划" in question:
            if _answer_is_no(answer):
                put("travel.specific_plans", answer="no", evidence=answer)
            elif _answer_is_yes(answer):
                put("travel.specific_plans", answer="yes", evidence=answer)
        elif "支付本次出行" in question or "承担本次旅行费用" in question:
            payer = ""
            if re.search(r"本人|自己", answer):
                payer = "self"
            elif re.search(r"父亲|母亲|父母|监护人|亲属|他人", answer):
                payer = "other_person"
            elif re.search(r"当前(?:公司|雇主)|所在公司", answer):
                payer = "present_employer"
            elif re.search(r"美国雇主", answer):
                payer = "us_employer"
            elif re.fullmatch(r"\s*(?:公司|单位|雇主)\s*", answer) and current_employment["details"]:
                payer = "present_employer"
            elif re.search(r"机构|学校|公司", answer):
                payer = "unknown"
            if payer:
                put(
                    "travel.payer", answer=payer, evidence=answer,
                    confidence=0.9 if payer == "present_employer" else 0.99,
                )
        elif "拒签" in question or "拒绝入境" in question:
            if _answer_is_no(answer):
                put("us_history.refusal_or_admission", answer="no", evidence=answer)
            elif _answer_is_yes(answer):
                put("us_history.refusal_or_admission", answer="yes", evidence=answer)
        elif "美国驾照" in question or "美国驾驶执照" in question:
            if _answer_is_no(answer) or _answer_is_yes(answer):
                put(
                    "us_history.drivers_license",
                    answer="no" if _answer_is_no(answer) else "yes",
                    evidence=answer,
                )
        elif "曾经去美国" in question or "去过美国" in question:
            if _answer_is_no(answer) or _answer_is_yes(answer):
                put(
                    "us_history.visited",
                    answer="no" if _answer_is_no(answer) else "yes",
                    evidence=answer,
                )
        elif "移民" in question and ("美签" in question or "申请" in question):
            if _answer_is_no(answer) or _answer_is_yes(answer):
                put(
                    "us_history.immigrant_petition",
                    answer="no" if _answer_is_no(answer) else "yes",
                    evidence=answer,
                )
        elif "5年内" in question and "其他电话" in question:
            phones = _extract_phone_values(answer)
            put(
                "contact.other_phones",
                answer="no" if _answer_is_no(answer) else "yes" if phones else "unknown",
                records=[{"phone": phone} for phone in phones],
                evidence=answer,
            )
        elif "5年内" in question and "其他邮箱" in question:
            emails = re.findall(
                r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", answer, re.IGNORECASE
            )
            put(
                "contact.other_emails",
                answer="no" if _answer_is_no(answer) else "yes" if emails else "unknown",
                records=[{"email": email.lower()} for email in emails],
                evidence=answer,
            )
        elif "社媒" in question or "社交媒体" in question:
            aliases = {
                "INS": "INSTAGRAM", "IG": "INSTAGRAM", "INSTAGRAM": "INSTAGRAM",
                "FACEBOOK": "FACEBOOK", "LINKEDIN": "LINKEDIN", "YOUTUBE": "YOUTUBE",
                "微博": "SINA_WEIBO", "豆瓣": "DOUBAN", "QQ空间": "QZONE",
            }
            records = []
            for platform, handle in re.findall(
                r"(INS|IG|INSTAGRAM|FACEBOOK|LINKEDIN|YOUTUBE|微博|豆瓣|QQ空间)\s*[:：]\s*([^\s,，;；]+)",
                answer,
                flags=re.IGNORECASE,
            ):
                platform_key = aliases.get(platform.upper(), aliases.get(platform, ""))
                if platform_key:
                    records.append({"platform": platform_key, "handle": handle})
            put(
                "contact.social_media",
                answer="no" if _answer_is_no(answer) else "yes" if records else "unknown",
                records=records,
                evidence=answer,
            )
        elif "丢过护照" in question or "护照" in question and "遗失" in question:
            if _answer_is_no(answer) or _answer_is_yes(answer):
                put(
                    "passport.lost_stolen",
                    answer="no" if _answer_is_no(answer) else "yes",
                    evidence=answer,
                )
        elif "美国" in question and ("联系人" in question or "联系组织" in question):
            if _answer_is_no(answer):
                put("us_contact.knows_person", answer="unknown", evidence=answer)
        elif "父亲" in question and "母亲" in question:
            parsed_relations = set()
            for relation, question_id in (
                ("父亲", "family.father_known"), ("母亲", "family.mother_known")
            ):
                match = re.search(
                    rf"{relation}\s*[:：]\s*([^\d\n,，;；]+?)\s*"
                    r"(\d{4}\s*[年./-]\s*\d{1,2}\s*[月./-]\s*\d{1,2}\s*日?)",
                    answer,
                )
                if not match:
                    continue
                surname, given_names = _split_chinese_person_name(match.group(1))
                details = {
                    "surname": surname,
                    "givenNames": given_names,
                    "dateOfBirth": _normalized_date(match.group(2)),
                }
                put(question_id, details=details, evidence=match.group(0))
                parsed_relations.add(relation)
            unlabelled_people = list(re.finditer(
                r"([\u3400-\u9fff·]{2,6})\s*"
                r"(\d{4}\s*[年./-]\s*\d{1,2}\s*[月./-]\s*\d{1,2}\s*日?)",
                answer,
            ))
            for relation, question_id, index in (
                ("父亲", "family.father_known", 0),
                ("母亲", "family.mother_known", 1),
            ):
                if relation in parsed_relations or index >= len(unlabelled_people):
                    continue
                match = unlabelled_people[index]
                surname, given_names = _split_chinese_person_name(match.group(1))
                put(
                    question_id,
                    details={
                        "surname": surname,
                        "givenNames": given_names,
                        "dateOfBirth": _normalized_date(match.group(2)),
                    },
                    evidence=match.group(0),
                )
            if re.search(r"(?:否|均?不在美国|都不在美国)\s*$", answer):
                put("family.father_in_us", answer="no", evidence=answer)
                put("family.mother_in_us", answer="no", evidence=answer)
        elif "亲戚在美国" in question or "亲属在美国" in question:
            if _answer_is_no(answer):
                put("family.immediate_relatives_us", answer="no", evidence=answer)
                put("family.other_relatives_us", answer="no", evidence=answer)
        elif (
            "公司的地址" in question
            and "开始时间" in question
            and "曾经就职" not in question
        ):
            details = current_employment.get("details") or {}
            if details:
                put(
                    "work.primary_occupation",
                    answer=current_employment.get("occupation") or "unknown",
                    details=details,
                    evidence=answer,
                    confidence=0.9,
                )
        elif "曾经就职" in question or "以前受雇" in question:
            if _answer_is_no(answer):
                put("work.previously_employed", answer="no", evidence=answer)
            elif _answer_is_yes(answer):
                put("work.previously_employed", answer="yes", evidence=answer)
        elif "高中及以上学校" in question or "教育经历" in question:
            records = _qa_education_records(answer)
            put(
                "work.education_secondary_or_above",
                answer="no" if _answer_is_no(answer) else "yes" if records else "unknown",
                records=records,
                evidence=answer,
            )
        elif "语言" in question and ("名称" in question or "哪些" in question):
            languages = [
                item for item in re.split(r"[\s,，;；、]+", answer.strip())
                if item
                and not _answer_is_no(item)
                and not re.fullmatch(r"\d+\s*(?:种|个|门)?", item)
            ]
            if languages:
                put(
                    "additional.languages",
                    records=[{"language": item} for item in languages],
                    evidence=answer,
                )
    return updates


def _plausible_known_field_value(field_id, value):
    cleaned = clean_text(value, 2000)
    if not cleaned or "?" in cleaned or "？" in cleaned:
        return False
    blocked_exact = {
        "专业", "学校", "公司", "职位", "姓名", "地址", "和联系方式",
        "开始和毕业时间", "开始时间", "联系方式", "具体时间地点和时长",
    }
    if cleaned.strip(" ,，;；:：") in blocked_exact:
        return False
    limits = {
        "personal.surname": 80, "personal.givenNames": 100,
        "personal.nativeName": 40, "contact.surname": 80,
        "contact.givenNames": 100, "education.schoolName": 240,
        "education.programName": 240, "work.employerName": 240,
        "work.title": 160,
    }
    if len(cleaned) > limits.get(field_id, 1200):
        return False
    if field_id in {
        "personal.surname", "personal.givenNames", "personal.nativeName",
        "contact.surname", "contact.givenNames",
    } and re.search(r"是否|联系|方式|地址|生日|名字|组织|公司|学校", cleaned):
        return False
    if field_id == "education.schoolName" and re.search(
        r"专业|开始|毕业|时间|电话|地址|是否", cleaned
    ):
        return False
    if field_id == "education.programName" and re.search(
        r"开始|毕业|时间|电话|地址|是否", cleaned
    ):
        return False
    if field_id == "work.employerName" and re.search(
        r"地址|电话|开始|入职|职责|是否", cleaned
    ):
        return False
    if field_id == "work.duties" and _extract_phone_values(cleaned) and re.search(
        r"省|市|区|路|街|大厦|小区", cleaned
    ):
        return False
    return True


def _known_field(
    field_id,
    value,
    original_text,
    provider="rule",
    confidence=0.92,
    translated_value="",
    force_review=False,
    allow_model_translation=True,
    original_value="",
):
    if not value or not _plausible_known_field_value(field_id, value):
        return None
    label, section, risk = _FIELD_LABELS[field_id]
    if translated_value and not contains_cjk(translated_value):
        translated = {
            "value": normalize_ceac_text(translated_value),
            "originalValue": value if contains_cjk(value) else "",
            "provider": provider,
            "reviewRequired": True,
        }
    else:
        translated = translate_ds160_value(
            value,
            field_id,
            context=label,
            preserve_native=field_id == "personal.nativeName",
            allow_model=allow_model_translation,
        )
    if original_value and not translated.get("originalValue"):
        translated["originalValue"] = clean_text(original_value, 1200)
    if provider != "rule" and translated.get("provider") == "original":
        translated["provider"] = provider
    return {
        "id": field_id,
        "label": label,
        "section": section,
        "value": translated["value"],
        "originalValue": translated.get("originalValue") or "",
        "sourceDocument": "顾问已知信息",
        "sourceDocumentId": None,
        "sourcePage": None,
        "evidence": clean_text(original_text, 500),
        "confidence": confidence,
        "riskLevel": risk,
        "requiresUserConfirmation": force_review or risk == "high" or translated.get("reviewRequired", False),
        "confirmed": False,
        "editedByUser": False,
        "autoVerified": (
            not force_review and risk != "high" and not translated.get("reviewRequired", False)
        ),
        "reviewReason": "顾问提供的已知信息，已自动整理为 DS-160 英文格式",
        "extractionMethod": "consultant_text",
        "translationProvider": translated.get("provider") or provider,
    }


def parse_consultant_information(value):
    text = clean_text(value, 20000)
    if not text:
        return {
            "fields": [], "address": {}, "unparsed": "",
            "analysisProviders": ["deterministic_rules"], "warnings": [],
            "questionnaireUpdates": {}, "qaPairCount": 0,
            "answeredQaCount": 0, "matchedQaCount": 0,
            "recognizedEntries": [],
        }
    qa_pairs = _extract_numbered_qa(text)
    questionnaire_updates = _qa_questionnaire_updates(qa_pairs) if qa_pairs else {}
    parse_text = _qa_fact_text(qa_pairs) if qa_pairs else text
    schema_matched_count = (
        _schema_questionnaire_updates(qa_pairs, questionnaire_updates)
        if qa_pairs else 0
    )
    if qa_pairs:
        # Numbered intake sheets have explicit question/answer boundaries. A model can
        # easily confuse prompt text with facts, so the dedicated parser takes priority.
        semantic_candidates, semantic_status = [], "numbered_qa"
    else:
        semantic_candidates, semantic_status = _ollama_consultant_extraction(text)
    semantic_by_id = {}
    for candidate in semantic_candidates:
        semantic_by_id.setdefault(candidate["fieldId"], candidate)
    fields = []

    def add(field_id, raw_value, evidence, confidence=0.92, **options):
        raw_value = clean_text(raw_value, 1200)
        if not raw_value or field_id not in _FIELD_LABELS:
            return
        candidate = semantic_by_id.get(field_id)
        if (
            field_id != "personal.nativeName"
            and candidate
            and candidate.get("englishValue")
            and contains_cjk(raw_value)
        ):
            raw_key = _evidence_key(raw_value)
            candidate_key = _evidence_key(candidate.get("value"))
            comparable = min(len(raw_key), len(candidate_key)) >= 2
            overlap = (
                raw_key == candidate_key
                or (
                    comparable
                    and (raw_key in candidate_key or candidate_key in raw_key)
                    and min(len(raw_key), len(candidate_key))
                    / max(len(raw_key), len(candidate_key)) >= 0.7
                )
            )
            if overlap:
                options.setdefault("translated_value", candidate["englishValue"])
                options.setdefault("provider", "ollama_structured")
        # Numbered intake sheets skip the whole-note semantic pass, so their
        # extracted Chinese values still need the configured translation service.
        options.setdefault("allow_model_translation", bool(qa_pairs))
        field = _known_field(
            field_id, raw_value, evidence, confidence=confidence, **options
        )
        if field:
            fields.append(field)

    def labelled(labels, limit=500, first_clause=True):
        result = _labelled_value(parse_text, labels, limit)
        return _first_clause(result, limit) if first_clause else result

    name = labelled(
        [r"申请人姓名", r"客户姓名", r"中文姓名", r"母语姓名", r"姓名"], 120
    )
    if not name:
        name_match = re.match(
            r"^\s*(?:客户|申请人)?\s*[:：]?\s*([\u3400-\u9fff·]{2,6})(?=[,，;；\s])",
            parse_text,
        )
        inferred_name = name_match.group(1) if name_match else ""
        if inferred_name not in {"客户资料", "申请资料", "基本信息", "个人信息"}:
            name = inferred_name
    if name and contains_cjk(name):
        add("personal.nativeName", name, f"姓名：{name}", confidence=0.98)

    surname = labelled([r"护照英文姓", r"英文姓", r"Surname"], 100)
    given_names = labelled([r"护照英文名", r"英文名", r"Given Names?"], 140)
    english_name = labelled([r"护照英文姓名", r"英文姓名"], 180)
    if english_name and not (surname or given_names):
        name_parts = re.findall(r"[A-Za-z][A-Za-z' -]*", english_name)
        flattened = " ".join(name_parts).upper().split()
        if len(flattened) >= 2:
            surname, given_names = flattened[0], " ".join(flattened[1:])
    if surname and re.fullmatch(r"[A-Za-z][A-Za-z' -]{0,79}", surname):
        add("personal.surname", surname, f"护照英文姓：{surname}", confidence=0.98)
    if given_names and re.fullmatch(r"[A-Za-z][A-Za-z' -]{0,99}", given_names):
        add("personal.givenNames", given_names, f"护照英文名：{given_names}", confidence=0.98)

    explicit_sex = labelled([r"性别"], 20).lower()
    sex_map = {"男": "MALE", "男性": "MALE", "male": "MALE", "m": "MALE",
               "女": "FEMALE", "女性": "FEMALE", "female": "FEMALE", "f": "FEMALE"}
    if explicit_sex in sex_map:
        add("personal.sex", sex_map[explicit_sex], f"性别：{explicit_sex}", confidence=0.99)

    explicit_birth = labelled([r"出生日期", r"生日"], 80)
    normalized_birth = _normalized_date(explicit_birth)
    if normalized_birth:
        add("personal.dateOfBirth", normalized_birth, f"出生日期：{explicit_birth}", confidence=0.99)

    nationality = labelled([r"当前国籍", r"国籍"], 80)
    if nationality:
        add("personal.nationality", nationality, f"国籍：{nationality}", confidence=0.97)
    birth_country = labelled([r"出生国家(?:或地区)?"], 100)
    if birth_country:
        add("personal.birthCountry", birth_country, f"出生国家：{birth_country}", confidence=0.97)
    birth_city = labelled([r"出生城市"], 120)
    birth_region = labelled([r"出生省份", r"出生省", r"出生省州"], 120)
    birth_place = labelled([r"出生地"], 240)
    if birth_place:
        region_match = re.search(r"([\u3400-\u9fff]{2,18}?)(?:省|自治区)", birth_place)
        city_source_text = birth_place[region_match.end():] if region_match else birth_place
        city_match = re.search(r"([\u3400-\u9fff]{2,18}?)(?:市|自治州|地区|盟)", city_source_text)
        birth_region = birth_region or (region_match.group(1) if region_match else "")
        if city_match:
            birth_city = birth_city or city_match.group(1)
    if birth_city:
        add("personal.birthCity", birth_city, f"出生城市：{birth_city}", confidence=0.94)
    if birth_region:
        add("personal.birthRegion", birth_region, f"出生省份：{birth_region}", confidence=0.94)

    national_id = labelled([r"公民身份号码", r"身份证(?:号|号码)?"], 40)
    if not national_id:
        match = re.search(r"(?<!\d)(\d{17}[0-9Xx])(?!\w)", parse_text)
        national_id = match.group(1) if match else ""
    national_id = re.sub(r"\s+", "", national_id).upper()
    if re.fullmatch(r"\d{17}[0-9X]", national_id):
        add("personal.nationalId", national_id, f"身份证号码：{national_id}", confidence=0.99)
        birth = national_id[6:14]
        add("personal.dateOfBirth", f"{birth[:4]}-{birth[4:6]}-{birth[6:8]}",
            "根据身份证号码中的出生日期整理", confidence=0.98)
        sex = "MALE" if int(national_id[16]) % 2 else "FEMALE"
        add("personal.sex", sex, "根据身份证号码校验位前一位整理", confidence=0.95)

    passport = labelled([r"护照(?:号码|号)"], 40)
    passport_match = re.search(r"\b[A-Z][A-Z0-9]{7,11}\b", passport.upper()) if passport else None
    if passport_match:
        add("passport.number", passport_match.group(0), f"护照号码：{passport_match.group(0)}", confidence=0.99)

    passport_issue_date = labelled([r"护照签发日期", r"签发日期"], 100)
    passport_expiration = labelled([r"护照到期日期", r"护照有效期", r"有效期至"], 100)
    normalized_issue = _normalized_date(passport_issue_date)
    normalized_expiration = _normalized_date(passport_expiration)
    if normalized_issue:
        add("passport.issueDate", normalized_issue, f"护照签发日期：{passport_issue_date}", confidence=0.98)
    if normalized_expiration:
        add("passport.expiration", normalized_expiration, f"护照到期日期：{passport_expiration}", confidence=0.98)
    issue_city = labelled([r"护照签发城市", r"签发城市"], 120)
    issue_region = labelled([r"护照签发省份", r"签发省份"], 120)
    issue_country = labelled([r"护照签发国家", r"签发国家"], 120)
    if issue_city:
        add("passport.issueCity", issue_city, f"护照签发城市：{issue_city}", confidence=0.94)
    if issue_region:
        add("passport.issueRegion", issue_region, f"护照签发省份：{issue_region}", confidence=0.94)
    if issue_country:
        add("passport.issueCountry", issue_country, f"护照签发国家：{issue_country}", confidence=0.94)

    email_match = re.search(
        r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}",
        parse_text,
        flags=re.IGNORECASE,
    )
    if email_match:
        add("contact.email", email_match.group(0).lower(), email_match.group(0), confidence=0.98)
    primary_phone_labels = [r"主要电话", r"手机(?:号|号码)?", r"联系电话"]
    if not qa_pairs:
        # In free-form notes a bare “电话” commonly means the applicant's phone.
        # In numbered questionnaires, however, the dedicated QA parser already
        # labels applicant phones explicitly; accepting a bare label here would
        # incorrectly copy “公司电话” into contact.primaryPhone.
        primary_phone_labels.append(r"电话")
    phone_specs = (
        ("contact.primaryPhone", primary_phone_labels, 0.97),
        ("contact.secondaryPhone", [r"次要电话", r"备用电话"], 0.92),
        ("contact.workPhone", [r"工作电话"], 0.92),
    )
    for field_id, labels, confidence in phone_specs:
        phone = labelled(labels, 60)
        normalized_phone = re.sub(r"[^0-9+]", "", phone)
        if len(re.sub(r"\D", "", normalized_phone)) >= 7:
            add(field_id, normalized_phone, f"电话：{phone}", confidence=confidence)

    home_address = labelled(
        [r"家庭住址", r"家庭地址", r"现住址", r"住址"], 1000, first_clause=False
    )
    us_address = labelled(
        [r"在美(?:停留)?地址", r"美国地址", r"酒店地址"], 1000, first_clause=False
    )
    if not home_address and not qa_pairs:
        # A numbered intake sheet has explicit question/answer boundaries.  Its
        # generic address fallback must not reinterpret a company or school
        # address as the applicant's home address.
        generic = re.search(
            r"(?:中国|中华人民共和国)?[^,，;；\n]{0,25}(?:省|自治区|北京市|上海市|天津市|重庆市)"
            r"[^,，;；\n]{2,180}(?:路|街|大道|巷|号|室)(?:[^,，;；\n]{0,40})",
            parse_text,
        )
        home_address = generic.group(0) if generic else ""

    parsed_home = structure_address(home_address, "CHINA") if home_address else {}
    home_postal = labelled([r"邮政编码", r"邮编"], 20)
    postal_match = re.search(r"\d{6}", home_postal)
    if parsed_home and not parsed_home.get("postalCode") and postal_match:
        parsed_home["postalCode"] = postal_match.group(0)
    home_mapping = {
        "line1": "contact.homeStreet1", "line2": "contact.homeStreet2",
        "city": "contact.homeCity", "region": "contact.homeRegion",
        "postalCode": "contact.homePostalCode", "country": "contact.homeCountry",
    }
    for part, field_id in home_mapping.items():
        if parsed_home.get(part):
            add(
                field_id, parsed_home[part], home_address, confidence=0.93,
                provider="address_parser", original_value=home_address,
            )

    parsed_us = structure_address(us_address, "UNITED STATES OF AMERICA") if us_address else {}
    if us_address:
        add("contact.usAddress", us_address, us_address, confidence=0.92)
    us_mapping = {
        "line1": "contact.usStreet1", "line2": "contact.usStreet2",
        "city": "contact.usCity", "region": "contact.usState",
        "postalCode": "contact.usPostalCode",
    }
    for part, field_id in us_mapping.items():
        if parsed_us.get(part):
            add(
                field_id, parsed_us[part], us_address, confidence=0.92,
                provider="address_parser", original_value=us_address,
            )

    school = labelled([r"当前学校(?:名称)?", r"学校名称", r"学校"], 240)
    if school:
        add("education.schoolName", school, f"学校：{school}", confidence=0.94)
    school_address = labelled([r"学校地址"], 1000, first_clause=False)
    if school_address:
        add("education.schoolAddress", school_address, school_address, confidence=0.92)
    school_phone = labelled([r"学校电话"], 60)
    if school_phone:
        add("education.schoolPhone", re.sub(r"[^0-9+]", "", school_phone), school_phone, confidence=0.92)
    program_name = labelled([r"专业名称", r"专业", r"课程"], 240)
    if program_name:
        add("education.programName", program_name, f"课程或专业：{program_name}", confidence=0.92)
    sevis_match = re.search(r"\bN\d{10}\b", parse_text, flags=re.IGNORECASE)
    sevis = labelled([r"SEVIS(?:\s*ID)?"], 40) or (sevis_match.group(0) if sevis_match else "")
    if re.fullmatch(r"N\d{10}", sevis.strip(), flags=re.IGNORECASE):
        add("education.sevisId", sevis.strip().upper(), f"SEVIS ID：{sevis}", confidence=0.99)
    program_number = labelled([r"Program Number", r"项目编号"], 80)
    if program_number:
        add("education.programNumber", program_number.upper(), f"Program Number：{program_number}", confidence=0.96)
    sponsor_name = labelled([r"Sponsor", r"项目赞助方"], 240)
    if sponsor_name:
        add("education.sponsorName", sponsor_name, f"Sponsor：{sponsor_name}", confidence=0.92)

    employer = labelled([r"雇主(?:名称)?", r"公司名称", r"公司"], 240)
    if employer:
        add("work.employerName", employer, f"雇主：{employer}", confidence=0.94)
    employer_address = labelled([r"雇主地址", r"公司地址"], 1000, first_clause=False)
    if employer_address:
        parsed_employer = structure_address(employer_address, "CHINA")
        normalized_employer_address = ", ".join(
            str(parsed_employer.get(key) or "").strip()
            for key in ("line1", "line2", "city", "region", "postalCode", "country")
            if str(parsed_employer.get(key) or "").strip()
        ) or employer_address
        add(
            "work.employerAddress", normalized_employer_address, employer_address,
            confidence=0.92, provider="address_parser", original_value=employer_address,
        )
    work_phone = labelled([r"雇主电话", r"公司电话"], 60)
    if work_phone:
        add("work.employerPhone", re.sub(r"[^0-9+]", "", work_phone), work_phone, confidence=0.92)
    job_title = labelled([r"职位"], 180)
    start_date = labelled([r"入职日期"], 100)
    monthly_income = labelled([r"月收入"], 100)
    duties = labelled([r"工作职责"], 1000, first_clause=False)
    if job_title:
        add("work.title", job_title, f"职位：{job_title}", confidence=0.92)
    if _normalized_date(start_date):
        add("work.startDate", _normalized_date(start_date), f"入职日期：{start_date}", confidence=0.94)
    if monthly_income:
        add("work.monthlyIncome", monthly_income, f"月收入：{monthly_income}", confidence=0.9)
    if duties:
        add("work.duties", duties, f"工作职责：{duties}", confidence=0.88)

    purpose = labelled([r"本次赴美目的", r"赴美目的", r"旅行目的"], 500, first_clause=False)
    arrival = labelled([r"预计抵达(?:美国)?日期", r"预计到达(?:美国)?日期"], 100)
    stay_duration = labelled([r"预计停留时间"], 100)
    if purpose:
        add("travel.purposeSummary", purpose, f"赴美目的：{purpose}", confidence=0.9)
    if _normalized_date(arrival):
        add("travel.arrivalDate", _normalized_date(arrival), f"预计抵达日期：{arrival}", confidence=0.94)
    if stay_duration:
        add(
            "travel.stayDuration", _normalized_duration(stay_duration),
            f"预计停留时间：{stay_duration}", confidence=0.9,
        )

    contact_organization = labelled([r"美国联系机构", r"美国联系人机构"], 240)
    contact_phone = labelled([r"美国联系人电话"], 80)
    contact_email = labelled([r"美国联系人邮箱"], 160)
    if contact_organization:
        add("contact.organizationName", contact_organization, contact_organization, confidence=0.92)
    if contact_phone:
        add("contact.phone", re.sub(r"[^0-9+]", "", contact_phone), contact_phone, confidence=0.92)
    if contact_email and re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", contact_email):
        add("contact.usEmail", contact_email.lower(), contact_email, confidence=0.96)

    previous_visa_number = labelled([r"美国签证号码", r"旧签证号码"], 80)
    previous_visa_date = labelled([r"美国签证签发日期"], 100)
    if previous_visa_number:
        add("history.previousVisaNumber", previous_visa_number.upper(), previous_visa_number, confidence=0.96)
    if _normalized_date(previous_visa_date):
        add("history.previousVisaIssueDate", _normalized_date(previous_visa_date), previous_visa_date, confidence=0.96)

    deduplicated = {}
    for field in fields:
        current = deduplicated.get(field["id"]) if field else None
        if field and (not current or field.get("confidence", 0) > current.get("confidence", 0)):
            deduplicated[field["id"]] = field
    semantic_added = 0
    for candidate in semantic_candidates:
        field_id = candidate["fieldId"]
        if field_id in deduplicated:
            continue
        raw_value = candidate["value"]
        if field_id.endswith("Date") or field_id in {"passport.expiration", "travel.arrivalDate"}:
            raw_value = _normalized_date(raw_value) or raw_value
        if field_id in {
            "personal.nationalId", "passport.number", "education.sevisId",
            "education.programNumber", "history.previousVisaNumber",
        }:
            raw_value = re.sub(r"\s+", "", raw_value).upper()
        semantic_field = _known_field(
            field_id,
            raw_value,
            candidate["evidence"],
            provider="ollama_structured",
            confidence=0.78,
            translated_value=candidate.get("englishValue") or "",
            force_review=True,
            allow_model_translation=False,
        )
        if semantic_field:
            semantic_field["extractionMethod"] = "consultant_text_semantic"
            semantic_field["reviewReason"] = "本地语义模型从顾问原文补充识别，已保留原文证据"
            deduplicated[field_id] = semantic_field
            semantic_added += 1
    warnings = []
    if semantic_status == "ollama_unavailable":
        warnings.append("本地语义识别未启动，本次已使用增强规则完成识别")
    recognized_entries = [
        {
            "number": pair.get("number"),
            "question": pair.get("question") or "",
            "answer": pair.get("answer") or "",
            "mappedQuestionIds": list(pair.get("mappedQuestionIds") or []),
            "mappedFieldIds": list(pair.get("mappedFieldIds") or []),
            "matched": bool(
                pair.get("mappedQuestionIds") or pair.get("mappedFieldIds")
            ),
        }
        for pair in qa_pairs if str(pair.get("answer") or "").strip()
    ]
    return {
        "fields": list(deduplicated.values()),
        "address": {"home": parsed_home, "us": parsed_us},
        "unparsed": text,
        "analysisProviders": [
            "deterministic_rules",
            *(["numbered_qa_parser"] if qa_pairs else []),
            *(["schema_question_matcher"] if schema_matched_count else []),
            *(["ollama_structured"] if semantic_added else []),
        ],
        "semanticAddedCount": semantic_added,
        "questionnaireUpdates": questionnaire_updates,
        "qaPairCount": len(qa_pairs),
        "answeredQaCount": len(recognized_entries),
        "matchedQaCount": sum(1 for item in recognized_entries if item["matched"]),
        "recognizedEntries": recognized_entries,
        "warnings": warnings,
    }
