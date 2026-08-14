"""Opt-in full DS-160-shaped acceptance with live Gemini and Chromium.

The browser is isolated and every ``ceac.state.gov`` request is intercepted
before the network.  The fixture contains only deliberately fictional data.
The live class is skipped unless ``DOCFLOW_RUN_FULL_LIVE_GEMINI_E2E=1`` is
set because it makes billable calls to Google's official Gemini API.

Useful optional switches:

* ``DOCFLOW_LIVE_E2E_HEADED=1`` shows the isolated Chromium window.
* ``DOCFLOW_LIVE_E2E_HOLD_SECONDS=N`` keeps the final Review page visible for
  at most 15 seconds; it never waits for human input.
* ``DOCFLOW_LIVE_E2E_ARTIFACT_DIR=/path`` writes a final screenshot and
  value-free telemetry.  Headed mode creates a timestamped directory under
  the system temporary directory when this is omitted.
"""

import base64
import html
import json
import math
import os
import re
import tempfile
import threading
import time
import unittest
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from visa_agent.adapters import (
    GeminiComputerUseAdapter,
    PlaywrightBrowserDriver,
)
from visa_agent.config import AgentConfig, ProviderConfig, load_config
from visa_agent.models import ActionKind, BrowserObservation, ComputerAction
from visa_agent.page_plans import PagePlanRegistry, classify_ceac_page
from visa_agent.providers import ProviderNotConfigured
from visa_agent.service import AgentService, ServiceError
from visa_agent.workflow import ComputerUseAgent


CEAC_COMPLETE_ROOT = "https://ceac.state.gov/GenNIV/General/complete/"
REVIEW_URL = (
    "https://ceac.state.gov/GenNIV/General/Review/"
    "ReviewReview.aspx?node=ReviewReview"
)
OFFICIAL_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com"


@dataclass(frozen=True)
class SyntheticField:
    id: str
    value: str
    label: str
    control: str
    prompt: str
    options: tuple = ()
    below_fold: bool = False
    dynamic: str = ""


@dataclass(frozen=True)
class SyntheticPage:
    key: str
    filename: str
    node: str
    title: str
    fields: tuple
    directory: str = "complete"

    @property
    def url(self):
        return (
            f"https://ceac.state.gov/GenNIV/General/{self.directory}/"
            f"{self.filename}?node={self.node}"
        )

    @property
    def plan_id(self):
        return (
            "ceac-plan-photo"
            if self.key == "photo"
            else f"ceac-plan-{self.key}"
        )


def field(field_id, value, prompt, control="text", *, options=(),
          below_fold=False, dynamic="", metadata="", hint=""):
    hint = hint or re.sub(
        r"[^A-Za-z0-9]+", "_", field_id
    ).strip("_").upper()
    descriptors = [f"control={control}", f"control_hints={hint}"]
    if metadata:
        descriptors.append(metadata)
    if control in {"select", "yes_no", "radio", "checkbox"}:
        descriptors.append(f"human-approved value={value}")
    return SyntheticField(
        id=field_id,
        value=value,
        label=f"{prompt} [{'; '.join(descriptors)}]",
        control=control,
        prompt=prompt,
        options=tuple(options),
        below_fold=below_fold,
        dynamic=dynamic,
    )


# These values are intentionally fictional.  Reserved 555 phone numbers,
# example domains, and DEMO identifiers make accidental real-world reuse
# conspicuous.  No value is read from a DocFlow case, upload, profile, or
# checkpoint.
PAGES = (
    SyntheticPage("personal1", "complete_personal.aspx", "Personal1",
                  "Personal Information 1", (
        field("personal.surname", "TESTER", "Surnames"),
        field(
            "ceac.personal1.001.other_names.used", "yes",
            "Have you ever used other names?", "radio",
            options=(("Y", "Yes"), ("N", "No")),
            metadata="occurrence=1; refresh_after_change=true",
            dynamic="branch",
        ),
        field(
            "ceac.personal1.002.other_names.surname", "FICTION",
            "Other Surnames", dynamic="conditional",
        ),
    )),
    SyntheticPage("personal2", "complete_personalcont.aspx", "Personal2",
                  "Personal Information 2", (
        field(
            "personal.nationality", "CANADA", "Nationality", "select",
            options=(("CAN", "CANADA"), ("MEX", "MEXICO")),
        ),
        field(
            "ceac.personal2.001.birth_date", "2000-01-15",
            "Date of Birth", "date",
        ),
    )),
    SyntheticPage("travel", "complete_travel.aspx", "Travel",
                  "Travel Information", (
        field(
            "travel.purpose", "TEMP. BUSINESS OR PLEASURE VISITOR (B)",
            "Purpose of Trip to the U.S.", "select",
            options=(("B", "TEMP. BUSINESS OR PLEASURE VISITOR (B)"),
                     ("F", "ACADEMIC STUDENT (F)")),
        ),
        field(
            "ceac.travel.002.trip_description",
            "SYNTHETIC PRODUCT CONFERENCE VISIT",
            "Brief Description of Travel Plans", "textarea",
        ),
    )),
    SyntheticPage(
        "travel_companions", "complete_travelcompanions.aspx",
        "TravelCompanions", "Travel Companions Information", (
            field(
                "ceac.travel_companions.002.surname", "ALPHA",
                "Companion Surname", metadata="occurrence=1",
                dynamic="repeater_row_1", hint="COMPANION_SURNAME",
            ),
            field(
                "ceac.travel_companions.001.rows", "2", "Add Another",
                "ensure_repeater",
                metadata=("expected_count=2; "
                          "record_labels=Companion Surname"),
                dynamic="repeater",
            ),
            field(
                "ceac.travel_companions.003.surname", "BETA",
                "Companion Surname", metadata="occurrence=2",
                dynamic="repeater_row_2", hint="COMPANION_SURNAME",
            ),
        ),
    ),
    SyntheticPage(
        "previous_us_travel", "complete_previousustravel.aspx",
        "PreviousUSTravel", "Previous U.S. Travel Information", (
            field(
                "ceac.previous_us_travel.001.has_visited", "NO",
                "Have you ever been in the U.S.?", "select",
                options=(("Y", "YES"), ("N", "NO")),
            ),
        ),
    ),
    SyntheticPage("address_phone", "complete_contact.aspx", "AddressPhone",
                  "Address and Phone Information", (
        field(
            "contact.homeAddress", "1 FICTION LOOP, EXAMPLEVILLE",
            "Home Address", "textarea",
        ),
        field(
            "contact.primaryPhone", "2025550142", "Primary Phone Number",
            "text_segments",
        ),
    )),
    SyntheticPage("passport", "complete_pptvisa.aspx", "PptVisa",
                  "Passport Information", (
        field(
            "passport.number", "DEMO00001",
            "Passport/Travel Document Number",
        ),
        field(
            "passport.issuingCountry", "CANADA",
            "Country/Authority that Issued Passport", "select",
            options=(("CAN", "CANADA"), ("MEX", "MEXICO")),
        ),
    )),
    SyntheticPage("us_contact", "complete_uscontact.aspx", "USContact",
                  "U.S. Point of Contact Information", (
        field(
            "contact.organizationName", "EXAMPLE CONFERENCE CENTER",
            "Organization Name",
        ),
        field(
            "ceac.us_contact.002.relationship", "OTHER",
            "Relationship to You", "select",
            options=(("OTHER", "OTHER"), ("SCHOOL", "SCHOOL")),
        ),
    )),
    SyntheticPage("relatives", "complete_family1.aspx", "Relatives",
                  "Family Information: Relatives", (
        field(
            "ceac.relatives.001.father_surname", "PARENTTEST",
            "Father's Surnames",
        ),
        field(
            "ceac.relatives.002.parent_details_confirmed", "true",
            "Parent Details Confirmed", "checkbox",
        ),
    )),
    SyntheticPage("spouse", "complete_family2.aspx", "Spouse",
                  "Family Information: Spouse", (
        field(
            "ceac.spouse.001.surname", "PARTNERTEST", "Spouse's Surnames",
        ),
        field(
            "ceac.spouse.002.birth_date", "2001-02-16",
            "Spouse's Date of Birth", "date",
        ),
    )),
    SyntheticPage(
        "work_education1", "complete_workeducation1.aspx", "WorkEducation1",
        "Present Work/Education/Training Information", (
            field(
                "ceac.work_education1.001.occupation", "STUDENT",
                "Primary Occupation", "select",
                options=(("STUDENT", "STUDENT"),
                         ("OTHER", "OTHER")),
            ),
            field(
                "ceac.work_education1.002.duties",
                "SYNTHETIC COURSEWORK AND RESEARCH",
                "Briefly Describe Your Duties", "textarea",
            ),
        ),
    ),
    SyntheticPage(
        "work_education2", "complete_workeducation2.aspx", "WorkEducation2",
        "Previous Work/Education/Training Information", (
            field(
                "ceac.work_education2.001.employer",
                "FICTIONAL LABORATORY LLC", "Employer Name",
            ),
        ),
    ),
    SyntheticPage(
        "work_education3", "complete_workeducation3.aspx", "WorkEducation3",
        "Additional Work/Education/Training Information", (
            field(
                "ceac.work_education3.001.skill",
                "SYNTHETIC DATA VISUALIZATION", "Specialized Skills",
                "textarea", below_fold=True,
            ),
        ),
    ),
    SyntheticPage("sevis", "complete_sevis.aspx", "SEVIS",
                  "Student and Exchange Visitor Information", (
        field("education.sevisId", "N0000000001", "SEVIS ID"),
        field(
            "education.schoolName", "EXAMPLE STATE UNIVERSITY",
            "Name of School",
        ),
    )),
    SyntheticPage(
        "additional_contacts", "complete_additionalpointcontact.aspx",
        "AdditionalPointContact", "Additional Point of Contact Information",
        (
            field(
                "ceac.additional_contacts.001.surname", "CONTACTONE",
                "Additional Contact Surname",
            ),
            field(
                "ceac.additional_contacts.002.email",
                "contact@example.test", "Additional Contact Email",
            ),
        ),
    ),
    *tuple(
        SyntheticPage(
            f"security_background{part}",
            f"complete_securityandbackground{part}.aspx",
            f"SecurityandBackground{part}",
            f"Security and Background: Part {part}",
            (
                field(
                    f"ceac.security_background{part}.001.answer", "NO",
                    f"Security and Background Part {part} Question",
                    "select", options=(("Y", "YES"), ("N", "NO")),
                ),
            ),
        )
        for part in range(1, 6)
    ),
    SyntheticPage(
        "photo", "PhotoUpload.aspx", "PhotoUpload", "Photo Confirmation",
        (
            field(
                "ceac.photo.001.photo_accepted", "true",
                "Use Accepted Synthetic Photo", "checkbox",
            ),
        ),
        directory="Photo",
    ),
)

PAGE_BY_NODE = {page.node: page for page in PAGES}
PAGE_BY_KEY = {page.key: page for page in PAGES}
ALL_FIELDS = tuple(item for page in PAGES for item in page.fields)
FIELD_BY_ID = {item.id: item for item in ALL_FIELDS}


def _safe_id(value):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")


def _attrs(item, suffix=""):
    identifier = _safe_id(item.id + suffix)
    values = {
        "id": identifier,
        "name": _safe_id(item.id),
        "data-test-field-id": item.id,
    }
    # Occurrence-bound controls intentionally share semantic name/label
    # evidence.  This forces the production occurrence resolver to disambiguate
    # them instead of bypassing the structural contract with an exact ID.
    if "occurrence=" not in item.label and item.control != "radio":
        values["data-docflow-field"] = item.id
    return " ".join(
        f'{key}="{html.escape(str(value), quote=True)}"'
        for key, value in values.items()
    )


def _render_control(item):
    escaped_prompt = html.escape(item.prompt)
    if item.control == "textarea":
        control = f"<textarea {_attrs(item)} maxlength='240'></textarea>"
    elif item.control == "select":
        options = "<option value=''>SELECT ONE</option>" + "".join(
            f"<option value='{html.escape(value, quote=True)}'>"
            f"{html.escape(label)}</option>"
            for value, label in item.options
        )
        control = f"<select {_attrs(item)}>{options}</select>"
    elif item.control == "checkbox":
        control = (
            f"<input {_attrs(item)} type='checkbox' value='true'>"
            f"<label class='inline' for='{_safe_id(item.id)}'>Yes</label>"
        )
    elif item.control == "radio":
        name = _safe_id(item.id)
        radios = []
        for index, (value, label) in enumerate(item.options, start=1):
            option_id = f"{name}_{index}"
            radios.append(
                f"<input id='{option_id}' name='{name}' type='radio' "
                f"data-test-field-id='{html.escape(item.id, quote=True)}' "
                f"value='{html.escape(value, quote=True)}'>"
                f"<label class='inline' for='{option_id}'>"
                f"{html.escape(label)}</label>"
            )
        control = "<div class='radio-row'>" + "".join(radios) + "</div>"
    elif item.control == "date":
        month_options = "".join(
            f"<option value='{number:02d}'>{name}</option>"
            for number, name in enumerate(
                ("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                 "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"),
                start=1,
            )
        )
        day_options = "".join(
            f"<option value='{number:02d}'>{number:02d}</option>"
            for number in range(1, 32)
        )
        group = html.escape(item.id, quote=True)
        control = (
            f"<div class='segments' data-test-field-id='{group}' "
            f"data-docflow-field='{group}'>"
            f"<select id='{_safe_id(item.id)}_month'>"
            f"<option value=''>MON</option>{month_options}</select>"
            f"<select id='{_safe_id(item.id)}_day'>"
            f"<option value=''>DAY</option>{day_options}</select>"
            f"<input id='{_safe_id(item.id)}_year' type='text' "
            "maxlength='4' placeholder='YYYY'></div>"
        )
    elif item.control == "text_segments":
        group = html.escape(item.id, quote=True)
        control = (
            f"<div class='segments' data-test-field-id='{group}' "
            f"data-docflow-field='{group}'>"
            f"<input id='{_safe_id(item.id)}_1' type='tel' maxlength='3'>"
            f"<input id='{_safe_id(item.id)}_2' type='tel' maxlength='3'>"
            f"<input id='{_safe_id(item.id)}_3' type='tel' maxlength='4'>"
            "</div>"
        )
    elif item.control == "ensure_repeater":
        control = (
            f"<button {_attrs(item)} type='button' class='add-another'>"
            "Add Another</button>"
        )
    else:
        control = f"<input {_attrs(item)} type='text' maxlength='180'>"
    spacer = "<div class='scroll-spacer'>Scroll down to continue</div>" \
        if item.below_fold else ""
    return (
        spacer
        + f"<div class='field' data-fixture-field='{html.escape(item.id)}'>"
        + f"<label>{escaped_prompt}</label>{control}</div>"
    )


COMMON_CSS = """
  :root { color-scheme: light; }
  body { margin: 0; background: #eef1f5; color: #162d5a;
         font: 17px Arial, sans-serif; }
  header { background: #073b62; color: white; padding: 16px 28px; }
  header strong { font-size: 24px; }
  nav { background: #a51d12; color: white; padding: 9px 28px; }
  main { width: 940px; margin: 22px auto 80px; background: white;
         padding: 24px 34px 38px; box-shadow: 0 2px 12px #bbc2ca; }
  h1 { margin: 0 0 8px; font-family: Georgia, serif; font-size: 34px; }
  .synthetic { margin: 0 0 18px; padding: 10px; background: #fff5cc;
               color: #5d4700; font-weight: 700; }
  .field { margin: 18px 0; }
  .field > label { display: block; margin-bottom: 7px; font-weight: 700; }
  input[type=text], input[type=tel], textarea, select {
    box-sizing: border-box; min-height: 42px; border: 1px solid #607493;
    background: #ffffdf; padding: 7px 9px; font-size: 17px;
  }
  input[type=text], textarea, .field > select { width: 660px; }
  textarea { height: 82px; resize: none; }
  .segments { display: flex; gap: 8px; }
  .segments select { width: 130px; }
  .segments input { width: 130px; }
  .radio-row { display: flex; align-items: center; gap: 10px; height: 48px; }
  .radio-row input, input[type=checkbox] { width: 28px; height: 28px; }
  label.inline { display: inline; margin-right: 28px; font-weight: 400; }
  .buttons { display: flex; gap: 14px; margin-top: 24px; }
  button { min-width: 180px; padding: 12px 22px; font-size: 17px; }
  .next { background: #a51d12; color: white; border: 0; }
  .errors { color: #a00000; min-height: 24px; font-weight: 700; }
  .scroll-spacer { height: 920px; display: flex; align-items: flex-start;
                   color: #667; font-style: italic; }
"""


COMMON_SCRIPT = r"""
  const statsKey = '__docflowFullLiveStats';
  const savedKey = '__docflowFullLiveSaved';
  const movesKey = '__docflowFullLiveMoves';
  const parseStore = (storage, key, fallback) => {
    try { return JSON.parse(storage.getItem(key) || fallback); }
    catch (_) { return JSON.parse(fallback); }
  };
  const bump = name => {
    const stats = parseStore(localStorage, statsKey, '{}');
    stats[name] = Number(stats[name] || 0) + 1;
    localStorage.setItem(statsKey, JSON.stringify(stats));
  };
  const savePage = (key, values) => {
    const saved = parseStore(localStorage, savedKey, '{}');
    saved[key] = values;
    localStorage.setItem(savedKey, JSON.stringify(saved));
    bump('save:' + key);
  };
  document.addEventListener('mousemove', event => {
    const moves = parseStore(sessionStorage, movesKey, '[]');
    moves.push([Math.round(event.clientX), Math.round(event.clientY), pageKey]);
    sessionStorage.setItem(movesKey, JSON.stringify(moves.slice(-5000)));
  }, true);
  const controlsFor = fieldId => Array.from(document.querySelectorAll(
    `[data-test-field-id="${CSS.escape(fieldId)}"]`
  ));
  const valueFor = fieldId => {
    if (fieldId === repeaterFieldId) return String(repeaterCount());
    const roots = controlsFor(fieldId);
    const controls = [];
    for (const root of roots) {
      if (root.matches('input, textarea, select')) controls.push(root);
      controls.push(...root.querySelectorAll('input, textarea, select'));
    }
    const unique = Array.from(new Set(controls));
    const radios = unique.filter(item => item.type === 'radio');
    if (radios.length) return radios.find(item => item.checked)?.value || '';
    if (unique.length === 1 && unique[0].type === 'checkbox') {
      return unique[0].checked ? 'true' : '';
    }
    return unique.map(item => String(item.value || '').trim())
      .filter(Boolean).join('|');
  };
  const isComplete = fieldId => {
    if (fieldId === repeaterFieldId) return repeaterCount() >= 2;
    return Boolean(valueFor(fieldId));
  };
  const installButtons = () => {
    document.getElementById('save').onclick = () => bump('manualSave');
    document.getElementById('next').onclick = () => {
      const missing = requiredFieldIds.filter(id => !isComplete(id));
      if (missing.length) {
        document.getElementById('errors').textContent =
          'Synthetic required fields are incomplete: ' + missing.join(', ');
        bump('validationBlocked:' + pageKey);
        return;
      }
      const values = Object.fromEntries(
        requiredFieldIds.map(id => [id, valueFor(id)])
      );
      savePage(pageKey, values);
      bump('next:' + pageKey);
      location.href = nextUrl;
    };
  };
"""


def _page_html(page, next_url):
    regular = [
        item for item in page.fields
        if item.dynamic not in {"conditional", "repeater_row_2"}
    ]
    body_controls = "".join(_render_control(item) for item in regular)
    required = [item.id for item in page.fields]
    repeater = next(
        (item.id for item in page.fields if item.control == "ensure_repeater"),
        "",
    )
    page_js = (
        f"const pageKey={json.dumps(page.key)};"
        f"const nextUrl={json.dumps(next_url)};"
        f"const requiredFieldIds={json.dumps(required)};"
        f"const repeaterFieldId={json.dumps(repeater)};"
        "const repeaterCount=()=>1;"
    )
    dynamic_script = ""

    if page.key == "personal1":
        branch = next(item for item in page.fields if item.dynamic == "branch")
        conditional = next(
            item for item in page.fields if item.dynamic == "conditional"
        )
        surname = page.fields[0]
        body_controls = "<div id='dynamic-host'></div>"
        dynamic_script = r"""
          const stateKey = '__docflowFullPersonal1';
          let state = parseStore(sessionStorage, stateKey,
            '{"surname":"","branch":"","conditional":""}');
          const keep = () => sessionStorage.setItem(stateKey, JSON.stringify(state));
          const render = () => {
            document.getElementById('dynamic-host').innerHTML = __CONTROLS__;
            const surname = document.querySelector('[data-test-field-id="__SURNAME__"]');
            surname.value = state.surname;
            surname.addEventListener('input', event => {
              state.surname = event.target.value; keep();
            });
            document.querySelectorAll('[data-test-field-id="__BRANCH__"]')
              .forEach(item => item.addEventListener('change', event => {
                if (!event.target.checked) return;
                state.branch = event.target.value;
                keep(); bump('generation:personal1'); render();
              }));
            const chosen = Array.from(document.querySelectorAll(
              '[data-test-field-id="__BRANCH__"]'
            )).find(item => item.value === state.branch);
            if (chosen) chosen.checked = true;
            const conditional = document.querySelector(
              '[data-test-field-id="__CONDITIONAL__"]'
            );
            if (conditional) {
              conditional.value = state.conditional;
              conditional.addEventListener('input', event => {
                state.conditional = event.target.value; keep();
              });
            }
            installButtons();
          };
          render();
        """
        dynamic_controls = (
            json.dumps(
                _render_control(surname)
                + _render_control(branch)
                + (
                    _render_control(conditional)
                    if False else "${state.branch === 'Y' ? "
                    + json.dumps(_render_control(conditional))
                    + " : ''}"
                )
                + "<div id='errors' class='errors'></div>"
                + "<div class='buttons'><button id='save' type='button'>"
                  "Save</button><button id='next' class='next' type='button'>"
                  "Next: Personal 2</button></div>"
            )
        )
        # JSON quoting cannot interpolate the conditional template.  Build a
        # JavaScript template literal whose fixture strings contain no backtick.
        dynamic_controls = (
            "`" + _render_control(surname) + _render_control(branch)
            + "${state.branch === 'Y' ? `"
            + _render_control(conditional)
            + "` : ''}<div id='errors' class='errors'></div>"
            + "<div class='buttons'><button id='save' type='button'>Save</button>"
            + "<button id='next' class='next' type='button'>"
              "Next: Personal 2</button></div>`"
        )
        dynamic_script = (
            dynamic_script
            .replace("__CONTROLS__", dynamic_controls)
            .replace("__SURNAME__", surname.id)
            .replace("__BRANCH__", branch.id)
            .replace("__CONDITIONAL__", conditional.id)
        )
    elif page.key == "travel_companions":
        row_one = next(
            item for item in page.fields if item.dynamic == "repeater_row_1"
        )
        row_two = next(
            item for item in page.fields if item.dynamic == "repeater_row_2"
        )
        repeater_item = next(
            item for item in page.fields if item.dynamic == "repeater"
        )
        body_controls = (
            "<div id='record-host'></div>"
            + _render_control(repeater_item)
        )
        page_js = (
            f"const pageKey={json.dumps(page.key)};"
            f"const nextUrl={json.dumps(next_url)};"
            f"const requiredFieldIds={json.dumps(required)};"
            f"const repeaterFieldId={json.dumps(repeater)};"
            "let rowCount=1; const repeaterCount=()=>rowCount;"
        )
        dynamic_script = r"""
          const rowState = ['', ''];
          const renderRows = () => {
            document.getElementById('record-host').innerHTML =
              __ROW_ONE__ + (rowCount > 1 ? __ROW_TWO__ : '');
            const first = document.querySelector('[data-test-field-id="__ID_ONE__"]');
            first.value = rowState[0];
            first.addEventListener('input', event => rowState[0] = event.target.value);
            const second = document.querySelector('[data-test-field-id="__ID_TWO__"]');
            if (second) {
              second.value = rowState[1];
              second.addEventListener('input', event => rowState[1] = event.target.value);
            }
          };
          renderRows();
          document.querySelector('.add-another').addEventListener('click', () => {
            if (rowCount < 2) {
              rowCount = 2; bump('generation:travel_companions'); renderRows();
            }
          });
          installButtons();
        """
        dynamic_script = (
            dynamic_script
            .replace("__ROW_ONE__", json.dumps(_render_control(row_one)))
            .replace("__ROW_TWO__", json.dumps(_render_control(row_two)))
            .replace("__ID_ONE__", row_one.id)
            .replace("__ID_TWO__", row_two.id)
        )
    else:
        dynamic_script = "installButtons();"

    if page.key not in {"personal1"}:
        body_controls += (
            "<div id='errors' class='errors'></div>"
            "<div class='buttons'>"
            "<button id='save' type='button'>Save</button>"
            f"<button id='next' class='next' type='button'>Next: "
            f"{html.escape(PAGE_BY_NODE.get(urlsplit(next_url).query, page).title if False else 'Continue')}"
            "</button></div>"
        )

    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{html.escape(page.title)}</title><style>{COMMON_CSS}</style>"
        "</head><body>"
        "<header><strong>Consular Electronic Application Center</strong></header>"
        "<nav>ONLINE NONIMMIGRANT VISA APPLICATION (DS-160) — COMPLETE</nav>"
        f"<main><h1>{html.escape(page.title)}</h1>"
        "<p class='synthetic'>SYNTHETIC DS-160 PRACTICE SITE — NO REAL "
        "APPLICANT DATA — NO CEAC NETWORK REQUEST</p>"
        "<form onsubmit='return false'>"
        "<input type='hidden' name='__VIEWSTATE' value='synthetic-viewstate'>"
        f"{body_controls}</form></main><script>{page_js}{COMMON_SCRIPT}"
        f"{dynamic_script}</script></body></html>"
    )


def _review_html():
    return f"""
<!doctype html><html><head><meta charset='utf-8'>
<title>Review Application</title><style>{COMMON_CSS}</style></head><body>
<header><strong>Consular Electronic Application Center</strong></header>
<nav>REVIEW — FINAL HUMAN BOUNDARY</nav><main>
<h1>Review Application</h1>
<p class='synthetic'>Synthetic application ready for final human review.</p>
<button id='sign' type='button'>Sign Application</button>
<button id='submit' type='button'>Submit Application</button>
</main><script>
const statsKey='__docflowFullLiveStats';
for (const id of ['sign','submit']) document.getElementById(id).onclick=()=>{{
  let stats={{}}; try {{ stats=JSON.parse(localStorage.getItem(statsKey)||'{{}}'); }}
  catch (_) {{}} stats.finalActionCount=Number(stats.finalActionCount||0)+1;
  localStorage.setItem(statsKey, JSON.stringify(stats));
}};
</script></body></html>
"""


def _next_url(index):
    return PAGES[index + 1].url if index + 1 < len(PAGES) else REVIEW_URL


HTML_BY_URL = {
    page.url: _page_html(page, _next_url(index))
    for index, page in enumerate(PAGES)
}
HTML_BY_URL[REVIEW_URL] = _review_html()


def route_full_synthetic_ceac(driver, network_log):
    """Fulfil every CEAC-shaped request locally; never continue one."""
    def fulfill(route):
        url = route.request.url
        body = HTML_BY_URL.get(url)
        network_log.append({"url": url, "fulfilled": body is not None})
        if body is None:
            route.abort()
            return
        route.fulfill(
            status=200,
            content_type="text/html; charset=utf-8",
            body=body,
        )

    driver._page.route("https://ceac.state.gov/**", fulfill)


def browser_provider(headed=False):
    return ProviderConfig(
        provider="playwright",
        model="chromium" if headed else "chromium-headless",
    )


class AcceptancePlaywrightDriver(PlaywrightBrowserDriver):
    """Production driver with value-free acceptance instrumentation."""
    def __init__(self, headed=False):
        super().__init__(browser_provider(headed=headed))
        self.executed_actions = []
        self.pointer_paths = []
        self.next_plans = []
        self.binding_results = []

    def bind_visual_field(self, action, labels=(), hints=()):
        result = super().bind_visual_field(action, labels, hints)
        self.binding_results.append({
            "field_id": action.field_id,
            "kind": action.kind.value,
            "bound": bool(result),
        })
        return result

    def execute(self, action):
        self.executed_actions.append({
            "id": action.id,
            "kind": action.kind.value,
            "field_id": action.field_id,
            "target": action.target_hint,
            "receipt_required": action.dispatch_receipt_required,
        })
        return super().execute(action)

    def plan_next(self):
        action = super().plan_next()
        if action is not None:
            self.next_plans.append({
                "id": action.id,
                "target": action.target_hint,
                "scope": action.dispatch_receipt_scope,
            })
        return action

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


class TimedLiveGemini(GeminiComputerUseAdapter):
    """Record only page IDs/counts/timing; never field values or API keys."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.page_batches = []

    def propose_actions(self, observation, available_field_ids,
                        completed_field_ids, page_field_ids=None):
        started = time.monotonic()
        node = parse_qs(urlsplit(observation.url).query).get("node", [""])[0]
        record = {
            "node": node,
            "page_id": observation.page_id,
            "pending_count": len(set(page_field_ids or ())
                                 - set(completed_field_ids)),
        }
        try:
            actions = super().propose_actions(
                observation,
                available_field_ids,
                completed_field_ids,
                page_field_ids,
            )
            record["action_count"] = len(actions)
            record["action_kinds"] = [action.kind.value for action in actions]
            return actions
        except Exception as error:
            record["error_type"] = type(error).__name__
            record["error"] = str(error)[:300]
            raise
        finally:
            record["elapsed_seconds"] = round(time.monotonic() - started, 3)
            self.page_batches.append(record)


class CountingAgentService(AgentService):
    """Distinguish one user start from service-owned watcher restarts."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.start_invocations = []
        self._start_invocations_lock = threading.Lock()

    def start_job(self, job_id, *args, **kwargs):
        with self._start_invocations_lock:
            self.start_invocations.append(threading.current_thread().name)
        return super().start_job(job_id, *args, **kwargs)

    @property
    def watcher_start_count(self):
        with self._start_invocations_lock:
            return sum(
                name.startswith("agent-auto-resume-")
                for name in self.start_invocations
            )


class FakeInteractionsTransport:
    """Value-free fake of the official page-batch function response."""
    def __init__(self, browser_flow=False):
        self.requests = []
        self.browser_flow = bool(browser_flow)
        self.page_calls = Counter()

    def json(self, method, url, payload, headers=None, timeout=None):
        self.requests.append({
            "method": method,
            "url": url,
            "headers": dict(headers or {}),
            "timeout": timeout,
        })
        prompt = next(
            block["text"]
            for block in payload.get("input") or []
            if block.get("type") == "text"
            and "Pending current-page field IDs:" in block.get("text", "")
        )
        matched = re.search(
            r"Pending current-page field IDs:\s*(\[[^\n]*\])", prompt
        )
        pending = json.loads(matched.group(1))
        url_match = re.search(r"Current URL:\s*(\S+)", prompt)
        current_url = url_match.group(1) if url_match else ""
        node = parse_qs(urlsplit(current_url).query).get("node", [""])[0]
        page_call = self.page_calls[node]
        self.page_calls[node] += 1
        if self.browser_flow and node == "WorkEducation3" and page_call == 0:
            return {
                "id": "fake-scroll",
                "steps": [{
                    "type": "function_call",
                    "name": "scroll",
                    "arguments": {
                        "direction": "down",
                        "magnitude_in_pixels": 800,
                        "x": 800,
                        "y": 800,
                        "intent": "Reveal the approved synthetic field",
                    },
                }],
            }
        if self.browser_flow and node == "Personal1" and page_call == 0:
            pending = [
                field_id for field_id in pending
                if FIELD_BY_ID[field_id].dynamic != "conditional"
            ]
        if self.browser_flow and node == "TravelCompanions" and page_call == 0:
            pending = [
                field_id for field_id in pending
                if FIELD_BY_ID[field_id].dynamic != "repeater_row_2"
            ]
        rows = []
        for index, field_id in enumerate(pending):
            item = FIELD_BY_ID[field_id]
            control_kind = (
                "click" if item.control == "ensure_repeater"
                else "select" if item.control in {
                    "select", "radio", "checkbox", "date"
                }
                else "type"
            )
            x, y = 250 + (index * 100), 200 + (index * 120)
            if item.control == "radio":
                # Personal 1's approved Yes control is deliberately first;
                # the descriptor's occurrence=1 independently proves it.
                x, y = 185, 395
            rows.append({
                "field_id": field_id,
                "control_kind": control_kind,
                "x": x,
                "y": y,
            })
        return {
            "id": "fake-full-fixture",
            "steps": [{
                "type": "function_call",
                "name": "fill_page_fields",
                "arguments": {"fields": rows},
            }],
        }


class FullLiveFixtureContractTests(unittest.TestCase):
    def test_fixture_covers_registry_routes_and_required_control_shapes(self):
        registry = PagePlanRegistry.default()
        expected_dynamic = {
            page_key
            for page_key, _path, _node
            in PagePlanRegistry.CEAC_DYNAMIC_PAGE_ROUTES
        }
        self.assertEqual(
            {page.key for page in PAGES if page.key != "photo"},
            expected_dynamic,
        )
        self.assertEqual(len(FIELD_BY_ID), len(ALL_FIELDS))
        self.assertTrue(all(1 <= len(page.fields) <= 3 for page in PAGES))
        self.assertEqual(
            {
                item.control for item in ALL_FIELDS
            }.intersection({
                "text", "textarea", "select", "radio", "checkbox",
                "date", "text_segments", "ensure_repeater",
            }),
            {
                "text", "textarea", "select", "radio", "checkbox",
                "date", "text_segments", "ensure_repeater",
            },
        )
        self.assertTrue(any(item.below_fold for item in ALL_FIELDS))
        self.assertTrue(any("occurrence=1" in item.label for item in ALL_FIELDS))
        self.assertTrue(any("occurrence=2" in item.label for item in ALL_FIELDS))
        self.assertTrue(any(item.dynamic == "branch" for item in ALL_FIELDS))

        for page in PAGES:
            observation = BrowserObservation(
                url=page.url,
                title=page.title,
                visible_text=page.title,
                form_control_count=2,
            )
            self.assertEqual(classify_ceac_page(observation).kind, "formal")
            plan = registry.match(observation)
            self.assertIsNotNone(plan, page.key)
            self.assertEqual(plan.id, page.plan_id)
            for item in page.fields:
                self.assertTrue(plan.allows_field(item.id), (page.key, item.id))
                self.assertIn(item.id, HTML_BY_URL[page.url])
                self.assertTrue(item.value)
        self.assertNotIn("AAoo", json.dumps(HTML_BY_URL))
        self.assertIn("NO REAL APPLICANT DATA", json.dumps(HTML_BY_URL))

    def test_fake_provider_parses_one_complete_batch_for_every_page(self):
        transport = FakeInteractionsTransport()
        adapter = GeminiComputerUseAdapter(
            ProviderConfig(
                provider="google",
                model="gemini-fixture-contract",
                api_base_url="https://fake.invalid",
                api_key="fake-contract-key",
            ),
            transport=transport,
        )
        screenshot = (
            "data:image/png;base64,"
            + base64.b64encode(
                base64.b64decode(
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC"
                    "AAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
                )
            ).decode("ascii")
        )
        for page in PAGES:
            page_ids = [item.id for item in page.fields]
            adapter.set_page_context({
                item.id: {"label": item.label} for item in page.fields
            })
            actions = adapter.propose_actions(
                BrowserObservation(
                    url=page.url,
                    title=page.title,
                    visible_text=page.title,
                    screenshot_ref=screenshot,
                    form_control_count=2,
                ),
                list(FIELD_BY_ID),
                [],
                page_ids,
            )
            self.assertEqual(
                [action.field_id for action in actions], page_ids, page.key
            )
            self.assertFalse(any(action.value for action in actions))
        self.assertEqual(len(transport.requests), len(PAGES))


def _curve_deviation(trace):
    start_x, start_y = trace["start"]
    end_x, end_y = trace["end"]
    delta_x, delta_y = end_x - start_x, end_y - start_y
    length = math.hypot(delta_x, delta_y)
    if length < 1:
        return 0.0
    return max(
        abs(delta_y * (x - start_x) - delta_x * (y - start_y)) / length
        for x, y in trace["points"]
    )


@unittest.skipUnless(
    os.environ.get("DOCFLOW_RUN_FULL_LIVE_GEMINI_E2E") == "1",
    "set DOCFLOW_RUN_FULL_LIVE_GEMINI_E2E=1 for the billable live acceptance",
)
class FullLiveGeminiPlaywrightAcceptanceTests(unittest.TestCase):
    def test_one_start_crosses_every_fixture_page_and_stops_at_review(self):
        configured = load_config()
        self.assertTrue(
            configured.computer_use.api_key,
            "The live acceptance requires a configured Gemini API key",
        )
        self.assertIn(
            configured.computer_use.provider.casefold(), {"google", "gemini"}
        )
        headed = os.environ.get("DOCFLOW_LIVE_E2E_HEADED") == "1"
        model_name = (
            os.environ.get("DOCFLOW_LIVE_GEMINI_MODEL")
            or configured.computer_use.model
            or "gemini-3.6-flash"
        )
        model = TimedLiveGemini(ProviderConfig(
            provider="google",
            model=model_name,
            api_base_url=OFFICIAL_GEMINI_BASE_URL,
            api_key=configured.computer_use.api_key,
        ))
        self.assertEqual(model.base_url, OFFICIAL_GEMINI_BASE_URL)

        fields = [
            {
                "id": item.id,
                "value": item.value,
                "label": item.label,
                "confidence": 1.0,
                "risk_level": "high",
            }
            for item in ALL_FIELDS
        ]
        required = [item["id"] for item in fields]
        service = None
        startup_errors = []
        ceac_network_log = []
        start_calls = 0
        resume_calls = 0

        with tempfile.TemporaryDirectory() as directory:
            def runtime_factory(job):
                driver = AcceptancePlaywrightDriver(headed=headed)
                driver.set_execution_mode("visual")
                try:
                    driver.start("about:blank")
                    route_full_synthetic_ceac(driver, ceac_network_log)
                    driver._page.goto(
                        job.start_url,
                        wait_until="domcontentloaded",
                        timeout=driver.NAVIGATION_TIMEOUT_MS,
                    )
                except Exception as error:
                    startup_errors.append(error)
                    driver.close()
                    raise ProviderNotConfigured(
                        "Playwright Chromium is unavailable for full live E2E"
                    ) from error
                return ComputerUseAgent(
                    model,
                    driver,
                    max_steps=180,
                    execution_mode="visual",
                )

            service = AgentService(
                AgentConfig(data_dir=Path(directory) / "checkpoints"),
                runtime_factory=runtime_factory,
            )
            try:
                created = service.create_job({
                    "startUrl": PAGES[0].url,
                    "requiredFieldIds": required,
                    "fields": fields,
                    "autoNext": True,
                })
                reviewed = service.review_job(created["id"], {
                    "actor": "full-live-gemini-synthetic-acceptance",
                    "decisions": [
                        {
                            "fieldId": item.id,
                            "approved": True,
                            "value": item.value,
                        }
                        for item in ALL_FIELDS
                    ],
                })
                try:
                    start_calls += 1
                    started = time.monotonic()
                    result = service.start_job(reviewed["id"])
                except ServiceError:
                    if startup_errors:
                        self.skipTest(
                            f"Playwright/Chromium unavailable: {startup_errors[-1]}"
                        )
                    raise

                # A single live Gemini page batch can consume its bounded
                # 22s + 8s provider budget.  That is a durable automatic-retry
                # checkpoint, not a request for a second user click.  Observe
                # status exactly as the frontend poller does and let the
                # service-owned watcher call its internal start path.  Never
                # call resume_job or a second external start_job here.
                poll_trace = []
                deadline = started + 8 * 60
                while result.get("state") != "review_required":
                    poll_trace.append({
                        "state": result.get("state"),
                        "wait_kind": result.get("wait_kind"),
                        "automatic_retry_pending": bool(
                            result.get("automatic_retry_pending")
                        ),
                        "watcher_armed": bool(
                            result.get("auto_resume_watcher_armed")
                        ),
                    })
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(0.5)
                    result = service.get_job(reviewed["id"])
                run_seconds = time.monotonic() - started

                with service._runtime_lock:
                    worker = service._runtimes[reviewed["id"]]

                def inspect(runtime):
                    browser = runtime.browser
                    page = browser._page
                    state = page.evaluate("""() => {
                      const parse=(storage,key,fallback)=>{
                        try{return JSON.parse(storage.getItem(key)||fallback);}
                        catch(_){return JSON.parse(fallback);}
                      };
                      const cursor=document.getElementById(
                        'docflow-agent-visible-cursor');
                      const cursorStyle=cursor?getComputedStyle(cursor):null;
                      const status=document.getElementById(
                        'docflow-agent-visual-status');
                      return {
                        stats:parse(localStorage,'__docflowFullLiveStats','{}'),
                        saved:parse(localStorage,'__docflowFullLiveSaved','{}'),
                        moves:parse(sessionStorage,'__docflowFullLiveMoves','[]'),
                        cursor:{present:Boolean(cursor),
                          display:cursorStyle?.display||'',
                          visibility:cursorStyle?.visibility||'',
                          width:cursor?.getBoundingClientRect().width||0},
                        status:{present:Boolean(status),
                          state:status?.dataset.state||''}
                      };
                    }""")
                    return {
                        "url": page.url,
                        "state": state,
                        "executed": list(browser.executed_actions),
                        "pointer_paths": list(browser.pointer_paths),
                        "next_plans": list(browser.next_plans),
                        "bindings": list(browser.binding_results),
                    }

                snapshot = worker.call(inspect, timeout=20)
                diagnostic = {
                    "result_state": result.get("state"),
                    "wait_kind": result.get("wait_kind"),
                    "last_error": result.get("last_error"),
                    "completed_count": len(result.get("completed_field_ids") or []),
                    "required_count": len(required),
                    "run_seconds": round(run_seconds, 3),
                    "interaction_count": model.interaction_count,
                    "request_count": model.request_count,
                    "page_batches": model.page_batches,
                    "poll_trace": poll_trace[-40:],
                    "event_tail": [
                        {
                            "kind": event.get("kind"),
                            "message": event.get("message"),
                        }
                        for event in result.get("events", [])[-20:]
                    ],
                }
                self.assertEqual(start_calls, 1)
                self.assertEqual(resume_calls, 0)
                self.assertLessEqual(run_seconds, 8 * 60, diagnostic)
                self.assertEqual(result["state"], "review_required", diagnostic)
                self.assertTrue(result["final_submission_boundary_reached"])
                self.assertEqual(set(result["completed_field_ids"]), set(required))
                self.assertEqual(snapshot["url"], REVIEW_URL)

                retry_observed = any(
                    sample["automatic_retry_pending"]
                    or sample["wait_kind"] == "automatic_retry"
                    for sample in poll_trace
                )
                if retry_observed:
                    self.assertTrue(
                        any(sample["watcher_armed"] for sample in poll_trace),
                        diagnostic,
                    )
                    event_kinds = {
                        event.get("kind") for event in result.get("events", [])
                    }
                    self.assertIn("automatic_retry_scheduled", event_kinds)
                    self.assertIn("automatic_retry_cleared", event_kinds)
                self.assertFalse(result.get("automatic_retry_pending"))

                executed = snapshot["executed"]
                field_counts = Counter(
                    action["field_id"] for action in executed
                    if action["field_id"]
                )
                self.assertEqual(
                    field_counts,
                    Counter({field_id: 1 for field_id in required}),
                    diagnostic,
                )
                next_actions = [
                    action for action in executed
                    if action["kind"] == ActionKind.CLICK.value
                    and action["target"].casefold().startswith("next")
                ]
                self.assertEqual(len(next_actions), len(PAGES), diagnostic)
                self.assertTrue(all(
                    action["receipt_required"] for action in next_actions
                ))
                self.assertEqual(len(snapshot["next_plans"]), len(PAGES))
                self.assertFalse(any(
                    "sign" in action["target"].casefold()
                    or "submit" in action["target"].casefold()
                    for action in executed
                ))

                stats = snapshot["state"]["stats"]
                saved = snapshot["state"]["saved"]
                for page in PAGES:
                    self.assertEqual(stats.get("next:" + page.key), 1, stats)
                    self.assertEqual(stats.get("save:" + page.key), 1, stats)
                    self.assertEqual(
                        set(saved.get(page.key, {})),
                        {item.id for item in page.fields},
                        (page.key, saved.get(page.key)),
                    )
                self.assertEqual(stats.get("manualSave", 0), 0, stats)
                self.assertEqual(stats.get("finalActionCount", 0), 0, stats)
                self.assertEqual(
                    sum(value for key, value in stats.items()
                        if key.startswith("validationBlocked:")),
                    0,
                    stats,
                )

                scroll_actions = sum(
                    action["kind"] == ActionKind.SCROLL.value
                    for action in executed
                )
                generation_events = sum(
                    value for key, value in stats.items()
                    if key.startswith("generation:")
                )
                self.assertGreaterEqual(scroll_actions, 1, diagnostic)
                self.assertEqual(generation_events, 2, stats)
                self.assertLessEqual(
                    model.interaction_count,
                    len(PAGES) + generation_events + scroll_actions,
                    diagnostic,
                )
                self.assertTrue(all(
                    batch["elapsed_seconds"]
                    <= model.PLANNING_TOTAL_BUDGET_SECONDS + 2
                    for batch in model.page_batches
                ), diagnostic)

                self.assertTrue(all(
                    binding["bound"] for binding in snapshot["bindings"]
                ), snapshot["bindings"])
                paths = snapshot["pointer_paths"]
                self.assertGreaterEqual(len(paths), len(executed))
                self.assertTrue(any(
                    len(trace["points"]) >= 3
                    and _curve_deviation(trace) > 2.0
                    for trace in paths
                ))
                cursor = snapshot["state"]["cursor"]
                self.assertTrue(cursor["present"])
                self.assertNotEqual(cursor["display"], "none")
                self.assertNotEqual(cursor["visibility"], "hidden")
                self.assertGreater(cursor["width"], 0)
                self.assertTrue(snapshot["state"]["status"]["present"])
                self.assertGreaterEqual(len(snapshot["state"]["moves"]), 80)

                self.assertTrue(ceac_network_log)
                self.assertTrue(all(
                    entry["fulfilled"] for entry in ceac_network_log
                ), ceac_network_log)
                self.assertEqual(
                    set(entry["url"] for entry in ceac_network_log),
                    set(HTML_BY_URL),
                )

                artifact_dir = os.environ.get(
                    "DOCFLOW_LIVE_E2E_ARTIFACT_DIR", ""
                ).strip()
                if not artifact_dir and headed:
                    artifact_dir = str(
                        Path(tempfile.gettempdir())
                        / ("docflow-full-live-e2e-"
                           + time.strftime("%Y%m%d-%H%M%S"))
                    )
                if artifact_dir:
                    output = Path(artifact_dir).expanduser().resolve()
                    output.mkdir(parents=True, exist_ok=True)
                    screenshot_path = output / "final-review.png"
                    worker.call(
                        lambda runtime: runtime.browser._page.screenshot(
                            path=str(screenshot_path), full_page=True
                        ),
                        timeout=20,
                    )
                    telemetry = {
                        **diagnostic,
                        "final_url": snapshot["url"],
                        "page_count": len(PAGES),
                        "field_count": len(required),
                        "next_count": len(next_actions),
                        "scroll_count": scroll_actions,
                        "generation_count": generation_events,
                        "headed": headed,
                        "screenshot": str(screenshot_path),
                    }
                    (output / "telemetry.json").write_text(
                        json.dumps(telemetry, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    print(f"DOCFLOW_FULL_LIVE_E2E_ARTIFACTS={output}")

                hold = 0.0
                try:
                    hold = float(os.environ.get(
                        "DOCFLOW_LIVE_E2E_HOLD_SECONDS", "0"
                    ))
                except ValueError:
                    hold = 0.0
                if headed and hold > 0:
                    worker.call(
                        lambda runtime: runtime.browser._page.wait_for_timeout(
                            int(min(15.0, max(0.0, hold)) * 1000)
                        ),
                        timeout=20,
                    )
                if os.environ.get("DOCFLOW_LIVE_E2E_REPORT") == "1":
                    print(
                        "DOCFLOW_FULL_LIVE_GEMINI_E2E="
                        + json.dumps(diagnostic, sort_keys=True)
                    )
            finally:
                if service is not None:
                    service.shutdown(timeout=20)


if __name__ == "__main__":
    unittest.main()
