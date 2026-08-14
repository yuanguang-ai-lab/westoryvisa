import json
import math
import re
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from visa_agent.adapters import (
    GeminiComputerUseAdapter,
    PlaywrightBrowserDriver,
)
from visa_agent.config import AgentConfig, ProviderConfig
from visa_agent.models import ActionKind
from visa_agent.providers import ProviderNotConfigured
from visa_agent.service import AgentService, ServiceError
from visa_agent.workflow import ComputerUseAgent


PERSONAL_URL = (
    "https://ceac.state.gov/GenNIV/General/complete/"
    "complete_personal.aspx?node=Personal1"
)
TRAVEL_URL = (
    "https://ceac.state.gov/GenNIV/General/complete/"
    "complete_travel.aspx?node=Travel"
)
ADDRESS_URL = (
    "https://ceac.state.gov/GenNIV/General/complete/"
    "complete_contact.aspx?node=AddressPhone"
)
PASSPORT_URL = (
    "https://ceac.state.gov/GenNIV/General/complete/"
    "complete_pptvisa.aspx?node=PptVisa"
)
REVIEW_URL = (
    "https://ceac.state.gov/GenNIV/General/Review/"
    "ReviewReview.aspx?node=ReviewReview"
)


# Every value in this fixture is deliberately fictitious.  The reserved 555
# phone range and explicit DEMO document number prevent accidental reuse as a
# real applicant record.
SYNTHETIC_VALUES = {
    "personal.surname": "TESTER",
    "personal.givenNames": "NOVA",
    "ceac.personal1.001.other_names.used": "yes",
    "ceac.personal1.002.other_names.surname": "FICTION",
    "travel.purpose": "TEMP. BUSINESS OR PLEASURE VISITOR (B)",
    "travel.arrivalDate": "2030-04-05",
    "ceac.address_phone.001.home_street": "1 FICTION LOOP",
    "ceac.address_phone.002.home_city": "EXAMPLEVILLE",
    "ceac.address_phone.003.home_country": "CANADA",
    "ceac.address_phone.004.primary_phone": "2025550199",
    "passport.number": "DEMO00001",
    "passport.issuingCountry": "CANADA",
}

BRANCH = "ceac.personal1.001.other_names.used"
CONDITIONAL = "ceac.personal1.002.other_names.surname"


FIELD_LABELS = {
    "personal.surname": (
        "Surnames [control=text; control_hints=APP_SURNAME]"
    ),
    "personal.givenNames": (
        "Given Names [control=text; control_hints=APP_GIVEN_NAME]"
    ),
    BRANCH: (
        "Have you ever used other names? "
        "[control=yes_no; occurrence=1; refresh_after_change=true; "
        "control_hints=OTHER_NAMES; human-approved value=yes]"
    ),
    CONDITIONAL: (
        "Other Surnames [control=text; control_hints=OTHER_SURNAME]"
    ),
    "travel.purpose": (
        "Purpose of Trip to the U.S. "
        "[control=select; control_hints=TRAVEL_PURPOSE; "
        "human-approved value=TEMP. BUSINESS OR PLEASURE VISITOR (B)]"
    ),
    "travel.arrivalDate": (
        "Intended Date of Arrival "
        "[control=text; control_hints=ARRIVAL_DATE]"
    ),
    "ceac.address_phone.001.home_street": (
        "Home Address Line 1 "
        "[control=text; control_hints=HOME_STREET1]"
    ),
    "ceac.address_phone.002.home_city": (
        "Home City [control=text; control_hints=HOME_CITY]"
    ),
    "ceac.address_phone.003.home_country": (
        "Home Country/Region "
        "[control=select; control_hints=HOME_COUNTRY; "
        "human-approved value=CANADA]"
    ),
    "ceac.address_phone.004.primary_phone": (
        "Primary Phone Number "
        "[control=text; control_hints=PRIMARY_PHONE]"
    ),
    "passport.number": (
        "Passport/Travel Document Number "
        "[control=text; control_hints=PPT_NUM]"
    ),
    "passport.issuingCountry": (
        "Country/Authority that Issued Passport "
        "[control=select; control_hints=PPT_ISSUED_CNTRY; "
        "human-approved value=CANADA]"
    ),
}


CONTROL_KINDS = {
    field_id: (
        "select"
        if "control=select" in label or "control=yes_no" in label
        else "type"
    )
    for field_id, label in FIELD_LABELS.items()
}


PAGE_FIELDS = {
    "Personal1": (
        "personal.surname",
        "personal.givenNames",
        BRANCH,
        CONDITIONAL,
    ),
    "Travel": (
        "travel.purpose",
        "travel.arrivalDate",
    ),
    "AddressPhone": (
        "ceac.address_phone.001.home_street",
        "ceac.address_phone.002.home_city",
        "ceac.address_phone.003.home_country",
        "ceac.address_phone.004.primary_phone",
    ),
    "PptVisa": (
        "passport.number",
        "passport.issuingCountry",
    ),
}


def _browser_provider():
    return ProviderConfig(
        provider="playwright",
        model="chromium-headless",
    )


class RecordedVisualDriver(PlaywrightBrowserDriver):
    """Production Playwright driver with read-only E2E instrumentation."""

    def __init__(self):
        super().__init__(_browser_provider())
        self.pointer_paths = []
        self.executed_actions = []
        self.next_plans = []
        self.refresh_checks = []
        self.binding_attempts = []
        self.visual_statuses = []

    def set_visual_status(self, state, message=""):
        self.visual_statuses.append({
            "state": str(state or ""),
            "message": str(message or ""),
        })
        return super().set_visual_status(state, message)

    def bind_visual_field(self, action, labels=(), hints=()):
        result = super().bind_visual_field(action, labels, hints)
        controls = []
        try:
            controls = self._page.locator(
                "input:not([type=hidden]), textarea, select"
            ).evaluate_all(
                "els => els.map(el => ({id: el.id, name: el.name, "
                "owner: el.getAttribute('data-docflow-field-owner')}))"
            )
        except Exception:
            pass
        self.binding_attempts.append({
            "field_id": action.field_id,
            "labels": list(labels or ()),
            "hints": list(hints or ()),
            "result": bool(result),
            "controls": controls,
        })
        return result

    def plan_next(self):
        action = super().plan_next()
        if action is not None:
            self.next_plans.append({
                "id": action.id,
                "target": action.target_hint,
                "scope": action.dispatch_receipt_scope,
            })
        return action

    def execute(self, action):
        self.executed_actions.append({
            "id": action.id,
            "kind": action.kind.value,
            "field_id": action.field_id,
            "target": action.target_hint,
            "receipt_required": action.dispatch_receipt_required,
            "receipt_scope": action.dispatch_receipt_scope,
        })
        return super().execute(action)

    def dynamic_refresh_detected(self, action=None):
        detected = super().dynamic_refresh_detected(action)
        self.refresh_checks.append({
            "field_id": getattr(action, "field_id", ""),
            "detected": bool(detected),
            "evidence": dict(self._last_dynamic_refresh_evidence),
        })
        return detected

    def _move_visible_pointer(self, x, y, clicking=False):
        start = (self._cursor_x, self._cursor_y)
        end = (float(x), float(y))
        self.pointer_paths.append({
            "start": start,
            "end": end,
            "points": self._human_pointer_path(*start, *end),
            "clicking": bool(clicking),
        })
        return super()._move_visible_pointer(x, y, clicking=clicking)


class LocalGeminiInteractionsAPI:
    """A real localhost HTTP endpoint for the production Gemini adapter.

    It returns value-free screenshot batches and injects exactly one HTTP 503
    before the first successful response.  That exercises the adapter's real
    API serialization, retry budget, and response parser without sending any
    applicant data or consuming an external model account.
    """

    def __init__(self):
        self.requests = []
        self.successful_batches = []
        self._lock = threading.Lock()
        self._failed_once = False
        self._page_success_counts = {}
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format, *_args):
                return

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except (UnicodeError, json.JSONDecodeError):
                    self.send_error(400)
                    return
                with owner._lock:
                    request_index = len(owner.requests)
                    owner.requests.append({
                        "path": self.path,
                        "api_key": self.headers.get("x-goog-api-key", ""),
                        "payload": payload,
                    })
                    should_fail = not owner._failed_once
                    if should_fail:
                        owner._failed_once = True
                if should_fail:
                    body = json.dumps({
                        "error": {"code": 503, "message": "synthetic once"}
                    }).encode("utf-8")
                    self.send_response(503)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return

                response = owner._response_for(payload, request_index)
                body = json.dumps(response).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="mock-gemini-interactions-api",
            daemon=True,
        )

    @property
    def base_url(self):
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def start(self):
        self._thread.start()
        return self

    def close(self):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    @staticmethod
    def _prompt(payload):
        for block in payload.get("input") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                text = str(block.get("text") or "")
                if "Pending current-page field IDs:" in text:
                    return text
        raise AssertionError("Gemini request omitted the page-batch prompt")

    @classmethod
    def _request_context(cls, payload):
        prompt = cls._prompt(payload)
        pending_match = re.search(
            r"Pending current-page field IDs:\s*(\[[^\n]*\])",
            prompt,
        )
        url_match = re.search(r"Current URL:\s*(\S+)", prompt)
        if not pending_match or not url_match:
            raise AssertionError("Gemini request omitted URL/pending IDs")
        pending = json.loads(pending_match.group(1))
        url = url_match.group(1)
        node_match = re.search(r"[?&]node=([^&]+)", url)
        node = node_match.group(1) if node_match else ""
        return prompt, pending, url, node

    @staticmethod
    def _has_screenshot(payload):
        return any(
            isinstance(block, dict)
            and block.get("type") == "image"
            and bool(block.get("data"))
            for block in payload.get("input") or []
        )

    def _response_for(self, payload, request_index):
        prompt, pending, url, node = self._request_context(payload)
        with self._lock:
            page_call = self._page_success_counts.get(node, 0)
            self._page_success_counts[node] = page_call + 1

        # A screenshot model normally returns controls in visual top-to-bottom
        # order, not the checkpoint's lexical field-ID order.  Keeping the
        # postback radio last also proves that the first page is one batch,
        # followed by exactly one new batch for the new DOM generation.
        visible_pending = [
            field_id
            for field_id in PAGE_FIELDS.get(node, tuple(pending))
            if field_id in pending
        ]
        if node == "Personal1" and page_call == 0:
            # The conditional control is absent from the first screenshot and
            # appears only after the same-URL radio postback.
            visible_pending = [
                field_id
                for field_id in visible_pending
                if field_id != CONDITIONAL
            ]

        fields = []
        for offset, field_id in enumerate(visible_pending):
            # The branch's first radio is deliberately fixed at 380x360 in a
            # 1440x900 viewport.  Its normalized coordinate lets production's
            # visual identity gate prove the radio group instead of bypassing
            # coordinate validation.
            if field_id == BRANCH:
                x, y = 264, 400
            else:
                x = 250 + (offset % 2) * 330
                y = 180 + offset * 145
            fields.append({
                "field_id": field_id,
                "control_kind": CONTROL_KINDS[field_id],
                "x": x,
                "y": min(850, y),
            })

        batch = {
            "request_index": request_index,
            "node": node,
            "url": url,
            "pending": list(pending),
            "returned": [row["field_id"] for row in fields],
            "has_screenshot": self._has_screenshot(payload),
            "prompt": prompt,
        }
        with self._lock:
            self.successful_batches.append(batch)
        return {
            "id": f"synthetic-interaction-{request_index}",
            "steps": [{
                "type": "function_call",
                "name": "fill_page_fields",
                "arguments": {
                    "reason": "Synthetic Gemini API screenshot page batch",
                    "fields": fields,
                },
            }],
        }


COMMON_STYLE = r"""
  body { margin: 0; font: 18px Arial, sans-serif; color: #15244a; }
  main { width: 900px; margin: 30px auto; }
  .field { margin: 18px 0; }
  label, .question { display: block; margin-bottom: 7px; font-weight: 700; }
  input[type=text], select { width: 620px; height: 40px; font-size: 18px; }
  .buttons { display: flex; gap: 18px; margin-top: 28px; }
  button { min-width: 190px; padding: 12px 24px; font-size: 18px; }
  #errors { color: #a00000; font-weight: 700; }
"""


COMMON_SCRIPT = r"""
  const statsKey = '__mockDs160Stats';
  const savedKey = '__mockDs160SavedPages';
  const movesKey = '__mockDs160MouseMoves';
  const parseStorage = (storage, key, fallback) => {
    try { return JSON.parse(storage.getItem(key) || fallback); }
    catch (_) { return JSON.parse(fallback); }
  };
  const bump = name => {
    const stats = parseStorage(localStorage, statsKey, '{}');
    stats[name] = Number(stats[name] || 0) + 1;
    localStorage.setItem(statsKey, JSON.stringify(stats));
  };
  const persistPage = (pageName, value) => {
    const saved = parseStorage(localStorage, savedKey, '{}');
    saved[pageName] = value;
    localStorage.setItem(savedKey, JSON.stringify(saved));
    bump(pageName + 'SaveCommit');
  };
  document.addEventListener('mousemove', event => {
    const moves = parseStorage(sessionStorage, movesKey, '[]');
    moves.push([
      Math.round(event.clientX), Math.round(event.clientY), document.title
    ]);
    sessionStorage.setItem(
      movesKey, JSON.stringify(moves.slice(-2500))
    );
  }, true);
"""


def _page_shell(title, body, script):
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{title}</title><style>{COMMON_STYLE}</style></head>"
        f"<body><main>{body}</main><script>{COMMON_SCRIPT}{script}"
        "</script></body></html>"
    )


def _personal_html():
    body = r"""
      <h1>Personal Information 1</h1>
      <p>SYNTHETIC DS-160 PRACTICE SITE — NO REAL APPLICANT DATA</p>
      <form onsubmit="return false">
        <input type="hidden" name="__VIEWSTATE" value="mock-viewstate-1">
        <div id="formHost"></div>
      </form>
    """
    script = r"""
      const stateKey = '__mockPersonalState';
      let state = parseStorage(sessionStorage, stateKey,
        '{"surname":"","given":"","otherNames":"","otherSurname":""}');
      const saveState = () => sessionStorage.setItem(
        stateKey, JSON.stringify(state)
      );
      const render = () => {
        document.getElementById('formHost').innerHTML = `
          <div class="field">
            <label for="APP_SURNAME">Surnames</label>
            <input id="APP_SURNAME" name="APP_SURNAME" type="text"
                   maxlength="100" value="${state.surname}">
          </div>
          <div class="field">
            <label for="APP_GIVEN_NAME">Given Names</label>
            <input id="APP_GIVEN_NAME" name="APP_GIVEN_NAME" type="text"
                   maxlength="100" value="${state.given}">
          </div>
          <div class="field" style="height:105px">
            <span class="question">Have you ever used other names?</span>
            <input id="OTHER_NAMES_Y" name="OTHER_NAMES" type="radio"
                   value="Y" ${state.otherNames === 'Y' ? 'checked' : ''}
                   style="position:absolute;left:362px;top:342px;width:36px;height:36px">
            <label for="OTHER_NAMES_Y"
                   style="position:absolute;left:408px;top:352px">Yes</label>
            <input id="OTHER_NAMES_N" name="OTHER_NAMES" type="radio"
                   value="N" ${state.otherNames === 'N' ? 'checked' : ''}
                   style="position:absolute;left:500px;top:342px;width:36px;height:36px">
            <label for="OTHER_NAMES_N"
                   style="position:absolute;left:546px;top:352px">No</label>
          </div>
          ${state.otherNames === 'Y' ? `
            <div class="field">
              <label for="OTHER_SURNAME">Other Surnames</label>
              <input id="OTHER_SURNAME" name="OTHER_SURNAME" type="text"
                     maxlength="100" value="${state.otherSurname}">
            </div>` : ''}
          <div id="errors"></div>
          <div class="buttons">
            <button id="save" type="button">Save</button>
            <button id="next" type="button">Next: Travel</button>
          </div>`;
        document.getElementById('APP_SURNAME').addEventListener(
          'input', event => { state.surname = event.target.value; saveState(); }
        );
        document.getElementById('APP_GIVEN_NAME').addEventListener(
          'input', event => { state.given = event.target.value; saveState(); }
        );
        document.querySelectorAll('input[name="OTHER_NAMES"]').forEach(
          item => item.addEventListener('change', event => {
            state.otherNames = event.target.value;
            saveState();
            bump('personal1Postback');
            setTimeout(render, 0);
          })
        );
        const other = document.getElementById('OTHER_SURNAME');
        if (other) other.addEventListener('input', event => {
          state.otherSurname = event.target.value; saveState();
        });
        document.getElementById('save').addEventListener(
          'click', () => bump('manualSaveClicks')
        );
        document.getElementById('next').addEventListener('click', () => {
          if (!state.surname || !state.given || state.otherNames !== 'Y'
              || !state.otherSurname) {
            document.getElementById('errors').textContent =
              'Complete the synthetic required fields.';
            return;
          }
          persistPage('personal1', state);
          bump('personal1Next');
          window.location.href = __TRAVEL_URL__;
        });
      };
      render();
    """.replace("__TRAVEL_URL__", json.dumps(TRAVEL_URL))
    return _page_shell("Personal Information 1", body, script)


def _travel_html():
    body = r"""
      <h1>Travel Information</h1>
      <p>SYNTHETIC DS-160 PRACTICE SITE</p>
      <form onsubmit="return false">
        <input type="hidden" name="__VIEWSTATE" value="mock-viewstate-2">
        <div class="field">
          <label for="TRAVEL_PURPOSE">Purpose of Trip to the U.S.</label>
          <select id="TRAVEL_PURPOSE" name="TRAVEL_PURPOSE">
            <option value="">Select one</option>
            <option value="B">TEMP. BUSINESS OR PLEASURE VISITOR (B)</option>
            <option value="F">ACADEMIC STUDENT (F)</option>
          </select>
        </div>
        <div class="field">
          <label for="ARRIVAL_DATE">Intended Date of Arrival</label>
          <input id="ARRIVAL_DATE" name="ARRIVAL_DATE" type="text"
                 maxlength="10">
        </div>
        <div id="errors"></div>
        <div class="buttons">
          <button id="save" type="button">Save</button>
          <button id="next" type="button">Next: Address &amp; Phone</button>
        </div>
      </form>
    """
    script = r"""
      const state = {purpose: '', arrival: ''};
      document.getElementById('TRAVEL_PURPOSE').addEventListener(
        'change', event => { state.purpose = event.target.selectedOptions[0].text; }
      );
      document.getElementById('ARRIVAL_DATE').addEventListener(
        'input', event => { state.arrival = event.target.value; }
      );
      document.getElementById('save').addEventListener(
        'click', () => bump('manualSaveClicks')
      );
      document.getElementById('next').addEventListener('click', () => {
        if (!state.purpose || !state.arrival) {
          document.getElementById('errors').textContent =
            'Complete the synthetic required fields.';
          return;
        }
        persistPage('travel', state);
        bump('travelNext');
        window.location.href = __ADDRESS_URL__;
      });
    """.replace("__ADDRESS_URL__", json.dumps(ADDRESS_URL))
    return _page_shell("Travel Information", body, script)


def _address_html():
    body = r"""
      <h1>Address and Phone Information</h1>
      <p>SYNTHETIC DS-160 PRACTICE SITE</p>
      <form onsubmit="return false">
        <input type="hidden" name="__VIEWSTATE" value="mock-viewstate-3">
        <div class="field">
          <label for="HOME_STREET1">Home Address Line 1</label>
          <input id="HOME_STREET1" name="HOME_STREET1" type="text"
                 maxlength="120">
        </div>
        <div class="field">
          <label for="HOME_CITY">Home City</label>
          <input id="HOME_CITY" name="HOME_CITY" type="text" maxlength="80">
        </div>
        <div class="field">
          <label for="HOME_COUNTRY">Home Country/Region</label>
          <select id="HOME_COUNTRY" name="HOME_COUNTRY">
            <option value="">Select one</option>
            <option value="CAN">CANADA</option>
            <option value="MEX">MEXICO</option>
          </select>
        </div>
        <div class="field">
          <label for="PRIMARY_PHONE">Primary Phone Number</label>
          <input id="PRIMARY_PHONE" name="PRIMARY_PHONE" type="text"
                 maxlength="14">
        </div>
        <div id="errors"></div>
        <div class="buttons">
          <button id="save" type="button">Save</button>
          <button id="next" type="button">Next: Passport</button>
        </div>
      </form>
    """
    script = r"""
      const state = {street: '', city: '', country: '', phone: ''};
      document.getElementById('HOME_STREET1').addEventListener(
        'input', event => { state.street = event.target.value; }
      );
      document.getElementById('HOME_CITY').addEventListener(
        'input', event => { state.city = event.target.value; }
      );
      document.getElementById('HOME_COUNTRY').addEventListener(
        'change', event => { state.country = event.target.selectedOptions[0].text; }
      );
      document.getElementById('PRIMARY_PHONE').addEventListener(
        'input', event => { state.phone = event.target.value; }
      );
      document.getElementById('save').addEventListener(
        'click', () => bump('manualSaveClicks')
      );
      document.getElementById('next').addEventListener('click', () => {
        if (!state.street || !state.city || !state.country || !state.phone) {
          document.getElementById('errors').textContent =
            'Complete the synthetic required fields.';
          return;
        }
        persistPage('addressPhone', state);
        bump('addressPhoneNext');
        window.location.href = __PASSPORT_URL__;
      });
    """.replace("__PASSPORT_URL__", json.dumps(PASSPORT_URL))
    return _page_shell("Address and Phone Information", body, script)


def _passport_html():
    body = r"""
      <h1>Passport Information</h1>
      <p>SYNTHETIC DS-160 PRACTICE SITE</p>
      <form onsubmit="return false">
        <input type="hidden" name="__VIEWSTATE" value="mock-viewstate-4">
        <div class="field">
          <label for="PPT_NUM">Passport/Travel Document Number</label>
          <input id="PPT_NUM" name="PPT_NUM" type="text" maxlength="20">
        </div>
        <div class="field">
          <label for="PPT_ISSUED_CNTRY">
            Country/Authority that Issued Passport
          </label>
          <select id="PPT_ISSUED_CNTRY" name="PPT_ISSUED_CNTRY">
            <option value="">Select one</option>
            <option value="CAN">CANADA</option>
            <option value="MEX">MEXICO</option>
          </select>
        </div>
        <div id="errors"></div>
        <div class="buttons">
          <button id="save" type="button">Save</button>
          <button id="next" type="button">Next: Review</button>
        </div>
      </form>
    """
    script = r"""
      const state = {number: '', country: ''};
      document.getElementById('PPT_NUM').addEventListener(
        'input', event => { state.number = event.target.value; }
      );
      document.getElementById('PPT_ISSUED_CNTRY').addEventListener(
        'change', event => { state.country = event.target.selectedOptions[0].text; }
      );
      document.getElementById('save').addEventListener(
        'click', () => bump('manualSaveClicks')
      );
      document.getElementById('next').addEventListener('click', () => {
        if (!state.number || !state.country) {
          document.getElementById('errors').textContent =
            'Complete the synthetic required fields.';
          return;
        }
        persistPage('passport', state);
        bump('passportNext');
        window.location.href = __REVIEW_URL__;
      });
    """.replace("__REVIEW_URL__", json.dumps(REVIEW_URL))
    return _page_shell("Passport Information", body, script)


def _review_html():
    body = r"""
      <h1>Review Application</h1>
      <p>Final human review, signature, and submission boundary.</p>
      <button id="sign" type="button">Sign Application</button>
      <button id="submit" type="button">Submit Application</button>
    """
    script = r"""
      for (const id of ['sign', 'submit']) {
        document.getElementById(id).addEventListener(
          'click', () => bump('finalActionCount')
        );
      }
    """
    return _page_shell("Review Application", body, script)


def _route_synthetic_ds160(driver):
    pages = {
        "node=Personal1": _personal_html,
        "node=Travel": _travel_html,
        "node=AddressPhone": _address_html,
        "node=PptVisa": _passport_html,
        "node=ReviewReview": _review_html,
    }

    def fulfill(route):
        page_factory = next(
            (factory for token, factory in pages.items()
             if token in route.request.url),
            None,
        )
        if page_factory is None:
            route.abort()
            return
        route.fulfill(
            status=200,
            content_type="text/html; charset=utf-8",
            body=page_factory(),
        )

    driver._page.route("https://ceac.state.gov/**", fulfill)


class MockDs160ApiPlaywrightE2ETests(unittest.TestCase):
    @staticmethod
    def _field(field_id):
        return {
            "id": field_id,
            "value": SYNTHETIC_VALUES[field_id],
            "label": FIELD_LABELS[field_id],
            "confidence": 1.0,
            "risk_level": "high",
        }

    @staticmethod
    def _curve_deviation(trace):
        start_x, start_y = trace["start"]
        end_x, end_y = trace["end"]
        delta_x = end_x - start_x
        delta_y = end_y - start_y
        length = math.hypot(delta_x, delta_y)
        if length < 1:
            return 0.0
        return max(
            abs(
                delta_y * (point_x - start_x)
                - delta_x * (point_y - start_y)
            ) / length
            for point_x, point_y in trace["points"]
        )

    def test_one_start_uses_gemini_api_and_reaches_review_boundary(self):
        required = list(SYNTHETIC_VALUES)
        startup_errors = []
        service = None
        try:
            api = LocalGeminiInteractionsAPI().start()
        except OSError as error:
            self.skipTest(
                "Local HTTP sockets are unavailable for Gemini API E2E: "
                f"{error}"
            )

        with tempfile.TemporaryDirectory() as directory:
            def runtime_factory(job):
                driver = RecordedVisualDriver()
                driver.set_execution_mode("visual")
                try:
                    driver.start("about:blank")
                    _route_synthetic_ds160(driver)
                    driver._page.goto(
                        job.start_url,
                        wait_until="domcontentloaded",
                        timeout=driver.NAVIGATION_TIMEOUT_MS,
                    )
                except Exception as error:
                    startup_errors.append(error)
                    driver.close()
                    raise ProviderNotConfigured(
                        "Playwright Chromium is unavailable for E2E"
                    ) from error

                model = GeminiComputerUseAdapter(ProviderConfig(
                    provider="google",
                    model="gemini-synthetic-api-test",
                    api_base_url=api.base_url,
                    api_key="synthetic-local-key",
                ))
                runtime = ComputerUseAgent(
                    model,
                    driver,
                    max_steps=100,
                    execution_mode="visual",
                )
                runtime._mock_api_model = model
                return runtime

            service = AgentService(
                AgentConfig(data_dir=Path(directory) / "checkpoints"),
                runtime_factory=runtime_factory,
            )
            try:
                created = service.create_job({
                    "startUrl": PERSONAL_URL,
                    "requiredFieldIds": required,
                    "fields": [self._field(field_id) for field_id in required],
                    "autoNext": True,
                })
                reviewed = service.review_job(created["id"], {
                    "actor": "synthetic-playwright-api-e2e",
                    "decisions": [
                        {
                            "fieldId": field_id,
                            "approved": True,
                            "value": SYNTHETIC_VALUES[field_id],
                        }
                        for field_id in required
                    ],
                })

                start_call_count = 0
                try:
                    start_call_count += 1
                    run_started = time.monotonic()
                    result = service.start_job(reviewed["id"])
                    run_duration_seconds = time.monotonic() - run_started
                except ServiceError:
                    if startup_errors:
                        self.skipTest(
                            "Playwright/Chromium unavailable: "
                            f"{startup_errors[-1]}"
                        )
                    raise

                self.assertEqual(start_call_count, 1)
                # A local API response is intentionally immediate. This broad
                # ceiling catches regressions back to minute-per-field loops
                # without making the test sensitive to normal CI variance.
                self.assertLess(run_duration_seconds, 30.0)
                with service._runtime_lock:
                    debug_worker = service._runtimes.get(reviewed["id"])
                debug_bindings = (
                    debug_worker.call(
                        lambda runtime: list(
                            runtime.browser.binding_attempts
                        ),
                        timeout=5,
                    )
                    if debug_worker is not None else []
                )
                self.assertEqual(
                    result["state"],
                    "review_required",
                    json.dumps({
                        "state": result.get("state"),
                        "checkpoint": result.get("human_checkpoint"),
                        "waitKind": result.get("wait_kind"),
                        "completed": result.get("completed_field_ids"),
                        "events": [
                            {
                                "kind": event.get("kind"),
                                "message": event.get("message"),
                                "detail": event.get("detail"),
                            }
                            for event in result.get("events", [])[-12:]
                        ],
                        "batches": api.successful_batches,
                        "bindings": debug_bindings,
                    }, ensure_ascii=False, default=str),
                )
                self.assertTrue(result["final_submission_boundary_reached"])
                self.assertFalse(result["continuous_run_requested"])
                self.assertEqual(
                    set(result["completed_field_ids"]),
                    set(required),
                )
                self.assertIn(
                    "最终签名和提交前停止",
                    result["human_checkpoint"],
                )

                with service._runtime_lock:
                    worker = service._runtimes[reviewed["id"]]

                def inspect(runtime):
                    browser = runtime.browser
                    observation = browser.observe_lightweight()
                    page_state = browser._page.evaluate(
                        """() => {
                            const parse = (storage, key, fallback) => {
                                try {
                                    return JSON.parse(
                                        storage.getItem(key) || fallback
                                    );
                                } catch (_) {
                                    return JSON.parse(fallback);
                                }
                            };
                            const cursor = document.getElementById(
                                'docflow-agent-visible-cursor'
                            );
                            const cursorStyle = cursor
                                ? getComputedStyle(cursor) : null;
                            const status = document.getElementById(
                                'docflow-agent-visual-status'
                            );
                            return {
                                stats: parse(
                                    localStorage,
                                    '__mockDs160Stats',
                                    '{}'
                                ),
                                saved: parse(
                                    localStorage,
                                    '__mockDs160SavedPages',
                                    '{}'
                                ),
                                moves: parse(
                                    sessionStorage,
                                    '__mockDs160MouseMoves',
                                    '[]'
                                ),
                                cursor: {
                                    present: Boolean(cursor),
                                    display: cursorStyle?.display || '',
                                    visibility: cursorStyle?.visibility || '',
                                    width: cursor?.getBoundingClientRect()
                                        .width || 0
                                },
                                status: {
                                    present: Boolean(status),
                                    state: status?.dataset.state || ''
                                }
                            };
                        }"""
                    )
                    return {
                        "url": observation.url,
                        "dispatch_ids": observation.dispatched_action_ids,
                        "dispatch_scope": observation.dispatch_receipt_scope,
                        "dispatch_authoritative": (
                            observation.dispatch_receipts_authoritative
                        ),
                        "executed": list(browser.executed_actions),
                        "next_plans": list(browser.next_plans),
                        "pointer_paths": list(browser.pointer_paths),
                        "refresh_checks": list(browser.refresh_checks),
                        "binding_attempts": list(browser.binding_attempts),
                        "visual_statuses": list(browser.visual_statuses),
                        "model_interactions": (
                            runtime._mock_api_model.interaction_count
                        ),
                        "model_requests": (
                            runtime._mock_api_model.request_count
                        ),
                        "page_state": page_state,
                    }

                snapshot = worker.call(inspect, timeout=15)
                self.assertEqual(snapshot["url"], REVIEW_URL)

                stats = snapshot["page_state"]["stats"]
                for page_key in (
                    "personal1", "travel", "addressPhone", "passport"
                ):
                    self.assertEqual(stats.get(page_key + "Next"), 1, stats)
                    self.assertEqual(
                        stats.get(page_key + "SaveCommit"), 1, stats
                    )
                self.assertEqual(stats.get("personal1Postback"), 1, stats)
                self.assertEqual(stats.get("manualSaveClicks", 0), 0, stats)
                self.assertEqual(stats.get("finalActionCount", 0), 0, stats)

                saved = snapshot["page_state"]["saved"]
                self.assertEqual(saved["personal1"], {
                    "surname": "TESTER",
                    "given": "NOVA",
                    "otherNames": "Y",
                    "otherSurname": "FICTION",
                })
                self.assertEqual(saved["travel"], {
                    "purpose": "TEMP. BUSINESS OR PLEASURE VISITOR (B)",
                    "arrival": "2030-04-05",
                })
                self.assertEqual(saved["addressPhone"], {
                    "street": "1 FICTION LOOP",
                    "city": "EXAMPLEVILLE",
                    "country": "CANADA",
                    "phone": "2025550199",
                })
                self.assertEqual(saved["passport"], {
                    "number": "DEMO00001",
                    "country": "CANADA",
                })

                executed = snapshot["executed"]
                field_actions = [
                    action for action in executed if action["field_id"]
                ]
                next_actions = [
                    action for action in executed
                    if action["kind"] == ActionKind.CLICK.value
                    and action["target"].lower().startswith("next")
                ]
                self.assertEqual(len(field_actions), len(required), field_actions)
                self.assertEqual(
                    {action["field_id"] for action in field_actions},
                    set(required),
                )
                self.assertEqual(len(next_actions), 4, next_actions)
                self.assertTrue(all(
                    action["receipt_required"] for action in next_actions
                ))
                self.assertEqual(
                    len({action["id"] for action in executed}),
                    len(executed),
                )
                self.assertFalse(any(
                    "sign" in action["target"].casefold()
                    or "submit" in action["target"].casefold()
                    for action in executed
                ))

                self.assertEqual(len(snapshot["next_plans"]), 4)
                receipt_scopes = {
                    plan["scope"] for plan in snapshot["next_plans"]
                }
                self.assertEqual(len(receipt_scopes), 1)
                self.assertNotEqual(receipt_scopes, {""})
                self.assertTrue(snapshot["dispatch_authoritative"])
                self.assertIn(snapshot["dispatch_scope"], receipt_scopes)
                self.assertTrue({
                    action["id"] for action in next_actions
                }.issubset(set(snapshot["dispatch_ids"])))

                batches = list(api.successful_batches)
                self.assertEqual(
                    [batch["node"] for batch in batches],
                    [
                        "Personal1", "Personal1", "Travel",
                        "AddressPhone", "PptVisa",
                    ],
                    batches,
                )
                self.assertEqual(
                    [batch["returned"] for batch in batches],
                    [
                        list(PAGE_FIELDS["Personal1"][:3]),
                        [CONDITIONAL],
                        list(PAGE_FIELDS["Travel"]),
                        list(PAGE_FIELDS["AddressPhone"]),
                        list(PAGE_FIELDS["PptVisa"]),
                    ],
                )
                self.assertTrue(all(
                    batch["has_screenshot"] for batch in batches
                ))
                self.assertEqual(snapshot["model_interactions"], 5)
                self.assertEqual(snapshot["model_requests"], 6)
                self.assertEqual(len(api.requests), 6)
                self.assertTrue(all(
                    request["path"] == "/v1beta/interactions"
                    for request in api.requests
                ))
                self.assertTrue(all(
                    request["api_key"] == "synthetic-local-key"
                    for request in api.requests
                ))
                self.assertTrue(all(
                    LocalGeminiInteractionsAPI._has_screenshot(
                        request["payload"]
                    )
                    for request in api.requests
                ))
                # The screenshot is binary/base64. Text-field values remain
                # system-owned and never appear in the Gemini JSON prompt.
                serialized_requests = json.dumps(
                    [request["payload"] for request in api.requests]
                )
                for value in (
                    "TESTER", "NOVA", "FICTION", "1 FICTION LOOP",
                    "EXAMPLEVILLE", "2025550199", "DEMO00001",
                ):
                    self.assertNotIn(value, serialized_requests)

                refresh_by_field = {
                    check["field_id"]: check
                    for check in snapshot["refresh_checks"]
                }
                self.assertTrue(refresh_by_field[BRANCH]["detected"])
                for field_id in set(required) - {BRANCH}:
                    self.assertFalse(
                        refresh_by_field[field_id]["detected"],
                        refresh_by_field[field_id],
                    )
                self.assertTrue(all(
                    attempt["result"]
                    for attempt in snapshot["binding_attempts"]
                ), snapshot["binding_attempts"])

                paths = snapshot["pointer_paths"]
                self.assertGreaterEqual(len(paths), len(executed))
                curves = [
                    trace for trace in paths
                    if len(trace["points"]) >= 3
                    and self._curve_deviation(trace) > 2.0
                ]
                self.assertGreaterEqual(len(curves), 6)
                self.assertGreaterEqual(
                    len(snapshot["page_state"]["moves"]), 60
                )
                cursor = snapshot["page_state"]["cursor"]
                self.assertTrue(cursor["present"])
                self.assertNotEqual(cursor["display"], "none")
                self.assertNotEqual(cursor["visibility"], "hidden")
                self.assertGreater(cursor["width"], 0)
                self.assertTrue(snapshot["page_state"]["status"]["present"])
                status_states = {
                    status["state"]
                    for status in snapshot["visual_statuses"]
                }
                self.assertTrue({
                    "observing", "thinking", "working", "navigating",
                    "paused",
                }.issubset(status_states), snapshot["visual_statuses"])
                self.assertTrue(any(
                    "等待 Gemini" in status["message"]
                    or "规划本页" in status["message"]
                    for status in snapshot["visual_statuses"]
                    if status["state"] == "thinking"
                ), snapshot["visual_statuses"])

                event_kinds = [event["kind"] for event in result["events"]]
                self.assertEqual(event_kinds.count("started"), 1)
                self.assertEqual(
                    event_kinds.count("model_planning_started"), 5
                )
                self.assertEqual(
                    event_kinds.count("page_navigation_verified"), 4
                )
                self.assertIn("dynamic_refresh_replanned", event_kinds)
            finally:
                if service is not None:
                    service.shutdown(timeout=15)
                api.close()


if __name__ == "__main__":
    unittest.main()
