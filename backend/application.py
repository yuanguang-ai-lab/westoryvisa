#!/usr/bin/env python3
import atexit
import json
import hashlib
import hmac
import html
import mimetypes
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse
from urllib.request import Request, urlopen

from .docling_client import DoclingError, check_docling
from .ocr_provider import (
    convert_file,
    selected_provider,
    service_status as provider_service_status,
)
from .browser_use_bridge import (
    CEAC_START_URL,
    CEAC_TRAVEL_URL,
    build_browser_workflow,
    build_travel_actions,
)
from .appointment_bridge import (
    APPOINTMENT_ALLOWED_DOMAIN,
    APPOINTMENT_START_URL,
    appointment_preflight_issues,
    build_appointment_workflow,
)
from .ds160_mapper import map_document, merge_extracted_fields
from .ds160_intake_schema import CLIENT_INTAKE_FIELDS, INTAKE_SCHEMA_VERSION
from .ds160_language import (
    LANGUAGE_SCHEMA_VERSION,
    contains_cjk,
    normalize_does_not_apply,
    parse_consultant_information,
    structure_address,
    translation_service_status,
    translate_ds160_value,
)
from .ds160_value_validation import (
    canonicalize_ds160_value,
    field_value_is_usable,
)
from .email_service import EmailDeliveryError, mail_service_status, sendEmail
from .env_config import load_env_file
from .school_directory import (
    enrich_education_updates,
    enrich_questionnaire_education,
)
from .status_page import render_status_page
from .ds160_rules import (
    RULESET_VERSION,
    active_detail_fields,
    active_record_fields,
    build_questionnaire,
    dependent_field_visible,
    details_complete,
    infer_questionnaire_answers,
    questionnaire_issues,
    sync_questionnaire_fields,
)
from .config import (
    DB_PATH,
    DATA_DIR,
    PROJECT_ROOT,
    UPLOAD_DIR,
    allowed_origins,
    bind_host,
    bind_port,
    cookie_domain,
    cookie_same_site,
    cookie_secure,
)


ROOT = PROJECT_ROOT
AUTH_COOKIE = "docflow_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30
PASSWORD_ITERATIONS = 240000
EMAIL_CODE_TTL_MINUTES = 10
EMAIL_CODE_RESEND_SECONDS = 60
EMAIL_CODE_MAX_ATTEMPTS = 5
EMAIL_CODE_MAX_PER_HOUR = 5
INTAKE_LINK_TTL_DAYS = 30
MAX_UPLOAD_SIZE = 25 * 1024 * 1024
ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}
ACTIVE_SCANS = set()
ACTIVE_SCANS_LOCK = threading.Lock()
DOCLING_PROCESS = None
DOCLING_LOG_HANDLE = None
DOCLING_PROCESS_LOCK = threading.Lock()
SCREEN_AGENT_PROCESSES = {}
SCREEN_AGENT_PROCESS_LOCK = threading.Lock()
SCREEN_AGENT_ALLOWED_FIELDS = (
    ("personal.surname", "Surname", False),
    ("personal.givenNames", "Given Names", False),
    ("personal.dateOfBirth", "Date of Birth", False),
    ("personal.placeOfBirth", "Place of Birth", False),
    ("passport.number", "Passport Number", False),
    ("passport.issueDate", "Passport Issue Date", False),
    ("passport.expiration", "Passport Expiration Date", False),
    ("travel.visaType", "Purpose of Trip", False),
    ("travel.arrivalDate", "Intended Date of Arrival", False),
    ("contact.usAddress", "Address Where You Will Stay", False),
    ("contact.organizationName", "U.S. Contact Organization", False),
    ("contact.phone", "U.S. Contact Phone", False),
    ("work.employerName", "Present Employer or School", False),
    ("education.schoolName", "School Name", True),
    ("education.sevisId", "SEVIS ID", True),
    ("education.programNumber", "Program Number", True),
)
OPEN_COWORK_DEMO_VALUES = {
    "personal.surname": "Example",
    "personal.givenNames": "Alex",
    "personal.dateOfBirth": "1990-01-15",
    "personal.placeOfBirth": "Sample City",
    "passport.number": "DEMO123456",
    "passport.issueDate": "2024-01-15",
    "passport.expiration": "2034-01-14",
    "travel.arrivalDate": "2027-05-10",
    "contact.usAddress": "100 Example Avenue, Sample City",
    "contact.organizationName": "Sample Training Center",
    "contact.phone": "+1 202-555-0142",
    "work.employerName": "Example Learning Studio",
    "education.schoolName": "Example Learning University",
    "education.sevisId": "DEMO-SEVIS-001",
    "education.programNumber": "DEMO-PROGRAM-01",
}
OPEN_COWORK_JOB_TTL_MINUTES = 15
CODEX_AGENT_JOB_TTL_MINUTES = 60
CODEX_AGENT_MAX_FETCHES = 12
CODEX_AGENT_EXECUTORS = {"codex-computer-use"}
COMPUTER_USE_INTERACTION_POLICY = {
    "mode": "deliberate",
    "betweenActionsMs": {"min": 900, "max": 1500},
    "afterDynamicSelectionMs": 2000,
    "afterNavigationMs": 2800,
    "maxActionsBeforeReinspect": 1,
    "reinspectAfterEveryAction": True,
    "reinspectAfterDynamicSelection": True,
    "verifyVisibleValueAfterEveryAction": True,
    "maxRetriesPerAction": 1,
}
DEFAULT_PRACTICE_LAB_AGENT_URL = (
    "http://127.0.0.1:4188/screen-agent-import.html"
)
BROWSER_USE_PYTHON = ROOT / ".venv-browser-use" / "bin" / "python"
BROWSER_USE_TRAVEL_WORKER = ROOT / "backend" / "workers" / "browser_use_travel_worker.py"
CHROME_EXECUTABLE = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
PRODUCT_ANALYTICS_CONSENT_VERSION = "anonymous-product-analytics-v1"
PRODUCT_EVENT_TYPES = {
    "page_view", "click", "section_view", "dwell", "wjx_open", "wjx_close",
}
BILLING_PROVIDER = "stripe"
BILLING_PRODUCTS = (
    {
        "id": "membership-monthly",
        "name": "月度会员",
        "description": "WestoryVisa 机构工作台 30 天使用权",
        "amount": 19900,
        "currency": "cny",
        "durationDays": 30,
    },
    {
        "id": "membership-yearly",
        "name": "年度会员",
        "description": "WestoryVisa 机构工作台 365 天使用权",
        "amount": 199000,
        "currency": "cny",
        "durationDays": 365,
    },
)

for env_name, env_value in load_env_file().items():
    os.environ.setdefault(env_name, env_value)


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def bounded_json(value, max_characters=10_000_000):
    serialized = json.dumps(value, ensure_ascii=False)
    if len(serialized) <= max_characters:
        return serialized
    return json.dumps({
        "truncated": True,
        "originalCharacters": len(serialized),
        "preview": serialized[: min(200_000, max_characters // 2)],
    }, ensure_ascii=False)


def clean_product_analytics_text(value, limit=240):
    cleaned = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or ""))
    return re.sub(r"\s+", " ", cleaned).strip()[:limit]


def validate_wjx_survey_url(value):
    url = clean_product_analytics_text(value, 2000)
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("问卷链接必须是完整的 HTTPS 地址")
    return url


def get_site_setting(setting_key):
    with connect() as conn:
        row = conn.execute(
            "SELECT setting_value FROM site_settings WHERE setting_key = ?",
            (setting_key,),
        ).fetchone()
    return row["setting_value"] if row else ""


def save_site_setting(setting_key, setting_value, user):
    stamped = now_iso()
    with connect() as conn:
        conn.execute(
            "INSERT INTO site_settings "
            "(setting_key, setting_value, updated_by_user_id, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(setting_key) DO UPDATE SET "
            "setting_value = excluded.setting_value, "
            "updated_by_user_id = excluded.updated_by_user_id, "
            "updated_at = excluded.updated_at",
            (setting_key, setting_value, user.get("id"), stamped),
        )


def product_public_config():
    configured_url = get_site_setting("wjx_survey_url")
    if not configured_url:
        configured_url = os.environ.get("WJX_SURVEY_URL", "").strip()
    try:
        configured_url = validate_wjx_survey_url(configured_url)
    except ValueError:
        configured_url = ""
    return {
        "wjxSurveyUrl": configured_url,
        "wjxConfigured": bool(configured_url),
        "analyticsConsentVersion": PRODUCT_ANALYTICS_CONSENT_VERSION,
        "analyticsMode": "anonymous_opt_in",
    }


def update_product_settings(payload, user):
    if not isinstance(payload, dict):
        raise ValueError("设置格式不正确")
    survey_url = validate_wjx_survey_url(payload.get("wjxSurveyUrl"))
    save_site_setting("wjx_survey_url", survey_url, user)
    return product_public_config()


def record_product_analytics_event(payload):
    if not isinstance(payload, dict):
        raise ValueError("事件格式不正确")
    session_id = clean_product_analytics_text(payload.get("sessionId"), 80)
    event_id = clean_product_analytics_text(payload.get("eventId"), 100)
    event_type = clean_product_analytics_text(payload.get("eventType"), 40)
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", session_id):
        raise ValueError("匿名访问会话无效")
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,100}", event_id):
        raise ValueError("访问事件编号无效")
    if event_type not in PRODUCT_EVENT_TYPES:
        raise ValueError("不支持的访问事件")

    page_path = clean_product_analytics_text(payload.get("pagePath"), 300)
    if page_path and not page_path.startswith("/"):
        page_path = f"/{page_path}"
    target = clean_product_analytics_text(payload.get("target"), 240)
    section_name = clean_product_analytics_text(payload.get("section"), 160)
    referrer_host = clean_product_analytics_text(payload.get("referrerHost"), 180)
    utm_source = clean_product_analytics_text(payload.get("utmSource"), 120)
    utm_medium = clean_product_analytics_text(payload.get("utmMedium"), 120)
    utm_campaign = clean_product_analytics_text(payload.get("utmCampaign"), 160)
    device_type = clean_product_analytics_text(payload.get("deviceType"), 30)
    locale = clean_product_analytics_text(payload.get("locale"), 40)
    consent_version = clean_product_analytics_text(payload.get("consentVersion"), 80)
    if consent_version != PRODUCT_ANALYTICS_CONSENT_VERSION:
        raise ValueError("匿名访问统计授权无效")
    active_ms = 0
    if event_type == "dwell":
        try:
            active_ms = max(0, min(int(payload.get("activeMs") or 0), 30 * 60 * 1000))
        except (TypeError, ValueError):
            active_ms = 0
    stamped = now_iso()
    minute_ago = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    retention_cutoff = (datetime.now(timezone.utc) - timedelta(days=180)).isoformat()

    with connect() as conn:
        recent_count = conn.execute(
            "SELECT COUNT(*) AS count FROM product_analytics_events "
            "WHERE session_id = ? AND created_at >= ?",
            (session_id, minute_ago),
        ).fetchone()["count"]
        if recent_count >= 180:
            raise ValueError("访问事件过于频繁")
        conn.execute(
            "INSERT OR IGNORE INTO product_visitor_sessions "
            "(id, first_seen_at, last_seen_at, active_ms, page_views, landing_path, "
            "last_path, referrer_host, utm_source, utm_medium, utm_campaign, "
            "device_type, locale, consent_version, converted_wjx) "
            "VALUES (?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
            (
                session_id, stamped, stamped, page_path, page_path, referrer_host,
                utm_source, utm_medium, utm_campaign, device_type, locale,
                consent_version,
            ),
        )
        inserted = conn.execute(
            "INSERT OR IGNORE INTO product_analytics_events "
            "(client_event_id, session_id, event_type, page_path, target, "
            "section_name, active_ms, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event_id, session_id, event_type, page_path, target,
                section_name, active_ms, stamped,
            ),
        ).rowcount
        if inserted:
            conn.execute(
                "UPDATE product_visitor_sessions SET "
                "last_seen_at = ?, active_ms = active_ms + ?, "
                "page_views = page_views + ?, last_path = CASE WHEN ? <> '' THEN ? ELSE last_path END, "
                "referrer_host = CASE WHEN COALESCE(referrer_host, '') = '' THEN ? ELSE referrer_host END, "
                "utm_source = CASE WHEN COALESCE(utm_source, '') = '' THEN ? ELSE utm_source END, "
                "utm_medium = CASE WHEN COALESCE(utm_medium, '') = '' THEN ? ELSE utm_medium END, "
                "utm_campaign = CASE WHEN COALESCE(utm_campaign, '') = '' THEN ? ELSE utm_campaign END, "
                "device_type = CASE WHEN COALESCE(device_type, '') = '' THEN ? ELSE device_type END, "
                "locale = CASE WHEN COALESCE(locale, '') = '' THEN ? ELSE locale END, "
                "converted_wjx = MAX(converted_wjx, ?) WHERE id = ?",
                (
                    stamped, active_ms, 1 if event_type == "page_view" else 0,
                    page_path, page_path, referrer_host, utm_source, utm_medium,
                    utm_campaign, device_type, locale,
                    1 if event_type == "wjx_open" else 0, session_id,
                ),
            )
        conn.execute(
            "DELETE FROM product_analytics_events WHERE created_at < ?",
            (retention_cutoff,),
        )
        conn.execute(
            "DELETE FROM product_visitor_sessions WHERE last_seen_at < ?",
            (retention_cutoff,),
        )
    return {"accepted": bool(inserted)}


def product_analytics_summary(days=30):
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 30
    days = min(max(days, 1), 365)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with connect() as conn:
        totals = conn.execute(
            "SELECT COUNT(*) AS visitors, COALESCE(SUM(page_views), 0) AS page_views, "
            "COALESCE(AVG(CASE WHEN active_ms > 0 THEN active_ms END), 0) AS average_active_ms, "
            "COALESCE(SUM(converted_wjx), 0) AS wjx_visitors "
            "FROM product_visitor_sessions WHERE last_seen_at >= ?",
            (since,),
        ).fetchone()
        trend = conn.execute(
            "SELECT substr(created_at, 1, 10) AS day, "
            "COUNT(DISTINCT session_id) AS visitors, "
            "SUM(CASE WHEN event_type = 'page_view' THEN 1 ELSE 0 END) AS page_views "
            "FROM product_analytics_events WHERE created_at >= ? "
            "GROUP BY substr(created_at, 1, 10) ORDER BY day",
            (since,),
        ).fetchall()
        clicks = conn.execute(
            "SELECT target, COUNT(*) AS count FROM product_analytics_events "
            "WHERE created_at >= ? AND event_type = 'click' "
            "AND COALESCE(target, '') <> '' GROUP BY target ORDER BY count DESC LIMIT 12",
            (since,),
        ).fetchall()
        sections = conn.execute(
            "SELECT section_name, COUNT(DISTINCT session_id) AS visitors "
            "FROM product_analytics_events WHERE created_at >= ? "
            "AND event_type = 'section_view' AND COALESCE(section_name, '') <> '' "
            "GROUP BY section_name ORDER BY visitors DESC LIMIT 12",
            (since,),
        ).fetchall()
        devices = conn.execute(
            "SELECT COALESCE(NULLIF(device_type, ''), 'unknown') AS device, COUNT(*) AS visitors "
            "FROM product_visitor_sessions WHERE last_seen_at >= ? "
            "GROUP BY device ORDER BY visitors DESC",
            (since,),
        ).fetchall()
        recent = conn.execute(
            "SELECT id, first_seen_at, last_seen_at, active_ms, page_views, "
            "landing_path, last_path, referrer_host, utm_source, utm_campaign, "
            "device_type, locale, converted_wjx "
            "FROM product_visitor_sessions WHERE last_seen_at >= ? "
            "ORDER BY last_seen_at DESC LIMIT 50",
            (since,),
        ).fetchall()
    visitors = int(totals["visitors"] or 0)
    wjx_visitors = int(totals["wjx_visitors"] or 0)
    return {
        "days": days,
        "totals": {
            "visitors": visitors,
            "pageViews": int(totals["page_views"] or 0),
            "averageActiveMs": int(totals["average_active_ms"] or 0),
            "wjxVisitors": wjx_visitors,
            "wjxConversionRate": round((wjx_visitors / visitors * 100), 1) if visitors else 0,
        },
        "trend": [dict(row) for row in trend],
        "clicks": [dict(row) for row in clicks],
        "sections": [dict(row) for row in sections],
        "devices": [dict(row) for row in devices],
        "recentSessions": [dict(row) for row in recent],
        "privacy": {
            "storesRawIp": False,
            "storesFormValues": False,
            "retentionDays": 180,
        },
    }


def product_analytics_session(session_id):
    session_id = clean_product_analytics_text(session_id, 80)
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", session_id):
        raise ValueError("匿名访问会话无效")
    with connect() as conn:
        session = conn.execute(
            "SELECT * FROM product_visitor_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not session:
            raise FileNotFoundError("没有找到这次访问")
        events = conn.execute(
            "SELECT event_type, page_path, target, section_name, active_ms, created_at "
            "FROM product_analytics_events WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        ).fetchall()
    return {"session": dict(session), "events": [dict(row) for row in events]}


def connect():
    DATA_DIR.mkdir(mode=0o700, exist_ok=True)
    os.chmod(DATA_DIR, 0o700)
    conn = sqlite3.connect(DB_PATH, factory=ClosingConnection)
    os.chmod(DB_PATH, 0o600)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA secure_delete = ON")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS organizations (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  organization_id TEXT REFERENCES organizations(id) ON DELETE SET NULL,
  name TEXT NOT NULL,
  email TEXT UNIQUE,
  phone TEXT,
  password_hash TEXT,
  password_salt TEXT,
  password_iterations INTEGER NOT NULL DEFAULT 240000,
  user_key TEXT,
  role TEXT,
  email_verified_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS email_verifications (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL,
  purpose TEXT NOT NULL,
  code_hash TEXT NOT NULL,
  code_salt TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  send_status TEXT NOT NULL DEFAULT 'sent',
  provider TEXT,
  failure_reason TEXT,
  consumed_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clients (
  id TEXT PRIMARY KEY,
  organization_id TEXT REFERENCES organizations(id) ON DELETE SET NULL,
  created_by_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  record_key TEXT,
  full_name TEXT NOT NULL,
  passport_number TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ds160_cases (
  id TEXT PRIMARY KEY,
  client_id TEXT REFERENCES clients(id) ON DELETE CASCADE,
  organization_id TEXT REFERENCES organizations(id) ON DELETE SET NULL,
  owner_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  owner_name TEXT,
  visa_type TEXT NOT NULL,
  status TEXT NOT NULL,
  current_step INTEGER NOT NULL DEFAULT 0,
  source_type TEXT,
  review_priority TEXT,
  notes TEXT,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL REFERENCES ds160_cases(id) ON DELETE CASCADE,
  slot TEXT NOT NULL,
  file_name TEXT,
  stored_path TEXT,
  mime_type TEXT,
  file_size INTEGER,
  sha256 TEXT,
  scan_status TEXT NOT NULL DEFAULT 'empty',
  scan_message TEXT,
  ocr_text TEXT,
  ocr_json TEXT,
  parser_name TEXT,
  parser_version TEXT,
  processed_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ds160_fields (
  id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL REFERENCES ds160_cases(id) ON DELETE CASCADE,
  field_key TEXT NOT NULL,
  section TEXT NOT NULL,
  label TEXT NOT NULL,
  value TEXT,
  source_document TEXT,
  source_document_id TEXT,
  source_page INTEGER,
  evidence_text TEXT,
  extraction_method TEXT,
  confidence REAL,
  risk_level TEXT,
  status TEXT NOT NULL,
  requires_user_confirmation INTEGER NOT NULL DEFAULT 0,
  confirmed INTEGER NOT NULL DEFAULT 0,
  edited_by_user INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS field_evidence (
  id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL REFERENCES ds160_cases(id) ON DELETE CASCADE,
  field_key TEXT NOT NULL,
  document_id TEXT REFERENCES documents(id) ON DELETE SET NULL,
  page_number INTEGER,
  evidence_text TEXT,
  confidence REAL,
  extraction_method TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ds160_answers (
  id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL REFERENCES ds160_cases(id) ON DELETE CASCADE,
  question_id TEXT NOT NULL,
  section TEXT NOT NULL,
  label TEXT NOT NULL,
  answer_value TEXT,
  details_json TEXT NOT NULL,
  status TEXT NOT NULL,
  source TEXT,
  sensitive INTEGER NOT NULL DEFAULT 0,
  confirmed_by_user INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_issues (
  id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL REFERENCES ds160_cases(id) ON DELETE CASCADE,
  issue_type TEXT NOT NULL,
  severity TEXT NOT NULL,
  category TEXT NOT NULL,
  message TEXT NOT NULL,
  requires_user_resolution INTEGER NOT NULL DEFAULT 0,
  resolved INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  case_id TEXT REFERENCES ds160_cases(id) ON DELETE CASCADE,
  actor TEXT,
  action TEXT NOT NULL,
  payload_json TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_session (
  id TEXT PRIMARY KEY,
  payload_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_sessions (
  token_hash TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS intake_links (
  id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL REFERENCES ds160_cases(id) ON DELETE CASCADE,
  organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  token_hash TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'pending',
  expires_at TEXT NOT NULL,
  respondent_name TEXT,
  identity_match INTEGER,
  draft_json TEXT,
  draft_updated_at TEXT,
  submitted_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS site_settings (
  setting_key TEXT PRIMARY KEY,
  setting_value TEXT NOT NULL,
  updated_by_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS product_visitor_sessions (
  id TEXT PRIMARY KEY,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  active_ms INTEGER NOT NULL DEFAULT 0,
  page_views INTEGER NOT NULL DEFAULT 0,
  landing_path TEXT,
  last_path TEXT,
  referrer_host TEXT,
  utm_source TEXT,
  utm_medium TEXT,
  utm_campaign TEXT,
  device_type TEXT,
  locale TEXT,
  consent_version TEXT,
  converted_wjx INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS product_analytics_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  client_event_id TEXT NOT NULL UNIQUE,
  session_id TEXT NOT NULL REFERENCES product_visitor_sessions(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,
  page_path TEXT,
  target TEXT,
  section_name TEXT,
  active_ms INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS billing_products (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT,
  amount INTEGER NOT NULL,
  currency TEXT NOT NULL,
  duration_days INTEGER NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS billing_orders (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  product_id TEXT NOT NULL REFERENCES billing_products(id),
  amount INTEGER NOT NULL,
  currency TEXT NOT NULL,
  status TEXT NOT NULL,
  provider TEXT NOT NULL,
  provider_checkout_id TEXT,
  provider_payment_id TEXT,
  checkout_url TEXT,
  paid_at TEXT,
  expires_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS payment_transactions (
  id TEXT PRIMARY KEY,
  order_id TEXT NOT NULL REFERENCES billing_orders(id) ON DELETE CASCADE,
  organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  provider TEXT NOT NULL,
  provider_transaction_id TEXT,
  transaction_type TEXT NOT NULL,
  amount INTEGER NOT NULL,
  currency TEXT NOT NULL,
  status TEXT NOT NULL,
  payload_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS billing_refunds (
  id TEXT PRIMARY KEY,
  order_id TEXT NOT NULL REFERENCES billing_orders(id) ON DELETE CASCADE,
  organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  requested_by_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  provider_refund_id TEXT,
  amount INTEGER NOT NULL,
  currency TEXT NOT NULL,
  status TEXT NOT NULL,
  reason TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS billing_subscriptions (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL UNIQUE REFERENCES organizations(id) ON DELETE CASCADE,
  product_id TEXT REFERENCES billing_products(id),
  source_order_id TEXT REFERENCES billing_orders(id) ON DELETE SET NULL,
  status TEXT NOT NULL,
  starts_at TEXT NOT NULL,
  current_period_end TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS billing_webhook_events (
  provider_event_id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  event_type TEXT NOT NULL,
  status TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  processed_at TEXT NOT NULL
);
"""


def init_db():
    with connect() as conn:
        conn.executescript(SCHEMA)
        ensure_columns(conn, "users", {
            "email": "TEXT",
            "phone": "TEXT",
            "password_hash": "TEXT",
            "password_salt": "TEXT",
            "password_iterations": "INTEGER NOT NULL DEFAULT 120000",
            "user_key": "TEXT",
            "email_verified_at": "TEXT",
        })
        ensure_columns(conn, "clients", {
            "created_by_user_id": "TEXT",
            "record_key": "TEXT",
        })
        ensure_columns(conn, "email_verifications", {
            "send_status": "TEXT NOT NULL DEFAULT 'sent'",
            "provider": "TEXT",
            "failure_reason": "TEXT",
        })
        ensure_columns(conn, "ds160_cases", {
            "owner_user_id": "TEXT",
        })
        ensure_columns(conn, "documents", {
            "stored_path": "TEXT",
            "mime_type": "TEXT",
            "file_size": "INTEGER",
            "sha256": "TEXT",
            "scan_status": "TEXT NOT NULL DEFAULT 'empty'",
            "scan_message": "TEXT",
            "ocr_text": "TEXT",
            "ocr_json": "TEXT",
            "parser_name": "TEXT",
            "parser_version": "TEXT",
            "processed_at": "TEXT",
        })
        ensure_columns(conn, "ds160_fields", {
            "source_document_id": "TEXT",
            "source_page": "INTEGER",
            "evidence_text": "TEXT",
            "extraction_method": "TEXT",
        })
        ensure_columns(conn, "intake_links", {
            "respondent_name": "TEXT",
            "identity_match": "INTEGER",
            "draft_json": "TEXT",
            "draft_updated_at": "TEXT",
        })
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_lower "
            "ON users(lower(email)) WHERE email IS NOT NULL AND trim(email) <> ''"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_user_key "
            "ON users(user_key) WHERE user_key IS NOT NULL"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_clients_record_key "
            "ON clients(record_key) WHERE record_key IS NOT NULL"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_auth_sessions_user_id ON auth_sessions(user_id)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_email_verifications_email_created "
            "ON email_verifications(email, purpose, created_at DESC)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_email_verifications_sending "
            "ON email_verifications(email, purpose) WHERE send_status = 'sending'"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cases_organization_id ON ds160_cases(organization_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_case_id ON documents(case_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_field_evidence_case_id ON field_evidence(case_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_field_evidence_document_id ON field_evidence(document_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ds160_answers_case_id ON ds160_answers(case_id)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_ds160_answers_case_question ON ds160_answers(case_id, question_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_intake_links_case_id ON intake_links(case_id, created_at DESC)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_intake_links_token_hash ON intake_links(token_hash)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_product_sessions_last_seen "
            "ON product_visitor_sessions(last_seen_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_product_events_created "
            "ON product_analytics_events(created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_product_events_session "
            "ON product_analytics_events(session_id, created_at)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_billing_orders_org_created ON billing_orders(organization_id, created_at DESC)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_billing_orders_checkout ON billing_orders(provider_checkout_id) WHERE provider_checkout_id IS NOT NULL")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_payment_transactions_order ON payment_transactions(order_id, created_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_billing_refunds_order ON billing_refunds(order_id, created_at DESC)")
        stamped = now_iso()
        for product in BILLING_PRODUCTS:
            conn.execute(
                """
                INSERT INTO billing_products (
                  id, name, description, amount, currency, duration_days,
                  active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  name = excluded.name, description = excluded.description,
                  amount = excluded.amount, currency = excluded.currency,
                  duration_days = excluded.duration_days, active = 1,
                  updated_at = excluded.updated_at
                """,
                (
                    product["id"], product["name"], product["description"],
                    product["amount"], product["currency"], product["durationDays"],
                    stamped, stamped,
                ),
            )
        backfill_record_keys(conn)
        backfill_questionnaires(conn)


def ensure_columns(conn, table, columns):
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for column, definition in columns.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def backfill_record_keys(conn):
    for row in conn.execute("SELECT id FROM users WHERE user_key IS NULL OR trim(user_key) = ''").fetchall():
        conn.execute("UPDATE users SET user_key = ? WHERE id = ?", (secrets.token_urlsafe(32), row["id"]))
    for row in conn.execute("SELECT id FROM clients WHERE record_key IS NULL OR trim(record_key) = ''").fetchall():
        conn.execute("UPDATE clients SET record_key = ? WHERE id = ?", (secrets.token_urlsafe(32), row["id"]))


def backfill_questionnaires(conn):
    stamped = now_iso()
    rows = conn.execute("SELECT id, visa_type, payload_json FROM ds160_cases").fetchall()
    for row in rows:
        payload = json.loads(row["payload_json"])
        answer_count = conn.execute(
            "SELECT COUNT(*) AS count FROM ds160_answers WHERE case_id = ?",
            (row["id"],),
        ).fetchone()["count"]
        expected_review_rows = {
            (f"{row['id']}-{item.get('id')}", item.get("message") or "")
            for item in (payload.get("validationResults") or [])
        }
        current_review_rows = {
            (item["id"], item["message"] or "")
            for item in conn.execute(
                "SELECT id, message FROM review_issues WHERE case_id = ?",
                (row["id"],),
            ).fetchall()
        }
        if (
            payload.get("branchQuestionnaireVersion") == RULESET_VERSION
            and payload.get("languageSchemaVersion") == LANGUAGE_SCHEMA_VERSION
            and answer_count
            and current_review_rows == expected_review_rows
        ):
            continue
        payload["extractedFields"] = normalize_extracted_fields_language(
            payload.get("extractedFields") or []
        )
        questionnaire = build_questionnaire(
            payload.get("visaType") or row["visa_type"],
            payload.get("branchQuestionnaire"),
            payload.get("extractedFields"),
        )
        questionnaire = normalize_questionnaire_language(questionnaire)
        questionnaire, _ = enrich_questionnaire_education(questionnaire)
        existing_validation = payload.get("validationResults") or []
        payload["branchQuestionnaire"] = questionnaire
        payload["extractedFields"] = normalize_extracted_fields_language(
            sync_questionnaire_fields(payload.get("extractedFields"), questionnaire)
        )
        payload["branchQuestionnaireVersion"] = RULESET_VERSION
        payload["intakeSchemaVersion"] = INTAKE_SCHEMA_VERSION
        payload["languageSchemaVersion"] = LANGUAGE_SCHEMA_VERSION
        selected_visa = str(payload.get("visaType") or row["visa_type"] or "").strip().upper()
        if selected_visa.startswith("B"):
            payload["prefillLog"] = [
                item for item in (payload.get("prefillLog") or [])
                if "SEVIS" not in str(item)
            ]
        payload["validationResults"] = [
            item for item in existing_validation
            if not str(item.get("id", "")).startswith("branch.")
            and item.get("id") != "sensitive.refusal"
        ] + questionnaire_issues(questionnaire, existing_validation)
        conn.execute(
            "UPDATE ds160_cases SET payload_json = ? WHERE id = ?",
            (json.dumps(payload, ensure_ascii=False), row["id"]),
        )
        replace_questionnaire_rows(conn, row["id"], questionnaire, stamped)
        replace_review_issue_rows(conn, row["id"], payload["validationResults"], stamped)


def replace_questionnaire_rows(conn, case_id, questionnaire, stamped):
    conn.execute("DELETE FROM ds160_answers WHERE case_id = ?", (case_id,))
    for item in questionnaire or []:
        conn.execute(
            """
            INSERT INTO ds160_answers (
              id, case_id, question_id, section, label, answer_value,
              details_json, status, source, sensitive, confirmed_by_user,
              created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"{case_id}-{item.get('id')}", case_id, item.get("id") or "",
                item.get("section") or "", item.get("label") or "",
                item.get("answer") or "",
                json.dumps({
                    "details": item.get("details") or {},
                    "records": item.get("records") or [],
                    "clientResponse": item.get("clientResponse") or "",
                    "clientSubmitted": bool(item.get("clientSubmitted")),
                    "visible": bool(item.get("visible", True)),
                }, ensure_ascii=False),
                item.get("status") or "待客户确认", item.get("source") or "客户确认",
                1 if item.get("sensitive") else 0,
                1 if item.get("confirmedByUser") else 0,
                stamped, stamped,
            ),
        )


def replace_review_issue_rows(conn, case_id, validation_results, stamped):
    conn.execute("DELETE FROM review_issues WHERE case_id = ?", (case_id,))
    for issue in validation_results or []:
        conn.execute(
            """
            INSERT INTO review_issues (
              id, case_id, issue_type, severity, category, message,
              requires_user_resolution, resolved, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"{case_id}-{issue.get('id')}", case_id, issue.get("type") or "",
                issue.get("severity") or "", issue.get("category") or "",
                issue.get("message") or "", 1 if issue.get("requiresUserResolution") else 0,
                1 if issue.get("resolved") else 0, stamped, stamped,
            ),
        )


def normalize_org_name(value):
    return str(value or "").strip().lower()


class EmailRateLimitError(ValueError):
    def __init__(self, message, retry_after=EMAIL_CODE_RESEND_SECONDS):
        super().__init__(message)
        self.retry_after = max(1, int(retry_after))


def registration_verification_mode():
    mode = os.environ.get("REGISTRATION_VERIFICATION", "none").strip().lower()
    return mode if mode in {"none", "email"} else "none"


def validate_email_address(email):
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise ValueError("请输入有效的邮箱地址")


def email_verification_secret():
    secret_path = DATA_DIR / "email_verification_secret"
    DATA_DIR.mkdir(mode=0o700, exist_ok=True)
    if not secret_path.exists() or not secret_path.read_text(encoding="utf-8").strip():
        secret_path.write_text(secrets.token_hex(32), encoding="utf-8")
    os.chmod(secret_path, 0o600)
    return secret_path.read_text(encoding="utf-8").strip().encode("utf-8")


def email_code_digest(email, code, salt):
    message = f"register|{normalize_org_name(email)}|{salt}|{code}".encode("utf-8")
    return hmac.new(email_verification_secret(), message, hashlib.sha256).hexdigest()


def verification_email_content(code):
    safe_code = html.escape(code)
    subject = "WestoryVisa 注册邮箱验证码"
    text_content = (
        "你正在注册 WestoryVisa 机构工作台。\n\n"
        f"验证码：{code}\n"
        f"验证码将在 {EMAIL_CODE_TTL_MINUTES} 分钟后失效。\n\n"
        "如果这不是你的操作，请忽略这封邮件。"
    )
    html_content = f"""\
<!doctype html>
<html lang="zh-CN">
  <body style="margin:0;background:#f6f5f2;color:#171717;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC',sans-serif;">
    <div style="max-width:520px;margin:0 auto;padding:40px 24px;">
      <div style="background:#ffffff;border:1px solid rgba(0,0,0,.08);border-radius:20px;padding:32px;">
        <div style="font-size:13px;color:#6b6b67;margin-bottom:22px;">WestoryVisa</div>
        <h1 style="font-size:22px;font-weight:600;margin:0 0 12px;">注册邮箱验证</h1>
        <p style="font-size:15px;line-height:1.75;margin:0;color:#52524f;">请输入以下六位验证码完成机构账号注册。验证码在 {EMAIL_CODE_TTL_MINUTES} 分钟内有效。</p>
        <div style="font-size:34px;font-weight:650;letter-spacing:8px;margin:28px 0;color:#111111;">{safe_code}</div>
        <p style="font-size:13px;line-height:1.7;margin:0;color:#777773;">如果这不是你的操作，请忽略这封邮件。</p>
      </div>
    </div>
  </body>
</html>"""
    return subject, text_content, html_content


def request_email_verification(payload):
    if registration_verification_mode() != "email":
        raise ValueError("邮箱验证码当前未启用")
    email = normalize_org_name(payload.get("email"))
    validate_email_address(email)
    service = mail_service_status()
    if not service["configured"]:
        raise EmailDeliveryError(service["message"])

    now = datetime.now(timezone.utc)
    hour_ago = (now - timedelta(hours=1)).isoformat()
    stamped = now.isoformat()
    verification_id = f"email-code-{secrets.token_hex(16)}"
    code = f"{secrets.randbelow(1_000_000):06d}"
    salt = secrets.token_hex(16)
    digest = email_code_digest(email, code, salt)

    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
            return {
                "ok": True,
                "message": "如果该邮箱可用于注册，验证码邮件将很快送达",
                "expiresIn": EMAIL_CODE_TTL_MINUTES * 60,
                "retryAfter": EMAIL_CODE_RESEND_SECONDS,
                "deliveryMode": service["provider"],
            }
        conn.execute(
            "DELETE FROM email_verifications WHERE created_at < ?",
            ((now - timedelta(days=1)).isoformat(),),
        )
        conn.execute(
            """
            UPDATE email_verifications
            SET send_status = 'failed', failure_reason = 'send_timeout', updated_at = ?
            WHERE email = ? AND purpose = 'register' AND send_status = 'sending'
              AND created_at < ?
            """,
            (stamped, email, (now - timedelta(minutes=2)).isoformat()),
        )
        latest = conn.execute(
            """
            SELECT created_at
            FROM email_verifications
            WHERE email = ? AND purpose = 'register'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (email,),
        ).fetchone()
        if latest:
            elapsed = (now - datetime.fromisoformat(latest["created_at"])).total_seconds()
            if elapsed < EMAIL_CODE_RESEND_SECONDS:
                retry_after = EMAIL_CODE_RESEND_SECONDS - int(elapsed)
                raise EmailRateLimitError(f"请在 {retry_after} 秒后重新发送", retry_after)
        sent_this_hour = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM email_verifications
            WHERE email = ? AND purpose = 'register' AND created_at >= ?
            """,
            (email, hour_ago),
        ).fetchone()["count"]
        if sent_this_hour >= EMAIL_CODE_MAX_PER_HOUR:
            raise EmailRateLimitError("发送次数过多，请一小时后再试", 3600)
        try:
            conn.execute(
                """
                INSERT INTO email_verifications (
                  id, email, purpose, code_hash, code_salt, expires_at,
                  attempts, send_status, provider, failure_reason,
                  consumed_at, created_at, updated_at
                )
                VALUES (?, ?, 'register', ?, ?, ?, 0, 'sending', ?, NULL, NULL, ?, ?)
                """,
                (
                    verification_id, email, digest, salt,
                    (now + timedelta(minutes=EMAIL_CODE_TTL_MINUTES)).isoformat(),
                    service["provider"],
                    stamped, stamped,
                ),
            )
        except sqlite3.IntegrityError as error:
            raise EmailRateLimitError("验证码正在发送，请稍后再试", EMAIL_CODE_RESEND_SECONDS) from error

    subject, text_content, html_content = verification_email_content(code)
    try:
        delivery = sendEmail(email, subject, text_content, html_content)
    except EmailDeliveryError:
        with connect() as conn:
            conn.execute(
                """
                UPDATE email_verifications
                SET send_status = 'failed', failure_reason = 'delivery_failed', updated_at = ?
                WHERE id = ?
                """,
                (now_iso(), verification_id),
            )
        raise

    with connect() as conn:
        conn.execute(
            """
            UPDATE email_verifications
            SET send_status = 'sent', provider = ?, failure_reason = NULL, updated_at = ?
            WHERE id = ? AND send_status = 'sending'
            """,
            (delivery.get("provider") or service["provider"], now_iso(), verification_id),
        )
    return {
        "ok": True,
        "message": "如果该邮箱可用于注册，验证码邮件将很快送达",
        "expiresIn": EMAIL_CODE_TTL_MINUTES * 60,
        "retryAfter": EMAIL_CODE_RESEND_SECONDS,
        "deliveryMode": delivery.get("mode") or "smtp",
    }


def verify_and_consume_email_code(email, code):
    email = normalize_org_name(email)
    code = str(code or "").strip()
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("请输入邮件中的六位验证码")

    now = datetime.now(timezone.utc)
    error_message = ""
    with connect() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM email_verifications
            WHERE email = ? AND purpose = 'register' AND send_status = 'sent'
              AND consumed_at IS NULL
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (email,),
        ).fetchone()
        if not row:
            error_message = "请先获取邮箱验证码"
        elif datetime.fromisoformat(row["expires_at"]) <= now:
            conn.execute(
                "UPDATE email_verifications SET consumed_at = ?, updated_at = ? WHERE id = ?",
                (now.isoformat(), now.isoformat(), row["id"]),
            )
            error_message = "验证码已过期，请重新发送"
        elif int(row["attempts"] or 0) >= EMAIL_CODE_MAX_ATTEMPTS:
            error_message = "验证码尝试次数过多，请重新发送"
        else:
            candidate = email_code_digest(email, code, row["code_salt"])
            if not hmac.compare_digest(candidate, row["code_hash"]):
                attempts = int(row["attempts"] or 0) + 1
                conn.execute(
                    "UPDATE email_verifications SET attempts = ?, updated_at = ? WHERE id = ?",
                    (attempts, now.isoformat(), row["id"]),
                )
                remaining = max(0, EMAIL_CODE_MAX_ATTEMPTS - attempts)
                error_message = f"验证码不正确，还可尝试 {remaining} 次"
            else:
                conn.execute(
                    "UPDATE email_verifications SET consumed_at = ?, updated_at = ? WHERE id = ?",
                    (now.isoformat(), now.isoformat(), row["id"]),
                )
    if error_message:
        raise ValueError(error_message)
    return True


def get_case_payloads(user):
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, payload_json, owner_user_id
            FROM ds160_cases
            WHERE organization_id = ?
            ORDER BY updated_at DESC
            """,
            (user["organizationId"],),
        ).fetchall()
        cases = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            payload["extractedFields"] = [
                field for field in (payload.get("extractedFields") or [])
                if field.get("id") != "application.plannedSubmissionDate"
            ]
            payload["extractedFields"] = normalize_extracted_fields_language(
                payload.get("extractedFields")
            )
            payload["branchQuestionnaire"] = build_questionnaire(
                payload.get("visaType"),
                payload.get("branchQuestionnaire"),
                payload.get("extractedFields"),
            )
            normalize_case_language(payload)
            payload["branchQuestionnaireVersion"] = RULESET_VERSION
            payload["intakeSchemaVersion"] = INTAKE_SCHEMA_VERSION
            case_meta = payload.setdefault("caseMeta", {})
            case_meta["organizationName"] = user["identity"]
            case_meta["organizationId"] = user["organizationId"]
            case_meta["ownerUserId"] = row["owner_user_id"]
            attach_document_metadata(conn, row["id"], payload)
            attach_intake_metadata(conn, row["id"], payload)
            cases.append(payload)
    return cases


def get_case_payload(case_id, user):
    with connect() as conn:
        row = conn.execute(
            """
            SELECT payload_json, owner_user_id
            FROM ds160_cases
            WHERE id = ? AND organization_id = ?
            """,
            (case_id, user["organizationId"]),
        ).fetchone()
        if not row:
            raise PermissionError("客户档案不存在或无权访问")
        payload = json.loads(row["payload_json"])
        payload["extractedFields"] = [
            field for field in (payload.get("extractedFields") or [])
            if field.get("id") != "application.plannedSubmissionDate"
        ]
        payload["extractedFields"] = normalize_extracted_fields_language(
            payload.get("extractedFields")
        )
        payload["branchQuestionnaire"] = build_questionnaire(
            payload.get("visaType"),
            payload.get("branchQuestionnaire"),
            payload.get("extractedFields"),
        )
        normalize_case_language(payload)
        payload["branchQuestionnaireVersion"] = RULESET_VERSION
        payload["intakeSchemaVersion"] = INTAKE_SCHEMA_VERSION
        case_meta = payload.setdefault("caseMeta", {})
        case_meta["organizationName"] = user["identity"]
        case_meta["organizationId"] = user["organizationId"]
        case_meta["ownerUserId"] = row["owner_user_id"]
        attach_document_metadata(conn, case_id, payload)
        attach_intake_metadata(conn, case_id, payload)
        return payload


def screen_agent_directory():
    target = DATA_DIR / "screen_agent_jobs"
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(target, 0o700)
    return target


def write_private_json(path, payload):
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    os.chmod(path, 0o600)


def redact_screen_agent_job(paths):
    try:
        payload = json.loads(paths["job"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    for field in payload.get("fields") or []:
        field["value"] = ""
        field.pop("source", None)
    for action in payload.get("actions") or []:
        action["value"] = ""
        action.pop("duration", None)
    payload["redactedAt"] = now_iso()
    write_private_json(paths["job"], payload)


def screen_agent_job_paths(job_id):
    if not re.fullmatch(r"screen-agent-[0-9a-f]{24}", str(job_id or "")):
        raise ValueError("Screen Agent 任务编号无效")
    directory = screen_agent_directory()
    return {
        "job": directory / f"{job_id}.json",
        "status": directory / f"{job_id}.status.json",
        "stop": directory / f"{job_id}.stop",
        "log": directory / f"{job_id}.log",
    }


def screen_agent_visa_type(visa_type):
    normalized = str(visa_type or "").strip().upper()
    if normalized.startswith("F"):
        return "F1"
    if normalized.startswith("J"):
        return "J1"
    if normalized.startswith("B"):
        return "B1/B2"
    return "OTHER"


def screen_agent_target_url(job_id, field_ids):
    base_url = os.environ.get(
        "PRACTICE_LAB_AGENT_URL", DEFAULT_PRACTICE_LAB_AGENT_URL
    ).strip()
    parsed = urlparse(base_url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.path != "/screen-agent-import.html"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError(
            "PRACTICE_LAB_AGENT_URL 必须是本机 http://127.0.0.1 练习站的 "
            "/screen-agent-import.html"
        )
    field_manifest = ",".join(field_ids)
    return (
        f"{base_url}?job={quote(job_id, safe='')}"
        f"&fields={quote(field_manifest, safe=',._-')}"
    )


def screen_agent_fields(payload):
    fields_by_id = {
        str(item.get("id") or ""): item
        for item in (payload.get("extractedFields") or [])
    }
    visa_type = str(payload.get("visaType") or "").strip()
    is_student_or_exchange = visa_type.upper().startswith(("F", "J"))
    result = []
    for field_id, label, student_only in SCREEN_AGENT_ALLOWED_FIELDS:
        if student_only and not is_student_or_exchange:
            continue
        if field_id == "travel.visaType":
            value = screen_agent_visa_type(visa_type)
        else:
            source_field = fields_by_id.get(field_id) or {}
            value = source_field.get("value")
        value = re.sub(r"\s+", " ", str(value or "")).strip()[:500]
        if not value:
            continue
        result.append({
            "id": field_id,
            "label": label,
            "value": value,
            "source": "WestoryVisa 客户档案客观字段",
        })
    return result


def codex_agent_directory():
    target = DATA_DIR / "codex_agent_jobs"
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(target, 0o700)
    return target


def codex_agent_job_paths(job_id):
    if not re.fullmatch(r"codex-agent-[0-9a-f]{24}", str(job_id or "")):
        raise ValueError("Codex 任务编号无效")
    directory = codex_agent_directory()
    return {
        "job": directory / f"{job_id}.json",
        "status": directory / f"{job_id}.status.json",
    }


def codex_agent_token_hash(token):
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def load_codex_agent_job(job_id):
    paths = codex_agent_job_paths(job_id)
    if not paths["job"].exists():
        raise FileNotFoundError("Codex 任务不存在")
    try:
        job = json.loads(paths["job"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("Codex 任务文件已损坏") from error
    return job, paths


def codex_agent_job_expired(job):
    try:
        return datetime.fromisoformat(job["expiresAt"]) <= datetime.now(timezone.utc)
    except (KeyError, TypeError, ValueError):
        return True


def codex_agent_actions(job):
    pages = job.get("pages") or []
    if pages:
        return [
            action
            for page in pages
            for action in (page.get("actions") or [])
        ]
    return list(job.get("actions") or [])


def redact_codex_agent_job(job, paths, reason="closed"):
    for action in codex_agent_actions(job):
        action["value"] = ""
        action.pop("duration", None)
    job["accessTokenHash"] = ""
    job["closedAt"] = job.get("closedAt") or now_iso()
    job["closedReason"] = reason
    write_private_json(paths["job"], job)


def revoke_open_codex_agent_jobs(case_id, user):
    directory = codex_agent_directory()
    for path in directory.glob("codex-agent-*.json"):
        if path.name.endswith(".status.json"):
            continue
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
            paths = codex_agent_job_paths(job.get("jobId"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        if (
            job.get("caseId") != case_id
            or job.get("organizationId") != user["organizationId"]
            or job.get("executor") not in CODEX_AGENT_EXECUTORS
            or job.get("closedAt")
        ):
            continue
        redact_codex_agent_job(job, paths, "replaced")
        write_private_json(paths["status"], {
            "jobId": job.get("jobId"),
            "caseId": case_id,
            "state": "revoked",
            "message": "已由当前客户档案的新任务替换",
            "completedFields": 0,
            "totalFields": len(codex_agent_actions(job)),
            "updatedAt": now_iso(),
        })


def open_url_in_google_chrome(url):
    if sys.platform != "darwin" or not CHROME_EXECUTABLE.exists():
        return False
    try:
        subprocess.Popen(
            ["/usr/bin/open", "-a", "Google Chrome", str(url)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except OSError:
        return False


def prepare_codex_agent_job(
    case_id, user, server_port, auto_next=True, launch_browser=False
):
    payload = get_case_payload(case_id, user)
    plan = build_browser_workflow(payload)
    pages = plan.get("pages") or []
    total_fields = int(plan.get("totalFields") or 0)
    if not pages or not total_fields:
        raise ValueError("当前客户档案没有可交给 Computer Use 的已确认字段")

    revoke_open_codex_agent_jobs(case_id, user)

    job_id = f"codex-agent-{secrets.token_hex(12)}"
    access_token = secrets.token_urlsafe(32)
    paths = codex_agent_job_paths(job_id)
    created_at = datetime.now(timezone.utc)
    expires_at = created_at + timedelta(minutes=CODEX_AGENT_JOB_TTL_MINUTES)
    task_url = (
        f"http://127.0.0.1:{int(server_port)}/api/codex-agent/jobs/"
        f"{quote(job_id, safe='')}"
    )
    job = {
        "version": 4,
        "workflowType": "ds160",
        "executor": "codex-computer-use",
        "jobId": job_id,
        "caseId": case_id,
        "organizationId": user["organizationId"],
        "page": "workflow",
        "targetUrl": plan.get("targetUrl") or CEAC_START_URL,
        "pages": pages,
        "autoNext": bool(auto_next),
        "interactionPolicy": dict(COMPUTER_USE_INTERACTION_POLICY),
        "accessTokenHash": codex_agent_token_hash(access_token),
        "fetchCount": 0,
        "createdAt": created_at.isoformat(),
        "expiresAt": expires_at.isoformat(),
        "safety": {
            "allowedDomain": "ceac.state.gov",
            "allowedPages": [page.get("key") for page in pages],
            "existingChromeSession": True,
            "visibleInteractionOnly": True,
            "browserExtension": "never",
            "domInjection": "never",
            "sensitiveQuestions": "manual_only",
            "captcha": "never",
            "credentials": "never",
            "legalDeclaration": "never",
            "payment": "never",
            "finalSubmission": "never",
            "save": "never",
            "next": "after_visible_verification_only",
        },
    }
    write_private_json(paths["job"], job)
    write_private_json(paths["status"], {
        "jobId": job_id,
        "caseId": case_id,
        "state": "prepared",
        "message": "逐页填写任务已准备，等待 Codex Computer Use 接收",
        "completedFields": 0,
        "totalFields": total_fields,
        "updatedAt": now_iso(),
    })
    browser_opened = open_url_in_google_chrome(CEAC_START_URL) if launch_browser else False
    return {
        "jobId": job_id,
        "caseId": case_id,
        "state": "prepared",
        "taskUrl": task_url,
        "accessToken": access_token,
        "totalFields": total_fields,
        "totalPages": len(pages),
        "expiresAt": expires_at.isoformat(),
        "browserOpened": browser_opened,
        "message": "任务已准备，将打开 CEAC 起始页；进入正式表格后可交给 Computer Use。",
    }


def prepare_appointment_agent_job(case_id, user, server_port, launch_browser=False):
    payload = get_case_payload(case_id, user)
    issues = appointment_preflight_issues(payload)
    if issues:
        labels = "、".join(item.get("label") or item.get("id") for item in issues[:5])
        remaining = len(issues) - min(len(issues), 5)
        suffix = f"，另有 {remaining} 项" if remaining else ""
        raise ValueError(f"预约资料尚未补齐：{labels}{suffix}")

    plan = build_appointment_workflow(payload)
    pages = plan.get("pages") or []
    total_fields = int(plan.get("totalFields") or 0)
    if not pages or not total_fields:
        raise ValueError("当前客户档案没有可交给预约辅助 Agent 的字段")

    revoke_open_codex_agent_jobs(case_id, user)

    job_id = f"codex-agent-{secrets.token_hex(12)}"
    access_token = secrets.token_urlsafe(32)
    paths = codex_agent_job_paths(job_id)
    created_at = datetime.now(timezone.utc)
    expires_at = created_at + timedelta(minutes=CODEX_AGENT_JOB_TTL_MINUTES)
    task_url = (
        f"http://127.0.0.1:{int(server_port)}/api/codex-agent/jobs/"
        f"{quote(job_id, safe='')}"
    )
    job = {
        "version": 4,
        "workflowType": "appointment",
        "executor": "codex-computer-use",
        "jobId": job_id,
        "caseId": case_id,
        "organizationId": user["organizationId"],
        "page": "workflow",
        "targetUrl": plan.get("targetUrl") or APPOINTMENT_START_URL,
        "pages": pages,
        "autoNext": False,
        "interactionPolicy": dict(COMPUTER_USE_INTERACTION_POLICY),
        "accessTokenHash": codex_agent_token_hash(access_token),
        "fetchCount": 0,
        "createdAt": created_at.isoformat(),
        "expiresAt": expires_at.isoformat(),
        "safety": {
            "allowedDomain": APPOINTMENT_ALLOWED_DOMAIN,
            "allowedPages": [page.get("key") for page in pages],
            "existingChromeSession": True,
            "visibleInteractionOnly": True,
            "browserExtension": "never",
            "domInjection": "never",
            "credentials": "never",
            "oneTimeCode": "never",
            "captcha": "never",
            "dependentAccountCreation": "never",
            "payment": "never",
            "appointmentSlot": "manual_only",
            "finalBooking": "never",
            "save": "never",
            "next": "manual_only",
        },
    }
    write_private_json(paths["job"], job)
    write_private_json(paths["status"], {
        "jobId": job_id,
        "caseId": case_id,
        "workflowType": "appointment",
        "state": "prepared",
        "message": "预约资料任务已准备，等待 Codex Computer Use 接收",
        "completedFields": 0,
        "totalFields": total_fields,
        "updatedAt": now_iso(),
    })
    browser_opened = (
        open_url_in_google_chrome(APPOINTMENT_START_URL) if launch_browser else False
    )
    return {
        "jobId": job_id,
        "caseId": case_id,
        "workflowType": "appointment",
        "state": "prepared",
        "taskUrl": task_url,
        "accessToken": access_token,
        "totalFields": total_fields,
        "totalPages": len(pages),
        "expiresAt": expires_at.isoformat(),
        "browserOpened": browser_opened,
        "message": "任务已准备，将打开预约网站；人工登录后可交给 Computer Use。",
    }


def validate_codex_agent_access(job, paths, access_token):
    if job.get("executor") not in CODEX_AGENT_EXECUTORS:
        raise PermissionError("Computer Use 任务类型无效")
    if job.get("closedAt") or not job.get("accessTokenHash"):
        raise PermissionError("Computer Use 任务已关闭")
    if codex_agent_job_expired(job):
        redact_codex_agent_job(job, paths, "expired")
        raise PermissionError("Computer Use 任务已过期")
    supplied_hash = codex_agent_token_hash(access_token)
    if not hmac.compare_digest(job.get("accessTokenHash", ""), supplied_hash):
        raise PermissionError("Computer Use 任务访问令牌无效")
    if int(job.get("fetchCount") or 0) >= CODEX_AGENT_MAX_FETCHES:
        redact_codex_agent_job(job, paths, "fetch_limit")
        raise PermissionError("Computer Use 任务读取次数已达上限")


def codex_agent_task_payload(job_id, access_token, server_port):
    job, paths = load_codex_agent_job(job_id)
    validate_codex_agent_access(job, paths, access_token)
    job["fetchCount"] = int(job.get("fetchCount") or 0) + 1
    job["claimedAt"] = job.get("claimedAt") or now_iso()
    write_private_json(paths["job"], job)
    write_private_json(paths["status"], {
        "jobId": job_id,
        "caseId": job["caseId"],
        "state": "claimed",
        "message": "Codex Computer Use 已读取并锁定当前任务",
        "completedFields": 0,
        "totalFields": len(codex_agent_actions(job)),
        "updatedAt": now_iso(),
    })
    workflow_type = str(job.get("workflowType") or "ds160")
    instructions = (
        [
            "Use Codex Computer Use only; do not use a Chrome extension, DOM injection, Playwright, Selenium, or RPA.",
            "Use only the user's visible usvisascheduling.com Chrome tab.",
            "Wait until the user completes sign-in, security verification, and CAPTCHA.",
            "Perform one visible action at a time, then reacquire fresh accessibility state and verify the visible value.",
            "After a selection reveals dynamic fields, wait for the page to settle and inspect the page again before continuing.",
            "Follow interactionPolicy for a deliberate, stability-oriented pace.",
            "Never create accounts, handle credentials or one-time codes, pay fees, select slots, or confirm a booking.",
            "Never click Save, Continue, Next, Submit, or any final confirmation control.",
        ]
        if workflow_type == "appointment"
        else [
            "Use Codex Computer Use only; do not use a Chrome extension, DOM injection, Playwright, Selenium, or RPA.",
            "Use only the user's visible CEAC Chrome tab.",
            "Wait until the user explicitly confirms they reached a form page.",
            "Perform one visible action at a time, then reacquire fresh accessibility state and verify the visible value.",
            "After Yes/No, dropdown, or Does Not Apply changes, wait for dynamic fields and inspect the whole visible form again.",
            "Follow interactionPolicy for a deliberate, stability-oriented pace.",
            "Next is allowed only when autoNext is true, the page is non-sensitive, all planned actions verify, and no visible required error remains.",
            "Never handle CAPTCHA, credentials, legal declarations, payment, or submission.",
        ]
    )
    return {
        "version": job["version"],
        "executor": job.get("executor") or "codex-computer-use",
        "workflowType": workflow_type,
        "jobId": job_id,
        "page": job["page"],
        "targetUrl": job["targetUrl"],
        "pages": job.get("pages") or [],
        "actions": job.get("actions") or [],
        "autoNext": bool(job.get("autoNext")),
        "interactionPolicy": job.get("interactionPolicy") or dict(
            COMPUTER_USE_INTERACTION_POLICY
        ),
        "safety": job["safety"],
        "statusUrl": (
            f"http://127.0.0.1:{int(server_port)}/api/codex-agent/jobs/"
            f"{quote(job_id, safe='')}/status"
        ),
        "expiresAt": job["expiresAt"],
        "instructions": instructions,
    }


def update_codex_agent_task_status(job_id, access_token, payload):
    job, paths = load_codex_agent_job(job_id)
    validate_codex_agent_access(job, paths, access_token)
    state = str((payload or {}).get("state") or "").strip()
    workflow_type = str(job.get("workflowType") or "ds160")
    messages = (
        {
            "waiting_for_entry": "预约网站已连接，等待顾问人工登录并进入资料页",
            "running": "Computer Use 正在填写预约资料页面",
            "review_required": "预约资料辅助填写已结束，等待顾问继续办理",
            "completed": "预约资料辅助任务已完成",
            "blocked": "Computer Use 已暂停，请顾问核对当前预约页面",
            "failed": "Computer Use 未完成预约资料任务",
        }
        if workflow_type == "appointment"
        else {
            "waiting_for_entry": "CEAC 已连接，等待顾问人工进入正式表格",
            "running": "Computer Use 正在填写当前页面",
            "review_required": "可自动填写的页面已完成，等待顾问核对",
            "completed": "当前逐页填写任务已完成",
            "blocked": "Computer Use 已暂停，需要顾问处理当前页面",
            "failed": "Computer Use 未完成当前任务",
        }
    )
    if state not in messages:
        raise ValueError("Computer Use 任务状态无效")
    total_fields = len(codex_agent_actions(job))
    completed_fields = max(
        0, min(int((payload or {}).get("completedFields") or 0), total_fields)
    )
    allowed_action_ids = {action.get("id") for action in codex_agent_actions(job)}
    failed_action_ids = []
    for action_id in (payload or {}).get("failedActionIds") or []:
        cleaned = str(action_id or "").strip()[:160]
        if cleaned in allowed_action_ids and cleaned not in failed_action_ids:
            failed_action_ids.append(cleaned)
        if len(failed_action_ids) >= 20:
            break
    missing_fields = []
    for label in (payload or {}).get("missingFields") or []:
        cleaned = re.sub(r"[\x00-\x1f\x7f]", " ", str(label or "")).strip()[:120]
        if cleaned and cleaned not in missing_fields:
            missing_fields.append(cleaned)
        if len(missing_fields) >= 20:
            break
    allowed_page_keys = {str(page.get("key") or "") for page in job.get("pages") or []}
    allowed_domain = str((job.get("safety") or {}).get("allowedDomain") or "")

    def sanitize_route(raw_route):
        if not isinstance(raw_route, dict):
            return None
        path = str(raw_route.get("path") or "").strip()[:180]
        valid_prefix = (
            path.startswith("/GenNIV/")
            if allowed_domain == "ceac.state.gov"
            else path.startswith("/")
            if allowed_domain == APPOINTMENT_ALLOWED_DOMAIN
            else False
        )
        if not valid_prefix or not re.fullmatch(r"/[A-Za-z0-9_./\-]+", path):
            return None
        node = re.sub(
            r"[^A-Za-z0-9_-]", "", str(raw_route.get("node") or "")
        )[:80]
        title = re.sub(
            r"[\x00-\x1f\x7f]", " ", str(raw_route.get("title") or "")
        ).strip()[:100]
        mapped_key = str(raw_route.get("mappedKey") or "").strip()[:80]
        if mapped_key not in allowed_page_keys:
            mapped_key = ""
        return {
            "path": path,
            "node": node,
            "title": title,
            "mappedKey": mapped_key,
            "mapped": bool(mapped_key),
            "observedAt": str(raw_route.get("observedAt") or "")[:40],
        }

    observed_routes = []
    observed_keys = set()
    for raw_route in (payload or {}).get("observedRoutes") or []:
        route = sanitize_route(raw_route)
        if not route:
            continue
        route_key = (route["path"], route["node"])
        if route_key in observed_keys:
            continue
        observed_keys.add(route_key)
        observed_routes.append(route)
        if len(observed_routes) >= 30:
            break
    current_route = sanitize_route((payload or {}).get("currentRoute"))
    status_code = re.sub(
        r"[^a-z0-9_]", "", str((payload or {}).get("statusCode") or "").lower()
    )[:64]
    reason = re.sub(
        r"[\x00-\x1f\x7f]", " ", str((payload or {}).get("reason") or "")
    ).strip()[:400]
    status = {
        "jobId": job_id,
        "caseId": job["caseId"],
        "workflowType": workflow_type,
        "state": state,
        "message": reason if reason and state in {"blocked", "failed"} else messages[state],
        "completedFields": completed_fields,
        "totalFields": total_fields,
        "failedActionIds": failed_action_ids,
        "missingFields": missing_fields,
        "statusCode": status_code,
        "currentRoute": current_route,
        "observedRoutes": observed_routes,
        "updatedAt": now_iso(),
    }
    page_label = re.sub(
        r"[^A-Za-z0-9 ._()/\-]", "", str((payload or {}).get("pageLabel") or "")
    ).strip()[:100]
    if page_label:
        status["pageLabel"] = page_label
    write_private_json(paths["status"], status)
    if state in {"review_required", "completed"}:
        redact_codex_agent_job(job, paths, "completed")
    return status


def codex_agent_job_for_user(case_id, job_id, user):
    job, paths = load_codex_agent_job(job_id)
    if (
        job.get("caseId") != case_id
        or job.get("organizationId") != user["organizationId"]
        or job.get("executor") not in CODEX_AGENT_EXECUTORS
    ):
        raise PermissionError("无权访问该 Computer Use 任务")
    return job, paths


def codex_agent_status(case_id, job_id, user):
    job, paths = codex_agent_job_for_user(case_id, job_id, user)
    try:
        status = json.loads(paths["status"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        status = {}
    if codex_agent_job_expired(job) and not job.get("closedAt"):
        redact_codex_agent_job(job, paths, "expired")
        status.update({
            "state": "expired",
            "message": "Computer Use 任务已过期，请重新准备",
            "updatedAt": now_iso(),
        })
        write_private_json(paths["status"], status)
    public_message = str(status.get("message") or "Computer Use 任务已准备")
    return {
        "jobId": job_id,
        "workflowType": str(job.get("workflowType") or "ds160"),
        "state": status.get("state") or "prepared",
        "message": public_message,
        "completedFields": int(status.get("completedFields") or 0),
        "totalFields": int(status.get("totalFields") or len(codex_agent_actions(job))),
        "pageLabel": status.get("pageLabel") or "",
        "failedActionIds": status.get("failedActionIds") or [],
        "missingFields": status.get("missingFields") or [],
        "statusCode": status.get("statusCode") or "",
        "currentRoute": status.get("currentRoute"),
        "observedRoutes": status.get("observedRoutes") or [],
        "updatedAt": status.get("updatedAt") or job.get("createdAt") or now_iso(),
        "expiresAt": job.get("expiresAt") or "",
        "closed": bool(job.get("closedAt")),
    }


def revoke_codex_agent_job(case_id, job_id, user):
    job, paths = codex_agent_job_for_user(case_id, job_id, user)
    if not job.get("closedAt"):
        redact_codex_agent_job(job, paths, "revoked")
    status = {
        "jobId": job_id,
        "caseId": case_id,
        "state": "revoked",
        "message": "Computer Use 任务已撤销，旧任务不能再读取客户字段",
        "completedFields": 0,
        "totalFields": len(codex_agent_actions(job)),
        "updatedAt": now_iso(),
    }
    write_private_json(paths["status"], status)
    return status


def open_cowork_directory():
    target = DATA_DIR / "open_cowork_jobs"
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(target, 0o700)
    return target


def open_cowork_job_paths(job_id):
    if not re.fullmatch(r"open-cowork-[0-9a-f]{24}", str(job_id or "")):
        raise ValueError("OpenCowork 任务编号无效")
    directory = open_cowork_directory()
    return {
        "job": directory / f"{job_id}.json",
        "status": directory / f"{job_id}.status.json",
    }


def open_cowork_demo_fields(payload):
    available_ids = {field["id"] for field in screen_agent_fields(payload)}
    visa_type = screen_agent_visa_type(payload.get("visaType"))
    fields = []
    for field_id, label, _student_only in SCREEN_AGENT_ALLOWED_FIELDS:
        if field_id not in available_ids:
            continue
        value = visa_type if field_id == "travel.visaType" else OPEN_COWORK_DEMO_VALUES[field_id]
        fields.append({
            "id": field_id,
            "label": label,
            "value": value,
            "source": "WestoryVisa 固定脱敏演示映射",
        })
    return fields


def open_cowork_application_path():
    candidates = (
        Path("/Applications/Open Cowork.app"),
        Path("/Applications/OpenCowork.app"),
        Path.home() / "Applications" / "Open Cowork.app",
        Path.home() / "Applications" / "OpenCowork.app",
    )
    return next((candidate for candidate in candidates if candidate.exists()), None)


def prepare_open_cowork_job(case_id, user):
    payload = get_case_payload(case_id, user)
    fields = open_cowork_demo_fields(payload)
    if not fields:
        raise ValueError("当前客户档案没有可供 OpenCowork 演示映射的客观字段")

    job_id = f"open-cowork-{secrets.token_hex(12)}"
    paths = open_cowork_job_paths(job_id)
    created_at = datetime.now(timezone.utc)
    target_url = screen_agent_target_url(
        job_id, [field["id"] for field in fields]
    )
    job = {
        "version": 1,
        "executor": "open-cowork",
        "jobId": job_id,
        "caseId": case_id,
        "organizationId": user["organizationId"],
        "targetUrl": target_url,
        "targetMarker": "VISA FORM PRACTICE LAB",
        "fields": fields,
        "createdAt": created_at.isoformat(),
        "expiresAt": (
            created_at + timedelta(minutes=OPEN_COWORK_JOB_TTL_MINUTES)
        ).isoformat(),
        "operatorAuthorized": True,
        "safety": {
            "localhostOnly": True,
            "practiceLabOnly": True,
            "sanitizedDemoOnly": True,
            "visibleComputerUse": True,
            "perFieldVisualAcknowledgement": True,
            "sensitiveQuestions": "manual_only",
            "captcha": "never",
            "credentials": "never",
            "accountCreation": "never",
            "legalDeclaration": "never",
            "payment": "never",
            "finalSubmission": "never",
        },
    }
    write_private_json(paths["job"], job)
    write_private_json(paths["status"], {
        "jobId": job_id,
        "caseId": case_id,
        "state": "prepared",
        "message": "OpenCowork 本机练习任务已准备",
        "completedFields": 0,
        "totalFields": len(fields),
        "updatedAt": now_iso(),
    })

    application_path = open_cowork_application_path()
    launched = False
    if application_path:
        try:
            subprocess.Popen(
                ["/usr/bin/open", str(application_path)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            launched = True
        except OSError:
            launched = False

    return {
        "jobId": job_id,
        "caseId": case_id,
        "state": "prepared",
        "targetUrl": target_url,
        "totalFields": len(fields),
        "expiresAt": job["expiresAt"],
        "installed": bool(application_path),
        "launched": launched,
        "prompt": (
            "运行 docflow-practice-lab Skill，任务编号 "
            f"{job_id}。只操作任务指定的本机 Practice Lab，并在 "
            "Security and Background 前停止。"
        ),
        "message": (
            "OpenCowork 已打开，请粘贴任务指令"
            if launched
            else "任务已准备；安装并打开 OpenCowork 后粘贴任务指令"
        ),
    }


def open_cowork_job_for_user(case_id, job_id, user):
    paths = open_cowork_job_paths(job_id)
    if not paths["job"].exists():
        raise FileNotFoundError("OpenCowork 任务不存在")
    try:
        job = json.loads(paths["job"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("OpenCowork 任务文件已损坏") from error
    if (
        job.get("caseId") != case_id
        or job.get("organizationId") != user["organizationId"]
        or job.get("executor") != "open-cowork"
    ):
        raise PermissionError("无权访问该 OpenCowork 任务")
    return job, paths


def open_cowork_status(case_id, job_id, user):
    job, paths = open_cowork_job_for_user(case_id, job_id, user)
    try:
        status = json.loads(paths["status"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        status = {}
    state = str(status.get("state") or "prepared")
    message = str(status.get("message") or "OpenCowork 本机练习任务已准备")
    try:
        expired = datetime.fromisoformat(job["expiresAt"]) <= datetime.now(timezone.utc)
    except (KeyError, TypeError, ValueError):
        expired = True
    if expired and state == "prepared" and not job.get("redactedAt"):
        state = "expired"
        message = "OpenCowork 任务已过期，请重新准备"
    total_fields = len(job.get("fields") or [])
    return {
        "jobId": job_id,
        "state": state,
        "message": message,
        "completedFields": min(
            int(status.get("completedFields") or 0), total_fields
        ),
        "totalFields": total_fields,
        "updatedAt": status.get("updatedAt") or job.get("createdAt") or now_iso(),
        "expiresAt": job.get("expiresAt") or "",
        "redacted": bool(job.get("redactedAt")),
    }


def prepare_screen_agent_job(case_id, user, server_port):
    payload = get_case_payload(case_id, user)
    plan = build_travel_actions(payload)
    actions = plan.get("actions") or []
    if not actions:
        raise ValueError("当前客户档案没有可写入 Travel 页的已收集信息")

    job_id = f"screen-agent-{secrets.token_hex(12)}"
    paths = screen_agent_job_paths(job_id)
    job = {
        "version": 2,
        "executor": "browser-use",
        "jobId": job_id,
        "caseId": case_id,
        "organizationId": user["organizationId"],
        "visaType": str(payload.get("visaType") or "")[:80],
        "page": "travel",
        "targetUrl": plan.get("targetUrl") or CEAC_TRAVEL_URL,
        "actions": actions,
        "clickSave": False,
        "clickNext": False,
        "createdAt": now_iso(),
        "safety": {
            "allowedDomain": "ceac.state.gov",
            "allowedPage": "Travel Information",
            "visibleBrowser": True,
            "pageScoped": True,
            "sensitiveQuestions": "manual_only",
            "captcha": "never",
            "legalDeclaration": "never",
            "payment": "never",
            "finalSubmission": "never",
            "save": "manual_only",
            "next": "disabled_in_travel_v1",
        },
    }
    write_private_json(paths["job"], job)
    write_private_json(paths["status"], {
        "jobId": job_id,
        "caseId": case_id,
        "state": "prepared",
        "message": "Travel 页任务已准备，等待 Browser Use 启动",
        "completedFields": 0,
        "totalFields": len(actions),
        "logs": [],
        "updatedAt": now_iso(),
    })
    paths["stop"].unlink(missing_ok=True)
    return job, paths


def screen_agent_runtime_status():
    launcher = "启动Screen Agent演示.command"
    if sys.platform != "darwin":
        return {
            "available": False,
            "mode": "unsupported",
            "message": "当前 Browser Use 桌面填写仅支持 macOS。",
            "browserOwnership": "dedicated",
            "launcher": launcher,
        }
    if os.environ.get("CODEX_SANDBOX") == "seatbelt":
        return {
            "available": False,
            "mode": "codex_preview",
            "message": (
                "当前页面由 Codex 预览服务启动，不能接管或创建 Chrome。"
                "请在 Finder 中双击“启动Screen Agent演示.command”，并使用它新打开的 WestoryVisa 页面。"
            ),
            "browserOwnership": "dedicated",
            "launcher": launcher,
        }
    if not BROWSER_USE_PYTHON.exists() or not BROWSER_USE_TRAVEL_WORKER.exists():
        return {
            "available": False,
            "mode": "missing_browser_use",
            "message": "Browser Use 尚未安装完整，请重新运行安装命令后再启动。",
            "browserOwnership": "dedicated",
            "launcher": launcher,
        }
    if not CHROME_EXECUTABLE.exists():
        return {
            "available": False,
            "mode": "missing_chrome",
            "message": "没有找到 Google Chrome，请安装后重新启动 WestoryVisa。",
            "browserOwnership": "dedicated",
            "launcher": launcher,
        }
    return {
        "available": True,
        "mode": "ready",
        "message": (
            "Browser Use 将新开一个专用 Chrome 窗口。只有这个窗口会被 Agent 控制；"
            "已经打开的普通 Chrome 标签不会被接管。"
        ),
        "browserOwnership": "dedicated",
        "launcher": launcher,
    }


def screen_agent_job_for_user(case_id, job_id, user):
    paths = screen_agent_job_paths(job_id)
    if not paths["job"].exists():
        raise FileNotFoundError("Screen Agent 任务不存在")
    try:
        job = json.loads(paths["job"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("Screen Agent 任务文件已损坏") from error
    if (
        job.get("caseId") != case_id
        or job.get("organizationId") != user["organizationId"]
    ):
        raise PermissionError("无权访问该 Screen Agent 任务")
    return job, paths


def launch_screen_agent(case_id, user, server_port):
    runtime = screen_agent_runtime_status()
    if not runtime["available"]:
        raise RuntimeError(runtime["message"])

    job, paths = prepare_screen_agent_job(case_id, user, server_port)
    command = [
        str(BROWSER_USE_PYTHON), str(BROWSER_USE_TRAVEL_WORKER),
        "--job", str(paths["job"]),
        "--status", str(paths["status"]),
        "--stop", str(paths["stop"]),
    ]
    worker_env = os.environ.copy()
    worker_env.update({
        "BROWSER_USE_CONFIG_DIR": str(DATA_DIR / "browser-use" / "config"),
        "BH_HOME": str(DATA_DIR / "browser-harness"),
        "ANONYMIZED_TELEMETRY": "false",
        "BROWSER_USE_CLOUD_SYNC": "false",
        "BROWSER_USE_DISABLE_EXTENSIONS": "1",
    })

    with SCREEN_AGENT_PROCESS_LOCK:
        for existing_job_id, item in list(SCREEN_AGENT_PROCESSES.items()):
            process = item["process"]
            if process.poll() is not None:
                SCREEN_AGENT_PROCESSES.pop(existing_job_id, None)
            elif item["caseId"] == case_id and item["organizationId"] == user["organizationId"]:
                process.terminate()
                SCREEN_AGENT_PROCESSES.pop(existing_job_id, None)

        paths["log"].parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            with paths["log"].open("ab") as output:
                process = subprocess.Popen(
                    command,
                    cwd=str(ROOT),
                    env=worker_env,
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
        except OSError as error:
            raise RuntimeError(f"Browser Use 无法启动：{error.strerror or error}") from error
        SCREEN_AGENT_PROCESSES[job["jobId"]] = {
            "process": process,
            "caseId": case_id,
            "organizationId": user["organizationId"],
            "paths": paths,
        }

    return {
        "jobId": job["jobId"],
        "caseId": case_id,
        "state": "starting",
        "targetUrl": job["targetUrl"],
        "totalFields": len(job["actions"]),
        "message": "Browser Use 正在打开可见 Chrome；如 CEAC 要求恢复会话或验证码，请在新窗口中人工完成",
    }


def screen_agent_status(case_id, job_id, user):
    job, paths = screen_agent_job_for_user(case_id, job_id, user)
    try:
        status = json.loads(paths["status"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        status = {
            "jobId": job_id,
            "caseId": case_id,
            "state": "starting",
            "message": "正在等待 Browser Use 返回状态",
            "completedFields": 0,
            "totalFields": len(job.get("actions") or []),
            "logs": [],
            "updatedAt": now_iso(),
        }
    with SCREEN_AGENT_PROCESS_LOCK:
        process_item = SCREEN_AGENT_PROCESSES.get(job_id)
        if process_item and process_item["process"].poll() is not None:
            SCREEN_AGENT_PROCESSES.pop(job_id, None)
            redact_screen_agent_job(paths)
    status["targetUrl"] = job.get("targetUrl")
    return status


def stop_screen_agent(case_id, job_id, user):
    _, paths = screen_agent_job_for_user(case_id, job_id, user)
    paths["stop"].write_text("stop\n", encoding="utf-8")
    os.chmod(paths["stop"], 0o600)
    with SCREEN_AGENT_PROCESS_LOCK:
        process_item = SCREEN_AGENT_PROCESSES.pop(job_id, None)
        if process_item and process_item["process"].poll() is None:
            process_item["process"].terminate()
    try:
        previous = json.loads(paths["status"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        previous = {}
    logs = list(previous.get("logs") or [])
    logs.append({"at": now_iso(), "type": "warning", "message": "顾问终止了 Browser Use"})
    write_private_json(paths["status"], {
        "jobId": job_id,
        "caseId": case_id,
        "state": "stopped",
        "message": "Browser Use 已由顾问急停",
        "completedFields": int(previous.get("completedFields") or 0),
        "totalFields": int(previous.get("totalFields") or 0),
        "logs": logs[-100:],
        "updatedAt": now_iso(),
    })
    redact_screen_agent_job(paths)
    return {"ok": True, "jobId": job_id, "state": "stopped"}


def attach_document_metadata(conn, case_id, payload):
    rows = conn.execute(
        """
        SELECT id, slot, file_name, mime_type, file_size, sha256,
               scan_status, scan_message, parser_name, processed_at
        FROM documents
        WHERE case_id = ?
        ORDER BY created_at, id
        """,
        (case_id,),
    ).fetchall()
    by_id = {row["id"]: row for row in rows}
    by_slot = {row["slot"]: row for row in rows}
    for index, document in enumerate(payload.get("documents") or []):
        document_id = document.get("id") or f"{case_id}-doc-{index}"
        document["id"] = document_id
        row = by_id.get(document_id) or by_slot.get(document.get("slot"))
        if not row:
            continue
        document.update({
            "id": row["id"],
            "fileName": row["file_name"] or "",
            "mimeType": row["mime_type"] or "",
            "fileSize": row["file_size"] or 0,
            "sha256": row["sha256"] or "",
            "scanStatus": row["scan_status"] or "empty",
            "scanMessage": row["scan_message"] or "",
            "parserName": row["parser_name"] or "",
            "processedAt": row["processed_at"] or "",
        })


_LEGACY_CLIENT_INTAKE_FIELDS = [
    {
        "id": "personal.surname", "label": "护照上的英文姓", "section": "基础信息",
        "inputType": "text", "placeholder": "例如：ZHANG", "riskLevel": "high",
        "hint": "请与护照资料页的 Surname 完全一致。",
    },
    {
        "id": "personal.givenNames", "label": "护照上的英文名", "section": "基础信息",
        "inputType": "text", "placeholder": "例如：WEI", "riskLevel": "high",
        "hint": "请与护照资料页的 Given Names 完全一致。",
    },
    {
        "id": "personal.dateOfBirth", "label": "出生日期", "section": "基础信息",
        "inputType": "date", "placeholder": "YYYY-MM-DD", "riskLevel": "high",
    },
    {
        "id": "personal.sex", "label": "性别", "section": "基础信息",
        "inputType": "select", "riskLevel": "medium",
        "choices": [
            {"value": "MALE", "label": "男 / Male"},
            {"value": "FEMALE", "label": "女 / Female"},
        ],
    },
    {
        "id": "personal.placeOfBirth", "label": "出生地", "section": "基础信息",
        "inputType": "text", "placeholder": "城市、省份、国家或地区", "riskLevel": "medium",
    },
    {
        "id": "personal.nationality", "label": "当前国籍", "section": "基础信息",
        "inputType": "text", "placeholder": "例如：中国 / CHINA", "riskLevel": "high",
    },
    {
        "id": "personal.nationalId", "label": "身份证件号码", "section": "基础信息",
        "inputType": "text", "placeholder": "请填写当前有效号码", "riskLevel": "high",
    },
    {
        "id": "contact.homeAddress", "label": "当前家庭住址", "section": "地址 / 电话 / 社交媒体",
        "inputType": "textarea", "placeholder": "请写明街道、城市、省份、邮编和国家或地区", "riskLevel": "medium",
    },
    {
        "id": "contact.primaryPhone", "label": "当前联系电话", "section": "地址 / 电话 / 社交媒体",
        "inputType": "tel", "placeholder": "含国家或地区区号", "riskLevel": "medium",
    },
    {
        "id": "contact.email", "label": "当前常用邮箱", "section": "地址 / 电话 / 社交媒体",
        "inputType": "email", "placeholder": "name@example.com", "riskLevel": "medium",
    },
    {
        "id": "passport.number", "label": "护照号码", "section": "护照信息",
        "inputType": "text", "placeholder": "请照护照资料页填写", "riskLevel": "high",
    },
    {
        "id": "passport.issuePlace", "label": "护照签发地 / 签发机关", "section": "护照信息",
        "inputType": "text", "placeholder": "请照护照资料页填写", "riskLevel": "medium",
    },
    {
        "id": "passport.issueDate", "label": "护照签发日期", "section": "护照信息",
        "inputType": "date", "placeholder": "YYYY-MM-DD", "riskLevel": "medium",
    },
    {
        "id": "passport.expiration", "label": "护照有效期至", "section": "护照信息",
        "inputType": "date", "placeholder": "YYYY-MM-DD", "riskLevel": "high",
    },
    {
        "id": "education.schoolName", "label": "美国学校名称", "section": "SEVIS / 学生信息",
        "inputType": "text", "placeholder": "请与 I-20 / DS-2019 一致", "riskLevel": "medium",
        "visaTypes": ["f1", "j1"],
    },
    {
        "id": "education.schoolAddress", "label": "美国学校或项目地址", "section": "SEVIS / 学生信息",
        "inputType": "textarea", "placeholder": "请与 I-20 / DS-2019 一致", "riskLevel": "medium",
        "visaTypes": ["f1", "j1"],
    },
    {
        "id": "education.programName", "label": "专业或项目名称", "section": "SEVIS / 学生信息",
        "inputType": "text", "placeholder": "请与 I-20 / DS-2019 一致", "riskLevel": "medium",
        "visaTypes": ["f1", "j1"],
    },
    {
        "id": "education.sevisId", "label": "SEVIS ID", "section": "SEVIS / 学生信息",
        "inputType": "text", "placeholder": "通常以 N00 开头", "riskLevel": "high",
        "visaTypes": ["f1", "j1"],
    },
    {
        "id": "education.programNumber", "label": "J-1 Program Number", "section": "SEVIS / 学生信息",
        "inputType": "text", "placeholder": "请与 DS-2019 一致", "riskLevel": "high",
        "visaTypes": ["j1"],
    },
    {
        "id": "education.sponsorName", "label": "J-1 Sponsor 名称", "section": "SEVIS / 学生信息",
        "inputType": "text", "placeholder": "请与 DS-2019 一致", "riskLevel": "medium",
        "visaTypes": ["j1"],
    },
]


def selected_visa_id(visa_type):
    normalized = str(visa_type or "").strip().upper()
    if normalized.startswith("B"):
        return "b1b2"
    if normalized.startswith("F2") or normalized.startswith("F-2"):
        return "f2"
    if normalized.startswith("J2") or normalized.startswith("J-2"):
        return "j2"
    if normalized.startswith("J"):
        return "j1"
    return "f1"


def intake_token_hash(token):
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def attach_intake_metadata(conn, case_id, payload):
    row = conn.execute(
        """
        SELECT status, expires_at, respondent_name, identity_match,
               submitted_at, created_at
        FROM intake_links
        WHERE case_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (case_id,),
    ).fetchone()
    payload["intakeMeta"] = ({
        "status": row["status"],
        "expiresAt": row["expires_at"],
        "respondentName": row["respondent_name"] or "",
        "identityMatch": None if row["identity_match"] is None else bool(row["identity_match"]),
        "submittedAt": row["submitted_at"],
        "createdAt": row["created_at"],
    } if row else {"status": "not_created"})


def client_prompt(label):
    value = str(label or "").strip()
    replacements = (
        ("是否有人为客户", "是否有人为你"),
        ("客户是否", "你是否"),
        ("客户当前", "你当前"),
        ("客户父亲", "你的父亲"),
        ("客户母亲", "你的母亲"),
        ("客户以前", "你以前"),
        ("客户使用", "你使用"),
        ("客户", "你"),
        ("填写当前配偶或伴侣资料", "请填写你当前配偶或伴侣的资料"),
        ("填写已故配偶资料", "请填写已故配偶的资料"),
        ("填写每一位前配偶资料", "请填写每一位前配偶的资料"),
        ("逐一填写同行人", "请填写同行人的资料"),
    )
    for source, target in replacements:
        value = value.replace(source, target)
    return value


def field_has_value(field, field_id=None):
    resolved_id = field_id or str((field or {}).get("id") or "")
    return field_value_is_usable(resolved_id, (field or {}).get("value"))


def intake_document_evidence(case_id, organization_id):
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT documents.id, documents.slot, documents.file_name,
                   documents.scan_status, documents.ocr_text
            FROM documents
            JOIN ds160_cases ON ds160_cases.id = documents.case_id
            WHERE documents.case_id = ? AND ds160_cases.organization_id = ?
              AND documents.stored_path IS NOT NULL
            ORDER BY documents.created_at ASC
            """,
            (case_id, organization_id),
        ).fetchall()
    return [dict(row) for row in rows]


def ensure_intake_documents_ready(documents):
    unfinished = [
        item for item in (documents or [])
        if item.get("file_name") and item.get("scan_status") != "completed"
    ]
    if not unfinished:
        return
    labels = "、".join(
        str(item.get("slot") or item.get("file_name") or "客户材料")
        for item in unfinished[:4]
    )
    suffix = "等材料" if len(unfinished) > 4 else ""
    raise ValueError(
        f"请先完成 {labels}{suffix} 的扫描，再生成客户补充问卷。"
        "这样材料中已有的信息不会被重复询问。"
    )


def document_covered_field_ids(documents, extracted_fields=None):
    """A file slot is not evidence that every expected field was extracted."""
    completed_document_ids = {
        str(item.get("id") or "")
        for item in (documents or [])
        if item.get("scan_status") == "completed"
    }
    covered = set()
    for field in extracted_fields or []:
        if not field_has_value(field):
            continue
        source_id = str(field.get("sourceDocumentId") or "")
        extraction_method = str(field.get("extractionMethod") or "")
        if source_id and completed_document_ids and source_id not in completed_document_ids:
            continue
        if extraction_method in {"questionnaire", "client_intake"} or source_id or field.get("sourceDocument"):
            covered.add(field.get("id"))
    return {field_id for field_id in covered if field_id}


def intake_field_definitions(payload, covered_field_ids=None):
    selected_visa = selected_visa_id(payload.get("visaType"))
    existing = {item.get("id"): item for item in (payload.get("extractedFields") or [])}
    covered_field_ids = set(covered_field_ids or [])
    definitions = []
    for definition in CLIENT_INTAKE_FIELDS:
        if definition.get("visaTypes") and selected_visa not in definition["visaTypes"]:
            continue
        coverage_ids = [definition["id"], *(definition.get("coveredBy") or [])]
        if any(field_has_value(existing.get(field_id), field_id) for field_id in coverage_ids):
            continue
        if any(field_id in covered_field_ids for field_id in coverage_ids):
            continue
        definitions.append(dict(definition))
    return definitions


def intake_details_complete(item, covered_field_ids=None):
    covered_field_ids = set(covered_field_ids or [])
    details = item.get("details") or {}
    for field in active_detail_fields(item):
        if field.get("fieldId") in covered_field_ids:
            continue
        if field.get("required") and not str(details.get(field.get("id")) or "").strip():
            return False
    if item.get("recordFields") and (
        item.get("answerType") == "records"
        or item.get("answer") in (item.get("triggerValues") or ["yes"])
    ):
        records = item.get("records") or []
        if len(records) < int(item.get("minRecords", 1)):
            return False
        for record in records:
            for field in active_record_fields(item, record):
                if field.get("required") and not str(record.get(field.get("id")) or "").strip():
                    return False
    return True


def question_needs_client_input(item, by_id, covered_field_ids=None):
    if item.get("id") == "photo.upload_result":
        return False
    parent_id = item.get("parentQuestionId")
    if parent_id:
        parent = by_id.get(parent_id) or {}
        parent_answer = parent.get("answer")
        if parent_answer and parent_answer != "unknown" and parent_answer not in (item.get("parentValues") or ["yes"]):
            return False
    if item.get("clientResponse"):
        return False
    if item.get("clientOptional") and not item.get("clientSubmitted"):
        return True
    answer_type = item.get("answerType")
    if answer_type in {"details", "records"}:
        return not intake_details_complete(item, covered_field_ids)
    if not item.get("answer") or item.get("answer") == "unknown":
        return True
    return not intake_details_complete(item, covered_field_ids)


def public_question_definition(item, covered_field_ids=None):
    current_answer = item.get("answer") if item.get("answer") != "unknown" else ""
    covered_field_ids = set(covered_field_ids or [])
    detail_fields = []
    for field in item.get("detailFields") or []:
        if not dependent_field_visible(field, item.get("details") or {}):
            continue
        if field.get("fieldId") in covered_field_ids:
            continue
        if str((item.get("details") or {}).get(field.get("id")) or "").strip():
            continue
        detail_fields.append({
            "id": field.get("id"),
            "label": client_prompt(field.get("label")),
            "type": field.get("type") or "text",
            "required": bool(field.get("required")),
            "placeholder": field.get("placeholder") or "",
            "when": field.get("when") or [],
            "hideWhen": field.get("hideWhen") or {},
            "choices": field.get("choices") or [],
        })
    system_resolved_record_fields = (
        {"address", "city", "region", "postalCode", "country"}
        if item.get("id") == "work.education_secondary_or_above"
        else set()
    )
    record_fields = [
        {
            "id": field.get("id"),
            "label": client_prompt(field.get("label")),
            "type": field.get("type") or "text",
            "required": bool(field.get("required")),
            "hideWhen": field.get("hideWhen") or {},
            "choices": field.get("choices") or [],
        }
        for field in (item.get("recordFields") or [])
        if field.get("id") not in system_resolved_record_fields
    ]
    public_record_ids = {
        str(field.get("id") or "") for field in record_fields if field.get("id")
    }
    current_records = []
    for raw_record in item.get("records") or []:
        if not isinstance(raw_record, dict):
            continue
        record = {
            key: value for key, value in raw_record.items()
            if key in public_record_ids and str(value or "").strip()
        }
        if record:
            current_records.append(record)
    choices = []
    for choice in item.get("choices") or []:
        label = choice.get("label") or choice.get("value") or ""
        if choice.get("value") == "unknown":
            label = "不确定，请顾问协助确认"
        choices.append({"value": choice.get("value"), "label": label})
    return {
        "id": item.get("id"),
        "section": item.get("section") or "其他信息",
        "prompt": client_prompt(item.get("label")),
        "englishPrompt": item.get("englishLabel") or "",
        "answerType": item.get("answerType") or "yes_no",
        "choices": choices,
        "guidance": client_prompt(item.get("guidance")),
        "sensitive": bool(item.get("sensitive")),
        "currentAnswer": current_answer or "",
        "lockAnswer": bool(current_answer),
        "detailFields": detail_fields,
        "recordFields": record_fields,
        "currentRecords": current_records,
        "recordLabel": client_prompt(item.get("recordLabel") or "相关记录"),
        "minRecords": (
            0 if item.get("clientOptional")
            else int(item.get("minRecords", 1))
        ),
        "clientOptional": bool(item.get("clientOptional")),
        "answerSuggestions": item.get("answerSuggestions") or {},
        "triggerValues": item.get("triggerValues") or [],
        "parentQuestionId": item.get("parentQuestionId"),
        "parentValues": item.get("parentValues") or ["yes"],
    }


def apply_intake_document_inferences(questionnaire, documents):
    slots = {
        str(item.get("slot") or "").strip().lower()
        for item in (documents or []) if item.get("scan_status") == "completed"
    }
    has_itinerary = any("travel itinerary" in slot or "旅行行程" in slot for slot in slots)
    if has_itinerary:
        question_item = next(
            (item for item in questionnaire if item.get("id") == "travel.specific_plans"),
            None,
        )
        if question_item and not question_item.get("answer"):
            question_item.update({
                "answer": "yes",
                "source": "材料判断：旅行行程单",
                "autoDetermined": True,
                "answerConfidence": 0.99,
                "answerEvidence": "已上传并完成扫描的旅行行程单",
                "updatedAt": now_iso(),
            })
    return questionnaire


def build_public_intake_definition(payload, documents=None):
    documents = documents or []
    covered_field_ids = document_covered_field_ids(documents, payload.get("extractedFields"))
    questionnaire = build_questionnaire(
        payload.get("visaType"),
        payload.get("branchQuestionnaire"),
        payload.get("extractedFields"),
    )
    questionnaire, _ = infer_questionnaire_answers(
        questionnaire,
        [
            {"fileName": item.get("file_name"), "text": item.get("ocr_text") or ""}
            for item in documents
        ],
        payload.get("extractedFields"),
    )
    questionnaire = apply_intake_document_inferences(questionnaire, documents)
    questionnaire = build_questionnaire(
        payload.get("visaType"),
        questionnaire,
        payload.get("extractedFields"),
    )
    by_id = {item.get("id"): item for item in questionnaire}
    questions = []
    for item in questionnaire:
        if not question_needs_client_input(item, by_id, covered_field_ids):
            continue
        questions.append(public_question_definition(item, covered_field_ids))
    return {
        "schemaVersion": INTAKE_SCHEMA_VERSION,
        "fields": intake_field_definitions(payload, covered_field_ids),
        "questions": questions,
    }


def create_intake_link(case_id, user):
    stamped = now_iso()
    expires_at = (datetime.now(timezone.utc) + timedelta(days=INTAKE_LINK_TTL_DAYS)).isoformat()
    token = secrets.token_urlsafe(32)
    documents = intake_document_evidence(case_id, user["organizationId"])
    ensure_intake_documents_ready(documents)
    with connect() as conn:
        case_row = conn.execute(
            "SELECT id FROM ds160_cases WHERE id = ? AND organization_id = ?",
            (case_id, user["organizationId"]),
        ).fetchone()
        if not case_row:
            raise PermissionError("客户档案不存在或无权访问")
        conn.execute(
            "UPDATE intake_links SET status = 'revoked', updated_at = ? WHERE case_id = ? AND status IN ('pending', 'submitting')",
            (stamped, case_id),
        )
        conn.execute(
            """
            INSERT INTO intake_links (
              id, case_id, organization_id, token_hash, status,
              expires_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)
            """,
            (
                f"intake-{secrets.token_hex(12)}", case_id, user["organizationId"],
                intake_token_hash(token), expires_at, stamped, stamped,
            ),
        )
    return {"token": token, "status": "pending", "expiresAt": expires_at}


def intake_row_for_token(conn, token):
    return conn.execute(
        """
        SELECT intake_links.*, ds160_cases.payload_json, ds160_cases.owner_user_id,
               ds160_cases.organization_id AS case_organization_id
        FROM intake_links
        JOIN ds160_cases ON ds160_cases.id = intake_links.case_id
        WHERE intake_links.token_hash = ?
        """,
        (intake_token_hash(token),),
    ).fetchone()


def public_intake_payload(token):
    with connect() as conn:
        row = intake_row_for_token(conn, token)
    if not row:
        raise PermissionError("补充链接无效或已被重新生成")
    if row["status"] == "submitted":
        return {"status": "submitted", "submittedAt": row["submitted_at"]}
    if row["status"] != "pending":
        raise PermissionError("补充链接已失效，请联系顾问重新发送")
    if datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
        raise PermissionError("补充链接已过期，请联系顾问重新发送")
    payload = json.loads(row["payload_json"])
    documents = intake_document_evidence(row["case_id"], row["case_organization_id"])
    ensure_intake_documents_ready(documents)
    definition = build_public_intake_definition(payload, documents)
    try:
        draft = json.loads(row["draft_json"]) if row["draft_json"] else None
    except (TypeError, json.JSONDecodeError):
        draft = None
    return {
        "status": "pending",
        "caseId": row["case_id"],
        "applicantName": payload.get("applicantName") or "客户",
        "visaType": payload.get("visaType") or "",
        "expiresAt": row["expires_at"],
        "schemaVersion": definition["schemaVersion"],
        "fields": definition["fields"],
        "questions": definition["questions"],
        "draft": draft,
        "draftUpdatedAt": row["draft_updated_at"],
    }


def owner_for_intake_case(case_id):
    with connect() as conn:
        row = conn.execute(
            """
            SELECT users.*, organizations.name AS organization_name
            FROM ds160_cases
            JOIN users ON users.id = ds160_cases.owner_user_id
            LEFT JOIN organizations ON organizations.id = users.organization_id
            WHERE ds160_cases.id = ?
            """,
            (case_id,),
        ).fetchone()
    if not row:
        raise PermissionError("客户档案负责人账号不存在")
    return public_user(row)


def clean_intake_value(value, limit=8000):
    return str(value or "").strip()[:limit]


def clean_ds160_intake_value(value, limit=8000):
    return normalize_does_not_apply(clean_intake_value(value, limit))[:limit]


def prepare_question_text(value, field_spec, context):
    normalized = clean_ds160_intake_value(value, 4000)
    field_type = str((field_spec or {}).get("type") or "text").lower()
    field_id = str((field_spec or {}).get("fieldId") or (field_spec or {}).get("id") or "")
    choice_value = match_question_choice(normalized, (field_spec or {}).get("choices") or [])
    if choice_value:
        normalized = choice_value
    if not normalized or field_type in {"select", "date", "email", "tel"}:
        return {
            "value": normalized,
            "originalValue": "",
            "provider": "normalizer",
            "reviewRequired": False,
        }
    return translate_ds160_value(normalized, field_id=field_id, context=context)


def match_question_choice(value, choices):
    """Map a stored Chinese/English choice label back to its stable option value."""
    cleaned = clean_ds160_intake_value(value, 4000)
    if not cleaned:
        return ""
    aliases = {
        "是": "yes", "否": "no", "有": "yes", "没有": "no",
        "不适用": "DOES NOT APPLY", "无": "DOES NOT APPLY",
    }
    alias = aliases.get(cleaned)
    if alias:
        cleaned = alias
    folded = cleaned.casefold()
    for choice in choices or []:
        choice_value = str(choice.get("value") or "").strip()
        if folded == choice_value.casefold():
            return choice_value
        label = str(choice.get("label") or "").strip()
        label_parts = [part.strip() for part in re.split(r"[/·|]", label) if part.strip()]
        if folded in {part.casefold() for part in label_parts}:
            return choice_value
    return cleaned


def normalize_extracted_fields_language(fields):
    """Upgrade OCR, client and legacy field values to CEAC-safe English."""
    normalized_fields = []
    address_meta = {
        "contact.homeStreet1": ("line1", "家庭地址 · 街道地址 1", "medium"),
        "contact.homeStreet2": ("line2", "家庭地址 · 街道地址 2", "low"),
        "contact.homeCity": ("city", "家庭地址 · 城市", "medium"),
        "contact.homeRegion": ("region", "家庭地址 · 省、州或地区", "medium"),
        "contact.homePostalCode": ("postalCode", "家庭地址 · 邮编", "low"),
        "contact.homeCountry": ("country", "家庭地址 · 国家或地区", "medium"),
    }
    address_parts = {field_id: meta[0] for field_id, meta in address_meta.items()}
    raw_fields = [dict(field) for field in (fields or []) if isinstance(field, dict)]
    existing_ids = {str(field.get("id") or "") for field in raw_fields}
    repair_source = next((
        field for field in raw_fields
        if field.get("id") in address_parts
        and contains_cjk(field.get("originalValue"))
        and not field.get("editedByUser")
        and not field.get("confirmed")
    ), None)
    if repair_source:
        repaired_address = structure_address(repair_source.get("originalValue"), "CHINA")
        for field_id, (part, label, risk) in address_meta.items():
            if field_id in existing_ids or not repaired_address.get(part):
                continue
            raw_fields.append({
                **repair_source,
                "id": field_id,
                "label": label,
                "section": "地址 / 电话 / 社交媒体",
                "riskLevel": risk,
                "value": repaired_address[part],
                "confirmed": False,
                "editedByUser": False,
                "translationProvider": "address_parser_v2",
            })
            existing_ids.add(field_id)

    for raw_field in raw_fields:
        if not isinstance(raw_field, dict):
            continue
        field = dict(raw_field)
        field_id = str(field.get("id") or "")
        existing_provider = str(field.get("translationProvider") or "")
        original_value = field.get("originalValue")
        if (
            field_id in address_parts
            and contains_cjk(original_value)
            and not field.get("editedByUser")
            and not field.get("confirmed")
        ):
            repaired_address = structure_address(original_value, "CHINA")
            repaired_value = repaired_address.get(address_parts[field_id]) or ""
            if repaired_value:
                field["value"] = canonicalize_ds160_value(field_id, repaired_value)
                field["translationProvider"] = "address_parser_v2"
                field["requiresUserConfirmation"] = True
                field["autoVerified"] = False
                field["reviewReason"] = "中文地址已按行政区、道路和门牌重新整理，请对照原文核对"
                normalized_fields.append(field)
                continue
        source_value = field.get("value")
        if (
            existing_provider in {
                "local_transliteration", "local_glossary",
                "local_glossary_transliteration",
            }
            and contains_cjk(field.get("originalValue"))
        ):
            source_value = field.get("originalValue")
        translated = translate_ds160_value(
            source_value,
            field_id=field_id,
            context=field.get("label") or field_id,
            preserve_native=field_id == "personal.nativeName",
        )
        translated_value = canonicalize_ds160_value(field_id, translated.get("value"))
        if translated_value != field.get("value"):
            field["value"] = translated_value
        source_value = translated.get("originalValue") or ""
        if source_value and not field.get("originalValue"):
            field["originalValue"] = source_value
        if source_value:
            field["translationProvider"] = translated.get("provider") or "normalizer"
        if translated.get("reviewRequired"):
            field["requiresUserConfirmation"] = True
            field["autoVerified"] = False
            field["reviewReason"] = "中文原文已自动转写为 DS-160 英文格式，建议顾问核对含义"
        normalized_fields.append(field)
    return normalized_fields


def normalize_questionnaire_language(questionnaire):
    """Translate all free-text questionnaire branches while retaining source text."""
    normalized_questions = []
    for raw_question in questionnaire or []:
        if not isinstance(raw_question, dict):
            continue
        question = dict(raw_question)
        question_id = str(question.get("id") or "")
        answer = match_question_choice(question.get("answer"), question.get("choices") or [])
        if answer:
            question["answer"] = answer

        detail_specs = {
            str(item.get("id") or ""): item
            for item in (question.get("detailFields") or [])
            if isinstance(item, dict) and item.get("id")
        }
        details = dict(question.get("details") or {})
        for detail_id, value in list(details.items()):
            spec = dict(detail_specs.get(str(detail_id)) or {})
            spec["fieldId"] = f"{question_id}.{detail_id}"
            existing_provider = str(
                (question.get("translationProviders") or {}).get(detail_id) or ""
            )
            source_value = value
            original_value = (question.get("originalDetails") or {}).get(detail_id)
            if (
                existing_provider in {
                    "local_transliteration", "local_glossary",
                    "local_glossary_transliteration",
                }
                and contains_cjk(original_value)
            ):
                source_value = original_value
            prepared = prepare_question_text(
                source_value, spec,
                f"{question.get('label') or question_id} · {spec.get('label') or detail_id}",
            )
            details[detail_id] = prepared["value"]
            if prepared.get("originalValue"):
                question.setdefault("originalDetails", {}).setdefault(
                    detail_id, prepared["originalValue"]
                )
                question.setdefault("translationProviders", {})[detail_id] = prepared.get("provider")
        question["details"] = details

        record_specs = {
            str(item.get("id") or ""): item
            for item in (question.get("recordFields") or [])
            if isinstance(item, dict) and item.get("id")
        }
        normalized_records = []
        original_records = list(question.get("originalRecords") or [])
        for index, raw_record in enumerate(question.get("records") or []):
            if not isinstance(raw_record, dict):
                continue
            record = dict(raw_record)
            original_record = dict(original_records[index]) if index < len(original_records) else {}
            for record_id, value in list(record.items()):
                spec = dict(record_specs.get(str(record_id)) or {})
                spec["fieldId"] = f"{question_id}.{record_id}"
                source_value = original_record.get(record_id) or value
                prepared = prepare_question_text(
                    source_value, spec,
                    f"{question.get('label') or question_id} · {spec.get('label') or record_id}",
                )
                record[record_id] = prepared["value"]
                if prepared.get("originalValue"):
                    original_record.setdefault(record_id, prepared["originalValue"])
            normalized_records.append(record)
            if original_record:
                while len(original_records) <= index:
                    original_records.append({})
                original_records[index] = original_record
        question["records"] = normalized_records
        if original_records:
            question["originalRecords"] = original_records

        if question.get("clientResponse"):
            source_value = question.get("clientResponse")
            if (
                str(question.get("clientResponseTranslationProvider") or "") in {
                    "local_transliteration", "local_glossary",
                    "local_glossary_transliteration",
                }
                and contains_cjk(question.get("originalClientResponse"))
            ):
                source_value = question.get("originalClientResponse")
            prepared = translate_ds160_value(
                source_value,
                field_id=f"{question_id}.clientResponse",
                context=question.get("label") or question_id,
            )
            question["clientResponse"] = prepared["value"]
            if prepared.get("originalValue"):
                question.setdefault("originalClientResponse", prepared["originalValue"])
                question["clientResponseTranslationProvider"] = prepared.get("provider")
        normalized_questions.append(question)
    return normalized_questions


def normalize_case_language(payload):
    payload["extractedFields"] = normalize_extracted_fields_language(
        payload.get("extractedFields") or []
    )
    payload["branchQuestionnaire"] = normalize_questionnaire_language(
        payload.get("branchQuestionnaire") or []
    )
    payload["branchQuestionnaire"], _ = enrich_questionnaire_education(
        payload["branchQuestionnaire"]
    )
    payload["languageSchemaVersion"] = LANGUAGE_SCHEMA_VERSION
    return payload


CONSULTANT_QA_SOURCE = "客户原始问答（顾问粘贴）"


def refresh_consultant_information_summary(payload):
    """Expose every recognized questionnaire value, not only scalar fields."""
    known = dict(payload.get("knownInformation") or {})
    if not known.get("text"):
        return
    parsed_fields = [
        item for item in (known.get("parsedFields") or [])
        if str(item.get("value") or "").strip()
    ]
    value_keys = {f"field:{item.get('id')}" for item in parsed_fields if item.get("id")}
    question_summaries = []
    record_count = 0
    recognized_entries = [
        item for item in (known.get("recognizedEntries") or [])
        if isinstance(item, dict) and str(item.get("answer") or "").strip()
    ]

    for question in payload.get("branchQuestionnaire") or []:
        if question.get("source") != CONSULTANT_QA_SOURCE:
            continue
        answer = str(question.get("answer") or "").strip()
        choice = next((
            item for item in (question.get("choices") or [])
            if str(item.get("value") or "") == answer
        ), None)
        answer_label = str((choice or {}).get("label") or answer).strip()
        if answer:
            value_keys.add(f"question:{question.get('id')}:answer")

        original_details = question.get("originalDetails") or {}
        details = []
        for definition in active_detail_fields(question):
            detail_id = str(definition.get("id") or "")
            value = str((question.get("details") or {}).get(detail_id) or "").strip()
            if not value:
                continue
            detail_choice = next((
                item for item in (definition.get("choices") or [])
                if str(item.get("value") or "") == value
            ), None)
            display_value = str((detail_choice or {}).get("label") or value)
            field_id = str(definition.get("fieldId") or "")
            value_keys.add(
                f"field:{field_id}" if field_id
                else f"question:{question.get('id')}:detail:{detail_id}"
            )
            details.append({
                "id": detail_id,
                "label": definition.get("label") or detail_id,
                "value": display_value,
                "originalValue": str(original_details.get(detail_id) or "").strip(),
            })

        original_records = question.get("originalRecords") or []
        records = []
        for index, record in enumerate(question.get("records") or []):
            if not isinstance(record, dict):
                continue
            original_record = (
                original_records[index]
                if index < len(original_records) and isinstance(original_records[index], dict)
                else {}
            )
            values = []
            for definition in active_record_fields(question, record):
                record_id = str(definition.get("id") or "")
                value = str(record.get(record_id) or "").strip()
                if not value:
                    continue
                record_choice = next((
                    item for item in (definition.get("choices") or [])
                    if str(item.get("value") or "") == value
                ), None)
                display_value = str((record_choice or {}).get("label") or value)
                value_keys.add(
                    f"question:{question.get('id')}:record:{index}:{record_id}"
                )
                values.append({
                    "id": record_id,
                    "label": definition.get("label") or record_id,
                    "value": display_value,
                    "originalValue": str(original_record.get(record_id) or "").strip(),
                })
            if values:
                records.append(values)
        record_count += len(records)

        if not answer_label and not details and not records:
            continue
        question_summaries.append({
            "id": question.get("id"),
            "section": question.get("section") or "DS-160 问答",
            "label": question.get("label") or question.get("id"),
            "answer": answer_label,
            "recordLabel": question.get("recordLabel") or "记录",
            "details": details,
            "records": records,
            "confidence": question.get("answerConfidence"),
            "evidence": question.get("answerEvidence") or "",
        })

    known.update({
        "parsedFields": parsed_fields,
        "parsedQuestions": question_summaries,
        "parsedQuestionCount": len(question_summaries),
        "parsedRecordCount": record_count,
        "recognizedGroupCount": len(parsed_fields) + len(question_summaries),
        "recognizedValueCount": len(value_keys),
        "recognizedSourceCount": len(recognized_entries),
        "matchedSourceCount": sum(
            1 for item in recognized_entries if item.get("matched")
        ),
    })
    payload["knownInformation"] = known


def apply_consultant_questionnaire_updates(payload, updates):
    """Replace unconfirmed answers previously derived from the same pasted note."""
    cleaned_existing = []
    for raw_item in payload.get("branchQuestionnaire") or []:
        item = dict(raw_item)
        if item.get("source") == CONSULTANT_QA_SOURCE and not item.get("confirmedByUser"):
            item.update({
                "answer": "", "details": {}, "records": [], "clientResponse": "",
                "originalClientResponse": "", "originalDetails": {},
                "originalRecords": [], "translationProviders": {},
                "clientSubmitted": False, "autoDetermined": False,
                "answerConfidence": None, "answerEvidence": "", "updatedAt": "",
            })
        cleaned_existing.append(item)

    questionnaire = build_questionnaire(
        payload.get("visaType"), cleaned_existing, payload.get("extractedFields")
    )
    by_id = {item.get("id"): item for item in questionnaire}
    applied = 0
    for question_id, raw_update in (updates or {}).items():
        question = by_id.get(question_id)
        if not question or not isinstance(raw_update, dict):
            continue
        if question.get("confirmedByUser"):
            continue
        changed = False
        answer = str(raw_update.get("answer") or "").strip()
        valid_answers = {
            str(choice.get("value")) for choice in (question.get("choices") or [])
        }
        if answer and (not valid_answers or answer in valid_answers):
            question["answer"] = answer
            changed = True

        detail_ids = {
            str(item.get("id")) for item in (question.get("detailFields") or [])
            if item.get("id")
        }
        details = {
            key: clean_ds160_intake_value(value, 2000)
            for key, value in (raw_update.get("details") or {}).items()
            if key in detail_ids and clean_ds160_intake_value(value, 2000)
        }
        if details:
            question["details"] = details
            changed = True

        record_ids = {
            str(item.get("id")) for item in (question.get("recordFields") or [])
            if item.get("id")
        }
        records = []
        for raw_record in (raw_update.get("records") or [])[:100]:
            if not isinstance(raw_record, dict):
                continue
            record = {
                key: clean_ds160_intake_value(value, 2000)
                for key, value in raw_record.items()
                if key in record_ids and clean_ds160_intake_value(value, 2000)
            }
            if record:
                records.append(record)
        if records:
            question["records"] = records
            changed = True

        if not changed:
            continue
        question.update({
            "clientSubmitted": True,
            "confirmedByUser": False,
            "source": CONSULTANT_QA_SOURCE,
            "autoDetermined": False,
            "answerConfidence": raw_update.get("answerConfidence") or 0.99,
            "answerEvidence": clean_intake_value(raw_update.get("answerEvidence"), 1200),
            "updatedAt": now_iso(),
        })
        applied += 1
    rebuilt = build_questionnaire(
        payload.get("visaType"), list(by_id.values()), payload.get("extractedFields")
    )
    applied = 0
    for question in rebuilt:
        if question.get("source") != CONSULTANT_QA_SOURCE:
            continue
        has_content = bool(str(question.get("answer") or "").strip())
        has_content = has_content or any(
            str(value or "").strip()
            for value in (question.get("details") or {}).values()
        )
        has_content = has_content or any(
            any(str(value or "").strip() for value in record.values())
            for record in (question.get("records") or [])
            if isinstance(record, dict)
        )
        if has_content:
            applied += 1
            continue
        question.update({
            "source": "", "clientSubmitted": False, "autoDetermined": False,
            "answerConfidence": None, "answerEvidence": "", "updatedAt": "",
        })
    payload["branchQuestionnaire"] = rebuilt
    return applied


def apply_consultant_information(case_id, user, submitted):
    submitted = submitted if isinstance(submitted, dict) else {}
    note_text = clean_intake_value(submitted.get("text"), 20000)
    if not note_text:
        raise ValueError("请先粘贴客户已经提供的文字资料")
    payload = get_case_payload(case_id, user)
    parsed = parse_consultant_information(note_text)
    parsed_fields = parsed.get("fields") or []
    questionnaire_updates = parsed.get("questionnaireUpdates") or {}
    questionnaire_updates, school_lookup_count = enrich_education_updates(
        questionnaire_updates
    )
    education_records = (
        (questionnaire_updates.get("work.education_secondary_or_above") or {}).get("records")
        or []
    )
    school_lookup_review_count = sum(
        1 for record in education_records
        if isinstance(record, dict) and (
            record.get("schoolLookupStatus") in {"partial", "unresolved"}
            or record.get("schoolLookupReviewRequired")
        )
    )
    parse_warnings = list(parsed.get("warnings") or [])
    if school_lookup_review_count:
        parse_warnings.append(
            f"{school_lookup_review_count} 所学校的公开实体或地址证据仍不完整，已标记待核对，系统未虚构缺失信息"
        )
    if not parsed_fields and not questionnaire_updates:
        parse_warnings.append(
            "未发现可以可靠写入 DS-160 的字段，原文已保存；请补充更明确的事实后重新整理"
        )
    consultant_question_field_ids = {
        str(definition.get("fieldId"))
        for question in (payload.get("branchQuestionnaire") or [])
        if question.get("source") == CONSULTANT_QA_SOURCE
        for definition in active_detail_fields(question)
        if definition.get("fieldId")
    }
    replaceable_methods = {"consultant_text", "consultant_text_semantic"}
    retained_fields = []
    removed_field_ids = set()
    for field in payload.get("extractedFields") or []:
        replace_consultant_field = (
            field.get("extractionMethod") in replaceable_methods
            and not field.get("editedByUser")
            and not field.get("confirmed")
        )
        replace_synced_question_field = (
            field.get("extractionMethod") == "questionnaire"
            and field.get("id") in consultant_question_field_ids
            and not field.get("confirmed")
        )
        if replace_consultant_field or replace_synced_question_field:
            removed_field_ids.add(field.get("id"))
            continue
        retained_fields.append(field)
    merged_fields, conflicts = merge_extracted_fields(
        retained_fields, parsed_fields, payload.get("visaType") or ""
    )
    payload["extractedFields"] = merged_fields
    parsed_question_count = apply_consultant_questionnaire_updates(
        payload, questionnaire_updates
    )
    current_issues = {
        item.get("id"): item for item in (payload.get("validationResults") or [])
        if item.get("id") and not (
            item.get("id") in {
                f"ocr.low.{field_id}" for field_id in removed_field_ids if field_id
            }
            or (
                "顾问已知信息" in str(item.get("message") or "")
                and str(item.get("id") or "").startswith("ocr.conflict")
            )
        )
    }
    for issue in conflicts:
        current_issues[issue.get("id")] = issue
    payload["validationResults"] = list(current_issues.values())
    payload["knownInformation"] = {
        "text": note_text,
        "updatedAt": now_iso(),
        "analysisProviders": parsed.get("analysisProviders") or ["deterministic_rules"],
        "schoolLookupCount": school_lookup_count,
        "schoolLookupReviewCount": school_lookup_review_count,
        "semanticAddedCount": int(parsed.get("semanticAddedCount") or 0),
        "parsedQuestionCount": parsed_question_count,
        "qaPairCount": int(parsed.get("qaPairCount") or 0),
        "answeredQaCount": int(parsed.get("answeredQaCount") or 0),
        "matchedQaCount": int(parsed.get("matchedQaCount") or 0),
        "recognizedEntries": [
            {
                "number": item.get("number"),
                "question": clean_intake_value(item.get("question"), 1200),
                "answer": clean_intake_value(item.get("answer"), 5000),
                "mappedQuestionIds": list(item.get("mappedQuestionIds") or []),
                "mappedFieldIds": list(item.get("mappedFieldIds") or []),
                "matched": bool(item.get("matched")),
            }
            for item in (parsed.get("recognizedEntries") or [])
            if isinstance(item, dict) and str(item.get("answer") or "").strip()
        ],
        "warnings": parse_warnings,
        "parsedFields": [
            {
                "id": field.get("id"),
                "label": field.get("label"),
                "value": field.get("value"),
                "originalValue": field.get("originalValue") or "",
                "translationProvider": field.get("translationProvider") or "original",
            }
            for field in parsed_fields
        ],
    }
    saved = upsert_case(payload, user)
    saved_known = saved.get("knownInformation") or {}
    return {
        "case": saved,
        "parsedCount": len(parsed_fields),
        "semanticAddedCount": int(parsed.get("semanticAddedCount") or 0),
        "parsedQuestionCount": int(saved_known.get("parsedQuestionCount") or parsed_question_count),
        "qaPairCount": int(parsed.get("qaPairCount") or 0),
        "answeredQaCount": int(saved_known.get("answeredQaCount") or 0),
        "matchedQaCount": int(saved_known.get("matchedQaCount") or 0),
        "recognizedSourceCount": int(saved_known.get("recognizedSourceCount") or 0),
        "matchedSourceCount": int(saved_known.get("matchedSourceCount") or 0),
        "parsedRecordCount": int(saved_known.get("parsedRecordCount") or 0),
        "recognizedGroupCount": int(saved_known.get("recognizedGroupCount") or 0),
        "recognizedValueCount": int(saved_known.get("recognizedValueCount") or 0),
        "analysisProviders": parsed.get("analysisProviders") or ["deterministic_rules"],
        "schoolLookupCount": school_lookup_count,
        "schoolLookupReviewCount": school_lookup_review_count,
        "warnings": parse_warnings,
        "translatedCount": sum(
            1 for field in parsed_fields if field.get("originalValue")
        ),
        "translationReviewCount": sum(
            1 for field in parsed_fields
            if field.get("originalValue") and field.get("requiresUserConfirmation")
        ),
        "translation": translation_service_status(),
    }


def sanitize_intake_draft(submitted, definition):
    submitted = submitted if isinstance(submitted, dict) else {}
    allowed_fields = {item["id"]: item for item in definition["fields"]}
    allowed_questions = {item["id"]: item for item in definition["questions"]}
    fields = {}
    for field_id, raw_value in (submitted.get("fields") or {}).items():
        value = canonicalize_ds160_value(
            field_id, clean_ds160_intake_value(raw_value, 2000)
        )
        if field_id in allowed_fields and value:
            fields[field_id] = value

    questions = {}
    for question_id, raw_payload in (submitted.get("questions") or {}).items():
        spec = allowed_questions.get(question_id)
        if not spec or not isinstance(raw_payload, dict):
            continue
        answer = clean_intake_value(raw_payload.get("answer"), 100)
        valid_answers = {str(choice.get("value")) for choice in spec.get("choices") or []}
        if spec.get("lockAnswer") or (answer and valid_answers and answer not in valid_answers):
            answer = ""
        allowed_details = {item.get("id") for item in spec.get("detailFields") or []}
        details = {
            detail_id: clean_ds160_intake_value(value, 4000)
            for detail_id, value in (raw_payload.get("details") or {}).items()
            if detail_id in allowed_details and clean_ds160_intake_value(value, 4000)
        }
        record_specs = {item.get("id"): item for item in spec.get("recordFields") or []}
        records = []
        for raw_record in (raw_payload.get("records") or [])[:100]:
            if not isinstance(raw_record, dict):
                continue
            record = {}
            for field_id, field_spec in record_specs.items():
                value = clean_ds160_intake_value(raw_record.get(field_id), 1000)
                choices = {str(choice.get("value")) for choice in field_spec.get("choices") or []}
                if value and (not choices or value in choices):
                    record[field_id] = value
            if record:
                records.append(record)
        client_response = clean_ds160_intake_value(raw_payload.get("clientResponse"), 8000)
        if answer or details or records or client_response:
            questions[question_id] = {
                "answer": answer,
                "details": details,
                "records": records,
                "clientResponse": client_response,
            }

    try:
        section_index = max(0, min(50, int(submitted.get("sectionIndex") or 0)))
    except (TypeError, ValueError):
        section_index = 0
    return {
        "respondentName": clean_intake_value(submitted.get("respondentName"), 160),
        "sectionIndex": section_index,
        "fields": fields,
        "questions": questions,
    }


def save_client_intake_draft(token, submitted):
    with connect() as conn:
        row = intake_row_for_token(conn, token)
    if not row or row["status"] != "pending":
        raise PermissionError("补充链接无效、已提交或已被重新生成")
    if datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
        raise PermissionError("补充链接已过期，请联系顾问重新发送")
    payload = json.loads(row["payload_json"])
    documents = intake_document_evidence(row["case_id"], row["case_organization_id"])
    ensure_intake_documents_ready(documents)
    definition = build_public_intake_definition(payload, documents)
    draft = sanitize_intake_draft(submitted, definition)
    stamped = now_iso()
    with connect() as conn:
        cursor = conn.execute(
            """
            UPDATE intake_links
            SET draft_json = ?, draft_updated_at = ?, updated_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (json.dumps(draft, ensure_ascii=False), stamped, stamped, row["id"]),
        )
    if cursor.rowcount == 0:
        raise PermissionError("补充链接状态已变化，请刷新页面")
    return {"ok": True, "savedAt": stamped}


def intake_names_match(expected, submitted):
    def normalize(value):
        return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", str(value or "").lower())

    expected_normalized = normalize(expected)
    submitted_normalized = normalize(submitted)
    if not expected_normalized or not submitted_normalized:
        return False
    if expected_normalized == submitted_normalized:
        return True
    expected_tokens = sorted(re.findall(r"[a-z0-9]+", str(expected or "").lower()))
    submitted_tokens = sorted(re.findall(r"[a-z0-9]+", str(submitted or "").lower()))
    return bool(expected_tokens) and expected_tokens == submitted_tokens


def submit_client_intake(token, submitted):
    stamped = now_iso()
    with connect() as conn:
        row = intake_row_for_token(conn, token)
        if not row or row["status"] != "pending":
            raise PermissionError("补充链接无效、已提交或已被重新生成")
        if datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
            raise PermissionError("补充链接已过期，请联系顾问重新发送")

    payload = json.loads(row["payload_json"])
    respondent_name = clean_intake_value(submitted.get("respondentName"), 160)
    if not respondent_name:
        raise ValueError("请先填写申请人姓名，以便顾问核对客户档案")
    identity_match = intake_names_match(payload.get("applicantName"), respondent_name)
    documents = intake_document_evidence(row["case_id"], row["case_organization_id"])
    ensure_intake_documents_ready(documents)
    definition = build_public_intake_definition(payload, documents)
    allowed_fields = {item["id"]: item for item in definition["fields"]}
    normalized_submitted_fields = {}
    for field_id, spec in allowed_fields.items():
        translated = translate_ds160_value(
            clean_ds160_intake_value(
                (submitted.get("fields") or {}).get(field_id), 2000
            ),
            field_id=field_id,
            context=spec.get("label") or field_id,
            preserve_native=field_id == "personal.nativeName",
        )
        value = canonicalize_ds160_value(field_id, translated["value"])
        if value and spec.get("required") and not field_value_is_usable(field_id, value):
            raise ValueError(
                f"“{spec['label']}”无法匹配 DS-160 的可选项"
            )
        if value:
            normalized_submitted_fields[field_id] = {**translated, "value": value}
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current_row = intake_row_for_token(conn, token)
        if not current_row or current_row["status"] != "pending":
            raise PermissionError("补充链接无效、已提交或已被重新生成")
        conn.execute(
            "UPDATE intake_links SET status = 'submitting', updated_at = ? WHERE id = ?",
            (stamped, current_row["id"]),
        )
        row = current_row
    existing_fields = {item.get("id"): item for item in (payload.get("extractedFields") or [])}
    critical_ids = {
        "personal.surname", "personal.givenNames", "personal.dateOfBirth",
        "personal.birthCity", "personal.birthRegion", "personal.birthCountry",
        "personal.nationality", "personal.nationalId", "passport.number",
        "passport.issuingAuthority", "passport.issueCity", "passport.issueRegion",
        "passport.issueCountry", "passport.issueDate", "passport.expiration",
        "education.sevisId", "education.programNumber",
    }
    for field_id, prepared in normalized_submitted_fields.items():
        value = prepared["value"]
        spec = allowed_fields.get(field_id)
        if not spec or not value:
            continue
        existing_fields[field_id] = {
            **existing_fields.get(field_id, {}),
            "id": field_id,
            "label": spec["label"],
            "section": spec["section"],
            "value": value,
            "originalValue": prepared.get("originalValue") or "",
            "translationProvider": prepared.get("provider") or "original",
            "sourceDocument": "客户补充链接",
            "sourceDocumentId": None,
            "sourcePage": None,
            "evidence": "客户通过专属补充链接提交",
            "confidence": 1,
            "riskLevel": spec.get("riskLevel") or "medium",
            "requiresUserConfirmation": (
                field_id in critical_ids or prepared.get("reviewRequired", False)
            ),
            "confirmed": False,
            "editedByUser": False,
            "autoVerified": (
                field_id not in critical_ids and not prepared.get("reviewRequired", False)
            ),
            "clientProvided": True,
            "reviewReason": (
                "客户中文原文已自动转写，待顾问核对英文"
                if prepared.get("reviewRequired", False)
                else "客户提交，关键字段待顾问复核"
                if field_id in critical_ids else "客户直接提交"
            ),
            "extractionMethod": "client_intake",
        }
    payload["extractedFields"] = list(existing_fields.values())

    questionnaire = build_questionnaire(
        payload.get("visaType"), payload.get("branchQuestionnaire"), payload.get("extractedFields")
    )
    by_id = {item.get("id"): item for item in questionnaire}
    allowed_questions = {item["id"]: item for item in definition["questions"]}
    for question_id, answer_payload in (submitted.get("questions") or {}).items():
        question = by_id.get(question_id)
        public_definition = allowed_questions.get(question_id)
        if not question or not public_definition or not isinstance(answer_payload, dict):
            continue
        answer = clean_intake_value(answer_payload.get("answer"), 100)
        valid_answers = {str(choice.get("value")) for choice in (question.get("choices") or [])}
        if answer and (not valid_answers or answer in valid_answers):
            question["answer"] = answer
            question["autoDetermined"] = False
        allowed_detail_specs = {
            field.get("id"): field for field in (question.get("detailFields") or [])
        }
        for detail_id, detail_value in (answer_payload.get("details") or {}).items():
            detail_spec = allowed_detail_specs.get(detail_id)
            prepared = prepare_question_text(
                detail_value,
                detail_spec,
                f"{question.get('label') or question_id} · {(detail_spec or {}).get('label') or detail_id}",
            )
            if detail_spec and prepared["value"]:
                question.setdefault("details", {})[detail_id] = prepared["value"]
                if prepared.get("originalValue"):
                    question.setdefault("originalDetails", {})[detail_id] = prepared["originalValue"]
                    question.setdefault("translationProviders", {})[detail_id] = prepared.get("provider")
        allowed_record_fields = {
            field.get("id"): field for field in (question.get("recordFields") or [])
        }
        submitted_records = []
        submitted_original_records = []
        for raw_record in answer_payload.get("records") or []:
            if not isinstance(raw_record, dict):
                continue
            record = {}
            original_record = {}
            for field_id, field_spec in allowed_record_fields.items():
                prepared = prepare_question_text(
                    raw_record.get(field_id),
                    field_spec,
                    f"{question.get('label') or question_id} · {field_spec.get('label') or field_id}",
                )
                value = prepared["value"]
                allowed_choices = {
                    str(choice.get("value")) for choice in (field_spec.get("choices") or [])
                }
                if value and (not allowed_choices or value in allowed_choices):
                    record[field_id] = value
                    if prepared.get("originalValue"):
                        original_record[field_id] = prepared["originalValue"]
            if record:
                submitted_records.append(record)
                submitted_original_records.append(original_record)
        if submitted_records:
            question["records"] = submitted_records
            if any(submitted_original_records):
                question["originalRecords"] = submitted_original_records
            else:
                question.pop("originalRecords", None)
        client_response = prepare_question_text(
            answer_payload.get("clientResponse"),
            {"type": "textarea"},
            question.get("label") or question_id,
        )
        if client_response["value"]:
            question["clientResponse"] = client_response["value"]
            if client_response.get("originalValue"):
                question["originalClientResponse"] = client_response["originalValue"]
        if answer or client_response["value"] or submitted_records or any((answer_payload.get("details") or {}).values()):
            question["clientSubmitted"] = True
            question["confirmedByUser"] = False
            question["source"] = "客户补充链接"
            question["updatedAt"] = stamped

    payload["branchQuestionnaire"] = list(by_id.values())
    payload["currentStep"] = max(4, int(payload.get("currentStep") or 0))
    owner = owner_for_intake_case(row["case_id"])
    try:
        saved = upsert_case(payload, owner)
    except Exception:
        with connect() as conn:
            conn.execute(
                "UPDATE intake_links SET status = 'pending', updated_at = ? WHERE id = ? AND status = 'submitting'",
                (now_iso(), row["id"]),
            )
        raise
    with connect() as conn:
        conn.execute(
            """
            UPDATE intake_links
            SET status = 'submitted', respondent_name = ?, identity_match = ?,
                draft_json = NULL, draft_updated_at = NULL,
                submitted_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (respondent_name, 1 if identity_match else 0, stamped, stamped, row["id"]),
        )
        conn.execute(
            "INSERT INTO audit_logs (case_id, actor, action, payload_json, created_at) VALUES (?, ?, 'client_intake_submitted', ?, ?)",
            (
                row["case_id"], "客户补充链接",
                json.dumps({
                    "fieldCount": len(submitted.get("fields") or {}),
                    "questionCount": len(submitted.get("questions") or {}),
                    "respondentName": respondent_name,
                    "identityMatch": identity_match,
                }, ensure_ascii=False),
                stamped,
            ),
        )
    return {
        "ok": True,
        "status": "submitted",
        "caseId": saved.get("id"),
        "identityMatch": identity_match,
    }


def make_org_id():
    return f"org-{secrets.token_hex(12)}"


def make_user_id(email):
    digest = hashlib.sha256(normalize_org_name(email).encode("utf-8")).hexdigest()[:24]
    return f"user-{digest}"


def hash_password(password, salt=None, iterations=PASSWORD_ITERATIONS):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    return salt, digest.hex()


def verify_password(password, salt, expected_hash, iterations=PASSWORD_ITERATIONS):
    _, candidate = hash_password(password, salt, iterations)
    return hmac.compare_digest(candidate, expected_hash or "")


def key_fingerprint(value):
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def public_user(row):
    if not row:
        return None
    return {
        "id": row["id"],
        "identity": row["organization_name"] or "",
        "organizationId": row["organization_id"],
        "name": row["name"],
        "email": row["email"],
        "phone": row["phone"],
        "role": row["role"] or "copywriter",
        "accountKeyId": key_fingerprint(row["user_key"]),
        "emailVerified": bool(row["email_verified_at"]),
    }


def validate_registration(email, password, org_name, name, phone):
    validate_email_address(email)
    if len(password) < 8:
        raise ValueError("密码至少需要 8 位")
    if not org_name:
        raise ValueError("请输入机构或团队名称")
    if not name:
        raise ValueError("请输入联系人姓名")
    phone_digits = re.sub(r"\D", "", phone)
    if len(phone_digits) < 6 or len(phone_digits) > 20:
        raise ValueError("请输入有效的手机号")


def find_claimable_legacy_org(conn, org_name):
    return conn.execute(
        """
        SELECT organizations.id
        FROM organizations
        WHERE lower(trim(organizations.name)) = ?
          AND NOT EXISTS (
            SELECT 1
            FROM users
            WHERE users.organization_id = organizations.id
              AND users.password_hash IS NOT NULL
          )
        ORDER BY organizations.created_at ASC
        LIMIT 1
        """,
        (normalize_org_name(org_name),),
    ).fetchone()


def register_user(payload):
    email = normalize_org_name(payload.get("email"))
    password = payload.get("password") or ""
    org_name = (payload.get("organizationName") or payload.get("identity") or "").strip()
    name = (payload.get("name") or "").strip()
    phone = (payload.get("phone") or "").strip()
    email_code = payload.get("emailCode") or ""
    role = payload.get("role") or "copywriter"
    validate_registration(email, password, org_name, name, phone)
    if role not in {"copywriter", "consultant", "reviewer", "manager"}:
        role = "copywriter"

    with connect() as conn:
        if conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
            raise ValueError("无法完成注册，请检查输入后重试")
    verification_mode = registration_verification_mode()
    if verification_mode == "email":
        verify_and_consume_email_code(email, email_code)

    stamped = now_iso()
    user_id = make_user_id(email)
    salt, password_digest = hash_password(password, iterations=PASSWORD_ITERATIONS)
    user_key = secrets.token_urlsafe(32)

    with connect() as conn:
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            raise ValueError("无法完成注册，请检查输入后重试")
        legacy_org = find_claimable_legacy_org(conn, org_name)
        org_id = legacy_org["id"] if legacy_org else make_org_id()
        if legacy_org:
            conn.execute(
                "UPDATE organizations SET name = ?, updated_at = ? WHERE id = ?",
                (org_name, stamped, org_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO organizations (id, name, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (org_id, org_name, stamped, stamped),
            )
        conn.execute(
            """
            INSERT INTO users (
              id, organization_id, name, email, phone, password_hash,
              password_salt, password_iterations, user_key, role, email_verified_at,
              created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id, org_id, name, email, phone, password_digest, salt,
                PASSWORD_ITERATIONS, user_key, role,
                stamped if verification_mode == "email" else None,
                stamped, stamped,
            ),
        )
        if legacy_org:
            conn.execute(
                "UPDATE clients SET created_by_user_id = COALESCE(created_by_user_id, ?) WHERE organization_id = ?",
                (user_id, org_id),
            )
            conn.execute(
                "UPDATE ds160_cases SET owner_user_id = COALESCE(owner_user_id, ?) WHERE organization_id = ?",
                (user_id, org_id),
            )
        row = conn.execute(
            """
            SELECT users.*, organizations.name AS organization_name
            FROM users
            LEFT JOIN organizations ON organizations.id = users.organization_id
            WHERE users.id = ?
            """,
            (user_id,),
        ).fetchone()
    return public_user(row)


def login_user(payload):
    email = normalize_org_name(payload.get("email"))
    password = payload.get("password") or ""
    with connect() as conn:
        row = conn.execute(
            """
            SELECT users.*, organizations.name AS organization_name
            FROM users
            LEFT JOIN organizations ON organizations.id = users.organization_id
            WHERE users.email = ?
            """,
            (email,),
        ).fetchone()
    iterations = row["password_iterations"] if row and row["password_iterations"] else PASSWORD_ITERATIONS
    if not row or not row["password_hash"] or not verify_password(
        password, row["password_salt"], row["password_hash"], iterations
    ):
        raise ValueError("邮箱或密码不正确")
    return public_user(row)


def session_token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_auth_session(user_id):
    token = secrets.token_urlsafe(48)
    stamped = now_iso()
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=SESSION_MAX_AGE)).isoformat()
    with connect() as conn:
        conn.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (stamped,))
        conn.execute(
            """
            INSERT INTO auth_sessions (token_hash, user_id, expires_at, created_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_token_hash(token), user_id, expires_at, stamped, stamped),
        )
    return token


def token_from_cookie(cookie_header):
    for item in (cookie_header or "").split(";"):
        name, separator, value = item.strip().partition("=")
        if separator and name == AUTH_COOKIE:
            return value
    return ""


def authenticated_user(cookie_header):
    token = token_from_cookie(cookie_header)
    if not token:
        return None
    stamped = now_iso()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT users.*, organizations.name AS organization_name
            FROM auth_sessions
            JOIN users ON users.id = auth_sessions.user_id
            LEFT JOIN organizations ON organizations.id = users.organization_id
            WHERE auth_sessions.token_hash = ? AND auth_sessions.expires_at > ?
            """,
            (session_token_hash(token), stamped),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE auth_sessions SET last_seen_at = ? WHERE token_hash = ?",
                (stamped, session_token_hash(token)),
            )
    return public_user(row)


def delete_auth_session(cookie_header):
    token = token_from_cookie(cookie_header)
    if not token:
        return
    with connect() as conn:
        conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (session_token_hash(token),))


class BillingConfigurationError(RuntimeError):
    pass


class BillingProviderError(RuntimeError):
    pass


def billing_settings():
    selected_provider = os.environ.get(
        "PAYMENT_PROVIDER", "four_party_aggregate"
    ).strip().lower()
    if selected_provider != "stripe":
        return {
            "provider": "four_party_aggregate",
            "providerLabel": "四方聚合支付",
            "configured": False,
            "checkoutConfigured": False,
            "webhookConfigured": False,
            "publicBaseUrlConfigured": False,
            "mode": "pending_integration",
            "message": "四方聚合支付待接入：取得服务商接口文档、商户号和签名规则后再启用真实交易。",
        }
    secret_key = os.environ.get("STRIPE_SECRET_KEY", "").strip()
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
    public_base_url = os.environ.get("BILLING_PUBLIC_BASE_URL", "").strip().rstrip("/")
    configured = bool(secret_key and webhook_secret and public_base_url)
    return {
        "provider": BILLING_PROVIDER,
        "providerLabel": "Stripe",
        "configured": configured,
        "checkoutConfigured": bool(secret_key and public_base_url),
        "webhookConfigured": bool(webhook_secret),
        "publicBaseUrlConfigured": bool(public_base_url),
        "mode": "live" if secret_key.startswith("sk_live_") else "test" if secret_key else "unconfigured",
        "message": (
            "Stripe 已配置，可创建真实支付订单。"
            if configured else
            "支付通道尚未配置：需要 STRIPE_SECRET_KEY、STRIPE_WEBHOOK_SECRET 和 BILLING_PUBLIC_BASE_URL。"
        ),
    }


def public_billing_product(row):
    return {
        "id": row["id"], "name": row["name"],
        "description": row["description"] or "", "amount": row["amount"],
        "currency": row["currency"], "durationDays": row["duration_days"],
        "active": bool(row["active"]),
    }


def public_billing_order(row):
    return {
        "id": row["id"], "productId": row["product_id"],
        "amount": row["amount"], "currency": row["currency"],
        "status": row["status"], "provider": row["provider"],
        "providerCheckoutId": row["provider_checkout_id"],
        "providerPaymentId": row["provider_payment_id"],
        "checkoutUrl": row["checkout_url"], "paidAt": row["paid_at"],
        "expiresAt": row["expires_at"], "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def public_billing_refund(row):
    return {
        "id": row["id"], "orderId": row["order_id"],
        "providerRefundId": row["provider_refund_id"],
        "amount": row["amount"], "currency": row["currency"],
        "status": row["status"], "reason": row["reason"] or "",
        "createdAt": row["created_at"], "updatedAt": row["updated_at"],
    }


def public_membership(row):
    if not row:
        return {"status": "inactive", "active": False}
    active = row["status"] == "active" and datetime.fromisoformat(
        row["current_period_end"]
    ) > datetime.now(timezone.utc)
    inactive_status = row["status"] if row["status"] in {"expired", "revoked"} else "expired"
    return {
        "id": row["id"], "status": row["status"] if active else inactive_status,
        "active": active, "productId": row["product_id"],
        "sourceOrderId": row["source_order_id"], "startsAt": row["starts_at"],
        "currentPeriodEnd": row["current_period_end"],
        "updatedAt": row["updated_at"],
    }


def active_membership_for_user(user):
    if not user or not user.get("organizationId"):
        return None
    with connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM billing_subscriptions
            WHERE organization_id = ? AND status = 'active' AND current_period_end > ?
            """,
            (user["organizationId"], now_iso()),
        ).fetchone()
    return public_membership(row) if row else None


def workspace_membership_required(path):
    return (
        path == "/api/cases"
        or path.startswith("/api/cases/")
        or path == "/api/ocr/start"
        or path == "/api/ocr/health"
    )


def billing_summary(user):
    with connect() as conn:
        product_rows = conn.execute(
            "SELECT * FROM billing_products WHERE active = 1 ORDER BY amount"
        ).fetchall()
        membership = conn.execute(
            "SELECT * FROM billing_subscriptions WHERE organization_id = ?",
            (user["organizationId"],),
        ).fetchone()
        order_rows = conn.execute(
            "SELECT * FROM billing_orders WHERE organization_id = ? ORDER BY created_at DESC LIMIT 50",
            (user["organizationId"],),
        ).fetchall()
        refund_rows = conn.execute(
            "SELECT * FROM billing_refunds WHERE organization_id = ? ORDER BY created_at DESC LIMIT 50",
            (user["organizationId"],),
        ).fetchall()
    return {
        "gateway": billing_settings(),
        "products": [public_billing_product(row) for row in product_rows],
        "membership": public_membership(membership),
        "orders": [public_billing_order(row) for row in order_rows],
        "refunds": [public_billing_refund(row) for row in refund_rows],
    }


def stripe_request(method, path, fields=None, *, idempotency_key=""):
    secret_key = os.environ.get("STRIPE_SECRET_KEY", "").strip()
    if not secret_key:
        raise BillingConfigurationError("Stripe 商户密钥尚未配置，不能创建真实交易。")
    request = Request(
        f"https://api.stripe.com/v1/{path.lstrip('/')}",
        data=urlencode(fields or {}, doseq=True).encode("utf-8") if method != "GET" else None,
        method=method,
        headers={
            "Authorization": f"Bearer {secret_key}",
            "Content-Type": "application/x-www-form-urlencoded",
            **({"Idempotency-Key": idempotency_key} if idempotency_key else {}),
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        try:
            payload = json.loads(error.read().decode("utf-8"))
            message = (payload.get("error") or {}).get("message")
        except (json.JSONDecodeError, UnicodeDecodeError):
            message = ""
        raise BillingProviderError(message or f"支付网关请求失败（HTTP {error.code}）") from error
    except URLError as error:
        raise BillingProviderError("暂时无法连接支付网关，请稍后重试。") from error


def create_checkout_order(payload, user):
    settings = billing_settings()
    if not settings["configured"]:
        raise BillingConfigurationError(settings["message"])
    product_id = str(payload.get("productId") or "").strip()
    with connect() as conn:
        product = conn.execute(
            "SELECT * FROM billing_products WHERE id = ? AND active = 1", (product_id,)
        ).fetchone()
    if not product:
        raise ValueError("会员商品不存在或已下架")

    order_id = f"order-{secrets.token_hex(12)}"
    stamped = now_iso()
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO billing_orders (
              id, organization_id, user_id, product_id, amount, currency,
              status, provider, expires_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'creating', ?, ?, ?, ?)
            """,
            (
                order_id, user["organizationId"], user["id"], product["id"],
                product["amount"], product["currency"], BILLING_PROVIDER,
                expires_at, stamped, stamped,
            ),
        )

    base_url = os.environ["BILLING_PUBLIC_BASE_URL"].strip().rstrip("/")
    try:
        checkout = stripe_request(
            "POST", "checkout/sessions",
            {
                "mode": "payment",
                "success_url": f"{base_url}/membership?checkout=success&session_id={{CHECKOUT_SESSION_ID}}",
                "cancel_url": f"{base_url}/membership?checkout=cancelled",
                "client_reference_id": order_id,
                "customer_email": user.get("email") or "",
                "line_items[0][price_data][currency]": product["currency"],
                "line_items[0][price_data][unit_amount]": str(product["amount"]),
                "line_items[0][price_data][product_data][name]": product["name"],
                "line_items[0][price_data][product_data][description]": product["description"] or "",
                "line_items[0][quantity]": "1",
                "metadata[order_id]": order_id,
                "metadata[organization_id]": user["organizationId"],
                "metadata[product_id]": product["id"],
                "payment_intent_data[metadata][order_id]": order_id,
                "expires_at": str(int((datetime.now(timezone.utc) + timedelta(minutes=30)).timestamp())),
            },
            idempotency_key=order_id,
        )
    except (BillingProviderError, BillingConfigurationError):
        with connect() as conn:
            conn.execute(
                "UPDATE billing_orders SET status = 'failed', updated_at = ? WHERE id = ?",
                (now_iso(), order_id),
            )
        raise
    if not checkout.get("id") or not checkout.get("url"):
        raise BillingProviderError("支付网关没有返回有效收银台地址")
    with connect() as conn:
        conn.execute(
            """
            UPDATE billing_orders
            SET status = 'pending', provider_checkout_id = ?, checkout_url = ?,
                updated_at = ? WHERE id = ?
            """,
            (checkout["id"], checkout["url"], now_iso(), order_id),
        )
        row = conn.execute("SELECT * FROM billing_orders WHERE id = ?", (order_id,)).fetchone()
    return public_billing_order(row)


def order_status(order_id, user):
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM billing_orders WHERE id = ? AND organization_id = ?",
            (order_id, user["organizationId"]),
        ).fetchone()
    if not row:
        raise FileNotFoundError("订单不存在")
    return public_billing_order(row)


def recalculate_membership(conn, organization_id, stamped):
    eligible = conn.execute(
        """
        SELECT billing_orders.*, billing_products.duration_days
        FROM billing_orders
        JOIN billing_products ON billing_products.id = billing_orders.product_id
        WHERE billing_orders.organization_id = ?
          AND billing_orders.status IN ('paid', 'partially_refunded')
          AND billing_orders.paid_at IS NOT NULL
        ORDER BY billing_orders.paid_at, billing_orders.created_at
        """,
        (organization_id,),
    ).fetchall()
    if not eligible:
        conn.execute(
            """
            UPDATE billing_subscriptions
            SET status = 'revoked', current_period_end = ?, updated_at = ?
            WHERE organization_id = ?
            """,
            (stamped, stamped, organization_id),
        )
        return
    starts_at = eligible[0]["paid_at"]
    period_end = None
    for item in eligible:
        paid_at = datetime.fromisoformat(item["paid_at"])
        period_base = max(paid_at, period_end) if period_end else paid_at
        period_end = period_base + timedelta(days=item["duration_days"])
    latest = eligible[-1]
    status = "active" if period_end > datetime.now(timezone.utc) else "expired"
    conn.execute(
        """
        INSERT INTO billing_subscriptions (
          id, organization_id, product_id, source_order_id, status,
          starts_at, current_period_end, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(organization_id) DO UPDATE SET
          product_id = excluded.product_id, source_order_id = excluded.source_order_id,
          status = excluded.status, starts_at = excluded.starts_at,
          current_period_end = excluded.current_period_end,
          updated_at = excluded.updated_at
        """,
        (
            f"membership-{organization_id}", organization_id, latest["product_id"],
            latest["id"], status, starts_at, period_end.isoformat(), stamped, stamped,
        ),
    )


def mark_order_paid(conn, order, payment_id, stamped, source_reference):
    if order["status"] == "paid":
        return
    conn.execute(
        """
        UPDATE billing_orders SET status = 'paid', provider_payment_id = ?,
          paid_at = ?, updated_at = ? WHERE id = ?
        """,
        (payment_id, stamped, stamped, order["id"]),
    )
    conn.execute(
        """
        INSERT INTO payment_transactions (
          id, order_id, organization_id, provider,
          provider_transaction_id, transaction_type, amount,
          currency, status, payload_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'payment', ?, ?, 'succeeded', ?, ?, ?)
        """,
        (
            f"payment-{secrets.token_hex(12)}", order["id"],
            order["organization_id"], BILLING_PROVIDER, payment_id,
            order["amount"], order["currency"],
            json.dumps({"source": source_reference}, ensure_ascii=False), stamped, stamped,
        ),
    )
    recalculate_membership(conn, order["organization_id"], stamped)


def refresh_checkout_order(order_id, user):
    with connect() as conn:
        order = conn.execute(
            "SELECT * FROM billing_orders WHERE id = ? AND organization_id = ?",
            (order_id, user["organizationId"]),
        ).fetchone()
    if not order:
        raise FileNotFoundError("订单不存在")
    if not order["provider_checkout_id"]:
        raise ValueError("订单尚未生成支付网关单号")
    checkout = stripe_request(
        "GET", f"checkout/sessions/{quote(order['provider_checkout_id'], safe='')}"
    )
    if int(checkout.get("amount_total") or 0) != order["amount"] or str(
        checkout.get("currency") or ""
    ).lower() != order["currency"]:
        raise ValueError("支付网关返回的金额或币种与订单不一致")
    stamped = now_iso()
    with connect() as conn:
        current = conn.execute(
            "SELECT * FROM billing_orders WHERE id = ?", (order["id"],)
        ).fetchone()
        if checkout.get("payment_status") == "paid":
            mark_order_paid(
                conn, current,
                str(checkout.get("payment_intent") or checkout.get("id") or ""),
                stamped, f"stripe-query:{checkout.get('id')}",
            )
        elif checkout.get("status") == "expired" and current["status"] == "pending":
            conn.execute(
                "UPDATE billing_orders SET status = 'expired', updated_at = ? WHERE id = ?",
                (stamped, current["id"]),
            )
        refreshed = conn.execute(
            "SELECT * FROM billing_orders WHERE id = ?", (order["id"],)
        ).fetchone()
    return public_billing_order(refreshed)


def verify_stripe_signature(raw_body, signature_header, webhook_secret, tolerance=300):
    pairs = {}
    for item in (signature_header or "").split(","):
        name, separator, value = item.partition("=")
        if separator:
            pairs.setdefault(name.strip(), []).append(value.strip())
    timestamp_values = pairs.get("t") or []
    signatures = pairs.get("v1") or []
    if not timestamp_values or not signatures:
        raise PermissionError("Stripe 回调签名格式无效")
    try:
        timestamp = int(timestamp_values[0])
    except ValueError as error:
        raise PermissionError("Stripe 回调时间戳无效") from error
    if abs(int(time.time()) - timestamp) > tolerance:
        raise PermissionError("Stripe 回调已过期")
    signed = f"{timestamp}.".encode("utf-8") + raw_body
    expected = hmac.new(webhook_secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, candidate) for candidate in signatures):
        raise PermissionError("Stripe 回调验签失败")


def process_stripe_webhook(raw_body, signature_header):
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
    if not webhook_secret:
        raise BillingConfigurationError("Stripe Webhook 密钥尚未配置")
    verify_stripe_signature(raw_body, signature_header, webhook_secret)
    try:
        event = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Stripe 回调内容不是有效 JSON") from error
    event_id = str(event.get("id") or "")
    event_type = str(event.get("type") or "")
    if not event_id or not event_type:
        raise ValueError("Stripe 回调缺少事件标识")
    stamped = now_iso()
    payload_hash = hashlib.sha256(raw_body).hexdigest()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT provider_event_id FROM billing_webhook_events WHERE provider_event_id = ?",
            (event_id,),
        ).fetchone()
        if existing:
            return {"received": True, "duplicate": True}
        obj = ((event.get("data") or {}).get("object") or {})
        if event_type in {"checkout.session.completed", "checkout.session.async_payment_succeeded"}:
            order_id = str((obj.get("metadata") or {}).get("order_id") or obj.get("client_reference_id") or "")
            order = conn.execute("SELECT * FROM billing_orders WHERE id = ?", (order_id,)).fetchone()
            if order:
                amount_total = int(obj.get("amount_total") or 0)
                currency = str(obj.get("currency") or "").lower()
                paid = obj.get("payment_status") == "paid"
                if amount_total != order["amount"] or currency != order["currency"]:
                    raise ValueError("支付金额或币种与订单不一致")
                if paid and order["status"] != "paid":
                    payment_id = str(obj.get("payment_intent") or obj.get("id") or "")
                    mark_order_paid(conn, order, payment_id, stamped, f"stripe-webhook:{event_id}")
        elif event_type == "checkout.session.expired":
            order_id = str((obj.get("metadata") or {}).get("order_id") or obj.get("client_reference_id") or "")
            conn.execute(
                "UPDATE billing_orders SET status = 'expired', updated_at = ? WHERE id = ? AND status = 'pending'",
                (stamped, order_id),
            )
        elif event_type == "refund.updated":
            refund_id = str(obj.get("id") or "")
            status = "succeeded" if obj.get("status") == "succeeded" else str(obj.get("status") or "pending")
            refund = conn.execute(
                "SELECT * FROM billing_refunds WHERE provider_refund_id = ?",
                (refund_id,),
            ).fetchone()
            conn.execute(
                "UPDATE billing_refunds SET status = ?, updated_at = ? WHERE provider_refund_id = ?",
                (status, stamped, refund_id),
            )
            if refund and status == "succeeded":
                order = conn.execute(
                    "SELECT * FROM billing_orders WHERE id = ?", (refund["order_id"],)
                ).fetchone()
                refunded_amount = conn.execute(
                    "SELECT COALESCE(SUM(amount), 0) AS amount FROM billing_refunds WHERE order_id = ? AND status = 'succeeded'",
                    (refund["order_id"],),
                ).fetchone()["amount"]
                order_status = "refunded" if refunded_amount >= order["amount"] else "partially_refunded"
                conn.execute(
                    "UPDATE billing_orders SET status = ?, updated_at = ? WHERE id = ?",
                    (order_status, stamped, order["id"]),
                )
                recalculate_membership(conn, order["organization_id"], stamped)
        elif event_type == "charge.refunded":
            payment_id = str(obj.get("payment_intent") or "")
            order = conn.execute(
                "SELECT * FROM billing_orders WHERE provider_payment_id = ?", (payment_id,)
            ).fetchone()
            if order:
                amount_refunded = int(obj.get("amount_refunded") or 0)
                order_status = "refunded" if amount_refunded >= order["amount"] else "partially_refunded"
                conn.execute(
                    "UPDATE billing_orders SET status = ?, updated_at = ? WHERE id = ?",
                    (order_status, stamped, order["id"]),
                )
                recalculate_membership(conn, order["organization_id"], stamped)
        conn.execute(
            """
            INSERT INTO billing_webhook_events (
              provider_event_id, provider, event_type, status,
              payload_sha256, processed_at
            ) VALUES (?, ?, ?, 'processed', ?, ?)
            """,
            (event_id, BILLING_PROVIDER, event_type, payload_hash, stamped),
        )
    return {"received": True, "duplicate": False}


def create_refund(order_id, payload, user):
    refund_id = f"refund-{secrets.token_hex(12)}"
    stamped = now_iso()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        order = conn.execute(
            "SELECT * FROM billing_orders WHERE id = ? AND organization_id = ?",
            (order_id, user["organizationId"]),
        ).fetchone()
        if not order:
            raise FileNotFoundError("订单不存在")
        if order["status"] not in {"paid", "partially_refunded"} or not order["provider_payment_id"]:
            raise ValueError("只有已支付订单可以退款")
        refunded = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS amount FROM billing_refunds WHERE order_id = ? AND status IN ('creating', 'pending', 'succeeded')",
            (order_id,),
        ).fetchone()["amount"]
        available = order["amount"] - int(refunded or 0)
        amount = int(payload.get("amount") or available)
        if amount <= 0 or amount > available:
            raise ValueError("退款金额超出可退金额")
        reason = str(payload.get("reason") or "requested_by_customer").strip()[:240]
        conn.execute(
            """
            INSERT INTO billing_refunds (
              id, order_id, organization_id, requested_by_user_id, amount,
              currency, status, reason, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'creating', ?, ?, ?)
            """,
            (
                refund_id, order["id"], order["organization_id"], user["id"],
                amount, order["currency"], reason, stamped, stamped,
            ),
        )
    try:
        stripe_refund = stripe_request(
            "POST", "refunds",
            {"payment_intent": order["provider_payment_id"], "amount": str(amount), "metadata[order_id]": order["id"]},
            idempotency_key=refund_id,
        )
    except (BillingProviderError, BillingConfigurationError):
        with connect() as conn:
            conn.execute("UPDATE billing_refunds SET status = 'failed', updated_at = ? WHERE id = ?", (now_iso(), refund_id))
        raise
    provider_status = str(stripe_refund.get("status") or "pending")
    status = (
        "succeeded" if provider_status == "succeeded"
        else "failed" if provider_status in {"failed", "canceled"}
        else "pending"
    )
    with connect() as conn:
        conn.execute(
            "UPDATE billing_refunds SET provider_refund_id = ?, status = ?, updated_at = ? WHERE id = ?",
            (stripe_refund.get("id"), status, now_iso(), refund_id),
        )
        if status == "succeeded":
            new_order_status = "refunded" if amount == available else "partially_refunded"
            conn.execute("UPDATE billing_orders SET status = ?, updated_at = ? WHERE id = ?", (new_order_status, now_iso(), order["id"]))
            recalculate_membership(conn, order["organization_id"], now_iso())
        row = conn.execute("SELECT * FROM billing_refunds WHERE id = ?", (refund_id,)).fetchone()
    return public_billing_refund(row)


def upsert_case(payload, user):
    payload = json.loads(json.dumps(payload, ensure_ascii=False))
    case_id = payload.get("id")
    if not case_id:
        raise ValueError("case payload requires id")

    stamped = now_iso()
    payload["extractedFields"] = [
        field for field in (payload.get("extractedFields") or [])
        if field.get("id") != "application.plannedSubmissionDate"
    ]
    payload["extractedFields"] = normalize_extracted_fields_language(
        payload.get("extractedFields")
    )
    case_meta = payload.get("caseMeta") or payload.get("partnerMeta") or {}
    payload["caseMeta"] = case_meta
    payload.pop("partnerMeta", None)
    payload["branchQuestionnaire"] = build_questionnaire(
        payload.get("visaType"),
        payload.get("branchQuestionnaire"),
        payload.get("extractedFields"),
    )
    payload["branchQuestionnaire"] = normalize_questionnaire_language(
        payload.get("branchQuestionnaire")
    )
    payload["branchQuestionnaire"], _ = enrich_questionnaire_education(
        payload["branchQuestionnaire"]
    )
    payload["extractedFields"] = sync_questionnaire_fields(
        payload.get("extractedFields"), payload["branchQuestionnaire"]
    )
    payload["extractedFields"] = normalize_extracted_fields_language(
        payload.get("extractedFields")
    )
    refresh_consultant_information_summary(payload)
    payload["branchQuestionnaireVersion"] = RULESET_VERSION
    payload["intakeSchemaVersion"] = INTAKE_SCHEMA_VERSION
    payload["languageSchemaVersion"] = LANGUAGE_SCHEMA_VERSION
    existing_validation = payload.get("validationResults") or []
    payload["validationResults"] = [
        item for item in existing_validation
        if not str(item.get("id", "")).startswith("branch.")
        and item.get("id") != "sensitive.refusal"
    ] + questionnaire_issues(payload["branchQuestionnaire"], existing_validation)
    org_name = user["identity"]
    org_id = user["organizationId"]
    client_id = "client-" + case_id
    owner_name = case_meta.get("owner") or user["name"]
    owner_user_id = user["id"]
    status = status_for_step(payload.get("currentStep", 0))
    case_meta["organizationName"] = org_name
    case_meta["organizationId"] = org_id
    case_meta["ownerUserId"] = owner_user_id
    case_meta["accountKeyId"] = user["accountKeyId"]
    case_meta["status"] = status
    case_meta.pop("userKey", None)
    case_meta.pop("ownerPhone", None)
    for index, document in enumerate(payload.get("documents") or []):
        document["id"] = document.get("id") or f"{case_id}-doc-{index}"

    with connect() as conn:
        existing_case = conn.execute(
            "SELECT organization_id, client_id FROM ds160_cases WHERE id = ?",
            (case_id,),
        ).fetchone()
        if existing_case and existing_case["organization_id"] != org_id:
            raise PermissionError("无权修改其他机构的客户档案")
        if existing_case and existing_case["client_id"]:
            client_id = existing_case["client_id"]

        existing_client = conn.execute(
            "SELECT record_key FROM clients WHERE id = ?",
            (client_id,),
        ).fetchone()
        record_key = (
            existing_client["record_key"]
            if existing_client and existing_client["record_key"]
            else secrets.token_urlsafe(32)
        )

        conn.execute(
            """
            INSERT INTO clients (
              id, organization_id, created_by_user_id, record_key,
              full_name, passport_number, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              organization_id = excluded.organization_id,
              created_by_user_id = COALESCE(clients.created_by_user_id, excluded.created_by_user_id),
              record_key = COALESCE(clients.record_key, excluded.record_key),
              full_name = excluded.full_name,
              passport_number = excluded.passport_number,
              updated_at = excluded.updated_at
            """,
            (
                client_id, org_id, owner_user_id, record_key,
                payload.get("applicantName") or "未命名客户",
                case_meta.get("passportNumber"), stamped, stamped,
            ),
        )

        conn.execute(
            """
            INSERT INTO ds160_cases (
              id, client_id, organization_id, owner_user_id, owner_name, visa_type, status,
              current_step, source_type, review_priority, notes, payload_json,
              created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              client_id = excluded.client_id,
              organization_id = excluded.organization_id,
              owner_user_id = excluded.owner_user_id,
              owner_name = excluded.owner_name,
              visa_type = excluded.visa_type,
              status = excluded.status,
              current_step = excluded.current_step,
              source_type = excluded.source_type,
              review_priority = excluded.review_priority,
              notes = excluded.notes,
              payload_json = excluded.payload_json,
              updated_at = excluded.updated_at
            """,
            (
                case_id, client_id, org_id, owner_user_id, owner_name, payload.get("visaType") or "",
                status, int(payload.get("currentStep") or 0), case_meta.get("sourceType"),
                case_meta.get("reviewPriority"), case_meta.get("notes"),
                json.dumps(payload, ensure_ascii=False), payload.get("createdAt") or stamped, stamped,
            ),
        )

        for index, item in enumerate(payload.get("documents") or []):
            document_id = item.get("id") or f"{case_id}-doc-{index}"
            initial_status = item.get("scanStatus") or ("uploaded" if item.get("fileName") else "empty")
            conn.execute(
                """
                INSERT INTO documents (
                  id, case_id, slot, file_name, scan_status, scan_message,
                  created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  slot = excluded.slot,
                  file_name = CASE
                    WHEN excluded.file_name IS NOT NULL AND excluded.file_name <> ''
                    THEN excluded.file_name ELSE documents.file_name
                  END,
                  updated_at = excluded.updated_at
                """,
                (
                    document_id, case_id, item.get("slot") or "", item.get("fileName") or "",
                    initial_status, item.get("scanMessage") or "", stamped, stamped,
                ),
            )

        conn.execute("DELETE FROM ds160_fields WHERE case_id = ?", (case_id,))
        conn.execute("DELETE FROM field_evidence WHERE case_id = ?", (case_id,))
        for field in payload.get("extractedFields") or []:
            confirmed = 1 if field.get("confirmed") else 0
            edited = 1 if field.get("editedByUser") else 0
            field_status = (
                "confirmed" if confirmed
                else "system_verified" if field.get("autoVerified")
                else "needs_review"
            )
            conn.execute(
                """
                INSERT INTO ds160_fields (
                  id, case_id, field_key, section, label, value, source_document,
                  source_document_id, source_page, evidence_text, extraction_method,
                  confidence, risk_level, status, requires_user_confirmation, confirmed,
                  edited_by_user, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"{case_id}-{field.get('id')}", case_id, field.get("id") or "",
                    field.get("section") or "", field.get("label") or "",
                    field.get("value") or "", field.get("sourceDocument") or "",
                    field.get("sourceDocumentId"), field.get("sourcePage"),
                    field.get("evidence") or "", field.get("extractionMethod") or "",
                    field.get("confidence"), field.get("riskLevel") or "",
                    field_status, 1 if field.get("requiresUserConfirmation") else 0,
                    confirmed, edited, stamped, stamped,
                ),
            )
            if field.get("evidence"):
                evidence_seed = f"{case_id}|{field.get('id')}|{field.get('sourceDocumentId')}|{field.get('evidence')}"
                evidence_id = "evidence-" + hashlib.sha256(evidence_seed.encode("utf-8")).hexdigest()[:24]
                conn.execute(
                    """
                    INSERT INTO field_evidence (
                      id, case_id, field_key, document_id, page_number,
                      evidence_text, confidence, extraction_method, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        evidence_id, case_id, field.get("id") or "",
                        field.get("sourceDocumentId"), field.get("sourcePage"),
                        field.get("evidence") or "", field.get("confidence"),
                        field.get("extractionMethod") or "", stamped,
                    ),
                )

        replace_questionnaire_rows(conn, case_id, payload.get("branchQuestionnaire"), stamped)

        replace_review_issue_rows(conn, case_id, payload.get("validationResults"), stamped)

        conn.execute(
            """
            INSERT INTO audit_logs (case_id, actor, action, payload_json, created_at)
            VALUES (?, ?, 'case_saved', ?, ?)
            """,
            (case_id, owner_name, json.dumps({"currentStep": payload.get("currentStep"), "status": status}, ensure_ascii=False), stamped),
        )

    return payload


def safe_path_component(value):
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(value or ""))[:120] or "item"


def validate_upload(filename, mime_type, data):
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_UPLOAD_EXTENSIONS:
        raise ValueError("仅支持 PDF、PNG、JPG、JPEG、TIF 和 TIFF 文件")
    if not data:
        raise ValueError("上传文件为空")
    if len(data) > MAX_UPLOAD_SIZE:
        raise ValueError("单个文件不能超过 25 MB")
    guessed_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    normalized_type = (mime_type or guessed_type).split(";", 1)[0].strip().lower()
    if extension == ".pdf" and not data.startswith(b"%PDF"):
        raise ValueError("文件扩展名是 PDF，但内容不是有效 PDF")
    if normalized_type not in {
        "application/pdf", "image/png", "image/jpeg", "image/tiff", "application/octet-stream"
    }:
        raise ValueError("文件类型不受支持")
    return extension, guessed_type if normalized_type == "application/octet-stream" else normalized_type


def save_uploaded_document(case_id, document_id, user, filename, mime_type, data):
    scan_key = (user["organizationId"], case_id)
    with ACTIVE_SCANS_LOCK:
        if scan_key in ACTIVE_SCANS:
            raise ValueError("当前客户档案正在扫描，请等待完成后再上传新材料")
    extension, normalized_type = validate_upload(filename, mime_type, data)
    stamped = now_iso()
    digest = hashlib.sha256(data).hexdigest()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT documents.*, ds160_cases.organization_id
            FROM documents
            JOIN ds160_cases ON ds160_cases.id = documents.case_id
            WHERE documents.id = ? AND documents.case_id = ?
            """,
            (document_id, case_id),
        ).fetchone()
        if not row or row["organization_id"] != user["organizationId"]:
            raise PermissionError("资料槽位不存在或无权访问")
        old_path = row["stored_path"]

    target_dir = UPLOAD_DIR / safe_path_component(user["organizationId"]) / safe_path_component(case_id)
    target_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(target_dir, 0o700)
    stored_name = f"{safe_path_component(document_id)}-{secrets.token_hex(8)}{extension}"
    target_path = target_dir / stored_name
    target_path.write_bytes(data)
    os.chmod(target_path, 0o600)

    try:
        with connect() as conn:
            conn.execute(
                """
                UPDATE documents
                SET file_name = ?, stored_path = ?, mime_type = ?, file_size = ?, sha256 = ?,
                    scan_status = 'uploaded', scan_message = '已上传，等待文档解析',
                    ocr_text = NULL, ocr_json = NULL, parser_name = NULL,
                    parser_version = NULL, processed_at = NULL, updated_at = ?
                WHERE id = ? AND case_id = ?
                """,
                (
                    Path(filename).name, str(target_path), normalized_type, len(data), digest,
                    stamped, document_id, case_id,
                ),
            )
        payload = get_case_payload(case_id, user)
        upsert_case(payload, user)
    except Exception:
        target_path.unlink(missing_ok=True)
        raise

    if old_path and old_path != str(target_path):
        old_file = Path(old_path)
        if UPLOAD_DIR in old_file.parents:
            old_file.unlink(missing_ok=True)
    return get_case_payload(case_id, user)


def get_document_ocr(case_id, document_id, user):
    with connect() as conn:
        row = conn.execute(
            """
            SELECT documents.file_name, documents.scan_status, documents.ocr_text,
                   documents.parser_name, ds160_cases.organization_id
            FROM documents
            JOIN ds160_cases ON ds160_cases.id = documents.case_id
            WHERE documents.id = ? AND documents.case_id = ?
            """,
            (document_id, case_id),
        ).fetchone()
    if not row or row["organization_id"] != user["organizationId"]:
        raise PermissionError("资料槽位不存在或无权访问")
    text = row["ocr_text"] or ""
    payload = get_case_payload(case_id, user)
    fields = [
        {
            "id": field.get("id"),
            "label": field.get("label"),
            "value": field.get("value"),
            "sourcePage": field.get("sourcePage"),
            "confidence": field.get("confidence"),
        }
        for field in payload.get("extractedFields") or []
        if field.get("sourceDocumentId") == document_id
    ]
    return {
        "fileName": row["file_name"] or "",
        "scanStatus": row["scan_status"] or "empty",
        "parserName": row["parser_name"] or "文档解析服务",
        "text": text[:50_000],
        "truncated": len(text) > 50_000,
        "fields": fields,
    }


def get_document_file(case_id, document_id, user):
    with connect() as conn:
        row = conn.execute(
            """
            SELECT documents.file_name, documents.stored_path, documents.mime_type,
                   documents.file_size, ds160_cases.organization_id
            FROM documents
            JOIN ds160_cases ON ds160_cases.id = documents.case_id
            WHERE documents.id = ? AND documents.case_id = ?
            """,
            (document_id, case_id),
        ).fetchone()
    if not row or row["organization_id"] != user["organizationId"]:
        raise PermissionError("资料不存在或无权访问")
    if not row["stored_path"] or not row["file_name"]:
        raise FileNotFoundError("原始文件尚未上传或已被删除")

    target = Path(row["stored_path"]).resolve()
    upload_root = UPLOAD_DIR.resolve()
    if upload_root not in target.parents or not target.is_file():
        raise FileNotFoundError("原始文件不存在")
    return {
        "path": target,
        "fileName": Path(row["file_name"]).name,
        "mimeType": row["mime_type"] or mimetypes.guess_type(row["file_name"])[0]
        or "application/octet-stream",
        "fileSize": row["file_size"] or target.stat().st_size,
    }


def delete_uploaded_document(case_id, document_id, user):
    scan_key = (user["organizationId"], case_id)
    with ACTIVE_SCANS_LOCK:
        if scan_key in ACTIVE_SCANS:
            raise ValueError("当前客户档案正在扫描，请等待完成后再删除材料")

    with connect() as conn:
        row = conn.execute(
            """
            SELECT documents.*, ds160_cases.organization_id
            FROM documents
            JOIN ds160_cases ON ds160_cases.id = documents.case_id
            WHERE documents.id = ? AND documents.case_id = ?
            """,
            (document_id, case_id),
        ).fetchone()
    if not row or row["organization_id"] != user["organizationId"]:
        raise PermissionError("资料槽位不存在或无权访问")
    if not row["stored_path"] and not row["file_name"]:
        raise ValueError("这份材料已经删除")

    payload = get_case_payload(case_id, user)
    payload["extractedFields"] = [
        field for field in payload.get("extractedFields") or []
        if field.get("sourceDocumentId") != document_id
    ]
    payload["validationResults"] = [
        item for item in payload.get("validationResults") or []
        if not str(item.get("id") or "").startswith("ocr.")
    ]
    for document in payload.get("documents") or []:
        if document.get("id") != document_id:
            continue
        document.update({
            "fileName": "",
            "mimeType": "",
            "fileSize": 0,
            "sha256": "",
            "scanStatus": "empty",
            "scanMessage": "等待上传材料",
            "parserName": "",
            "processedAt": "",
        })
        break

    upsert_case(payload, user)
    with connect() as conn:
        conn.execute(
            """
            UPDATE documents
            SET file_name = '', stored_path = NULL, mime_type = NULL, file_size = NULL,
                sha256 = NULL, scan_status = 'empty', scan_message = '等待上传材料',
                ocr_text = NULL, ocr_json = NULL, parser_name = NULL,
                parser_version = NULL, processed_at = NULL, updated_at = ?
            WHERE id = ? AND case_id = ?
            """,
            (now_iso(), document_id, case_id),
        )
        conn.execute(
            """
            INSERT INTO audit_logs (case_id, actor, action, payload_json, created_at)
            VALUES (?, ?, 'document_deleted', ?, ?)
            """,
            (
                case_id,
                user.get("name"),
                json.dumps({"documentId": document_id, "slot": row["slot"]}, ensure_ascii=False),
                now_iso(),
            ),
        )

    target = Path(row["stored_path"]) if row["stored_path"] else None
    if target:
        try:
            target_resolved = target.resolve()
            upload_root = UPLOAD_DIR.resolve()
            if upload_root == target_resolved.parent or upload_root in target_resolved.parents:
                target_resolved.unlink(missing_ok=True)
        except OSError:
            pass
    return get_case_payload(case_id, user)


def start_case_scan(case_id, user):
    get_case_payload(case_id, user)
    scan_key = (user["organizationId"], case_id)
    with ACTIVE_SCANS_LOCK:
        already_running = scan_key in ACTIVE_SCANS
        if not already_running:
            ACTIVE_SCANS.add(scan_key)
    if already_running:
        return scan_status(case_id, user)
    try:
        service = ocr_service_status()
        if not service["available"]:
            raise DoclingError(
                service["message"]
            )

        with connect() as conn:
            rows = conn.execute(
                """
                SELECT documents.id
                FROM documents
                JOIN ds160_cases ON ds160_cases.id = documents.case_id
                WHERE documents.case_id = ? AND ds160_cases.organization_id = ?
                  AND documents.stored_path IS NOT NULL
                """,
                (case_id, user["organizationId"]),
            ).fetchall()
            if not rows:
                raise ValueError("请至少上传一份真实文件后再开始扫描")
            conn.execute(
                """
                UPDATE documents
                SET scan_status = 'queued', scan_message = '等待文档解析与中英文识别', updated_at = ?
                WHERE case_id = ? AND stored_path IS NOT NULL
                """,
                (now_iso(), case_id),
            )

        worker = threading.Thread(
            target=process_case_documents,
            args=(case_id, dict(user), scan_key),
            name=f"docflow-scan-{safe_path_component(case_id)}",
            daemon=True,
        )
        worker.start()
        return scan_status(case_id, user)
    except Exception:
        with ACTIVE_SCANS_LOCK:
            ACTIVE_SCANS.discard(scan_key)
        raise


def process_case_documents(case_id, user, scan_key):
    successful_fields = []
    successful_documents = []
    completed_count = 0
    try:
        with connect() as conn:
            documents = conn.execute(
                """
                SELECT documents.*
                FROM documents
                JOIN ds160_cases ON ds160_cases.id = documents.case_id
                WHERE documents.case_id = ? AND ds160_cases.organization_id = ?
                  AND documents.stored_path IS NOT NULL
                ORDER BY documents.created_at, documents.id
                """,
                (case_id, user["organizationId"]),
            ).fetchall()

        payload = get_case_payload(case_id, user)
        visa_type = payload.get("visaType") or ""
        for document in documents:
            document_id = document["id"]
            update_document_scan_status(document_id, "running", "正在解析版面与文字")
            try:
                result = convert_file(
                    document["stored_path"],
                    filename=document["file_name"],
                    mime_type=document["mime_type"],
                    document_type=document["slot"],
                )
                fields = map_document(
                    document["slot"], document["file_name"], result["text"],
                    document_id, visa_type, page_texts=result.get("pages"),
                )
                successful_fields.extend(fields)
                successful_documents.append({
                    "documentId": document_id,
                    "fileName": document["file_name"],
                    "slot": document["slot"],
                    "text": result["text"],
                })
                completed_count += 1
                quality_percent = round(float(result.get("textQuality") or 0) * 100)
                if result.get("parser") == "mineru":
                    processing_mode = "MinerU 精准解析"
                else:
                    processing_mode = "增强 OCR" if result.get("forcedOcr") else "版面解析 + OCR"
                if result.get("fallbackReason"):
                    processing_mode += "（MinerU 异常，已回退本地解析）"
                rotation = int(result.get("rotationApplied") or 0)
                rotation_note = f" · 自动旋转 {rotation}°" if rotation else ""
                parser_name = f"{result['parser']} / {result['ocrEngine']}"
                if rotation:
                    parser_name += f" / auto-rotate-{rotation}"
                with connect() as conn:
                    conn.execute(
                        """
                        UPDATE documents
                        SET scan_status = 'completed', scan_message = ?, ocr_text = ?, ocr_json = ?,
                            parser_name = ?, parser_version = 'v2', processed_at = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            f"扫描完成，映射出 {len(fields)} 个 DS-160 字段 · {processing_mode}{rotation_note} · 文字质量 {quality_percent}%",
                            result["text"][:5_000_000],
                            bounded_json(result["json"]),
                            parser_name,
                            now_iso(), now_iso(), document_id,
                        ),
                    )
            except Exception as error:
                update_document_scan_status(document_id, "failed", str(error)[:500])

        payload = get_case_payload(case_id, user)
        merged_fields, conflicts = merge_extracted_fields(
            payload.get("extractedFields") or [], successful_fields, visa_type,
        )
        payload["extractedFields"] = merged_fields
        questionnaire = build_questionnaire(
            visa_type,
            payload.get("branchQuestionnaire"),
            merged_fields,
        )
        questionnaire, answer_issues = infer_questionnaire_answers(
            questionnaire,
            successful_documents,
            merged_fields,
        )
        payload["branchQuestionnaire"] = build_questionnaire(
            visa_type,
            questionnaire,
            merged_fields,
        )
        payload["validationResults"] = [
            item for item in (payload.get("validationResults") or [])
            if not str(item.get("id", "")).startswith("ocr.")
        ] + conflicts + answer_issues
        all_completed = completed_count == len(documents) and len(documents) > 0
        payload["currentStep"] = 3 if completed_count else 2
        payload["agentTimeline"] = build_scan_agent_timeline(
            payload.get("agentTimeline") or [], completed_count, len(documents), all_completed,
        )
        upsert_case(payload, user)
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO audit_logs (case_id, actor, action, payload_json, created_at)
                VALUES (?, ?, 'documents_scanned', ?, ?)
                """,
                (
                    case_id, user.get("name"),
                    json.dumps({
                        "documents": len(documents), "completed": completed_count,
                        "fields": len(successful_fields),
                    }, ensure_ascii=False),
                    now_iso(),
                ),
            )
    except Exception as error:
        with connect() as conn:
            conn.execute(
                """
                UPDATE documents
                SET scan_status = 'failed', scan_message = ?, updated_at = ?
                WHERE case_id = ? AND scan_status IN ('queued', 'running')
                """,
                (str(error)[:500], now_iso(), case_id),
            )
    finally:
        with ACTIVE_SCANS_LOCK:
            ACTIVE_SCANS.discard(scan_key)


def build_scan_agent_timeline(timeline, completed_count, total_count, all_completed):
    result = []
    for agent in timeline:
        name = agent.get("name") or ""
        if completed_count == 0:
            status = "pending"
            output = "等待可用的扫描结果"
        elif all_completed:
            status = "completed"
            output = f"已基于 {completed_count} 份真实材料完成处理"
        elif "OCR" in name or "Parsing" in name or "解析" in name:
            status = "completed"
            output = f"已完成 {completed_count} / {total_count} 份材料解析"
        else:
            status = "running"
            output = "正在映射可用结果，失败材料保留待处理状态"
        result.append({**agent, "status": status, "output": output})
    return result


def update_document_scan_status(document_id, status, message):
    with connect() as conn:
        conn.execute(
            "UPDATE documents SET scan_status = ?, scan_message = ?, updated_at = ? WHERE id = ?",
            (status, message, now_iso(), document_id),
        )


def scan_status(case_id, user):
    with connect() as conn:
        case = conn.execute(
            "SELECT id FROM ds160_cases WHERE id = ? AND organization_id = ?",
            (case_id, user["organizationId"]),
        ).fetchone()
        if not case:
            raise PermissionError("客户档案不存在或无权访问")
        rows = conn.execute(
            """
            SELECT id, slot, file_name, scan_status, scan_message, parser_name, processed_at
            FROM documents
            WHERE case_id = ? AND stored_path IS NOT NULL
            ORDER BY created_at, id
            """,
            (case_id,),
        ).fetchall()

    documents = [{
        "id": row["id"],
        "slot": row["slot"],
        "fileName": row["file_name"],
        "scanStatus": row["scan_status"],
        "scanMessage": row["scan_message"] or "",
        "parserName": row["parser_name"] or "",
        "processedAt": row["processed_at"] or "",
    } for row in rows]
    total = len(documents)
    completed = sum(item["scanStatus"] == "completed" for item in documents)
    failed = sum(item["scanStatus"] == "failed" for item in documents)
    terminal = completed + failed
    scan_key = (user["organizationId"], case_id)
    with ACTIVE_SCANS_LOCK:
        active = scan_key in ACTIVE_SCANS

    if not total:
        status = "idle"
    elif active:
        status = "running"
    elif terminal < total:
        status = "interrupted"
    elif completed and failed:
        status = "completed_with_errors"
    elif completed:
        status = "completed"
    else:
        status = "failed"
    return {
        "status": status,
        "progress": round((terminal / total) * 100) if total else 0,
        "total": total,
        "completed": completed,
        "failed": failed,
        "documents": documents,
        "case": get_case_payload(case_id, user) if status in {
            "completed", "completed_with_errors", "failed", "interrupted"
        } else None,
    }


def delete_case(case_id, user):
    stored_paths = []
    with connect() as conn:
        stored_paths = [
            row["stored_path"]
            for row in conn.execute(
                """
                SELECT documents.stored_path
                FROM documents
                JOIN ds160_cases ON ds160_cases.id = documents.case_id
                WHERE documents.case_id = ? AND ds160_cases.organization_id = ?
                  AND documents.stored_path IS NOT NULL
                """,
                (case_id, user["organizationId"]),
            ).fetchall()
        ]
        cursor = conn.execute(
            "DELETE FROM ds160_cases WHERE id = ? AND organization_id = ?",
            (case_id, user["organizationId"]),
        )
        if cursor.rowcount == 0:
            raise PermissionError("客户档案不存在或无权删除")
    for stored_path in stored_paths:
        target = Path(stored_path)
        if UPLOAD_DIR in target.parents:
            target.unlink(missing_ok=True)


def status_for_step(step):
    if step >= 7:
        return "已完成"
    if step >= 6:
        return "初稿已生成"
    if step >= 3:
        return "待人工核查"
    if step >= 1:
        return "资料收集中"
    return "未开始"


def ocr_service_status():
    installed = (ROOT / ".venv-docling" / "bin" / "docling-serve").exists()
    return provider_service_status(docling_installed=installed)


def start_ocr_service():
    if selected_provider() == "mineru":
        return ocr_service_status()
    return start_docling_service()


def ensure_docling_api_key():
    key_path = DATA_DIR / "docling_api_key"
    DATA_DIR.mkdir(mode=0o700, exist_ok=True)
    if not key_path.exists() or not key_path.read_text(encoding="utf-8").strip():
        key_path.write_text(secrets.token_hex(32), encoding="utf-8")
    os.chmod(key_path, 0o600)
    return key_path.read_text(encoding="utf-8").strip()


def start_docling_service():
    global DOCLING_PROCESS, DOCLING_LOG_HANDLE
    current = ocr_service_status()
    if current["available"]:
        return {**current, "starting": False}

    executable = ROOT / ".venv-docling" / "bin" / "docling-serve"
    if not executable.exists():
        raise DoclingError(
            "文档扫描环境尚未安装。请先运行“安装文档扫描.command”，完成后再回到页面启动扫描。"
        )

    with DOCLING_PROCESS_LOCK:
        process_running = DOCLING_PROCESS is not None and DOCLING_PROCESS.poll() is None
        if not process_running:
            api_key = ensure_docling_api_key()
            log_path = DATA_DIR / "docling-serve.log"
            if DOCLING_LOG_HANDLE is not None:
                DOCLING_LOG_HANDLE.close()
            DOCLING_LOG_HANDLE = log_path.open("ab")
            environment = os.environ.copy()
            environment.update({
                "UVICORN_HOST": "127.0.0.1",
                "UVICORN_PORT": "5001",
                "UVICORN_WORKERS": "1",
                "DOCLING_SERVE_ENABLE_UI": "true",
                "DOCLING_SERVE_ENG_KIND": "local",
                "DOCLING_SERVE_ENG_LOC_NUM_WORKERS": "1",
                "DOCLING_SERVE_MAX_SYNC_WAIT": "300",
                "DOCLING_SERVE_API_KEY": api_key,
            })
            try:
                DOCLING_PROCESS = subprocess.Popen(
                    [str(executable), "run", "--enable-ui"],
                    cwd=str(ROOT),
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=DOCLING_LOG_HANDLE,
                    stderr=subprocess.STDOUT,
                )
            except OSError as error:
                DOCLING_LOG_HANDLE.close()
                DOCLING_LOG_HANDLE = None
                raise DoclingError(f"扫描服务启动失败：{error}") from error

    for _ in range(24):
        ready = check_docling(timeout=0.5)
        if ready.get("available"):
            return {**ocr_service_status(), "starting": False}
        if DOCLING_PROCESS is not None and DOCLING_PROCESS.poll() is not None:
            raise DoclingError(
                "扫描服务启动后立即退出。请查看 data/docling-serve.log，或重新运行“启动完整版本.command”。"
            )
        time.sleep(0.5)

    return {
        **ocr_service_status(),
        "starting": True,
        "message": "扫描服务正在启动，首次加载通常需要 10 至 30 秒，请稍后重新检查",
    }


def stop_managed_docling_service():
    global DOCLING_PROCESS, DOCLING_LOG_HANDLE
    with DOCLING_PROCESS_LOCK:
        if DOCLING_PROCESS is not None and DOCLING_PROCESS.poll() is None:
            DOCLING_PROCESS.terminate()
            try:
                DOCLING_PROCESS.wait(timeout=5)
            except subprocess.TimeoutExpired:
                DOCLING_PROCESS.kill()
        DOCLING_PROCESS = None
        if DOCLING_LOG_HANDLE is not None:
            DOCLING_LOG_HANDLE.close()
            DOCLING_LOG_HANDLE = None


def stop_managed_screen_agents():
    with SCREEN_AGENT_PROCESS_LOCK:
        items = list(SCREEN_AGENT_PROCESSES.values())
        SCREEN_AGENT_PROCESSES.clear()
    for item in items:
        process = item.get("process")
        if process is not None and process.poll() is None:
            process.terminate()
        paths = item.get("paths")
        if paths:
            redact_screen_agent_job(paths)


atexit.register(stop_managed_docling_service)
atexit.register(stop_managed_screen_agents)


def health_payload():
    verification_mode = registration_verification_mode()
    return {
        "ok": True,
        "auth": "cookie-v1",
        "apiVersion": "2026-08-13-billing-v1",
        "apiRevision": 20,
        "registrationVerification": {
            "mode": verification_mode,
            "required": verification_mode != "none",
        },
        "emailVerification": mail_service_status(),
        "translation": translation_service_status(),
        "screenAgent": screen_agent_runtime_status(),
        "billing": billing_settings(),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "DocFlowDS160/0.1"

    def do_GET(self):
        if not self.ensure_origin_allowed():
            return None
        parsed = urlparse(self.path)
        if workspace_membership_required(parsed.path) and not self.require_member():
            return None
        if parsed.path in {"/", "/status"}:
            return self.html_response(
                render_status_page(health_payload(), ocr_service_status())
            )
        codex_task_match = re.fullmatch(
            r"/api/codex-agent/jobs/([^/]+)", parsed.path
        )
        if codex_task_match:
            try:
                authorization = self.headers.get("Authorization", "")
                if not authorization.startswith("Bearer "):
                    raise PermissionError("Codex 任务缺少访问令牌")
                return self.json_response(codex_agent_task_payload(
                    unquote(codex_task_match.group(1)),
                    authorization[7:].strip(),
                    self.server.server_port,
                ))
            except (PermissionError, FileNotFoundError) as error:
                return self.json_response({"error": str(error)}, status=401)
            except ValueError as error:
                return self.json_response({"error": str(error)}, status=400)
        if parsed.path == "/api/health":
            return self.json_response(health_payload())
        if parsed.path == "/api/product/config":
            return self.json_response(product_public_config())
        if parsed.path == "/api/product/analytics":
            user = self.require_user()
            if not user:
                return None
            query = parse_qs(parsed.query)
            return self.json_response(product_analytics_summary(
                (query.get("days") or [30])[0]
            ))
        analytics_session_match = re.fullmatch(
            r"/api/product/analytics/sessions/([^/]+)", parsed.path
        )
        if analytics_session_match:
            user = self.require_user()
            if not user:
                return None
            try:
                return self.json_response(product_analytics_session(
                    unquote(analytics_session_match.group(1))
                ))
            except FileNotFoundError as error:
                return self.json_response({"error": str(error)}, status=404)
            except ValueError as error:
                return self.json_response({"error": str(error)}, status=400)
        if parsed.path == "/api/intake":
            try:
                token = self.headers.get("X-DocFlow-Intake", "")
                if not token:
                    raise PermissionError("补充链接缺少访问令牌")
                return self.json_response(public_intake_payload(token))
            except PermissionError as error:
                return self.json_response({"error": str(error)}, status=410)
            except ValueError as error:
                return self.json_response({"error": str(error)}, status=409)
        if parsed.path == "/api/ocr/health":
            user = self.require_user()
            if not user:
                return None
            return self.json_response(ocr_service_status())
        if parsed.path == "/api/session":
            return self.json_response({"user": self.current_user()})
        if parsed.path == "/api/billing":
            user = self.require_user()
            if not user:
                return None
            return self.json_response(billing_summary(user))
        billing_order_match = re.fullmatch(r"/api/billing/orders/([^/]+)", parsed.path)
        if billing_order_match:
            user = self.require_user()
            if not user:
                return None
            try:
                return self.json_response(order_status(
                    unquote(billing_order_match.group(1)), user
                ))
            except FileNotFoundError as error:
                return self.json_response({"error": str(error)}, status=404)
        if parsed.path == "/api/cases":
            user = self.require_user()
            if not user:
                return None
            return self.json_response({"cases": get_case_payloads(user)})
        file_match = re.fullmatch(r"/api/cases/([^/]+)/documents/([^/]+)/file", parsed.path)
        if file_match:
            user = self.require_user()
            if not user:
                return None
            try:
                document = get_document_file(
                    unquote(file_match.group(1)), unquote(file_match.group(2)), user
                )
                return self.serve_document_file(document)
            except PermissionError as error:
                return self.json_response({"error": str(error)}, status=404)
            except FileNotFoundError as error:
                return self.json_response({"error": str(error)}, status=404)
        ocr_match = re.fullmatch(r"/api/cases/([^/]+)/documents/([^/]+)/ocr", parsed.path)
        if ocr_match:
            user = self.require_user()
            if not user:
                return None
            try:
                result = get_document_ocr(
                    unquote(ocr_match.group(1)), unquote(ocr_match.group(2)), user
                )
                return self.json_response(result)
            except PermissionError as error:
                return self.json_response({"error": str(error)}, status=404)
        scan_match = re.fullmatch(r"/api/cases/([^/]+)/scan-status", parsed.path)
        if scan_match:
            user = self.require_user()
            if not user:
                return None
            try:
                return self.json_response(scan_status(unquote(scan_match.group(1)), user))
            except PermissionError as error:
                return self.json_response({"error": str(error)}, status=404)
        open_cowork_status_match = re.fullmatch(
            r"/api/cases/([^/]+)/open-cowork/([^/]+)", parsed.path
        )
        if open_cowork_status_match:
            user = self.require_user()
            if not user:
                return None
            try:
                return self.json_response(open_cowork_status(
                    unquote(open_cowork_status_match.group(1)),
                    unquote(open_cowork_status_match.group(2)),
                    user,
                ))
            except (PermissionError, FileNotFoundError) as error:
                return self.json_response({"error": str(error)}, status=404)
            except ValueError as error:
                return self.json_response({"error": str(error)}, status=400)
        screen_agent_status_match = re.fullmatch(
            r"/api/cases/([^/]+)/screen-agent/([^/]+)", parsed.path
        )
        if screen_agent_status_match:
            user = self.require_user()
            if not user:
                return None
            try:
                return self.json_response(screen_agent_status(
                    unquote(screen_agent_status_match.group(1)),
                    unquote(screen_agent_status_match.group(2)),
                    user,
                ))
            except (PermissionError, FileNotFoundError) as error:
                return self.json_response({"error": str(error)}, status=404)
            except ValueError as error:
                return self.json_response({"error": str(error)}, status=400)
        codex_agent_status_match = re.fullmatch(
            r"/api/cases/([^/]+)/codex-agent/([^/]+)", parsed.path
        )
        if codex_agent_status_match:
            user = self.require_user()
            if not user:
                return None
            try:
                return self.json_response(codex_agent_status(
                    unquote(codex_agent_status_match.group(1)),
                    unquote(codex_agent_status_match.group(2)),
                    user,
                ))
            except (PermissionError, FileNotFoundError) as error:
                return self.json_response({"error": str(error)}, status=404)
            except ValueError as error:
                return self.json_response({"error": str(error)}, status=400)
        if parsed.path.startswith("/api/"):
            return self.json_response({"error": "Not found"}, status=404)
        return self.json_response({"error": "Backend API only"}, status=404)

    def do_POST(self):
        if not self.ensure_origin_allowed():
            return None
        parsed = urlparse(self.path)
        if workspace_membership_required(parsed.path) and not self.require_member():
            return None
        if parsed.path == "/api/billing/webhooks/stripe":
            try:
                return self.json_response(process_stripe_webhook(
                    self.read_raw_body(), self.headers.get("Stripe-Signature", "")
                ))
            except PermissionError as error:
                return self.json_response({"error": str(error)}, status=400)
            except BillingConfigurationError as error:
                return self.json_response({"error": str(error)}, status=503)
            except (ValueError, TypeError) as error:
                return self.json_response({"error": str(error)}, status=400)
        if parsed.path == "/api/billing/checkout":
            user = self.require_user()
            if not user:
                return None
            try:
                return self.json_response(
                    {"order": create_checkout_order(self.read_json(), user)}, status=201
                )
            except BillingConfigurationError as error:
                return self.json_response({"error": str(error)}, status=503)
            except BillingProviderError as error:
                return self.json_response({"error": str(error)}, status=502)
            except (ValueError, TypeError) as error:
                return self.json_response({"error": str(error)}, status=400)
        billing_refresh_match = re.fullmatch(
            r"/api/billing/orders/([^/]+)/refresh", parsed.path
        )
        if billing_refresh_match:
            user = self.require_user()
            if not user:
                return None
            try:
                return self.json_response({"order": refresh_checkout_order(
                    unquote(billing_refresh_match.group(1)), user
                )})
            except FileNotFoundError as error:
                return self.json_response({"error": str(error)}, status=404)
            except BillingConfigurationError as error:
                return self.json_response({"error": str(error)}, status=503)
            except BillingProviderError as error:
                return self.json_response({"error": str(error)}, status=502)
            except (ValueError, TypeError) as error:
                return self.json_response({"error": str(error)}, status=400)
        billing_refund_match = re.fullmatch(
            r"/api/billing/orders/([^/]+)/refunds", parsed.path
        )
        if billing_refund_match:
            user = self.require_user()
            if not user:
                return None
            try:
                return self.json_response({"refund": create_refund(
                    unquote(billing_refund_match.group(1)), self.read_json(), user
                )}, status=201)
            except FileNotFoundError as error:
                return self.json_response({"error": str(error)}, status=404)
            except BillingConfigurationError as error:
                return self.json_response({"error": str(error)}, status=503)
            except BillingProviderError as error:
                return self.json_response({"error": str(error)}, status=502)
            except (ValueError, TypeError) as error:
                return self.json_response({"error": str(error)}, status=400)
        if parsed.path == "/api/product/analytics/events":
            try:
                return self.json_response(
                    record_product_analytics_event(self.read_json()), status=202
                )
            except (ValueError, TypeError, json.JSONDecodeError) as error:
                return self.json_response({"error": str(error)}, status=400)
        if parsed.path == "/api/product/settings":
            user = self.require_user()
            if not user:
                return None
            try:
                return self.json_response(update_product_settings(self.read_json(), user))
            except (ValueError, TypeError, json.JSONDecodeError) as error:
                return self.json_response({"error": str(error)}, status=400)
        codex_task_status_match = re.fullmatch(
            r"/api/codex-agent/jobs/([^/]+)/status", parsed.path
        )
        if codex_task_status_match:
            try:
                authorization = self.headers.get("Authorization", "")
                if not authorization.startswith("Bearer "):
                    raise PermissionError("Codex 任务缺少访问令牌")
                return self.json_response(update_codex_agent_task_status(
                    unquote(codex_task_status_match.group(1)),
                    authorization[7:].strip(),
                    self.read_json(),
                ))
            except (PermissionError, FileNotFoundError) as error:
                return self.json_response({"error": str(error)}, status=401)
            except (ValueError, TypeError) as error:
                return self.json_response({"error": str(error)}, status=400)
        if parsed.path == "/api/intake":
            try:
                token = self.headers.get("X-DocFlow-Intake", "")
                if not token:
                    raise PermissionError("补充链接缺少访问令牌")
                result = submit_client_intake(token, self.read_json())
                return self.json_response(result)
            except PermissionError as error:
                return self.json_response({"error": str(error)}, status=410)
            except (ValueError, TypeError) as error:
                return self.json_response({"error": str(error)}, status=400)
        if parsed.path == "/api/email-verification/send":
            try:
                return self.json_response(request_email_verification(self.read_json()), status=202)
            except EmailRateLimitError as error:
                return self.json_response(
                    {"error": str(error), "retryAfter": error.retry_after}, status=429
                )
            except EmailDeliveryError as error:
                return self.json_response({"error": str(error)}, status=503)
            except ValueError as error:
                return self.json_response({"error": str(error)}, status=400)
        if parsed.path == "/api/register":
            try:
                user = register_user(self.read_json())
                token = create_auth_session(user["id"])
                return self.json_response({"user": user}, auth_token=token)
            except ValueError as error:
                return self.json_response({"error": str(error)}, status=400)
        if parsed.path == "/api/login":
            try:
                user = login_user(self.read_json())
                token = create_auth_session(user["id"])
                return self.json_response({"user": user}, auth_token=token)
            except ValueError as error:
                return self.json_response({"error": str(error)}, status=401)
        if parsed.path == "/api/logout":
            delete_auth_session(self.headers.get("Cookie"))
            return self.json_response({"ok": True}, clear_auth=True)
        if parsed.path == "/api/ocr/start":
            user = self.require_user()
            if not user:
                return None
            try:
                result = start_ocr_service()
                return self.json_response(
                    result,
                    status=200 if result.get("available") else 202,
                )
            except DoclingError as error:
                return self.json_response({"error": str(error)}, status=503)
        intake_link_match = re.fullmatch(r"/api/cases/([^/]+)/intake-link", parsed.path)
        if intake_link_match:
            user = self.require_user()
            if not user:
                return None
            try:
                return self.json_response(
                    create_intake_link(unquote(intake_link_match.group(1)), user),
                    status=201,
                )
            except PermissionError as error:
                return self.json_response({"error": str(error)}, status=404)
            except ValueError as error:
                return self.json_response({"error": str(error)}, status=409)
        known_information_match = re.fullmatch(
            r"/api/cases/([^/]+)/known-information", parsed.path
        )
        if known_information_match:
            user = self.require_user()
            if not user:
                return None
            try:
                return self.json_response(apply_consultant_information(
                    unquote(known_information_match.group(1)),
                    user,
                    self.read_json(),
                ))
            except PermissionError as error:
                return self.json_response({"error": str(error)}, status=404)
            except (ValueError, TypeError) as error:
                return self.json_response({"error": str(error)}, status=400)
        upload_match = re.fullmatch(r"/api/cases/([^/]+)/documents/([^/]+)", parsed.path)
        if upload_match:
            user = self.require_user()
            if not user:
                return None
            try:
                file_info = self.read_multipart_file("file")
                saved = save_uploaded_document(
                    unquote(upload_match.group(1)), unquote(upload_match.group(2)), user,
                    file_info["filename"], file_info["contentType"], file_info["data"],
                )
                return self.json_response({"case": saved}, status=201)
            except PermissionError as error:
                return self.json_response({"error": str(error)}, status=403)
            except ValueError as error:
                return self.json_response({"error": str(error)}, status=400)
        scan_match = re.fullmatch(r"/api/cases/([^/]+)/scan", parsed.path)
        if scan_match:
            user = self.require_user()
            if not user:
                return None
            try:
                result = start_case_scan(unquote(scan_match.group(1)), user)
                return self.json_response(result, status=202)
            except PermissionError as error:
                return self.json_response({"error": str(error)}, status=403)
            except ValueError as error:
                return self.json_response({"error": str(error)}, status=400)
            except DoclingError as error:
                return self.json_response({"error": str(error)}, status=503)
        open_cowork_prepare_match = re.fullmatch(
            r"/api/cases/([^/]+)/open-cowork/prepare", parsed.path
        )
        if open_cowork_prepare_match:
            user = self.require_user()
            if not user:
                return None
            try:
                return self.json_response(
                    prepare_open_cowork_job(
                        unquote(open_cowork_prepare_match.group(1)), user
                    ),
                    status=201,
                )
            except PermissionError as error:
                return self.json_response({"error": str(error)}, status=404)
            except ValueError as error:
                return self.json_response({"error": str(error)}, status=409)
        screen_agent_run_match = re.fullmatch(
            r"/api/cases/([^/]+)/screen-agent/run", parsed.path
        )
        if screen_agent_run_match:
            user = self.require_user()
            if not user:
                return None
            try:
                return self.json_response(
                    launch_screen_agent(
                        unquote(screen_agent_run_match.group(1)),
                        user,
                        self.server.server_port,
                    ),
                    status=202,
                )
            except PermissionError as error:
                return self.json_response({"error": str(error)}, status=404)
            except ValueError as error:
                return self.json_response({"error": str(error)}, status=409)
            except RuntimeError as error:
                return self.json_response({"error": str(error)}, status=503)
        codex_agent_prepare_match = re.fullmatch(
            r"/api/cases/([^/]+)/codex-agent/prepare", parsed.path
        )
        if codex_agent_prepare_match:
            user = self.require_user()
            if not user:
                return None
            try:
                options = self.read_json()
                return self.json_response(prepare_codex_agent_job(
                    unquote(codex_agent_prepare_match.group(1)),
                    user,
                    self.server.server_port,
                    auto_next=options.get("autoNext", True) is True,
                    launch_browser=True,
                ), status=201)
            except PermissionError as error:
                return self.json_response({"error": str(error)}, status=404)
            except ValueError as error:
                return self.json_response({"error": str(error)}, status=409)
        appointment_agent_prepare_match = re.fullmatch(
            r"/api/cases/([^/]+)/appointment-agent/prepare", parsed.path
        )
        if appointment_agent_prepare_match:
            user = self.require_user()
            if not user:
                return None
            try:
                return self.json_response(prepare_appointment_agent_job(
                    unquote(appointment_agent_prepare_match.group(1)),
                    user,
                    self.server.server_port,
                    launch_browser=True,
                ), status=201)
            except PermissionError as error:
                return self.json_response({"error": str(error)}, status=404)
            except ValueError as error:
                return self.json_response({"error": str(error)}, status=409)
        screen_agent_stop_match = re.fullmatch(
            r"/api/cases/([^/]+)/screen-agent/([^/]+)/stop", parsed.path
        )
        if screen_agent_stop_match:
            user = self.require_user()
            if not user:
                return None
            try:
                return self.json_response(stop_screen_agent(
                    unquote(screen_agent_stop_match.group(1)),
                    unquote(screen_agent_stop_match.group(2)),
                    user,
                ))
            except (PermissionError, FileNotFoundError) as error:
                return self.json_response({"error": str(error)}, status=404)
            except ValueError as error:
                return self.json_response({"error": str(error)}, status=400)
        if parsed.path == "/api/cases":
            user = self.require_user()
            if not user:
                return None
            try:
                payload = self.read_json()
                saved = upsert_case(payload.get("case") or payload, user)
                return self.json_response({"case": saved})
            except (ValueError, PermissionError) as error:
                return self.json_response({"error": str(error)}, status=403)
        return self.json_response({"error": "Not found"}, status=404)

    def do_PUT(self):
        if not self.ensure_origin_allowed():
            return None
        parsed = urlparse(self.path)
        if workspace_membership_required(parsed.path) and not self.require_member():
            return None
        if parsed.path == "/api/intake":
            try:
                token = self.headers.get("X-DocFlow-Intake", "")
                if not token:
                    raise PermissionError("补充链接缺少访问令牌")
                return self.json_response(
                    save_client_intake_draft(token, self.read_json())
                )
            except PermissionError as error:
                return self.json_response({"error": str(error)}, status=410)
            except (ValueError, TypeError) as error:
                return self.json_response({"error": str(error)}, status=400)
        if parsed.path.startswith("/api/cases/"):
            user = self.require_user()
            if not user:
                return None
            try:
                payload = self.read_json()
                case_payload = payload.get("case") or payload
                path_case_id = unquote(parsed.path.split("/")[-1])
                if case_payload.get("id") != path_case_id:
                    raise ValueError("客户档案 ID 不一致")
                saved = upsert_case(case_payload, user)
                return self.json_response({"case": saved})
            except (ValueError, PermissionError) as error:
                return self.json_response({"error": str(error)}, status=403)
        return self.json_response({"error": "Not found"}, status=404)

    def do_DELETE(self):
        if not self.ensure_origin_allowed():
            return None
        parsed = urlparse(self.path)
        if workspace_membership_required(parsed.path) and not self.require_member():
            return None
        codex_agent_match = re.fullmatch(
            r"/api/cases/([^/]+)/codex-agent/([^/]+)", parsed.path
        )
        if codex_agent_match:
            user = self.require_user()
            if not user:
                return None
            try:
                return self.json_response(revoke_codex_agent_job(
                    unquote(codex_agent_match.group(1)),
                    unquote(codex_agent_match.group(2)),
                    user,
                ))
            except (PermissionError, FileNotFoundError) as error:
                return self.json_response({"error": str(error)}, status=404)
            except ValueError as error:
                return self.json_response({"error": str(error)}, status=400)
        document_match = re.fullmatch(r"/api/cases/([^/]+)/documents/([^/]+)", parsed.path)
        if document_match:
            user = self.require_user()
            if not user:
                return None
            try:
                saved = delete_uploaded_document(
                    unquote(document_match.group(1)), unquote(document_match.group(2)), user
                )
                return self.json_response({"case": saved})
            except PermissionError as error:
                return self.json_response({"error": str(error)}, status=404)
            except ValueError as error:
                return self.json_response({"error": str(error)}, status=409)
        if parsed.path.startswith("/api/cases/"):
            user = self.require_user()
            if not user:
                return None
            case_id = unquote(parsed.path.split("/")[-1])
            try:
                delete_case(case_id, user)
                return self.json_response({"ok": True})
            except PermissionError as error:
                return self.json_response({"error": str(error)}, status=404)
        return self.json_response({"error": "Not found"}, status=404)

    def current_user(self):
        return authenticated_user(self.headers.get("Cookie"))

    def require_user(self):
        user = self.current_user()
        if not user:
            self.json_response({"error": "登录已失效，请重新登录"}, status=401, clear_auth=True)
        return user

    def require_member(self):
        user = self.require_user()
        if not user:
            return None
        membership = active_membership_for_user(user)
        if not membership:
            self.json_response({
                "error": "请先购买有效会员后再使用机构工作台",
                "code": "membership_required",
                "redirect": "/membership",
            }, status=402)
            return None
        return user

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        return json.loads(raw or "{}")

    def read_raw_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length) if length else b""

    def read_multipart_file(self, field_name):
        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("multipart/form-data"):
            raise ValueError("文件上传必须使用 multipart/form-data")
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            raise ValueError("没有收到上传文件")
        if length > MAX_UPLOAD_SIZE + (1024 * 1024):
            raise ValueError("单个文件不能超过 25 MB")
        raw = self.rfile.read(length)
        message = BytesParser(policy=policy.default).parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + raw
        )
        if not message.is_multipart():
            raise ValueError("无法解析上传内容")
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if name != field_name:
                continue
            filename = Path(part.get_filename() or "").name
            data = part.get_payload(decode=True) or b""
            if not filename:
                raise ValueError("上传文件缺少文件名")
            return {
                "filename": filename,
                "contentType": part.get_content_type(),
                "data": data,
            }
        raise ValueError("没有找到上传文件")

    def request_origin(self):
        return self.headers.get("Origin", "").strip().rstrip("/")

    def ensure_origin_allowed(self):
        origin = self.request_origin()
        if not origin or origin in allowed_origins():
            return True
        self.json_response({"error": "Cross-origin request denied"}, status=403)
        return False

    def send_cors_headers(self):
        origin = self.request_origin()
        if origin and origin in allowed_origins():
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Vary", "Origin")

    def auth_cookie(self, value, max_age):
        attributes = [
            f"{AUTH_COOKIE}={value}",
            "Path=/",
            "HttpOnly",
            f"SameSite={cookie_same_site()}",
            f"Max-Age={max_age}",
        ]
        domain = cookie_domain()
        if domain:
            attributes.append(f"Domain={domain}")
        if cookie_secure():
            attributes.append("Secure")
        return "; ".join(attributes)

    def html_response(self, content, status=200):
        body = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def json_response(self, payload, status=200, auth_token=None, clear_auth=False):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_cors_headers()
        if auth_token:
            self.send_header("Set-Cookie", self.auth_cookie(auth_token, SESSION_MAX_AGE))
        elif clear_auth:
            self.send_header("Set-Cookie", self.auth_cookie("", 0))
        self.end_headers()
        self.wfile.write(body)

    def serve_document_file(self, document):
        target = document["path"]
        filename = document["fileName"]
        self.send_response(200)
        self.send_header("Content-Type", document["mimeType"])
        self.send_header("Content-Length", str(document["fileSize"]))
        self.send_header(
            "Content-Disposition",
            f"inline; filename*=UTF-8''{quote(filename, safe='')}",
        )
        self.send_header("Cache-Control", "private, no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_cors_headers()
        self.end_headers()
        with target.open("rb") as source:
            while chunk := source.read(64 * 1024):
                self.wfile.write(chunk)

    def do_OPTIONS(self):
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            return self.json_response({"error": "Not found"}, status=404)
        if not self.ensure_origin_allowed():
            return None
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.send_cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        requested_headers = self.headers.get(
            "Access-Control-Request-Headers",
            "Content-Type, Authorization, X-DocFlow-Intake",
        )
        self.send_header("Access-Control-Allow-Headers", requested_headers)
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()


class ApiHandler(Handler):
    """Explicit name for the backend-only HTTP handler."""


def create_server(host, preferred_port, handler_class=ApiHandler):
    init_db()
    server = None
    port = preferred_port
    for candidate in range(preferred_port, preferred_port + 20):
        try:
            server = ThreadingHTTPServer((host, candidate), handler_class)
            port = candidate
            break
        except OSError:
            continue
    if server is None:
        raise RuntimeError(f"Could not bind a local port from {preferred_port} to {preferred_port + 19}")
    return server, port


def run_server(*, default_port=4176):
    preferred_port = int(sys.argv[1]) if len(sys.argv) > 1 else bind_port(default_port)
    host = bind_host()
    server, port = create_server(host, preferred_port, ApiHandler)
    print(f"WestoryVisa backend API running at http://{host}:{port}")
    print(f"SQLite database: {DB_PATH}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main():
    run_server(default_port=4176)


if __name__ == "__main__":
    main()
