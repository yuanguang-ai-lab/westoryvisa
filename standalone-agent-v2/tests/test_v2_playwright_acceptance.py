import sys
import tempfile
import time
import unittest
from pathlib import Path

from visa_agent.config import AgentConfig, ProviderConfig
from visa_agent.models import ActionKind, ComputerAction
from visa_agent.providers import ProviderNotConfigured
from visa_agent.service import AgentService, ServiceError

from visa_agent_v2.browser import (
    ControlPostbackTimeout,
    FastVisiblePlaywrightBrowser,
)
from visa_agent_v2.native_input import (
    MacOSNativeInput,
    NativeInputUnavailable,
    browser_scoped_input_readiness,
)
from visa_agent_v2.workflow import FastComputerUseAgent


LEGACY_TESTS = (
    Path(__file__).resolve().parents[2]
    / "standalone-agent"
    / "tests"
)
sys.path.insert(0, str(LEGACY_TESTS))
from test_playwright_service_continuous_e2e import (  # noqa: E402
    BRANCH,
    CONDITIONAL,
    GIVEN_NAMES,
    NATIONAL_ID,
    NATIONALITY,
    PAGE_1_URL,
    REVIEW_URL,
    SURNAME,
    browser_provider,
    route_synthetic_ceac,
)


class ModelMustNotRun:
    def __init__(self):
        self.calls = 0

    def propose_action(self, *_args):
        self.calls += 1
        raise AssertionError("V2 semantic controls must not call Gemini")

    def propose_actions(self, *_args):
        self.calls += 1
        raise AssertionError("V2 semantic controls must not call Gemini")


class RecordingFastBrowser(FastVisiblePlaywrightBrowser):
    def __init__(self):
        # This fixture remains visible while every input stays page-scoped.
        super().__init__(ProviderConfig(
            provider="playwright",
            model="chromium-headless",
        ))
        self.executed_actions = []

    def execute(self, action):
        self.executed_actions.append({
            "kind": action.kind.value,
            "field_id": action.field_id,
            "target": action.target_hint,
        })
        return super().execute(action)


def field(field_id, value, label):
    return {
        "id": field_id,
        "value": value,
        "label": label,
        "confidence": 1.0,
        "risk_level": "high",
    }


class V2PlaywrightAcceptanceTests(unittest.TestCase):
    def test_v2_select_backend_is_page_scoped_and_global_input_disabled(self):
        browser = FastVisiblePlaywrightBrowser(browser_provider())

        class FakeLocator:
            def __init__(self):
                self.calls = 0

            def evaluate(self, script, desired, **_kwargs):
                self.calls += 1
                self.assert_script = script
                return {
                    "value": desired["value"],
                    "text": desired["text"],
                }

        locator = FakeLocator()
        selected = browser._activate_select_option(
            locator,
            {"value": "B", "text": "BUSINESS (B)"},
        )
        readiness = browser_scoped_input_readiness()

        self.assertTrue(selected)
        self.assertEqual(locator.calls, 1)
        self.assertFalse(hasattr(browser, "_native_input"))
        self.assertEqual(readiness["backend"], "playwright-scoped")
        self.assertTrue(readiness["globalInputDisabled"])
        with self.assertRaises(NativeInputUnavailable):
            MacOSNativeInput()

    def test_live_travel_select_resolves_one_page_scoped_option(self):
        class RecordingScopedBrowser(FastVisiblePlaywrightBrowser):
            def __init__(self):
                super().__init__(browser_provider())
                self.scoped_options = []

            def _activate_select_option(self, _locator, selected):
                self.scoped_options.append(selected)
                return True

        browser = RecordingScopedBrowser()
        self.assertFalse(hasattr(browser, "_native_input"))

        class FakeLocator:
            def evaluate(self, script, **_kwargs):
                if "options: Array.from" in script:
                    return {
                        "tag": "select",
                        "options": [
                            {"value": "", "text": "SELECT ONE"},
                            {"value": "A", "text": "OFFICIAL (A)"},
                            {
                                "value": "B",
                                "text": (
                                    "TEMP. BUSINESS OR PLEASURE VISITOR (B)"
                                ),
                            },
                        ],
                    }
                raise AssertionError("unexpected DOM read")

        locator = FakeLocator()

        selected = browser._select_native_ceac_option(
            locator,
            "TEMP. BUSINESS OR PLEASURE VISITOR (B)",
        )

        self.assertTrue(selected)
        self.assertEqual(len(browser.scoped_options), 1)
        self.assertEqual(browser.scoped_options[0]["value"], "B")

    def test_headless_page_scoped_travel_commit_emits_change(self):
        """The page-scoped change event must create the child select."""
        browser = FastVisiblePlaywrightBrowser(ProviderConfig(
            provider="playwright",
            model="chromium-headless",
        ))
        try:
            try:
                browser.start("about:blank")
            except Exception as error:
                self.skipTest(
                    "Headed Playwright/Chromium unavailable: "
                    f"{error}"
                )
            browser._page.set_content("""
                <title>DocFlow Page-scoped Select Fixture</title>
                <style>
                  select { width: 480px; height: 36px; margin: 80px; }
                </style>
                <label for="primary">Purpose of Trip to the U.S.</label>
                <select id="primary" name="ctl00$PurposeOfTrip">
                  <option value="">PLEASE SELECT A VISA CLASS</option>
                  <option value="A">OFFICIAL (A)</option>
                  <option value="C">TRANSIT (C)</option>
                  <option value="E">TREATY TRADER OR INVESTOR (E)</option>
                  <option value="H">TEMPORARY WORKER (H)</option>
                  <option value="B">
                    TEMP. BUSINESS OR PLEASURE VISITOR (B)
                  </option>
                </select>
                <div id="secondary-slot"></div>
                <script>
                  window.postbackCount = 0;
                  window.lastChangeTrusted = null;
                  window.__doPostBack = target => {
                      if (target !== 'ctl00$PurposeOfTrip') return;
                      window.postbackCount += 1;
                      if (document.querySelector('#primary').value !== 'B') {
                        return;
                      }
                      document.querySelector('#secondary-slot').innerHTML = `
                        <label for="secondary">Specify visa class</label>
                        <select id="secondary">
                          <option value="">SELECT ONE</option>
                          <option value="B1B2">B1/B2</option>
                        </select>`;
                  };
                  document.querySelector('#primary').addEventListener(
                    'change',
                    event => {
                      window.lastChangeTrusted = event.isTrusted;
                      window.__doPostBack('ctl00$PurposeOfTrip', '');
                    }
                  );
                </script>
            """)

            selected = browser._select_native_ceac_option(
                browser._page.locator("#primary"),
                "TEMP. BUSINESS OR PLEASURE VISITOR (B)",
            )

            self.assertTrue(selected)
            browser._page.locator("#secondary").wait_for(
                state="visible",
                timeout=3000,
            )
            self.assertEqual(
                browser._page.locator("#primary").input_value(),
                "B",
            )
            self.assertEqual(
                browser._page.evaluate("window.postbackCount"),
                1,
            )
            self.assertIs(
                browser._page.evaluate("window.lastChangeTrusted"),
                False,
            )
        finally:
            browser.close()

    def test_travel_primary_waits_for_async_postback_before_rebinding(self):
        """An UpdatePanel may remove primary long before its response ends."""
        browser = RecordingFastBrowser()
        travel_url = (
            "https://ceac.state.gov/GenNIV/General/complete/"
            "complete_travel.aspx?node=Travel"
        )
        try:
            try:
                browser.start("about:blank")
            except Exception as error:
                self.skipTest(
                    "Playwright/Chromium unavailable: "
                    f"{error}"
                )
            browser._page.route(
                travel_url,
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/html",
                    body="""
                    <style>select { width: 480px; height: 34px; }</style>
                    <div id="purpose-slot">
                      <label for="primary">Purpose of Trip to the U.S.</label>
                      <select id="primary" name="ctl00$PurposeOfTrip">
                        <option value="">PLEASE SELECT A VISA CLASS</option>
                        <option value="B">
                          TEMP. BUSINESS OR PLEASURE VISITOR (B)
                        </option>
                      </select>
                    </div>
                    <div id="secondary-slot"></div>
                    <script>
                      window.postbackCount = 0;
                      window.beginHandlers = [];
                      window.endHandlers = [];
                      const manager = {
                        add_beginRequest: callback =>
                          window.beginHandlers.push(callback),
                        add_endRequest: callback =>
                          window.endHandlers.push(callback)
                      };
                      window.Sys = {WebForms: {PageRequestManager: {
                        getInstance: () => manager
                      }}};
                      window.bindPrimary = () => {
                        document.querySelector('#primary').addEventListener(
                          'change',
                          () => window.__doPostBack(
                            'ctl00$PurposeOfTrip', ''
                          )
                        );
                      };
                      window.renderPrimary = value => {
                        document.querySelector('#purpose-slot').innerHTML = `
                          <label for="primary">
                            Purpose of Trip to the U.S.
                          </label>
                          <select id="primary"
                            name="ctl00$PurposeOfTrip">
                            <option value="">
                              PLEASE SELECT A VISA CLASS
                            </option>
                            <option value="B">
                              TEMP. BUSINESS OR PLEASURE VISITOR (B)
                            </option>
                          </select>`;
                        document.querySelector('#primary').value = value;
                        window.bindPrimary();
                      };
                      window.__doPostBack = () => {
                        window.postbackCount += 1;
                        const value = document.querySelector('#primary').value;
                        for (const callback of window.beginHandlers) callback();
                        document.querySelector('#purpose-slot').innerHTML = '';
                        window.setTimeout(() => {
                          window.renderPrimary(value);
                          if (value === 'B') {
                            document.querySelector(
                              '#secondary-slot'
                            ).innerHTML = `
                              <label for="secondary">Specify</label>
                              <select id="secondary"
                                name="ctl00$OtherPurpose">
                                <option value="">PLEASE SELECT</option>
                                <option value="B1B2">B1/B2</option>
                              </select>`;
                          }
                          for (const callback of window.endHandlers) callback();
                        }, 900);
                      };
                      window.bindPrimary();
                    </script>
                    """,
                ),
            )
            browser._page.goto(travel_url, wait_until="domcontentloaded")
            primary = "ceac.travel.travel.purpose.primary"
            secondary = "ceac.travel.travel.purpose.secondary"
            labels = {
                primary: (
                    "Purpose of Trip to the U.S. [control=select_text; "
                    "human-approved value=TEMP. BUSINESS OR PLEASURE "
                    "VISITOR (B)]",
                ),
                secondary: (
                    "Specify visa class [control=select_text; "
                    "human-approved value=B1/B2]",
                ),
            }
            actions, _unresolved = browser.plan_fields(
                [primary, secondary], labels, {},
            )
            self.assertEqual([item.field_id for item in actions], [primary])
            actions[0].value = "TEMP. BUSINESS OR PLEASURE VISITOR (B)"

            started = time.monotonic()
            browser.execute(actions[0])
            elapsed = time.monotonic() - started

            self.assertGreaterEqual(elapsed, 0.85)
            self.assertEqual(browser._page.evaluate("window.postbackCount"), 1)
            self.assertEqual(browser._page.locator("#primary").input_value(), "B")
            self.assertTrue(browser._page.locator("#secondary").is_visible())
            self.assertIn(actions[0].id, browser._acknowledged)
        finally:
            browser.close()

    def test_travel_primary_repairs_missing_native_change_postback(self):
        """A trusted value change may need its exact WebForms target replayed."""
        browser = RecordingFastBrowser()
        travel_url = (
            "https://ceac.state.gov/GenNIV/General/complete/"
            "complete_travel.aspx?node=Travel"
        )
        try:
            try:
                browser.start("about:blank")
            except Exception as error:
                self.skipTest(
                    "Playwright/Chromium unavailable: "
                    f"{error}"
                )
            browser._page.route(
                travel_url,
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/html",
                    body="""
                <style>select { width: 480px; height: 34px; }</style>
                <label for="primary">Purpose of Trip to the U.S.</label>
                <select id="primary" name="ctl00$PurposeOfTrip">
                  <option value="">PLEASE SELECT A VISA CLASS</option>
                  <option value="B">
                    TEMP. BUSINESS OR PLEASURE VISITOR (B)
                  </option>
                </select>
                <div id="secondary-slot"></div>
                <script>
                  window.nativeChangeCount = 0;
                  window.nativeChangeTrusted = null;
                  window.replayedPostbackCount = 0;
                  window.__doPostBack = target => {
                    if (target !== 'ctl00$PurposeOfTrip') return;
                    window.replayedPostbackCount += 1;
                    document.querySelector('#secondary-slot').innerHTML = `
                      <label for="secondary">Specify</label>
                      <select id="secondary" name="ctl00$OtherPurpose">
                        <option value="">PLEASE SELECT</option>
                        <option value="B1B2">B1/B2</option>
                      </select>`;
                  };
                  document.querySelector('#primary').addEventListener(
                    'change',
                    event => {
                      window.nativeChangeCount += 1;
                      window.nativeChangeTrusted = event.isTrusted;
                      // Reproduce production's failed callback: the real
                      // change updates the select but starts no postback.
                    }
                  );
                </script>
                    """,
                ),
            )
            browser._page.goto(travel_url, wait_until="domcontentloaded")
            primary = "ceac.travel.travel.purpose.primary"
            labels = {
                primary: (
                    "Purpose of Trip to the U.S. [control=select_text; "
                    "human-approved value=TEMP. BUSINESS OR PLEASURE "
                    "VISITOR (B)]",
                ),
            }
            actions, unresolved = browser.plan_fields(
                [primary], labels, {},
            )
            self.assertFalse(unresolved)
            self.assertEqual([item.field_id for item in actions], [primary])
            actions[0].value = "TEMP. BUSINESS OR PLEASURE VISITOR (B)"

            browser.execute(actions[0])

            self.assertEqual(
                browser._page.evaluate("window.nativeChangeCount"),
                1,
            )
            self.assertIs(
                browser._page.evaluate("window.nativeChangeTrusted"),
                False,
            )
            self.assertEqual(
                browser._page.evaluate("window.replayedPostbackCount"),
                1,
            )
            self.assertTrue(browser._page.locator("#secondary").is_visible())
            self.assertEqual(
                browser._last_control_postback_diagnostic.get("result"),
                "control-do-postback-received",
            )
            self.assertIn(actions[0].id, browser._acknowledged)
        finally:
            browser.close()

    def test_travel_primary_forces_exact_form_post_when_callback_is_noop(self):
        """A no-op WebForms callback is upgraded to one same-origin POST."""
        browser = RecordingFastBrowser()
        travel_url = (
            "https://ceac.state.gov/GenNIV/General/complete/"
            "complete_travel.aspx?node=Travel"
        )
        post_count = {"value": 0}

        def route_travel(route):
            is_post = route.request.method.upper() == "POST"
            if is_post:
                post_count["value"] += 1
            selected = " selected" if is_post else ""
            secondary = """
                <label for="secondary">Specify</label>
                <select id="secondary" name="ctl00$OtherPurpose">
                  <option value="">PLEASE SELECT</option>
                  <option value="B1B2">B1/B2</option>
                </select>
            """ if is_post else ""
            route.fulfill(
                status=200,
                content_type="text/html",
                body=f"""
                <form method="post" action="{travel_url}">
                  <input type="hidden" name="__EVENTTARGET" value="">
                  <input type="hidden" name="__EVENTARGUMENT" value="">
                  <label for="primary">Purpose of Trip to the U.S.</label>
                  <select id="primary" name="ctl00$PurposeOfTrip">
                    <option value="">PLEASE SELECT A VISA CLASS</option>
                    <option value="B"{selected}>
                      TEMP. BUSINESS OR PLEASURE VISITOR (B)
                    </option>
                  </select>
                  <div id="secondary-slot">{secondary}</div>
                </form>
                <script>
                  window.__doPostBack = () => {{
                    // Reproduce a callback that returns without traffic.
                  }};
                </script>
                """,
            )

        try:
            try:
                browser.start("about:blank")
            except Exception as error:
                self.skipTest(
                    "Playwright/Chromium unavailable: "
                    f"{error}"
                )
            browser._page.route(travel_url, route_travel)
            browser._page.goto(travel_url, wait_until="domcontentloaded")
            primary = "ceac.travel.travel.purpose.primary"
            labels = {
                primary: (
                    "Purpose of Trip to the U.S. [control=select_text; "
                    "human-approved value=TEMP. BUSINESS OR PLEASURE "
                    "VISITOR (B)]",
                ),
            }
            actions, unresolved = browser.plan_fields(
                [primary], labels, {},
            )
            self.assertFalse(unresolved)
            actions[0].value = "TEMP. BUSINESS OR PLEASURE VISITOR (B)"

            browser.execute(actions[0])

            self.assertEqual(post_count["value"], 1)
            self.assertTrue(browser._page.locator("#secondary").is_visible())
            self.assertEqual(
                browser._last_control_postback_diagnostic.get("result"),
                "native-form-post-received",
            )
            self.assertIn(actions[0].id, browser._acknowledged)
        finally:
            browser.close()

    def test_passport_required_city_and_dates_bind_without_visual_coordinates(self):
        browser = RecordingFastBrowser()
        # This is the physical filename currently returned by production
        # CEAC.  Older tests covered only complete_pptvisa.aspx, which let a
        # real-route regression escape while node=PptVisa still selected the
        # high-level page plan.
        passport_url = (
            "https://ceac.state.gov/GenNIV/General/complete/"
            "Passport_Visa_Info.aspx?node=PptVisa"
        )
        try:
            try:
                browser.start("about:blank")
            except Exception as error:
                self.skipTest(
                    "Playwright/Chromium unavailable: "
                    f"{error}"
                )
            browser._page.route(
                passport_url,
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/html",
                    body="""
                    <style>
                      label, span { display: block; }
                      input, select { display: inline-block; }
                    </style>
                    <form>
                      <label for="authority">
                        Country/Authority that Issued Passport/Travel Document
                      </label>
                      <select id="authority"><option>CHINA</option></select>
                      <h4>Where was the Passport/Travel Document Issued?</h4>
                      <label for="city">City</label>
                      <input id="city" value="HANGZHOU">
                      <label for="region">
                        State/Province *If shown on passport
                      </label>
                      <input id="region" value="ZHEJIANG">
                      <label for="country">Country/Region</label>
                      <select id="country"><option>CHINA</option></select>
                      <div>
                        <span>Issuance Date</span>
                        <select id="issue-day"><option>12</option></select>
                        <select id="issue-month"><option>JUL</option></select>
                        <input id="issue-year" value="2024">
                      </div>
                      <div>
                        <span>Expiration Date</span>
                        <select id="expire-day"><option>11</option></select>
                        <select id="expire-month"><option>JUL</option></select>
                        <input id="expire-year" value="2034">
                      </div>
                    </form>
                    """,
                ),
            )
            browser._page.goto(passport_url, wait_until="domcontentloaded")
            specs = {
                "ceac.passport.passport.issuingauthority": (
                    "select_text", "CHINA", "authority",
                ),
                "ceac.passport.passport.issuecity": (
                    "text", "HANGZHOU", "city",
                ),
                "ceac.passport.passport.issueregion": (
                    "text", "ZHEJIANG", "region",
                ),
                "ceac.passport.passport.issuecountry": (
                    "select_text", "CHINA", "country",
                ),
                "ceac.passport.passport.issuedate": (
                    "date", "2024-07-12", "issue-day",
                ),
                "ceac.passport.passport.expiration": (
                    "date", "2034-07-11", "expire-day",
                ),
            }
            labels = {
                field_id: (
                    f"fixture [control={kind}; human-approved value={value}]",
                )
                for field_id, (kind, value, _target) in specs.items()
            }
            actions, unresolved = browser._plan_semantic_fields_once(
                list(specs), labels, {}
            )

            self.assertEqual(unresolved, [])
            self.assertEqual(
                {action.field_id for action in actions}, set(specs)
            )
            for field_id, (_kind, _value, target_id) in specs.items():
                selector = browser._field_selectors[field_id]
                self.assertEqual(
                    browser._page.locator(selector).get_attribute("id"),
                    target_id,
                )

            # Planning alone is not enough: execute every deterministic
            # binding and prove the final live composite values.  No Gemini
            # coordinate is involved anywhere in this route.
            for action in actions:
                action.value = specs[action.field_id][1]
                browser.execute(action)
            self.assertEqual(
                browser._page.locator("#authority").input_value(), "CHINA"
            )
            self.assertEqual(
                browser._page.locator("#city").input_value(), "HANGZHOU"
            )
            self.assertEqual(
                browser._page.locator("#issue-day").input_value(), "12"
            )
            self.assertEqual(
                browser._page.locator("#issue-month").input_value(), "JUL"
            )
            self.assertEqual(
                browser._page.locator("#issue-year").input_value(), "2024"
            )
        finally:
            browser.close()

    def test_unclassed_production_ceac_error_box_is_detected(self):
        browser = RecordingFastBrowser()
        page_url = (
            "https://ceac.state.gov/GenNIV/General/complete/"
            "Passport_Visa_Info.aspx?node=PptVisa"
        )
        try:
            try:
                browser.start("about:blank")
            except Exception as error:
                self.skipTest(
                    "Playwright/Chromium unavailable: "
                    f"{error}"
                )
            browser._page.route(
                page_url,
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/html",
                    body="""
                    <div style="border: 1px solid red; color: red">
                      Please correct all areas in error as indicated below.
                      <ul>
                        <li>City has not been completed.</li>
                        <li>Issuance Date is invalid. Month, Day, and Year are required.</li>
                      </ul>
                    </div>
                    <form><input id="city"></form>
                    """,
                ),
            )
            browser._page.goto(page_url, wait_until="domcontentloaded")

            errors = browser._validation_errors()

            self.assertTrue(any(
                "Please correct all areas" in item for item in errors
            ))
            self.assertTrue(any(
                "City has not been completed" in item for item in errors
            ))
            self.assertTrue(any(
                "Issuance Date is invalid" in item for item in errors
            ))
        finally:
            browser.close()

    def test_family_parent_panels_are_present_filled_and_read_back(self):
        browser = RecordingFastBrowser()
        page_url = (
            "https://ceac.state.gov/GenNIV/General/complete/"
            "complete_family1.aspx?node=Relatives"
        )
        try:
            try:
                browser.start("about:blank")
            except Exception as error:
                self.skipTest(
                    "Playwright/Chromium unavailable: "
                    f"{error}"
                )
            browser._page.route(
                page_url,
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/html",
                    body="""
                    <style>
                      h3, label, span { display: block; }
                      input, select { display: inline-block; }
                      section { margin: 24px 0; }
                    </style>
                    <form>
                      <section>
                        <h3>Father's Full Name and Date of Birth</h3>
                        <label for="father-surname">Surnames</label>
                        <input id="father-surname">
                        <label for="father-given">Given Names</label>
                        <input id="father-given">
                        <div>
                          <span>Date of Birth</span>
                          <select id="father-day"></select>
                          <select id="father-month"><option value="MAY">MAY</option></select>
                          <input id="father-year">
                        </div>
                        <div class="question">
                          <span>Is your father in the U.S.?</span>
                          <input type="radio" name="FATHER_US" id="father-yes" value="Yes">
                          <label for="father-yes">Yes</label>
                          <input type="radio" name="FATHER_US" id="father-no" value="No">
                          <label for="father-no">No</label>
                        </div>
                      </section>
                      <section>
                        <h3>Mother's Full Name and Date of Birth</h3>
                        <label for="mother-surname">Surnames</label>
                        <input id="mother-surname">
                        <label for="mother-given">Given Names</label>
                        <input id="mother-given">
                        <div>
                          <span>Date of Birth</span>
                          <select id="mother-day"></select>
                          <select id="mother-month"><option value="AUG">AUG</option></select>
                          <input id="mother-year">
                        </div>
                        <div class="question">
                          <span>Is your mother in the U.S.?</span>
                          <input type="radio" name="MOTHER_US" id="mother-yes" value="Yes">
                          <label for="mother-yes">Yes</label>
                          <input type="radio" name="MOTHER_US" id="mother-no" value="No">
                          <label for="mother-no">No</label>
                        </div>
                      </section>
                      <section class="question">
                        <span>
                          Do you have any immediate relatives, not including
                          parents, in the United States?
                        </span>
                        <!-- Production uses an OTHER_RELATIVE-like control
                             identity for this visible immediate-relative
                             prompt.  The binder must trust the prompt, not
                             alias this group to the separate reviewed field. -->
                        <input type="radio" name="OTHER_RELATIVE" id="relative-yes" value="Yes">
                        <label for="relative-yes">Yes</label>
                        <input type="radio" name="OTHER_RELATIVE" id="relative-no" value="No">
                        <label for="relative-no">No</label>
                      </section>
                      <section class="question" id="other-relative-question" style="display:none">
                        <span>
                          Do you have any other relatives in the United States?
                        </span>
                        <input type="radio" name="OTHER_RELATIVE_FOLLOWUP" id="other-relative-yes" value="Yes">
                        <label for="other-relative-yes">Yes</label>
                        <input type="radio" name="OTHER_RELATIVE_FOLLOWUP" id="other-relative-no" value="No">
                        <label for="other-relative-no">No</label>
                      </section>
                    </form>
                    <script>
                      for (const id of ['father-day', 'mother-day']) {
                        const select = document.getElementById(id);
                        for (let day = 1; day <= 31; day += 1) {
                          const option = document.createElement('option');
                          option.value = String(day);
                          option.textContent = String(day);
                          select.appendChild(option);
                        }
                      }
                      for (const id of ['relative-yes', 'relative-no']) {
                        document.getElementById(id).addEventListener(
                          'change',
                          () => setTimeout(() => {
                            document.getElementById(
                              'other-relative-question'
                            ).style.display = 'block';
                          }, 300),
                        );
                      }
                    </script>
                    """,
                ),
            )
            browser._page.goto(page_url, wait_until="domcontentloaded")
            parent_specs = {
                "ceac.relatives.family.father.surname": (
                    "text", "XIA", "father-surname",
                ),
                "ceac.relatives.family.father.givennames": (
                    "text", "XIAO HAI", "father-given",
                ),
                "ceac.relatives.family.father.dateofbirth": (
                    "date", "1976-05-30", "father-day",
                ),
                "ceac.relatives.family.mother.surname": (
                    "text", "LIN", "mother-surname",
                ),
                "ceac.relatives.family.mother.givennames": (
                    "text", "DI", "mother-given",
                ),
                "ceac.relatives.family.mother.dateofbirth": (
                    "date", "1976-08-24", "mother-day",
                ),
            }
            choice_specs = {
                "ceac.relatives.family.father_in_us": (
                    "yes_no", "no", "FATHER_US",
                ),
                "ceac.relatives.family.mother_in_us": (
                    "yes_no", "no", "MOTHER_US",
                ),
                "ceac.relatives.family.immediate_relatives_us": (
                    "yes_no", "no", "OTHER_RELATIVE",
                ),
                "ceac.relatives.family.other_relatives_us": (
                    "yes_no", "no", "OTHER_RELATIVE_FOLLOWUP",
                ),
            }
            specs = {**parent_specs, **choice_specs}
            labels = {
                field_id: (
                    f"fixture [control={kind}; human-approved value={value}]",
                )
                for field_id, (kind, value, _target) in specs.items()
            }

            presence = browser.classify_field_presence(
                list(specs), labels, {}
            )
            actions, unresolved = browser._plan_semantic_fields_once(
                list(specs), labels, {}
            )

            self.assertEqual(
                set(presence["present"]),
                set(parent_specs) | {
                    "ceac.relatives.family.father_in_us",
                    "ceac.relatives.family.mother_in_us",
                    "ceac.relatives.family.immediate_relatives_us",
                },
            )
            self.assertEqual(presence["absent"], [])
            self.assertEqual(
                presence["unresolved"],
                ["ceac.relatives.family.other_relatives_us"],
            )
            self.assertEqual(
                {action.field_id for action in actions}, set(parent_specs)
            )
            for action in actions:
                action.value = specs[action.field_id][1]
                browser.execute(action)
            choice_actions, choice_unresolved = (
                browser._plan_semantic_fields_once(
                    list(choice_specs), labels, {}
                )
            )
            self.assertEqual(
                {action.field_id for action in choice_actions},
                {
                    "ceac.relatives.family.father_in_us",
                    "ceac.relatives.family.mother_in_us",
                    "ceac.relatives.family.immediate_relatives_us",
                },
            )
            self.assertEqual(
                choice_unresolved,
                ["ceac.relatives.family.other_relatives_us"],
            )
            for action in choice_actions:
                action.value = choice_specs[action.field_id][1]
                browser.execute(action)
            dependent_id = "ceac.relatives.family.other_relatives_us"
            dependent_actions, dependent_unresolved = browser.plan_fields(
                [dependent_id], labels, {}
            )
            self.assertEqual(dependent_unresolved, [])
            self.assertEqual(
                [action.field_id for action in dependent_actions],
                [dependent_id],
            )
            dependent_actions[0].value = choice_specs[dependent_id][1]
            browser.execute(dependent_actions[0])
            self.assertEqual(
                browser._page.locator("#father-surname").input_value(),
                "XIA",
            )
            self.assertEqual(
                browser._page.locator("#father-year").input_value(),
                "1976",
            )
            self.assertEqual(
                browser._page.locator("#mother-surname").input_value(),
                "LIN",
            )
            self.assertEqual(
                browser._page.locator("#mother-year").input_value(),
                "1976",
            )
            self.assertTrue(browser._page.locator("#father-no").is_checked())
            self.assertTrue(browser._page.locator("#mother-no").is_checked())
            self.assertTrue(browser._page.locator("#relative-no").is_checked())
            self.assertTrue(
                browser._page.locator("#other-relative-no").is_checked()
            )
            for field_id, (_kind, _value, group_name) in choice_specs.items():
                selector = browser._field_selectors[field_id]
                self.assertEqual(
                    browser._page.locator(selector).get_attribute("name"),
                    group_name,
                )
            self.assertEqual(
                browser.rebind_page_fields_for_revalidation(
                    [
                        *parent_specs,
                        "ceac.relatives.family.father_in_us",
                        "ceac.relatives.family.mother_in_us",
                        "ceac.relatives.family.immediate_relatives_us",
                        "ceac.relatives.family.other_relatives_us",
                    ],
                    labels,
                ),
                [],
            )
        finally:
            browser.close()

    def test_work_restored_occupation_replays_one_postback_for_missing_panel(self):
        browser = RecordingFastBrowser()
        page_url = (
            "https://ceac.state.gov/GenNIV/General/complete/"
            "complete_workeducation1.aspx?node=WorkEducation1"
        )
        try:
            try:
                browser.start("about:blank")
            except Exception as error:
                self.skipTest(
                    "Playwright/Chromium unavailable: "
                    f"{error}"
                )
            browser._page.route(
                page_url,
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/html",
                    body="""
                    <style>
                      label, input, select { display: block; }
                      #employer-panel { display: none; }
                    </style>
                    <form>
                      <label for="occupation">Primary Occupation</label>
                      <select id="occupation" name="occupation"
                        onchange="__doPostBack(this.name, '')">
                        <option value="">- SELECT ONE -</option>
                        <option value="BUSINESS" selected>BUSINESS</option>
                      </select>
                      <section id="employer-panel">
                        <label for="organization">
                          Present Employer or School Name
                        </label>
                        <input id="organization">
                      </section>
                    </form>
                    <script>
                      window.__postbackCount = Number(
                        sessionStorage.getItem('postback-count') || '0'
                      );
                      if (
                        sessionStorage.getItem('posted-occupation')
                        === 'BUSINESS'
                      ) {
                        document.getElementById(
                          'employer-panel'
                        ).style.display = 'block';
                      }
                      window.__doPostBack = target => {
                        if (target !== 'occupation') return;
                        window.__postbackCount += 1;
                        sessionStorage.setItem(
                          'postback-count',
                          String(window.__postbackCount),
                        );
                        const approved = (
                          document.getElementById('occupation').value
                          === 'BUSINESS'
                        );
                        sessionStorage.setItem(
                          'posted-occupation',
                          approved ? 'BUSINESS' : '',
                        );
                      };
                    </script>
                    """,
                ),
            )
            browser._page.goto(page_url, wait_until="domcontentloaded")
            primary_id = "ceac.work_education1.work.primary_occupation"
            organization_id = "ceac.work_education1.work.organization"
            labels = {
                primary_id: (
                    "Primary Occupation [control=select_text; "
                    "refresh_after_change=true; "
                    "repair_missing_branch=aspnet-reset-reload-v7; "
                    "human-approved value=business]",
                ),
                organization_id: (
                    "Present Employer or School Name [control=text; "
                    "human-approved value=XINZHUOSHIYE]",
                ),
            }

            # Production resumes with only pending field labels; the already
            # completed controller is intentionally absent from this subset.
            resumed_labels = {
                organization_id: labels[organization_id],
            }
            replay_actions, unresolved = browser.plan_fields(
                [organization_id], resumed_labels, {}
            )
            self.assertEqual(unresolved, [organization_id])
            self.assertEqual(
                [action.field_id for action in replay_actions],
                [primary_id],
            )
            self.assertIn(
                "missing-branch controller replay",
                replay_actions[0].reason,
            )
            replay_actions[0].value = "business"
            browser.execute(replay_actions[0])
            browser._page.wait_for_timeout(50)
            self.assertTrue(
                browser.dynamic_refresh_detected(replay_actions[0])
            )
            self.assertEqual(
                browser._page.evaluate("window.__postbackCount"), 2
            )
            # The safe reload rebuilds the server-side branch but the live
            # CEAC select itself can return to its placeholder.  The repaired
            # driver must rebind the replacement select and restore BUSINESS
            # without a third postback, otherwise final page revalidation
            # retires the controller and never unlocks Next.
            self.assertEqual(
                browser._page.locator("#occupation").input_value(),
                "BUSINESS",
            )
            self.assertEqual(
                browser._page.locator(
                    browser._field_selectors[primary_id]
                ).get_attribute("id"),
                "occupation",
            )

            dependent_actions, dependent_unresolved = browser.plan_fields(
                [organization_id], labels, {}
            )
            self.assertEqual(dependent_unresolved, [])
            self.assertEqual(
                [action.field_id for action in dependent_actions],
                [organization_id],
            )
            dependent_actions[0].value = "XINZHUOSHIYE"
            browser.execute(dependent_actions[0])
            self.assertEqual(
                browser._page.locator("#organization").input_value(),
                "XINZHUOSHIYE",
            )
            self.assertEqual(
                browser._page.evaluate("window.__postbackCount"), 2
            )
            self.assertEqual(
                browser._plan_missing_work_branch_replay(
                    [organization_id], resumed_labels,
                ),
                [],
            )
            self.assertEqual(
                browser.rebind_page_fields_for_revalidation(
                    [primary_id, organization_id],
                    labels,
                ),
                [],
            )
        finally:
            browser.close()

    def test_work_education2_yes_branch_cannot_be_declared_inapplicable(self):
        browser = RecordingFastBrowser()
        page_url = (
            "https://ceac.state.gov/GenNIV/General/complete/"
            "complete_workeducation2.aspx?node=WorkEducation2"
        )
        controller_id = (
            "ceac.work_education2.work.education_secondary_or_above"
        )
        school_id = (
            "ceac.work_education2.work.education.record.school.key"
        )
        city_id = (
            "ceac.work_education2.work.education.record.city.key"
        )
        labels = {
            controller_id: (
                "Secondary education [control=yes_no; "
                "human-approved value=yes]",
            ),
            school_id: (
                "Name of Institution [control=text; occurrence=1; "
                "label_terms=Name of Institution; "
                "human-approved value=TEST SCHOOL]",
            ),
            city_id: (
                "School City [control=text; occurrence=1; "
                "label_terms=School City|City; "
                "human-approved value=SHENZHEN]",
            ),
        }
        try:
            try:
                browser.start("about:blank")
            except Exception as error:
                self.skipTest(
                    "Playwright/Chromium unavailable: "
                    f"{error}"
                )
            browser._page.route(
                page_url,
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/html",
                    body="""
                    <style>
                      label, span { display: block; }
                      input { display: inline-block; }
                      #school-panel { display: none; }
                    </style>
                    <form>
                      <div>
                        <span>Have you attended any educational institutions
                          at a secondary level or above?</span>
                        <label><input type="radio" name="education"
                          value="Y" checked>Yes</label>
                        <label><input type="radio" name="education"
                          value="N">No</label>
                      </div>
                      <section id="school-panel">
                        <label for="school">Name of Institution</label>
                        <input id="school">
                        <label for="city">City</label>
                        <input id="city">
                      </section>
                    </form>
                    """,
                ),
            )
            browser._page.goto(page_url, wait_until="domcontentloaded")

            # This is the exact dangerous interval seen in production: Yes is
            # already selected but the school panel is not in the rendered
            # branch yet.  Neither reviewed school field may be put into the
            # inapplicable set, so the workflow cannot plan Next.
            pending = [school_id, city_id]
            presence = browser.classify_field_presence(
                pending,
                labels,
                {},
            )
            self.assertEqual(presence["present"], [])
            self.assertEqual(presence["absent"], [])
            self.assertEqual(set(presence["unresolved"]), set(pending))

            browser._page.locator("#school-panel").evaluate(
                "el => { el.style.display = 'block'; }"
            )
            revealed = browser.classify_field_presence(
                pending,
                labels,
                {},
            )
            self.assertEqual(revealed["absent"], [])
        finally:
            browser.close()

    def test_work_panel_waits_for_occupation_then_fills_every_visible_field(self):
        browser = RecordingFastBrowser()
        page_url = (
            "https://ceac.state.gov/GenNIV/General/complete/"
            "complete_workeducation1.aspx?node=WorkEducation1"
        )
        try:
            try:
                browser.start("about:blank")
            except Exception as error:
                self.skipTest(
                    "Playwright/Chromium unavailable: "
                    f"{error}"
                )
            browser._page.route(
                page_url,
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/html",
                    body="""
                    <style>
                      label, span { display: block; }
                      input, select, textarea { display: inline-block; }
                      #employer-panel { display: none; }
                    </style>
                    <form>
                      <label for="occupation">Primary Occupation</label>
                      <select id="occupation">
                        <option value="">- SELECT ONE -</option>
                        <option value="BUSINESS">BUSINESS</option>
                      </select>
                      <section id="employer-panel">
                        <label for="organization">
                          Present Employer or School Name
                        </label>
                        <input id="organization">
                        <label for="line1">Street Address (Line 1)</label>
                        <input id="line1">
                        <label for="line2">
                          Street Address (Line 2) *Optional
                        </label>
                        <input id="line2">
                        <label for="city">City</label>
                        <input id="city">
                        <label for="region">State/Province</label>
                        <input id="region">
                        <label for="postal">Postal Zone/ZIP Code</label>
                        <input id="postal">
                        <label for="country">Country/Region</label>
                        <select id="country">
                          <option value="">- SELECT ONE -</option>
                          <option value="CHINA">CHINA</option>
                        </select>
                        <label for="phone">Phone Number</label>
                        <input id="phone">
                        <div>
                          <span>Start Date</span>
                          <select id="start-day"></select>
                          <select id="start-month">
                            <option value="APR">APR</option>
                          </select>
                          <input id="start-year">
                        </div>
                        <label for="income">
                          Monthly Income in Local Currency (if employed)
                        </label>
                        <input id="income">
                        <label for="duties">
                          Briefly Describe your Duties
                        </label>
                        <textarea id="duties"></textarea>
                      </section>
                    </form>
                    <script>
                      const daySelect = document.getElementById('start-day');
                      for (let day = 1; day <= 31; day += 1) {
                        const option = document.createElement('option');
                        option.value = String(day).padStart(2, '0');
                        option.textContent = option.value;
                        daySelect.appendChild(option);
                      }
                      document.getElementById('occupation').addEventListener(
                        'change',
                        () => setTimeout(() => {
                          document.getElementById(
                            'employer-panel'
                          ).style.display = 'block';
                        }, 300),
                      );
                    </script>
                    """,
                ),
            )
            browser._page.goto(page_url, wait_until="domcontentloaded")
            primary_id = "ceac.work_education1.work.primary_occupation"
            specs = {
                primary_id: ("select_text", "business", "occupation"),
                "ceac.work_education1.work.organization": (
                    "text", "XINZHUOSHIYE", "organization",
                ),
                "ceac.work_education1.work.phone": (
                    "text", "15078485005", "phone",
                ),
                "ceac.work_education1.work.startdate": (
                    "date", "2026-04-01", "start-day",
                ),
                "ceac.work_education1.work.monthlyincome": (
                    "text", "100000", "income",
                ),
                "ceac.work_education1.work.duties": (
                    "text", "TECHNOLOGY", "duties",
                ),
                "ceac.work_education1.work.present.address.record.line1.key": (
                    "text", "ROOM 10C, LINGNAN BUILDING", "line1",
                ),
                "ceac.work_education1.work.present.address.record.city.key": (
                    "text", "SHENZHEN", "city",
                ),
                "ceac.work_education1.work.present.address.record.region.key": (
                    "text", "GUANGDONG", "region",
                ),
                "ceac.work_education1.work.present.address.record.postalcode.key": (
                    "text", "518000", "postal",
                ),
                "ceac.work_education1.work.present.address.record.country.key": (
                    "select_text", "CHINA", "country",
                ),
            }
            optional_id = "ceac.work_education1.work.jobtitle"
            blank_optional_id = (
                "ceac.work_education1.work.present.address.record."
                "line2.key"
            )
            labels = {
                field_id: (
                    f"fixture [control={kind}; human-approved value={value}]",
                )
                for field_id, (kind, value, _target) in specs.items()
            }
            labels[primary_id] = (
                "Primary Occupation [control=select_text; "
                "control_hints=PRIMARY_OCCUPATION; "
                "human-approved value=business]",
            )
            labels[optional_id] = (
                "fixture [control=text; human-approved value=MANAGER]",
            )
            labels[blank_optional_id] = (
                "Street Address (Line 2) *Optional "
                "[control=text; occurrence=1; human-approved value=]",
            )
            dependent_ids = [
                field_id for field_id in specs if field_id != primary_id
            ]

            presence = browser.classify_field_presence(
                [*dependent_ids, optional_id, blank_optional_id], labels, {}
            )
            self.assertEqual(presence["present"], [])
            self.assertEqual(
                set(presence["absent"]),
                {optional_id, blank_optional_id},
            )
            self.assertEqual(
                set(presence["unresolved"]),
                set(dependent_ids),
            )

            controller_actions, controller_unresolved = (
                browser._plan_semantic_fields_once(
                    list(specs), labels, {}
                )
            )
            self.assertEqual(
                [action.field_id for action in controller_actions],
                [primary_id],
            )
            self.assertEqual(
                set(controller_unresolved), set(dependent_ids)
            )
            controller_actions[0].value = specs[primary_id][1]
            browser.execute(controller_actions[0])

            # The optional Line 2 input is now visibly rendered, exactly as on
            # the production Work/Education page.  A reviewed blank still
            # means "leave it empty"; it must never remain pending and block
            # the fixed Next control.
            visible_optional = browser.classify_field_presence(
                [blank_optional_id], labels, {}
            )
            self.assertEqual(
                visible_optional["absent"], [blank_optional_id]
            )
            self.assertEqual(
                browser._page.locator("#line2").input_value(), ""
            )
            browser._page.wait_for_timeout(400)

            # A reviewed non-empty optional value is still real data and must
            # be filled deterministically.  Conversely the branch does not
            # render Job Title, so that profile-only value remains absent and
            # must never be delegated to a visual action.
            nonblank_optional_labels = dict(labels)
            nonblank_optional_labels[blank_optional_id] = (
                "Street Address (Line 2) *Optional "
                "[control=text; occurrence=1; human-approved value="
                "NO. 3085 QIAOXIANG ROAD, FUTIAN DISTRICT]",
            )
            optional_presence = browser.classify_field_presence(
                [optional_id, blank_optional_id],
                nonblank_optional_labels,
                {},
            )
            self.assertEqual(
                optional_presence["present"], [blank_optional_id]
            )
            self.assertEqual(optional_presence["absent"], [optional_id])
            optional_actions, optional_unresolved = browser.plan_fields(
                [optional_id, blank_optional_id],
                nonblank_optional_labels,
                {},
            )
            self.assertEqual(optional_unresolved, [])
            self.assertEqual(
                [action.field_id for action in optional_actions],
                [blank_optional_id],
            )
            optional_actions[0].value = (
                "NO. 3085 QIAOXIANG ROAD, FUTIAN DISTRICT"
            )
            browser.execute(optional_actions[0])
            self.assertEqual(
                browser._page.locator("#line2").input_value(),
                "NO. 3085 QIAOXIANG ROAD, FUTIAN DISTRICT",
            )

            dependent_actions, dependent_unresolved = browser.plan_fields(
                dependent_ids, labels, {}
            )
            self.assertEqual(dependent_unresolved, [])
            self.assertEqual(
                {action.field_id for action in dependent_actions},
                set(dependent_ids),
            )
            for action in dependent_actions:
                action.value = specs[action.field_id][1]
                browser.execute(action)

            for field_id, (_kind, expected, target_id) in specs.items():
                if field_id == primary_id:
                    continue
                if field_id.endswith(".startdate"):
                    self.assertEqual(
                        browser._page.locator("#start-day").input_value(),
                        "01",
                    )
                    self.assertEqual(
                        browser._page.locator("#start-month").input_value(),
                        "APR",
                    )
                    self.assertEqual(
                        browser._page.locator("#start-year").input_value(),
                        "2026",
                    )
                else:
                    self.assertEqual(
                        browser._page.locator(f"#{target_id}").input_value(),
                        expected,
                    )
            self.assertEqual(
                browser.rebind_page_fields_for_revalidation(
                    list(specs), labels,
                ),
                [],
            )
            self.assertEqual(ModelMustNotRun().calls, 0)
        finally:
            browser.close()

    def test_language_row_is_filled_before_add_another_and_growth_is_verified(self):
        browser = RecordingFastBrowser()
        page_url = (
            "https://ceac.state.gov/GenNIV/General/complete/"
            "complete_workeducation3.aspx?node=WorkEducation3"
        )
        try:
            try:
                browser.start("about:blank")
            except Exception as error:
                self.skipTest(
                    "Playwright/Chromium unavailable: "
                    f"{error}"
                )
            posted_forms = []

            def language_page(second_row=False):
                rows = """
                        <div class="language-row">
                          <label for="language-1">Language Name</label>
                          <input id="language-1" name="language-1"
                                 value="ENGLISH">
                        </div>
                """ if second_row else """
                        <div class="language-row">
                          <label for="language-1">Language Name</label>
                          <input id="language-1" name="language-1">
                        </div>
                """
                if second_row:
                    rows += """
                        <div class="language-row">
                          <label for="language-2">Language Name</label>
                          <input id="language-2" name="language-2">
                        </div>
                    """
                duplicate_add = """
                    <a id="add-language-2"
                       href="javascript:__doPostBack('language-insert','')">
                      Add Another
                    </a>
                """ if second_row else ""
                return """
                    <style>
                      label { display: block; }
                      input, a { display: inline-block; }
                    </style>
                    <form id="language-form" method="post">
                      <input type="hidden" name="__EVENTTARGET"
                             id="__EVENTTARGET">
                      <input type="hidden" name="__EVENTARGUMENT"
                             id="__EVENTARGUMENT">
                      <label for="tooltip-language">Tooltip Language</label>
                      <select id="tooltip-language" name="LANGUAGE">
                        <option value="ENGLISH">ENGLISH</option>
                        <option value="CHINESE">CHINESE</option>
                      </select>
                      <div id="languages">
                        __ROWS__
                      </div>
                      <a id="add-language"
                         href="javascript:__doPostBack('language-insert','')">
                        Add Another
                      </a>
                      __DUPLICATE_ADD__
                    </form>
                    <script>
                      window.__doPostBack = (target, argument) => {
                          if (target !== 'language-insert') return;
                          const first = document.getElementById('language-1');
                          if (!first.value.trim()) return;
                          if (document.getElementById('language-2')) return;
                          document.getElementById('__EVENTTARGET').value = target;
                          document.getElementById('__EVENTARGUMENT').value =
                            argument || '';
                          document.getElementById('language-form').submit();
                      };
                    </script>
                """.replace("__ROWS__", rows).replace(
                    "__DUPLICATE_ADD__", duplicate_add
                )

            def route_language_page(route):
                is_post = route.request.method.upper() == "POST"
                if is_post:
                    posted_forms.append(route.request.post_data or "")
                route.fulfill(
                    status=200,
                    content_type="text/html",
                    body=language_page(second_row=is_post),
                )

            browser._page.route(page_url, route_language_page)
            browser._page.goto(page_url, wait_until="domcontentloaded")
            first_id = (
                "ceac.work_education3.additional.languages.record."
                "language.first"
            )
            ensure_id = (
                "ceac.work_education3.additional.languages.ensure.2"
            )
            second_id = (
                "ceac.work_education3.additional.languages.record."
                "language.second"
            )
            labels = {
                first_id: (
                    "Language Name [control=text; occurrence=1; "
                    "control_hints=LANGUAGE; human-approved value=ENGLISH]",
                ),
                ensure_id: (
                    "Add Another [control=ensure_repeater; "
                    "refresh_after_change=true; expected_count=2; "
                    "record_labels=Language Name; human-approved value=2]",
                ),
                second_id: (
                    "Language Name [control=text; occurrence=2; "
                    "control_hints=LANGUAGE; "
                    "human-approved value=MANDARIN CHINESE]",
                ),
            }

            # The real page plan provides sorted ids, where ``ensure`` sorts
            # before ``record``.  The browser must override only that unsafe
            # structural order and fill the already-visible row first.
            actions, unresolved = browser.plan_fields(
                sorted([first_id, ensure_id, second_id]), labels, {}
            )
            self.assertEqual(
                [action.field_id for action in actions],
                [first_id, ensure_id],
            )
            self.assertEqual(unresolved, [second_id])

            actions[0].value = "ENGLISH"
            browser.execute(actions[0])
            self.assertEqual(
                browser._page.locator("#language-1").input_value(),
                "ENGLISH",
            )
            browser.execute(actions[1])
            self.assertTrue(
                browser.settle_after_dynamic_refresh(
                    ensure_id, labels[ensure_id], ()
                )
            )
            self.assertEqual(len(posted_forms), 1)
            self.assertIn("__EVENTTARGET=language-insert", posted_forms[0])
            self.assertIn("language-1=ENGLISH", posted_forms[0])
            self.assertEqual(
                browser._count_repeater_records(["Language Name"]), 2
            )

            second_actions, second_unresolved = browser.plan_fields(
                [second_id], labels, {}
            )
            self.assertEqual(second_unresolved, [])
            self.assertEqual(
                [action.field_id for action in second_actions], [second_id]
            )
            second_actions[0].value = "MANDARIN CHINESE"
            browser.execute(second_actions[0])
            self.assertEqual(
                browser._page.locator("#language-2").input_value(),
                "MANDARIN CHINESE",
            )

            # Reproduce the production DOM from the reported failure: both
            # completed rows expose an identically named Add Another link.
            # Planning must count the rows first, return an idempotent ensure
            # action, and execute it without dispatching another form post.
            self.assertEqual(
                browser._page.get_by_role(
                    "link", name="Add Another", exact=True
                ).count(),
                2,
            )
            satisfied_actions, satisfied_unresolved = browser.plan_fields(
                [ensure_id], labels, {}
            )
            self.assertEqual(satisfied_unresolved, [])
            self.assertEqual(len(satisfied_actions), 1)
            self.assertEqual(satisfied_actions[0].kind, ActionKind.CLICK)
            self.assertIn(
                "current_count=2", satisfied_actions[0].reason
            )
            browser.execute(satisfied_actions[0])
            self.assertEqual(len(posted_forms), 1)
        finally:
            browser.close()

    def test_language_add_another_forces_exact_form_post_when_callback_is_noop(self):
        browser = RecordingFastBrowser()
        page_url = (
            "https://ceac.state.gov/GenNIV/General/complete/"
            "complete_workeducation3.aspx?node=WorkEducation3"
        )
        try:
            try:
                browser.start("about:blank")
            except Exception as error:
                self.skipTest(
                    "Playwright/Chromium unavailable: "
                    f"{error}"
                )
            posted_forms = []

            def language_page(second_row=False):
                extra_row = """
                    <div class="language-row">
                      <label for="language-2">Language Name</label>
                      <input id="language-2" name="language-2">
                    </div>
                """ if second_row else ""
                return """
                    <style>
                      label { display: block; }
                      input, a { display: inline-block; }
                    </style>
                    <form id="language-form" method="post">
                      <input type="hidden" name="__EVENTTARGET"
                             id="__EVENTTARGET">
                      <input type="hidden" name="__EVENTARGUMENT"
                             id="__EVENTARGUMENT">
                      <label for="tooltip-language">Tooltip Language</label>
                      <select id="tooltip-language" name="LANGUAGE">
                        <option value="ENGLISH">ENGLISH</option>
                        <option value="CHINESE">CHINESE</option>
                      </select>
                      <div id="languages">
                        <div class="language-row">
                          <label for="language-1">Language Name</label>
                          <input id="language-1" name="language-1"
                                 value="ENGLISH">
                        </div>
                        __EXTRA_ROW__
                      </div>
                      <a id="add-language"
                         href="javascript:__doPostBack('language-insert','')">
                        Add Another
                      </a>
                    </form>
                    <script>
                      // Reproduce the live failure: the function exists and
                      // accepts the exact target, but starts no form request.
                      window.__doPostBack = () => {};
                    </script>
                """.replace("__EXTRA_ROW__", extra_row)

            def route_language_page(route):
                is_post = route.request.method.upper() == "POST"
                if is_post:
                    posted_forms.append(route.request.post_data or "")
                route.fulfill(
                    status=200,
                    content_type="text/html",
                    body=language_page(second_row=is_post),
                )

            browser._page.route(page_url, route_language_page)
            browser._page.goto(page_url, wait_until="domcontentloaded")
            ensure_id = (
                "ceac.work_education3.additional.languages.ensure.2"
            )
            action = ComputerAction(
                kind=ActionKind.CLICK,
                field_id=ensure_id,
                target_hint="Add Another",
                reason=(
                    "Deterministic repeater ensure [expected_count=2; "
                    "current_count=1; record_labels=Language Name]"
                ),
            )
            locator = browser._find_repeater_button("Add Another")
            self.assertIsNotNone(locator)
            browser._mark_field(locator, action)

            browser.execute(action)
            self.assertEqual(len(posted_forms), 1)
            self.assertIn("__EVENTTARGET=language-insert", posted_forms[0])
            self.assertIn("language-1=ENGLISH", posted_forms[0])
            self.assertEqual(
                browser._count_repeater_records(["Language Name"]), 2
            )
            diagnostic = browser.repeater_dispatch_diagnostic()
            self.assertEqual(
                diagnostic.get("result"), "fallback_postback_grew"
            )
            self.assertTrue(
                diagnostic.get("fallbackDispatch", {}).get(
                    "forcedNativeFormSubmit"
                )
            )
        finally:
            browser.close()

    def test_passport_city_never_binds_stale_does_not_apply_descriptor(self):
        browser = RecordingFastBrowser()
        passport_url = (
            "https://ceac.state.gov/GenNIV/General/complete/"
            "complete_pptvisa.aspx?node=PptVisa"
        )
        try:
            try:
                browser.start("about:blank")
            except Exception as error:
                self.skipTest(
                    "Playwright/Chromium unavailable: "
                    f"{error}"
                )
            browser._page.route(
                passport_url,
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/html",
                    body='<label for="city">City</label>'
                         '<input id="city" value="">',
                ),
            )
            browser._page.goto(passport_url, wait_until="domcontentloaded")
            field_id = "ceac.passport.passport.issuecity"
            labels = {
                field_id: (
                    "City Where Issued [control=does_not_apply; "
                    "human-approved value=true]",
                )
            }

            actions, unresolved = browser._plan_semantic_fields_once(
                [field_id], labels, {}
            )

            self.assertEqual(actions, [])
            self.assertEqual(unresolved, [field_id])
            self.assertEqual(browser._page.locator("#city").input_value(), "")
        finally:
            browser.close()

    def test_address_phone_history_choices_use_exact_prompt_groups(self):
        browser = RecordingFastBrowser()
        address_url = (
            "https://ceac.state.gov/GenNIV/General/complete/"
            "complete_contact.aspx?node=AddressPhone"
        )
        try:
            try:
                browser.start("about:blank")
            except Exception as error:
                self.skipTest(
                    "Playwright/Chromium unavailable: "
                    f"{error}"
                )
            browser._page.route(
                address_url,
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/html",
                    body="""
                    <style>
                      .question { margin: 24px 0; }
                      .prompt { display: block; width: 700px; }
                      .answer { display: block; margin-top: 8px; }
                    </style>
                    <form>
                      <div class="question">
                        <span class="prompt">
                          Have you used any other phone numbers in the last
                          five years?
                        </span>
                        <span class="answer">
                          <input type="radio" name="mysteryA" id="a-y">
                          <label for="a-y">Yes</label>
                          <input type="radio" name="mysteryA" id="a-n">
                          <label for="a-n">No</label>
                        </span>
                      </div>
                      <div class="question">
                        <span class="prompt">
                          Have you used any other email addresses in the last
                          five years?
                        </span>
                        <span class="answer">
                          <input type="radio" name="mysteryB" id="b-y">
                          <label for="b-y">Yes</label>
                          <input type="radio" name="mysteryB" id="b-n">
                          <label for="b-n">No</label>
                        </span>
                      </div>
                      <div class="question">
                        <span class="prompt">
                          Do you have a social media presence?
                        </span>
                        <span class="answer">
                          <input type="radio" name="mysteryC" id="c-y">
                          <label for="c-y">Yes</label>
                          <input type="radio" name="mysteryC" id="c-n">
                          <label for="c-n">No</label>
                        </span>
                      </div>
                      <div class="question">
                        <span class="prompt">
                          Do you wish to provide information about your
                          presence on any other websites or applications you
                          have used within the last five years?
                        </span>
                        <span class="answer">
                          <input type="radio" name="mysteryD" id="d-y">
                          <label for="d-y">Yes</label>
                          <input type="radio" name="mysteryD" id="d-n">
                          <label for="d-n">No</label>
                        </span>
                      </div>
                    </form>
                    """,
                ),
            )
            browser._page.goto(address_url, wait_until="domcontentloaded")
            expected = {
                "ceac.address_phone.contact.other_phones": "mysteryA",
                "ceac.address_phone.contact.other_emails": "mysteryB",
                "ceac.address_phone.contact.social_media": "mysteryC",
                "ceac.address_phone.contact.other_platforms": "mysteryD",
            }
            labels = {
                field_id: (
                    "production descriptor [control=yes_no; "
                    "human-approved value=no]",
                )
                for field_id in expected
            }

            actions, unresolved = browser.plan_choice_fields(
                list(expected), labels, {}
            )

            self.assertEqual(unresolved, [])
            self.assertEqual(
                {action.field_id for action in actions}, set(expected)
            )
            for field_id, group_name in expected.items():
                selector = browser._field_selectors[field_id]
                self.assertEqual(
                    browser._page.locator(selector).get_attribute("name"),
                    group_name,
                )
        finally:
            browser.close()

    def test_personal2_permanent_resident_is_present_and_blocks_next_until_answered(self):
        browser = RecordingFastBrowser()
        page_url = (
            "https://ceac.state.gov/GenNIV/General/complete/"
            "complete_personalcont.aspx?node=Personal2"
        )
        try:
            try:
                browser.start("about:blank")
            except Exception as error:
                self.skipTest(
                    "Playwright/Chromium unavailable: "
                    f"{error}"
                )
            browser._page.route(
                page_url,
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/html",
                    body="""
                    <style>
                      .question { margin: 24px; width: 760px; }
                      .prompt, .answers { display: block; margin: 8px; }
                      input { display: inline-block; }
                    </style>
                    <form>
                      <div class="question">
                        <span class="prompt">
                          Do you hold or have you held any nationality other
                          than the one indicated above on nationality?
                        </span>
                        <span class="answers">
                          <input id="other-y" type="radio"
                                 name="ctl00$OTHER_NATL" value="Y">
                          <label for="other-y">Yes</label>
                          <input id="other-n" type="radio"
                                 name="ctl00$OTHER_NATL" value="N" checked>
                          <label for="other-n">No</label>
                        </span>
                      </div>
                      <div class="question">
                        <span class="prompt">
                          Are you a permanent resident of a country/region
                          other than your country/region of origin
                          (nationality) indicated above?
                        </span>
                        <span class="answers">
                          <input id="resident-y" type="radio"
                                 name="ctl00$OTHER_RESIDENT" value="Y">
                          <label for="resident-y">Yes</label>
                          <input id="resident-n" type="radio"
                                 name="ctl00$OTHER_RESIDENT" value="N">
                          <label for="resident-n">No</label>
                        </span>
                      </div>
                    </form>
                    """,
                ),
            )
            browser._page.goto(page_url, wait_until="domcontentloaded")
            field_id = (
                "ceac.personal2.personal."
                "permanent_resident_other_country"
            )
            labels = {
                field_id: (
                    "Are you a permanent resident of a country/region other "
                    "than your country of nationality? "
                    "[control=yes_no; refresh_after_change=true; "
                    "human-approved value=no]",
                ),
            }
            hints = {field_id: ("OTH_RES", "PERM_RES")}

            presence = browser.classify_field_presence(
                [field_id], labels, hints,
            )
            self.assertEqual(presence["present"], [field_id])
            self.assertEqual(presence["absent"], [])
            self.assertEqual(
                browser.unanswered_visible_choice_fields(
                    [field_id], labels, hints,
                ),
                [field_id],
            )

            actions, unresolved = browser.plan_choice_fields(
                [field_id], labels, hints,
            )
            self.assertEqual(unresolved, [])
            self.assertEqual(len(actions), 1)
            actions[0].value = "no"
            browser.execute(actions[0])
            self.assertTrue(
                browser._page.locator("#resident-n").is_checked()
            )
            self.assertEqual(
                browser.unanswered_visible_choice_fields(
                    [field_id], labels, hints,
                ),
                [],
            )
        finally:
            browser.close()

    def test_security2_exact_control_suffix_beats_shared_trafficking_hint(self):
        browser = RecordingFastBrowser()
        page_url = (
            "https://ceac.state.gov/GenNIV/General/complete/"
            "complete_securityandbackground2.aspx?"
            "node=SecurityandBackground2"
        )
        try:
            try:
                browser.start("about:blank")
            except Exception as error:
                self.skipTest(
                    "Playwright/Chromium unavailable: "
                    f"{error}"
                )
            browser._page.route(
                page_url,
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/html",
                    body="""
                    <style>
                      .question { margin: 24px; width: 760px; }
                      .prompt, .answers { display: block; margin: 8px; }
                      input { display: inline-block; }
                    </style>
                    <form>
                      <div class="question">
                        <span class="prompt">
                          Have you ever committed or conspired to commit a
                          human trafficking offense in the United States or
                          outside the United States?
                        </span>
                        <span class="answers">
                          <input id="traffic-y" type="radio"
                                 name="ctl00$rblHumanTrafficking" value="Y">
                          <label for="traffic-y">Yes</label>
                          <input id="traffic-n" type="radio"
                                 name="ctl00$rblHumanTrafficking" value="N">
                          <label for="traffic-n">No</label>
                        </span>
                      </div>
                      <div class="question">
                        <span class="prompt">
                          Have you ever knowingly aided, abetted, assisted or
                          colluded with an individual who has committed a
                          severe human trafficking offense?
                        </span>
                        <span class="answers">
                          <input id="assist-y" type="radio"
                                 name="ctl00$rblHumanTraffickingAssist"
                                 value="Y">
                          <input id="assist-n" type="radio"
                                 name="ctl00$rblHumanTraffickingAssist"
                                 value="N">
                        </span>
                      </div>
                      <div class="question">
                        <span class="prompt">
                          Are you the spouse, son, or daughter of an individual
                          involved in human trafficking?
                        </span>
                        <span class="answers">
                          <input id="family-y" type="radio"
                                 name="ctl00$rblHumanTraffickingFamily"
                                 value="Y">
                          <input id="family-n" type="radio"
                                 name="ctl00$rblHumanTraffickingFamily"
                                 value="N">
                        </span>
                      </div>
                    </form>
                    """,
                ),
            )
            browser._page.goto(page_url, wait_until="domcontentloaded")
            field_id = (
                "ceac.security_background2.security."
                "trafficking_participation"
            )
            labels = {
                field_id: (
                    "Have you ever committed or conspired to commit a human "
                    "trafficking offense in the United States or outside the "
                    "United States? [control=yes_no; "
                    "human-approved value=no]",
                ),
            }
            hints = {
                field_id: (
                    "HUMAN_TRAFFICKING",
                    "TRAFFICKING_OFFENSE",
                ),
            }

            actions, unresolved = browser.plan_choice_fields(
                [field_id], labels, hints,
            )
            self.assertEqual(unresolved, [])
            self.assertEqual(len(actions), 1)
            selector = browser._field_selectors[field_id]
            self.assertEqual(
                browser._page.locator(selector).get_attribute("name"),
                "ctl00$rblHumanTrafficking",
            )
            actions[0].value = "no"
            browser.execute(actions[0])
            self.assertTrue(browser._page.locator("#traffic-n").is_checked())
            self.assertFalse(browser._page.locator("#assist-n").is_checked())
            self.assertFalse(browser._page.locator("#family-n").is_checked())
        finally:
            browser.close()

    def test_travel_fallback_separates_us_and_payer_sections(self):
        browser = RecordingFastBrowser()
        travel_url = (
            "https://ceac.state.gov/GenNIV/General/complete/"
            "complete_travel.aspx?node=Travel"
        )
        try:
            try:
                browser.start("about:blank")
            except Exception as error:
                self.skipTest(
                    "Playwright/Chromium unavailable: "
                    f"{error}"
                )
            browser._page.route(
                travel_url,
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/html",
                    body="""
                    <style>
                      section, label { display: block; }
                      input, select { display: inline-block; }
                      section { margin-bottom: 24px; }
                    </style>
                    <form>
                      <section>
                        <span>Intended Date of Arrival</span>
                        <select id="arrival-day"><option>02</option></select>
                        <select id="arrival-month"><option>JUN</option></select>
                        <input id="arrival-year" value="2027">
                        <span>Intended Length of Stay in U.S.</span>
                        <input id="stay-amount" value="7">
                        <select id="stay-unit"><option>DAY(S)</option></select>
                      </section>
                      <section>
                        <h3>Address Where You Will Stay in the U.S.</h3>
                        <label for="us-line1">Street Address (Line 1)</label>
                        <input id="us-line1" value="HOLLYWOOD">
                        <label for="us-line2">
                          Street Address (Line 2) *Optional
                        </label>
                        <input id="us-line2" value="WU">
                        <label for="us-city">City</label>
                        <input id="us-city" value="LOS ANGELES">
                        <label for="us-postal">ZIP Code (if known)</label>
                        <input id="us-postal" value="90001">
                      </section>
                      <section>
                        <label for="payer">
                          Person/Entity Paying for Your Trip
                        </label>
                        <select id="payer">
                          <option>PRESENT EMPLOYER</option>
                        </select>
                        <label for="payer-org">Organization Name</label>
                        <input id="payer-org" value="EXAMPLE COMPANY">
                        <label for="payer-phone">Telephone Number</label>
                        <input id="payer-phone" value="15078485005">
                        <label for="payer-line1">Street Address (Line 1)</label>
                        <input id="payer-line1" value="ROOM 10C">
                        <label for="payer-line2">Street Address (Line 2)</label>
                        <input id="payer-line2" value="QIAOXIANG ROAD">
                        <label for="payer-city">City</label>
                        <input id="payer-city" value="SHENZHEN">
                        <label for="payer-region">State/Province</label>
                        <input id="payer-region" value="GUANGDONG">
                        <label for="payer-postal">Postal Zone/ZIP Code</label>
                        <input id="payer-postal" value="518000">
                        <label for="payer-country">Country/Region</label>
                        <select id="payer-country"><option>CHINA</option></select>
                      </section>
                    </form>
                    """,
                ),
            )
            browser._page.goto(travel_url, wait_until="domcontentloaded")
            field_specs = {
                "ceac.travel.travel.arrivaldate": (
                    "date", "2027-06-02", "arrival-day",
                ),
                "ceac.travel.travel.stayduration": (
                    "duration", "7 DAY", "stay-amount",
                ),
                "ceac.travel.travel.usstreet1": (
                    "text", "HOLLYWOOD", "us-line1",
                ),
                "ceac.travel.travel.usstreet2": (
                    "text", "WU", "us-line2",
                ),
                "ceac.travel.travel.uspostalcode": (
                    "text", "90001", "us-postal",
                ),
                "ceac.travel.travel.payerorganization": (
                    "text", "EXAMPLE COMPANY", "payer-org",
                ),
                "ceac.travel.travel.payerphone": (
                    "text", "15078485005", "payer-phone",
                ),
                "ceac.travel.travel.payeraddress.record.line1.key": (
                    "text", "ROOM 10C", "payer-line1",
                ),
                "ceac.travel.travel.payeraddress.record.line2.key": (
                    "text", "QIAOXIANG ROAD", "payer-line2",
                ),
                "ceac.travel.travel.payeraddress.record.city.key": (
                    "text", "SHENZHEN", "payer-city",
                ),
                "ceac.travel.travel.payeraddress.record.region.key": (
                    "text", "GUANGDONG", "payer-region",
                ),
                "ceac.travel.travel.payeraddress.record.postalcode.key": (
                    "text", "518000", "payer-postal",
                ),
                "ceac.travel.travel.payeraddress.record.country.key": (
                    "select_text", "CHINA", "payer-country",
                ),
            }
            labels = {
                field_id: (
                    f"fixture [control={kind}; "
                    f"human-approved value={value}]",
                )
                for field_id, (kind, value, _target) in field_specs.items()
            }

            actions, unresolved = browser._plan_semantic_fields_once(
                list(field_specs),
                labels,
                {},
            )

            self.assertEqual(unresolved, [])
            self.assertEqual(
                {action.field_id for action in actions},
                set(field_specs),
            )
            for field_id, (_kind, _value, target_id) in field_specs.items():
                selector = browser._field_selectors[field_id]
                self.assertEqual(
                    browser._page.locator(selector).get_attribute("id"),
                    target_id,
                )
        finally:
            browser.close()

    def test_travel_purpose_secondary_is_pending_then_planned_before_specific(self):
        browser = RecordingFastBrowser()
        travel_url = (
            "https://ceac.state.gov/GenNIV/General/complete/"
            "complete_travel.aspx?node=Travel"
        )
        try:
            try:
                browser.start("about:blank")
            except Exception as error:
                self.skipTest(
                    "Playwright/Chromium unavailable: "
                    f"{error}"
                )
            browser._page.route(
                travel_url,
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/html",
                    body="""
                    <style>
                      label, span { display: block; }
                      input, select { width: 320px; height: 30px; }
                    </style>
                    <form>
                      <label for="primary">Purpose of Trip to the U.S.</label>
                      <select id="primary" name="ctl00$PurposeOfTrip">
                        <option value="">SELECT ONE</option>
                        <option value="B">
                          TEMP. BUSINESS OR PLEASURE VISITOR (B)
                        </option>
                      </select>
                      <div id="secondary-slot"></div>
                      <div>
                        <span>Have you made specific travel plans?</span>
                        <label><input type="radio" name="ctl00$SpecificTravel"
                          value="Y">Yes</label>
                        <label><input type="radio" name="ctl00$SpecificTravel"
                          value="N">No</label>
                      </div>
                      <div id="arrival-slot"></div>
                    </form>
                    <script>
                      document.querySelector('#primary').addEventListener(
                        'change',
                        () => {
                          document.querySelector(
                            '#secondary-slot'
                          ).innerHTML = `
                            <label for="secondary">Specify</label>
                            <select id="secondary"
                              name="ctl00$OtherPurpose">
                              <option value="">SELECT ONE</option>
                              <option value="B1B2">
                                BUSINESS &amp; TOURISM
                                (TEMPORARY VISITOR) (B1/B2)
                              </option>
                            </select>`;
                          document.querySelector(
                            '#secondary'
                          ).addEventListener('change', () => {
                            document.querySelector(
                              '#arrival-slot'
                            ).innerHTML = `
                              <span>Intended Date of Arrival</span>
                              <select id="arrival-day">
                                <option>06</option>
                              </select>
                              <select id="arrival-month">
                                <option>FEB</option>
                              </select>
                              <input id="arrival-year">`;
                          });
                        }
                      );
                    </script>
                    """,
                ),
            )
            browser._page.goto(travel_url, wait_until="domcontentloaded")
            primary = "ceac.travel.travel.purpose.primary"
            secondary = "ceac.travel.travel.purpose.secondary"
            specific = "ceac.travel.travel.specific_plans"
            arrival = "ceac.travel.travel.arrivaldate"
            labels = {
                primary: (
                    "Purpose of Trip to the U.S. [control=select_text; "
                    "human-approved value=TEMP. BUSINESS OR PLEASURE "
                    "VISITOR (B)]",
                ),
                secondary: (
                    "Specify visa class [control=select_text; "
                    "human-approved value=BUSINESS & TOURISM "
                    "(TEMPORARY VISITOR) (B1/B2)]",
                ),
                specific: (
                    "Have you made specific travel plans? "
                    "[control=yes_no; human-approved value=no]",
                ),
                arrival: (
                    "Intended Date of Arrival [control=date; "
                    "human-approved value=2027-02-06]",
                ),
            }
            hints = {
                primary: ("PurposeOfTrip",),
                secondary: ("OtherPurpose",),
                specific: ("SpecificTravel",),
            }

            presence = browser.classify_field_presence(
                [secondary], labels, hints,
            )
            self.assertEqual(presence["absent"], [])
            self.assertEqual(presence["unresolved"], [secondary])

            actions, _unresolved = browser.plan_fields(
                [primary, secondary, specific, arrival], labels, hints,
            )
            self.assertEqual([action.field_id for action in actions], [primary])
            actions[0].value = "TEMP. BUSINESS OR PLEASURE VISITOR (B)"
            browser.execute(actions[0])

            actions, _unresolved = browser.plan_fields(
                [secondary, specific, arrival], labels, hints,
            )
            self.assertEqual(
                [action.field_id for action in actions],
                [secondary],
            )
            actions[0].value = (
                "BUSINESS & TOURISM (TEMPORARY VISITOR) (B1/B2)"
            )
            browser.execute(actions[0])
            self.assertTrue(browser._page.locator("#arrival-year").is_visible())
        finally:
            browser.close()

    def test_primary_visa_class_placeholder_never_binds_as_secondary(self):
        """CEAC's visa-class placeholder belongs to the primary select."""
        browser = RecordingFastBrowser()
        travel_url = (
            "https://ceac.state.gov/GenNIV/General/complete/"
            "complete_travel.aspx?node=Travel"
        )
        try:
            try:
                browser.start("about:blank")
            except Exception as error:
                self.skipTest(
                    "Playwright/Chromium unavailable: "
                    f"{error}"
                )
            browser._page.route(
                travel_url,
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/html",
                    body="""
                    <style>
                      label { display: block; }
                      select { width: 360px; height: 30px; }
                    </style>
                    <form>
                      <label for="purpose">
                        Purpose of Trip to the U.S.
                      </label>
                      <div id="purpose-slot">
                        <select id="purpose" name="ctl00$PurposeOfTrip">
                          <option value="">
                            PLEASE SELECT A VISA CLASS
                          </option>
                          <option value="B">
                            TEMP. BUSINESS OR PLEASURE VISITOR (B)
                          </option>
                        </select>
                      </div>
                      <div id="secondary-slot"></div>
                    </form>
                    <script>
                      document.querySelector('#purpose').addEventListener(
                        'change',
                        event => {
                          // The dependent branch belongs to this control's
                          // own change handler.  Directly assigning ``value``
                          // and calling __doPostBack bypasses this handler;
                          // select_option must execute it just like a manual
                          // selection does before the WebForms postback.
                          if (event.target.value !== 'B') return;
                          document.querySelector('#secondary-slot').innerHTML = `
                            <label for="visa-class">Specify</label>
                            <select id="visa-class" name="ctl00$VisaClass">
                              <option value="">SELECT ONE</option>
                              <option value="B1B2">
                                BUSINESS &amp; TOURISM
                                (TEMPORARY VISITOR) (B1/B2)
                              </option>
                            </select>`;
                        }
                      );
                    </script>
                    """,
                ),
            )
            browser._page.goto(travel_url, wait_until="domcontentloaded")
            primary = "ceac.travel.travel.purpose.primary"
            secondary = "ceac.travel.travel.purpose.secondary"
            labels = {
                primary: (
                    "Purpose of Trip to the U.S. [control=select_text; "
                    "human-approved value=TEMP. BUSINESS OR PLEASURE "
                    "VISITOR (B)]",
                ),
                secondary: (
                    "Specify visa class [control=select_text; "
                    "human-approved value=BUSINESS & TOURISM "
                    "(TEMPORARY VISITOR) (B1/B2)]",
                ),
            }

            presence = browser.classify_field_presence(
                [primary, secondary], labels, {},
            )
            self.assertEqual(presence["present"], [primary])
            self.assertEqual(presence["absent"], [])
            self.assertEqual(presence["unresolved"], [secondary])

            actions, unresolved = browser._plan_semantic_fields_once(
                [primary, secondary], labels, {},
            )
            self.assertEqual([action.field_id for action in actions], [primary])
            self.assertEqual(unresolved, [secondary])
            actions[0].value = "TEMP. BUSINESS OR PLEASURE VISITOR (B)"
            browser.execute(actions[0])

            actions, unresolved = browser._plan_semantic_fields_once(
                [primary, secondary], labels, {},
            )
            self.assertEqual([action.field_id for action in actions], [primary])
            self.assertEqual(unresolved, [secondary])
            # The completed primary is normally excluded from the next page
            # plan; request only the newly rendered dependent controller.
            actions, unresolved = browser._plan_semantic_fields_once(
                [secondary], labels, {},
            )
            self.assertEqual([action.field_id for action in actions], [secondary])
            self.assertEqual(unresolved, [])
            self.assertEqual(
                browser._page.locator(
                    browser._field_selectors[secondary]
                ).get_attribute("id"),
                "visa-class",
            )

            presence = browser.classify_field_presence(
                [primary, secondary], labels, {},
            )
            self.assertEqual(presence["present"], [primary, secondary])
            self.assertEqual(presence["absent"], [])
            self.assertEqual(presence["unresolved"], [])

            actions[0].value = (
                "BUSINESS & TOURISM (TEMPORARY VISITOR) (B1/B2)"
            )
            browser.execute(actions[0])
            self.assertEqual(
                browser._page.locator("#visa-class").input_value(),
                "B1B2",
            )
            self.assertEqual(
                browser.stale_completed_branch_controller_fields(
                    [secondary],
                    {
                        secondary: (
                            "BUSINESS & TOURISM "
                            "(TEMPORARY VISITOR) (B1/B2)"
                        ),
                    },
                    labels,
                ),
                [],
            )

            # Reproduce the production sequence from the failing job: an
            # unrelated later CEAC postback re-renders the already verified
            # visa-class select at its placeholder while dependent address
            # fields are still pending.
            browser._page.evaluate(
                """() => {
                    document.querySelector('#secondary-slot').innerHTML = `
                      <label for="visa-class">Specify</label>
                      <select id="visa-class" name="ctl00$VisaClass">
                        <option value="" selected>
                          SELECT ONE
                        </option>
                        <option value="B1B2">
                          BUSINESS &amp; TOURISM
                          (TEMPORARY VISITOR) (B1/B2)
                        </option>
                      </select>`;
                }"""
            )
            self.assertEqual(
                browser.stale_completed_branch_controller_fields(
                    [secondary],
                    {
                        secondary: (
                            "BUSINESS & TOURISM "
                            "(TEMPORARY VISITOR) (B1/B2)"
                        ),
                    },
                    labels,
                ),
                [secondary],
            )
            actions, unresolved = browser.plan_fields(
                [secondary], labels, {},
            )
            self.assertEqual([action.field_id for action in actions], [secondary])
            self.assertEqual(unresolved, [])
        finally:
            browser.close()

    def test_missing_travel_secondary_resets_primary_through_placeholder(self):
        browser = RecordingFastBrowser()
        travel_url = (
            "https://ceac.state.gov/GenNIV/General/complete/"
            "complete_travel.aspx?node=Travel"
        )
        try:
            try:
                browser.start("about:blank")
            except Exception as error:
                self.skipTest(
                    "Playwright/Chromium unavailable: "
                    f"{error}"
                )
            browser._page.route(
                travel_url,
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/html",
                    body="""
                    <label for="primary">Purpose of Trip to the U.S.</label>
                    <select id="primary" name="ctl00$PurposeOfTrip">
                      <option value="">SELECT ONE</option>
                      <option value="B" selected>
                        TEMP. BUSINESS OR PLEASURE VISITOR (B)
                      </option>
                    </select>
                    <div id="secondary-slot"></div>
                    <script>
                      window.serverPurpose = 'B';
                      window.bindPurpose = () => {
                        document.querySelector('#primary').addEventListener(
                          'change',
                          () => window.__doPostBack(
                            'ctl00$PurposeOfTrip',
                            ''
                          )
                        );
                      };
                      window.__doPostBack = () => {
                        const oldControl = document.querySelector('#primary');
                        const value = oldControl.value;
                        const changed = value !== window.serverPurpose;
                        window.serverPurpose = value;
                        const replacement = oldControl.cloneNode(true);
                        // A server response renders the posted value. Native
                        // selection changes selectedIndex, not the original
                        // HTML selected attribute copied by cloneNode.
                        replacement.value = value;
                        oldControl.replaceWith(replacement);
                        window.bindPurpose();
                        if (value !== 'B') {
                          document.querySelector(
                            '#secondary-slot'
                          ).innerHTML = '';
                        } else if (changed) {
                          document.querySelector(
                            '#secondary-slot'
                          ).innerHTML = `
                            <label for="secondary">Specify</label>
                            <select id="secondary"
                              name="ctl00$OtherPurpose">
                              <option value="">SELECT ONE</option>
                              <option value="B1B2">
                                BUSINESS &amp; TOURISM
                                (TEMPORARY VISITOR) (B1/B2)
                              </option>
                            </select>`;
                        }
                      };
                      window.bindPurpose();
                    </script>
                    """,
                ),
            )
            browser._page.goto(travel_url, wait_until="domcontentloaded")
            primary = "ceac.travel.travel.purpose.primary"
            secondary = "ceac.travel.travel.purpose.secondary"
            labels = {
                primary: (
                    "Purpose of Trip to the U.S. [control=select_text; "
                    "human-approved value=TEMP. BUSINESS OR PLEASURE "
                    "VISITOR (B)]",
                ),
                secondary: (
                    "Specify visa class [control=select_text; "
                    "human-approved value=BUSINESS & TOURISM "
                    "(TEMPORARY VISITOR) (B1/B2)]",
                ),
            }
            hints = {primary: ("PurposeOfTrip",)}

            actions, unresolved = browser.plan_fields(
                [secondary], labels, hints,
            )
            self.assertEqual(unresolved, [secondary])
            self.assertEqual([action.field_id for action in actions], [primary])
            self.assertIn("controller replay", actions[0].reason)
            actions[0].value = "TEMP. BUSINESS OR PLEASURE VISITOR (B)"
            browser.execute(actions[0])

            actions, unresolved = browser.plan_fields(
                [secondary], labels, hints,
            )
            self.assertEqual([action.field_id for action in actions], [secondary])
            self.assertEqual(unresolved, [])
            self.assertEqual(
                browser._page.locator("#primary").input_value(),
                "B",
            )
        finally:
            browser.close()

    def test_missing_travel_secondary_never_get_reloads_reviewed_primary(self):
        browser = RecordingFastBrowser()
        travel_url = (
            "https://ceac.state.gov/GenNIV/General/complete/"
            "complete_travel.aspx?node=Travel"
        )
        try:
            try:
                browser.start("about:blank")
            except Exception as error:
                self.skipTest(
                    "Playwright/Chromium unavailable: "
                    f"{error}"
                )
            browser._page.route(
                travel_url,
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/html",
                    body="""
                    <label for="primary">Purpose of Trip to the U.S.</label>
                    <select id="primary" name="ctl00$PurposeOfTrip">
                      <option value="">SELECT ONE</option>
                      <option value="B" selected>
                        TEMP. BUSINESS OR PLEASURE VISITOR (B)
                      </option>
                    </select>
                    <div id="secondary-slot"></div>
                    <script>
                      const loadCount = Number(
                        sessionStorage.getItem('purpose-load-count') || '0'
                      ) + 1;
                      sessionStorage.setItem(
                        'purpose-load-count', String(loadCount)
                      );
                      window.renderSecondary = () => {
                        document.querySelector(
                          '#secondary-slot'
                        ).innerHTML = `
                          <label for="secondary">Specify</label>
                          <select id="secondary" name="ctl00$OtherPurpose">
                            <option value="">SELECT ONE</option>
                            <option value="B1B2">
                              BUSINESS &amp; TOURISM
                              (TEMPORARY VISITOR) (B1/B2)
                            </option>
                          </select>`;
                      };
                      if (
                        sessionStorage.getItem('posted-purpose') === 'B'
                      ) {
                        window.renderSecondary();
                      }
                      window.bindPurpose = () => {
                        document.querySelector('#primary').addEventListener(
                          'change',
                          () => window.__doPostBack(
                            'ctl00$PurposeOfTrip', ''
                          )
                        );
                      };
                      window.__doPostBack = () => {
                        const oldControl = document.querySelector('#primary');
                        const value = oldControl.value;
                        sessionStorage.setItem('posted-purpose', value);
                        const replacement = oldControl.cloneNode(true);
                        replacement.value = value;
                        oldControl.replaceWith(replacement);
                        window.bindPurpose();
                        document.querySelector('#secondary-slot').innerHTML = '';
                      };
                      window.bindPurpose();
                    </script>
                    """,
                ),
            )
            browser._page.goto(travel_url, wait_until="domcontentloaded")
            primary = "ceac.travel.travel.purpose.primary"
            secondary = "ceac.travel.travel.purpose.secondary"
            labels = {
                primary: (
                    "Purpose of Trip to the U.S. [control=select_text; "
                    "human-approved value=TEMP. BUSINESS OR PLEASURE "
                    "VISITOR (B)]",
                ),
                secondary: (
                    "Specify visa class [control=select_text; "
                    "human-approved value=BUSINESS & TOURISM "
                    "(TEMPORARY VISITOR) (B1/B2)]",
                ),
            }
            hints = {primary: ("PurposeOfTrip",)}

            actions, unresolved = browser.plan_fields(
                [secondary], labels, hints,
            )
            self.assertEqual(unresolved, [secondary])
            self.assertEqual([action.field_id for action in actions], [primary])
            actions[0].value = "TEMP. BUSINESS OR PLEASURE VISITOR (B)"
            browser.execute(actions[0])

            self.assertEqual(
                browser._page.evaluate(
                    "sessionStorage.getItem('purpose-load-count')"
                ),
                "1",
            )
            actions, unresolved = browser.plan_fields(
                [secondary], labels, hints,
            )
            self.assertEqual(actions, [])
            self.assertEqual(unresolved, [secondary])
            self.assertEqual(
                browser._page.locator("#primary").input_value(),
                "B",
            )
        finally:
            browser.close()

    def test_missing_travel_secondary_never_uses_next_as_refresh(self):
        """A missing dependent select must never submit the incomplete page."""
        browser = RecordingFastBrowser()
        travel_url = (
            "https://ceac.state.gov/GenNIV/General/complete/"
            "complete_travel.aspx?node=Travel"
        )
        try:
            try:
                browser.start("about:blank")
            except Exception as error:
                self.skipTest(
                    "Playwright/Chromium unavailable: "
                    f"{error}"
                )
            browser._page.route(
                travel_url,
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/html",
                    body="""
                    <form>
                      <label for="primary">Purpose of Trip to the U.S.</label>
                      <select id="primary" name="ctl00$PurposeOfTrip">
                        <option value="">SELECT ONE</option>
                        <option value="B" selected>
                          TEMP. BUSINESS OR PLEASURE VISITOR (B)
                        </option>
                      </select>
                      <div id="secondary-slot"></div>
                      <div id="specific-plans">
                        <p>Have you made specific travel plans?</p>
                        <label><input type="radio" name="plans" value="Y">
                          Yes</label>
                        <label><input type="radio" name="plans" value="N">
                          No</label>
                      </div>
                      <button id="next" type="submit">
                        Next: Travel Companions
                      </button>
                    </form>
                    <script>
                      window.nextRefreshCount = 0;
                      document.querySelector('#next').addEventListener(
                        'click',
                        event => {
                          event.preventDefault();
                          window.nextRefreshCount += 1;
                          document.querySelector(
                            '#secondary-slot'
                          ).innerHTML = `
                            <label for="secondary">Specify</label>
                            <select id="secondary" name="ctl00$VisaClass">
                              <option value="">SELECT ONE</option>
                              <option value="B1B2">
                                BUSINESS &amp; TOURISM
                                (TEMPORARY VISITOR) (B1/B2)
                              </option>
                            </select>`;
                        }
                      );
                    </script>
                    """,
                ),
            )
            browser._page.goto(travel_url, wait_until="domcontentloaded")

            self.assertFalse(hasattr(
                browser,
                "_attempt_travel_purpose_next_validation_refresh",
            ))
            self.assertIsNone(browser._travel_purpose_control(
                "secondary", ("Specify", "Specify visa class"),
            ))
            self.assertEqual(
                browser._page.evaluate("window.nextRefreshCount"),
                0,
            )
        finally:
            browser.close()

    def test_missing_secondary_keeps_primary_unacknowledged(self):
        """A displayed B value alone must never complete the primary field."""
        browser = RecordingFastBrowser()
        travel_url = (
            "https://ceac.state.gov/GenNIV/General/complete/"
            "complete_travel.aspx?node=Travel"
        )
        try:
            try:
                browser.start("about:blank")
            except Exception as error:
                self.skipTest(
                    "Playwright/Chromium unavailable: "
                    f"{error}"
                )
            browser._page.route(
                travel_url,
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/html",
                    body="""
                    <form>
                      <label for="primary">Purpose of Trip to the U.S.</label>
                      <select id="primary" name="ctl00$PurposeOfTrip">
                        <option value="">PLEASE SELECT A VISA CLASS</option>
                        <option value="B">
                          TEMP. BUSINESS OR PLEASURE VISITOR (B)
                        </option>
                      </select>
                      <div id="specific-plans">
                        <p>Have you made specific travel plans?</p>
                        <label><input type="radio" name="ctl00$SpecificTravel"
                          value="Y">Yes</label>
                        <label><input type="radio" name="ctl00$SpecificTravel"
                          value="N">No</label>
                      </div>
                      <label for="payer">Person/Entity Paying for Your Trip</label>
                      <select id="payer" name="ctl00$Payer">
                        <option value="">SELECT ONE</option>
                        <option value="EMPLOYER">PRESENT EMPLOYER</option>
                      </select>
                      <button id="next" type="submit">
                        Next: Travel Companions
                      </button>
                    </form>
                    <script>
                      window.nextRefreshCount = 0;
                      document.querySelector('#next').addEventListener(
                        'click',
                        event => {
                          event.preventDefault();
                          window.nextRefreshCount += 1;
                        }
                      );
                    </script>
                    """,
                ),
            )
            browser._page.goto(travel_url, wait_until="domcontentloaded")
            # The fixture intentionally never renders Specify.  Keep this
            # postcondition test fast; separate live/fixture tests cover the
            # bounded wait schedule itself.
            browser._wait_for_travel_purpose_secondary = lambda: None
            primary = "ceac.travel.travel.purpose.primary"
            secondary = "ceac.travel.travel.purpose.secondary"
            specific = "ceac.travel.travel.specific_plans"
            payer = "ceac.travel.travel.payer"
            labels = {
                primary: (
                    "Purpose of Trip to the U.S. [control=select_text; "
                    "human-approved value=TEMP. BUSINESS OR PLEASURE "
                    "VISITOR (B)]",
                ),
                secondary: (
                    "Specify visa class [control=select_text; "
                    "human-approved value=BUSINESS & TOURISM "
                    "(TEMPORARY VISITOR) (B1/B2)]",
                ),
                specific: (
                    "Have you made specific travel plans? "
                    "[control=yes_no; human-approved value=no]",
                ),
                payer: (
                    "Person/Entity Paying for Your Trip "
                    "[control=select_text; "
                    "human-approved value=PRESENT EMPLOYER]",
                ),
            }
            hints = {
                primary: ("PurposeOfTrip",),
                secondary: ("OtherPurpose",),
                specific: ("SpecificTravel",),
                payer: ("Payer",),
            }

            actions, _unresolved = browser.plan_fields(
                [primary, secondary, specific, payer], labels, hints,
            )
            self.assertEqual([action.field_id for action in actions], [primary])
            actions[0].value = "TEMP. BUSINESS OR PLEASURE VISITOR (B)"
            browser.execute(actions[0])
            self.assertEqual(browser._page.locator("#primary").input_value(), "B")
            self.assertEqual(browser._page.evaluate("window.nextRefreshCount"), 0)
            passed, reason = browser.action_postcondition(actions[0])
            self.assertFalse(passed)
            self.assertIn("Specify visa class", reason)
            self.assertNotIn(actions[0].id, browser._acknowledged)
            self.assertTrue(
                browser.action_postcondition_requires_hard_boundary(
                    actions[0]
                )
            )
            followup, unresolved = browser.plan_fields(
                [secondary, specific, payer], labels, hints,
            )
            self.assertEqual(followup, [])
            self.assertEqual(
                unresolved,
                [secondary, specific, payer],
            )
        finally:
            browser.close()

    def test_travel_stay_duration_never_types_unit_into_amount_input(self):
        browser = RecordingFastBrowser()
        travel_url = (
            "https://ceac.state.gov/GenNIV/General/complete/"
            "complete_travel.aspx?node=Travel"
        )
        try:
            try:
                browser.start("about:blank")
            except Exception as error:
                self.skipTest(
                    "Playwright/Chromium unavailable: "
                    f"{error}"
                )
            browser._page.route(
                travel_url,
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/html",
                    body="""
                    <style>
                      span { display: block; }
                      .amount-wrap, .unit-wrap { display: inline-block; }
                      .other { display: block; margin-top: 30px; }
                      input, select { height: 30px; }
                    </style>
                    <form>
                      <section>
                        <span>Intended Length of Stay in U.S.</span>
                        <div class="amount-wrap">
                          <input id="stay-amount" maxlength="3" value="7 D">
                        </div>
                        <div class="unit-wrap">
                          <select id="stay-unit">
                            <option value="DAY">DAY(S)</option>
                            <option value="MONTH">MONTH(S)</option>
                          </select>
                        </div>
                        <div class="other">
                          <input id="unrelated" value="2027">
                        </div>
                      </section>
                    </form>
                    """,
                ),
            )
            browser._page.goto(travel_url, wait_until="domcontentloaded")
            field_id = "ceac.travel.travel.stayduration"
            labels = {
                field_id: (
                    "fixture [control=duration; "
                    "human-approved value=7 DAY]",
                )
            }
            actions, unresolved = browser._plan_semantic_fields_once(
                [field_id], labels, {}
            )

            self.assertEqual(unresolved, [])
            self.assertEqual(len(actions), 1)
            actions[0].value = "7 DAY"

            # The amount box is maxlength=3, but this is a composite field.
            # Generic text preflight must not persist the invalid legacy
            # representation ``7 D`` before the writer splits amount/unit.
            self.assertIsNone(browser.constrain_action_value(actions[0]))
            self.assertEqual(actions[0].value, "7 DAY")
            browser.execute(actions[0])

            amount = browser._page.locator("#stay-amount").input_value()
            self.assertEqual(amount, "7")
            self.assertRegex(amount, r"^\d+$")
            self.assertEqual(
                browser._page.locator("#stay-unit").input_value(),
                "DAY",
            )
            selector = browser._field_selectors[field_id]
            self.assertEqual(
                browser._live_control_value(field_id, selector, 800),
                "7 DAY",
            )

            # The workflow audits completed controls again before Next.  A
            # fresh semantic bind must reconstruct the duration pair even if
            # CEAC replaced the original tagged controls, otherwise the audit
            # sees only the amount and repeatedly refills it.
            browser._page.locator(
                "[data-docflow-duration-group]"
            ).evaluate_all(
                """items => items.forEach(item => {
                    item.removeAttribute('data-docflow-duration-group');
                    item.removeAttribute('data-docflow-v2-duration-part');
                })"""
            )
            # Model the checkpoint created by the old maxlength bug.  Even
            # without the temporary pair attributes, live readback must use
            # the actual amount + selected unit and return the canonical
            # reviewed representation.
            browser._verified_field_values[field_id] = "7 D"
            self.assertEqual(
                browser.observe_lightweight().control_values.get(field_id),
                "7 DAY",
            )
            browser._page.locator("#stay-amount").fill("8")
            self.assertEqual(
                browser.observe_lightweight().control_values.get(field_id),
                "8 DAY",
            )
            browser._page.locator("#stay-amount").fill("7")
            self.assertEqual(
                browser.rebind_page_fields_for_revalidation(
                    [field_id], labels,
                ),
                [],
            )
            observation = browser.observe_lightweight()
            self.assertEqual(
                observation.control_values.get(field_id),
                "7 DAY",
            )
        finally:
            browser.close()

    def test_travel_stay_duration_timeout_reloads_before_bounded_retry(self):
        browser = RecordingFastBrowser()
        travel_url = (
            "https://ceac.state.gov/GenNIV/General/complete/"
            "complete_travel.aspx?node=Travel"
        )
        load_count = {"value": 0}

        def route_travel(route):
            load_count["value"] += 1
            route.fulfill(
                status=200,
                content_type="text/html",
                body=f"""
                    <style>
                      span {{ display: block; }}
                      input, select {{ display: inline-block; height: 30px; }}
                    </style>
                    <form method="post" action="{travel_url}">
                      <input type="hidden" name="__EVENTTARGET" value="">
                      <input type="hidden" name="__EVENTARGUMENT" value="">
                      <span>Intended Length of Stay in U.S.</span>
                      <input id="stay-amount" name="ctl00$tbxTRAVEL_LOS"
                             maxlength="3" value="">
                      <select id="stay-unit"
                              name="ctl00$ddlTRAVEL_LOS_CD">
                        <option value="">-Select One-</option>
                        <option value="D">Day(s)</option>
                      </select>
                    </form>
                    <script>window.__doPostBack = () => {{}};</script>
                """,
            )

        try:
            try:
                browser.start("about:blank")
            except Exception as error:
                self.skipTest(
                    "Playwright/Chromium unavailable: "
                    f"{error}"
                )
            browser._page.route(travel_url, route_travel)
            browser._page.goto(travel_url, wait_until="domcontentloaded")
            field_id = "ceac.travel.travel.stayduration"
            labels = {
                field_id: (
                    "fixture [control=duration; "
                    "human-approved value=7 DAY]",
                )
            }
            actions, unresolved = browser._plan_semantic_fields_once(
                [field_id], labels, {},
            )
            self.assertEqual(unresolved, [])
            actions[0].value = "7 DAY"
            self.assertEqual(
                browser._page.locator("#stay-unit").input_value(),
                "",
            )

            ensure_calls = []

            def uncertain_postback(locator, *_args, **_kwargs):
                ensure_calls.append(locator.input_value())
                raise ControlPostbackTimeout("fixture uncertain POST")

            browser._ensure_travel_control_postback = uncertain_postback
            with self.assertRaises(RuntimeError) as raised:
                browser.execute(actions[0])

            self.assertNotIsInstance(
                raised.exception,
                ControlPostbackTimeout,
                f"{browser._last_control_postback_diagnostic}; "
                f"ensure_calls={ensure_calls}",
            )
            self.assertIn(
                "safely reloaded for bounded re-verification",
                str(raised.exception),
            )
            self.assertTrue(
                browser.interrupted_action_retry_safe(
                    actions[0], raised.exception,
                )
            )
            # One first-run unit selection reached the postback guard.  The
            # former bug issued an earlier reset with the placeholder value.
            self.assertEqual(ensure_calls, ["D"])
            self.assertGreaterEqual(load_count["value"], 2)
            self.assertTrue(
                browser._last_control_postback_diagnostic.get(
                    "safeReloadAfterUnknownPostback"
                )
            )
            self.assertTrue(
                browser._page.locator("#stay-amount").is_visible()
            )
            self.assertEqual(
                browser._page.locator("#stay-unit").input_value(),
                "",
            )
        finally:
            browser.close()

    def test_missing_payer_branch_reopens_controller_without_touching_us_address(
        self,
    ):
        browser = RecordingFastBrowser()
        travel_url = (
            "https://ceac.state.gov/GenNIV/General/complete/"
            "complete_travel.aspx?node=Travel"
        )
        try:
            try:
                browser.start("about:blank")
            except Exception as error:
                self.skipTest(
                    "Playwright/Chromium unavailable: "
                    f"{error}"
                )
            browser._page.route(
                travel_url,
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/html",
                    body="""
                    <form>
                      <h3>Address Where You Will Stay in the U.S.</h3>
                      <label for="us-line1">Street Address (Line 1)</label>
                      <input id="us-line1" value="HOLLYWOOD">
                      <label for="payer">
                        Person/Entity Paying for Your Trip
                      </label>
                      <select id="payer">
                        <option value="other_organization">
                          OTHER COMPANY/ORGANIZATION
                        </option>
                      </select>
                    </form>
                    """,
                ),
            )
            browser._page.goto(travel_url, wait_until="domcontentloaded")
            payer_id = "ceac.travel.travel.payer"
            line1_id = (
                "ceac.travel.travel."
                "payeraddress.record.line1.key"
            )
            labels = {
                payer_id: (
                    "Person/Entity Paying for Your Trip "
                    "[control=select_text; "
                    "human-approved value=other_organization]",
                ),
                line1_id: (
                    "Paying Party Street Address (Line 1) "
                    "[control=text; occurrence=1; "
                    "human-approved value=ROOM 10C]",
                ),
            }

            actions, unresolved = browser._plan_semantic_fields_once(
                [line1_id],
                labels,
                {},
            )

            self.assertEqual(
                [action.field_id for action in actions],
                [payer_id],
            )
            self.assertEqual(unresolved, [line1_id])
            self.assertIsNone(
                browser._page.locator("#us-line1").get_attribute(
                    "data-docflow-field-owner"
                )
            )
            presence = browser.classify_field_presence(
                [line1_id],
                labels,
                {},
            )
            self.assertEqual(presence["present"], [])
            self.assertEqual(presence["absent"], [line1_id])
            self.assertEqual(presence["unresolved"], [])
        finally:
            browser.close()

    def test_travel_revalidation_rebind_exposes_stale_us_line2(self):
        browser = RecordingFastBrowser()
        travel_url = (
            "https://ceac.state.gov/GenNIV/General/complete/"
            "complete_travel.aspx?node=Travel"
        )
        try:
            try:
                browser.start("about:blank")
            except Exception as error:
                self.skipTest(
                    "Playwright/Chromium unavailable: "
                    f"{error}"
                )
            browser._page.route(
                travel_url,
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/html",
                    body="""
                    <style>
                      label { display: block; }
                    </style>
                    <h3>Address Where You Will Stay in the U.S.</h3>
                    <label for="us-line2">
                      Street Address (Line 2) *Optional
                    </label>
                    <input id="us-line2" value="WRONG PAYER ADDRESS">
                    <label for="payer">
                      Person/Entity Paying for Your Trip
                    </label>
                    <select id="payer">
                      <option>PRESENT EMPLOYER</option>
                    </select>
                    """,
                ),
            )
            browser._page.goto(travel_url, wait_until="domcontentloaded")
            field_id = "ceac.travel.travel.usstreet2"
            labels = {
                field_id: (
                    "U.S. Street Address (Line 2) "
                    "[control=text; human-approved value=WU]",
                )
            }

            unresolved = browser.rebind_page_fields_for_revalidation(
                [field_id],
                labels,
            )
            observation = browser.observe_lightweight()

            self.assertEqual(unresolved, [])
            self.assertEqual(
                observation.control_values.get(field_id),
                "WRONG PAYER ADDRESS",
            )
        finally:
            browser.close()

    def test_late_visible_travel_date_uses_semantic_retry_not_gemini(self):
        browser = RecordingFastBrowser()
        try:
            try:
                browser.start("about:blank")
            except Exception as error:
                self.skipTest(
                    "Playwright/Chromium unavailable: "
                    f"{error}"
                )
            browser._page.set_content(
                """
                <div id="travel-fields"></div>
                <script>
                  setTimeout(() => {
                    document.querySelector("#travel-fields").innerHTML = `
                      <div>
                        <span>Intended Date of Arrival</span>
                        <select id="ARRIVAL_DATE_DAY">
                          <option value="6">6</option>
                        </select>
                        <select id="ARRIVAL_DATE_MONTH">
                          <option value="2">FEB</option>
                        </select>
                        <input id="ARRIVAL_DATE_YEAR" value="">
                      </div>
                    `;
                  }, 320);
                </script>
                """
            )
            field_id = "ceac.travel.travel.arrivaldate"
            labels = {
                field_id: (
                    "Intended Date of Arrival "
                    "[control=date; human-approved value=2027-02-06]",
                )
            }
            hints = {field_id: ("ARRIVAL_DATE",)}

            started = time.monotonic()
            actions, unresolved = browser.plan_fields(
                [field_id],
                labels,
                hints,
            )

            self.assertEqual(unresolved, [])
            self.assertEqual(len(actions), 1)
            self.assertLess(time.monotonic() - started, 2.0)
        finally:
            browser.close()

    def test_delayed_branch_request_finishes_before_semantic_replan(self):
        browser = RecordingFastBrowser()
        try:
            try:
                browser.start("about:blank")
            except Exception as error:
                self.skipTest(
                    "Playwright/Chromium unavailable: "
                    f"{error}"
                )
            browser._page.route(
                "https://docflow.test/branch",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    headers={"Access-Control-Allow-Origin": "*"},
                    body='{"ok":true}',
                ),
            )
            browser._page.set_content(
                """
                <label for="payer">Person/Entity Paying for Your Trip</label>
                <select id="payer" onchange="window.__doPostBack()">
                  <option value="self">SELF</option>
                  <option value="other_organization">
                    OTHER COMPANY/ORGANIZATION
                  </option>
                </select>
                <div id="branch"></div>
                <script>
                  window.__doPostBack = () => {
                    setTimeout(async () => {
                      await fetch("https://docflow.test/branch", {
                        method: "POST"
                      });
                      document.querySelector("#branch").innerHTML = `
                        <label for="organization">Organization Name</label>
                        <input id="organization" type="text">
                      `;
                    }, 1050);
                  };
                </script>
                """
            )
            payer_id = "ceac.travel.travel.payer"
            payer_labels = (
                "Person/Entity Paying for Your Trip "
                "[control=select_text; refresh_after_change=true; "
                "label_terms=Person/Entity Paying for Your Trip; "
                "human-approved value=other_organization]",
            )
            actions, unresolved = browser.plan_fields(
                [payer_id],
                {payer_id: payer_labels},
                {},
            )
            self.assertEqual(unresolved, [])
            self.assertEqual(len(actions), 1)
            actions[0].value = "other_organization"
            browser._v2_network_inflight.add(
                "guid:request-from-before-payer-action"
            )

            started = time.monotonic()
            browser.execute(actions[0])
            self.assertTrue(browser.dynamic_refresh_detected(actions[0]))
            self.assertTrue(
                browser.settle_after_dynamic_refresh(
                    payer_id,
                    payer_labels,
                    (),
                )
            )
            elapsed = time.monotonic() - started

            organization_id = (
                "ceac.travel.travel.payerorganization"
            )
            organization_labels = (
                "Organization Name [control=text; "
                "label_terms=Organization Name]",
            )
            dependent, dependent_unresolved = browser.plan_fields(
                [organization_id],
                {organization_id: organization_labels},
                {},
            )
            self.assertGreater(elapsed, 1.0)
            self.assertLess(elapsed, 3.0)
            self.assertIn(
                "guid:request-from-before-payer-action",
                browser._v2_network_inflight,
            )
            self.assertEqual(dependent_unresolved, [])
            self.assertEqual(len(dependent), 1)
        finally:
            browser.close()

    def test_semantic_first_visible_run_fills_two_pages_without_gemini(self):
        fields = [
            field(SURNAME, "XIA", "Surname"),
            field(GIVEN_NAMES, "YICHENG", "Given Names"),
            field(
                BRANCH,
                "true",
                "Have you ever used other names? "
                "[control=checkbox; refresh_after_change=true; "
                "control_hints=OtherNamesToggle; "
                "human-approved value=true]",
            ),
            field(
                CONDITIONAL,
                "CHEN",
                "Conditional Other Surnames "
                "[control=text; control_hints=OtherSurname]",
            ),
            field(NATIONALITY, "CHINA", "Nationality"),
            field(
                NATIONAL_ID,
                "110000200001010001",
                "National Identification Number",
            ),
        ]
        required = [item["id"] for item in fields]
        startup_errors = []

        with tempfile.TemporaryDirectory() as directory:
            model = ModelMustNotRun()

            def runtime_factory(job):
                browser = RecordingFastBrowser()
                browser.set_execution_mode("visual")
                try:
                    browser.start("about:blank")
                    route_synthetic_ceac(browser)
                    browser._page.goto(
                        job.start_url,
                        wait_until="domcontentloaded",
                        timeout=browser.NAVIGATION_TIMEOUT_MS,
                    )
                except Exception as error:
                    startup_errors.append(error)
                    browser.close()
                    raise ProviderNotConfigured(
                        "Playwright Chromium is unavailable for V2 acceptance"
                    ) from error
                return FastComputerUseAgent(
                    model,
                    browser,
                    max_steps=40,
                    execution_mode="hybrid",
                )

            service = AgentService(
                AgentConfig(data_dir=Path(directory) / "checkpoints"),
                runtime_factory=runtime_factory,
            )
            try:
                created = service.create_job({
                    "startUrl": PAGE_1_URL,
                    "requiredFieldIds": required,
                    "fields": fields,
                    "autoNext": True,
                })
                reviewed = service.review_job(created["id"], {
                    "actor": "v2-playwright-acceptance",
                    "decisions": [
                        {
                            "fieldId": item["id"],
                            "approved": True,
                            "value": item["value"],
                        }
                        for item in fields
                    ],
                })
                started = time.monotonic()
                try:
                    result = service.start_job(reviewed["id"])
                except ServiceError:
                    if startup_errors:
                        self.skipTest(
                            "Playwright/Chromium unavailable: "
                            f"{startup_errors[-1]}"
                        )
                    raise
                elapsed = time.monotonic() - started

                self.assertEqual(result["state"], "review_required")
                self.assertEqual(
                    set(result["completed_field_ids"]),
                    set(required),
                )
                self.assertEqual(model.calls, 0)
                self.assertLess(elapsed, 20.0)

                with service._runtime_lock:
                    worker = service._runtimes[reviewed["id"]]

                def inspect(runtime):
                    browser = runtime.browser
                    stats = browser._page.evaluate(
                        """() => JSON.parse(localStorage.getItem(
                            '__docflowE2EStats'
                        ) || '{}')"""
                    )
                    return {
                        "url": browser._page.url,
                        "stats": stats,
                        "executed": list(browser.executed_actions),
                    }

                snapshot = worker.call(inspect, timeout=10)
                self.assertEqual(snapshot["url"], REVIEW_URL)
                self.assertEqual(
                    snapshot["stats"].get("page1NextCount"),
                    1,
                )
                self.assertEqual(
                    snapshot["stats"].get("page2NextCount"),
                    1,
                )
                self.assertEqual(
                    snapshot["stats"].get("finalActionCount", 0),
                    0,
                )
                next_actions = [
                    action
                    for action in snapshot["executed"]
                    if (
                        action["kind"] == ActionKind.CLICK.value
                        and action["target"].lower().startswith("next")
                    )
                ]
                self.assertEqual(len(next_actions), 2)
            finally:
                service.shutdown(timeout=15)


if __name__ == "__main__":
    unittest.main()
