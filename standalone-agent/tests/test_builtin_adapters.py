import base64
import io
import json
import signal
import tempfile
import time
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest import mock

from visa_agent.adapters import (
    ControlBindingUnavailable,
    ControlValueConstraintError,
    DeepSeekAdapter,
    GeminiComputerUseAdapter,
    HTTPClient,
    MinerUAdapter,
    OpenRouterComputerUseAdapter,
    PaddleOCRAdapter,
    PlaywrightBrowserDriver,
    ProviderRequestError,
    register_builtin_providers,
)
from visa_agent.config import AgentConfig, ProviderConfig, load_config
from visa_agent.factory import build_service
from visa_agent.models import ActionKind, BrowserObservation, ComputerAction
from visa_agent.models import NextDispatchReceiptUnavailable
from visa_agent.providers import (
    FallbackOCRProvider,
    ProviderNotConfigured,
    ProviderRegistry,
)
from visa_agent.workflow import ComputerUseAgent
from visa_agent.verification import DeterministicActionVerifier


class FakeTransport:
    def __init__(self, json_responses=None, raw_response=None):
        self.json_responses = list(json_responses or [])
        self.raw_response = raw_response
        self.calls = []

    def json(self, method, url, payload, headers=None, timeout=120):
        self.calls.append((method, url, payload, headers, timeout))
        return self.json_responses.pop(0)

    def request(self, method, url, body=None, headers=None, timeout=120):
        self.calls.append((method, url, body, headers, timeout))
        return self.raw_response


class FakePaddleCloudClient:
    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        self.call = None
        self.closed = False

    def ocr(self, file_path, model):
        self.call = {
            "file_path": file_path,
            "file_bytes": Path(file_path).read_bytes(),
            "model": model,
        }

        class Page:
            pruned_result = {"rec_texts": ["Cloud", "OCR"]}

        class Result:
            pages = [Page()]

        return Result()

    def close(self):
        self.closed = True


class FlakyTransport(FakeTransport):
    def __init__(self, failures, json_responses):
        super().__init__(json_responses=json_responses)
        self.failures = failures

    def json(self, method, url, payload, headers=None, timeout=120):
        self.calls.append((method, url, payload, headers, timeout))
        if self.failures:
            self.failures -= 1
            raise ProviderRequestError("Provider connection failed: IncompleteRead")
        return self.json_responses.pop(0)


class RejectingTransport(FakeTransport):
    def __init__(self, status_code):
        super().__init__()
        self.status_code = int(status_code)

    def json(self, method, url, payload, headers=None, timeout=120):
        self.calls.append((method, url, payload, headers, timeout))
        raise ProviderRequestError(
            f"Provider HTTP {self.status_code}: request rejected",
            status_code=self.status_code,
            retryable=(
                self.status_code == 429
                or self.status_code >= 500
            ),
        )


class TimingOutTransport(FakeTransport):
    def json(self, method, url, payload, headers=None, timeout=120):
        self.calls.append((method, url, payload, headers, timeout))
        raise ProviderRequestError(
            "Provider connection failed: TimeoutError",
            retryable=True,
        )


def provider(provider, model="", url="", key=""):
    return ProviderConfig(
        provider=provider,
        model=model,
        api_base_url=url,
        api_key=key,
    )


class BuiltinAdapterTests(unittest.TestCase):
    def test_http_client_classifies_unsupported_provider_location_safely(self):
        body = io.BytesIO(json.dumps({
            "error": {
                "message": "User location is not supported for API use.",
                "status": "FAILED_PRECONDITION",
            }
        }).encode("utf-8"))
        rejected = urllib.error.HTTPError(
            "https://provider.invalid/v1/interactions",
            400,
            "Bad Request",
            {},
            body,
        )
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=rejected,
        ):
            with self.assertRaises(ProviderRequestError) as caught:
                HTTPClient().request(
                    "POST",
                    "https://provider.invalid/v1/interactions",
                    body=b"{}",
                )

        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(
            caught.exception.reason_code,
            "unsupported_location",
        )
        self.assertIn("location is not supported", str(caught.exception))
        self.assertNotIn("FAILED_PRECONDITION", str(caught.exception))

    def test_provider_request_retryability_classifies_statuses(self):
        self.assertFalse(ProviderRequestError(
            "Provider HTTP 400: request rejected",
            status_code=400,
        ).retryable)
        self.assertTrue(ProviderRequestError(
            "Provider HTTP 429: request rejected",
            status_code=429,
        ).retryable)
        self.assertTrue(ProviderRequestError(
            "Provider HTTP 503: request rejected",
            status_code=503,
        ).retryable)
        self.assertTrue(ProviderRequestError(
            "Provider connection failed: TimeoutError",
        ).retryable)

    def test_playwright_prefers_formal_ceac_page_over_landing_page(self):
        class FakePage:
            def __init__(self, url, form_control_count=0):
                self.url = url
                self.form_control_count = form_control_count
                self.front_count = 0

            def is_closed(self):
                return False

            def bring_to_front(self):
                self.front_count += 1

        class FakeContext:
            def __init__(self, pages):
                self.pages = pages

        driver = PlaywrightBrowserDriver(provider("playwright"))
        landing = FakePage(
            "https://ceac.state.gov/GenNIV/Default.aspx"
        )
        formal = FakePage(
            "https://ceac.state.gov/GenNIV/General/complete/"
            "complete_personal.aspx?node=Personal1",
            form_control_count=3,
        )
        driver._page = landing
        driver._context = FakeContext([landing, formal])
        driver._field_selectors["old"] = "#old"
        driver._target_selectors["old"] = "#old"
        driver._verified_field_values["old"] = "old"

        driver._select_best_page()

        self.assertIs(driver._page, formal)
        self.assertEqual(formal.front_count, 1)
        self.assertEqual(driver._field_selectors, {})
        self.assertEqual(driver._target_selectors, {})
        self.assertEqual(driver._verified_field_values, {})
        driver._temporary.cleanup()

    def test_playwright_restored_tabs_follow_physical_ds160_order(self):
        class FakePage:
            def __init__(self, url, form_control_count=0):
                self.url = url
                self.form_control_count = form_control_count
                self.front_count = 0

            def is_closed(self):
                return False

            def bring_to_front(self):
                self.front_count += 1

        class FakeContext:
            def __init__(self, pages):
                self.pages = pages

        address = FakePage(
            "https://ceac.state.gov/GenNIV/General/complete/"
            "complete_contact.aspx?node=AddressPhone",
            form_control_count=3,
        )
        passport = FakePage(
            "https://ceac.state.gov/GenNIV/General/complete/"
            "complete_pptvisa.aspx?node=PptVisa",
            form_control_count=3,
        )
        travel = FakePage(
            "https://ceac.state.gov/GenNIV/General/complete/"
            "complete_travel.aspx?node=Travel",
            form_control_count=3,
        )
        previous = FakePage(
            "https://ceac.state.gov/GenNIV/General/complete/"
            "complete_previousustravel.aspx?node=PreviousUSTravel",
            form_control_count=3,
        )
        driver = PlaywrightBrowserDriver(provider("playwright"))
        driver._page = address
        driver._context = FakeContext([
            address,
            passport,
            travel,
            previous,
        ])

        driver._select_best_page()

        self.assertIs(driver._page, passport)
        self.assertEqual(passport.front_count, 1)
        self.assertEqual(previous.front_count, 0)
        driver._temporary.cleanup()

    def test_playwright_restored_review_beats_every_complete_tab(self):
        class FakePage:
            def __init__(self, url):
                self.url = url
                self.front_count = 0

            def is_closed(self):
                return False

            def bring_to_front(self):
                self.front_count += 1

        class FakeContext:
            def __init__(self, pages):
                self.pages = pages

        passport = FakePage(
            "https://ceac.state.gov/GenNIV/General/complete/"
            "complete_pptvisa.aspx?node=PptVisa"
        )
        review = FakePage(
            "https://ceac.state.gov/GenNIV/General/Review/"
            "review_review.aspx?node=Review"
        )
        driver = PlaywrightBrowserDriver(provider("playwright"))
        driver._page = passport
        driver._context = FakeContext([passport, review])

        driver._select_best_page()

        self.assertIs(driver._page, review)
        self.assertEqual(review.front_count, 1)
        driver._temporary.cleanup()

    def test_playwright_classifies_live_conditional_field_presence(self):
        class FakePage:
            def evaluate(self, _script, args, timeout=None):
                self.timeout = timeout
                return {
                    "hintMatch": (
                        "UNIQUE_VISIBLE_CONTROL"
                        in (args.get("hints") or ())
                    ),
                    "labelMatch": any(
                        label == "Ambiguous branch question"
                        for label in args.get("labels") or ()
                    ),
                }

        driver = PlaywrightBrowserDriver(provider("playwright"))
        driver._page = FakePage()
        result = driver.classify_field_presence(
            ["visible.field", "ambiguous.field", "absent.field"],
            {
                "visible.field": ("Visible field",),
                "ambiguous.field": ("Ambiguous branch question",),
                "absent.field": ("Hidden conditional detail",),
            },
            {"visible.field": ("UNIQUE_VISIBLE_CONTROL",)},
        )
        driver._page = None
        driver._temporary.cleanup()

        self.assertEqual(result["present"], ["visible.field"])
        self.assertEqual(result["unresolved"], ["ambiguous.field"])
        self.assertEqual(result["absent"], ["absent.field"])

    def test_playwright_presence_ignores_generic_hint_collision_real_dom(
        self,
    ):
        driver = PlaywrightBrowserDriver(
            provider("playwright", "chromium-headless")
        )
        try:
            try:
                driver.start("about:blank")
            except ProviderNotConfigured as error:
                self.skipTest(str(error))
            driver._page.set_content(
                """
                <form>
                  <label for="payer">Person/Entity Paying for Your Trip</label>
                  <select id="ctl00_PAYING_FOR_TRIP" name="payer">
                    <option>PRESENT EMPLOYER</option>
                  </select>
                  <div>Have you made specific travel plans?</div>
                </form>
                """
            )
            result = driver.classify_field_presence(
                [
                    "travel.payer",
                    "travel.payer.hidden_address",
                    "travel.visible_question",
                ],
                {
                    "travel.payer": (
                        "Person/Entity Paying for Your Trip",
                    ),
                    "travel.payer.hidden_address": (
                        "Paying Party Street Address (Line 1)",
                    ),
                    "travel.visible_question": (
                        "Have you made specific travel plans?",
                    ),
                },
                {
                    "travel.payer": ("PAYING_FOR_TRIP",),
                    "travel.payer.hidden_address": (
                        "PAYER",
                        "ADDRESS",
                    ),
                },
            )

            self.assertIn("travel.payer", result["present"])
            self.assertIn(
                "travel.visible_question",
                result["unresolved"],
            )
            self.assertIn(
                "travel.payer.hidden_address",
                result["absent"],
            )
        finally:
            driver.close()

    def test_playwright_scroll_geometry_rejects_real_page_edge_noop(self):
        driver = PlaywrightBrowserDriver(
            provider("playwright", "chromium-headless")
        )
        verifier = DeterministicActionVerifier()
        try:
            try:
                driver.start("about:blank")
            except ProviderNotConfigured as error:
                self.skipTest(str(error))
            driver._page.set_content(
                "<main style='height:3000px'>Tall form</main>"
            )
            before = driver.observe_lightweight()
            moved_action = ComputerAction(
                kind=ActionKind.SCROLL,
                scroll_direction="down",
                scroll_amount=600,
            )
            driver.execute(moved_action)
            driver._page.wait_for_timeout(100)
            moved = driver.observe_lightweight()

            self.assertGreater(moved.scroll_y, before.scroll_y)
            self.assertTrue(
                verifier.verify(moved_action, before, moved).verified
            )

            driver._page.evaluate(
                "() => window.scrollTo(0, document.body.scrollHeight)"
            )
            at_bottom = driver.observe_lightweight()
            noop_action = ComputerAction(
                kind=ActionKind.SCROLL,
                scroll_direction="down",
                scroll_amount=600,
            )
            driver.execute(noop_action)
            driver._page.wait_for_timeout(100)
            still_at_bottom = driver.observe_lightweight()

            self.assertEqual(
                still_at_bottom.scroll_y,
                at_bottom.scroll_y,
            )
            self.assertFalse(
                verifier.verify(
                    noop_action,
                    at_bottom,
                    still_at_bottom,
                ).verified
            )
        finally:
            driver.close()


    def test_playwright_structured_value_helpers(self):
        self.assertEqual(
            PlaywrightBrowserDriver._parse_iso_date("1977-06-19"),
            (1977, 6, 19),
        )
        self.assertIsNone(
            PlaywrightBrowserDriver._parse_iso_date("19-JUN-1977")
        )
        self.assertTrue(
            PlaywrightBrowserDriver._choice_matches("否 / No", "NO N")
        )
        self.assertTrue(
            PlaywrightBrowserDriver._choice_matches(
                "商业 / Business",
                "BUSINESS",
            )
        )
        self.assertTrue(
            PlaywrightBrowserDriver._choice_matches(
                "TEMP. BUSINESS PLEASURE VISITOR (B)",
                "TEMP. BUSINESS OR PLEASURE VISITOR (B) B",
            )
        )
        self.assertFalse(
            PlaywrightBrowserDriver._choice_matches(
                "TEMP. BUSINESS PLEASURE VISITOR (B)",
                "STUDENT (F) F",
            )
        )
        self.assertFalse(
            PlaywrightBrowserDriver._choice_matches("是 / Yes", "NO N")
        )
        self.assertEqual(
            PlaywrightBrowserDriver._month_option({
                "text": "JUN", "value": "06"
            }),
            6,
        )
        self.assertEqual(
            PlaywrightBrowserDriver._parse_duration("12 MONTHS"),
            ("12", "MONTH"),
        )
        self.assertIsNone(
            PlaywrightBrowserDriver._parse_duration("DOES NOT APPLY DAY")
        )
        self.assertEqual(
            PlaywrightBrowserDriver._control_kind((
                "Contact Person",
                "Contact Person [control=does_not_apply; "
                "human-approved value=true]",
            )),
            "does_not_apply",
        )
        self.assertEqual(
            PlaywrightBrowserDriver._control_occurrence((
                "Former Employer",
                "Former Employer [control=text; occurrence=2; "
                "human-approved value=EXAMPLE]",
            )),
            (2, True),
        )
        self.assertEqual(
            PlaywrightBrowserDriver._control_occurrence(("Employer",)),
            (None, True),
        )
        self.assertEqual(
            PlaywrightBrowserDriver._control_occurrence((
                "Employer [control=text; occurrence=0]",
            )),
            (None, False),
        )
        self.assertEqual(
            PlaywrightBrowserDriver._control_occurrence((
                "Employer [control=text; occurrence=1]",
                "Employer [control=text; occurrence=2]",
            )),
            (None, False),
        )
        self.assertEqual(
            PlaywrightBrowserDriver._numeric_segment_value("123-45-6789"),
            "123456789",
        )
        self.assertIsNone(
            PlaywrightBrowserDriver._numeric_segment_value("123-AB-6789")
        )

    def test_playwright_constrains_text_before_any_dom_mutation(self):
        class FakeLocator:
            @property
            def first(self):
                return self

            def count(self):
                return 1

            def evaluate(self, _script):
                return {
                    "tag": "input",
                    "type": "text",
                    "hasMaxLength": True,
                    "maxLength": 40,
                    "composite": False,
                }

            def fill(self, _value):
                raise AssertionError("constraint preflight must not fill")

            def click(self):
                raise AssertionError("constraint preflight must not click")

        class FakePage:
            def __init__(self):
                self.control = FakeLocator()

            def locator(self, _selector):
                return self.control

        driver = PlaywrightBrowserDriver(provider("playwright"))
        driver._page = FakePage()
        driver._field_selectors["contact.usStreet1"] = "#address"
        driver._semantic_field_bindings.add("contact.usStreet1")
        action = ComputerAction(
            kind=ActionKind.TYPE,
            field_id="contact.usStreet1",
            value="X" * 52,
        )

        constraint = driver.constrain_action_value(action)

        self.assertEqual(action.value, "X" * 40)
        self.assertEqual(constraint["originalLength"], 52)
        self.assertEqual(constraint["maxLength"], 40)
        driver._page = None
        driver._temporary.cleanup()

    def test_playwright_normalizes_us_contact_phone_before_dom_mutation(self):
        class FakeLocator:
            @property
            def first(self):
                return self

            def count(self):
                return 1

            def evaluate(self, _script):
                return {
                    "tag": "input",
                    "type": "text",
                    "hasMaxLength": True,
                    "maxLength": 15,
                    "composite": False,
                }

            def fill(self, _value):
                raise AssertionError("constraint preflight must not fill")

            def click(self):
                raise AssertionError("constraint preflight must not click")

        class FakePage:
            def __init__(self):
                self.control = FakeLocator()

            def locator(self, _selector):
                return self.control

        field_id = "ceac.us_contact.002.us_contact.phone"
        driver = PlaywrightBrowserDriver(provider("playwright"))
        driver._page = FakePage()
        driver._field_selectors[field_id] = "#US_CONTACT_PHONE"
        driver._semantic_field_bindings.add(field_id)
        action = ComputerAction(
            kind=ActionKind.TYPE,
            field_id=field_id,
            value="+1 (202) 555-0142",
        )

        constraint = driver.constrain_action_value(action)

        self.assertEqual(action.value, "12025550142")
        self.assertEqual(constraint["normalization"], "phone-digits-only")
        self.assertEqual(constraint["effectiveLength"], 11)
        driver._page = None
        driver._temporary.cleanup()

    def test_playwright_constraint_preflight_separates_binding_from_value(self):
        class MissingLocator:
            @property
            def first(self):
                return self

            def count(self):
                return 0

        class ConstrainedLocator(MissingLocator):
            def count(self):
                return 1

            def evaluate(self, _script):
                return {
                    "tag": "input",
                    "type": "text",
                    "hasMaxLength": True,
                    "maxLength": 0,
                    "composite": False,
                }

        class FakePage:
            def __init__(self, locator):
                self.control = locator

            def locator(self, _selector):
                return self.control

        driver = PlaywrightBrowserDriver(provider("playwright"))
        action = ComputerAction(
            kind=ActionKind.TYPE,
            field_id="contact.usStreet1",
            value="APPROVED",
        )
        driver._field_selectors[action.field_id] = "#address"
        driver._semantic_field_bindings.add(action.field_id)

        driver._page = FakePage(MissingLocator())
        with self.assertRaises(ControlBindingUnavailable):
            driver.constrain_action_value(action)

        driver._page = FakePage(ConstrainedLocator())
        with self.assertRaises(ControlValueConstraintError):
            driver.constrain_action_value(action)

        self.assertEqual(action.value, "APPROVED")
        driver._page = None
        driver._temporary.cleanup()

    def test_playwright_batch_never_assigns_two_fields_to_one_control(self):
        driver = PlaywrightBrowserDriver(
            provider("playwright", "chromium-headless")
        )
        try:
            try:
                driver.start("about:blank")
            except ProviderNotConfigured as error:
                self.skipTest(str(error))
            driver._page.set_content(
                """
                <label for="shared-control">Shared Address</label>
                <input id="shared-control" name="shared-control"
                       maxlength="80">
                """
            )
            first = "ceac.travel.record.address"
            duplicate = "ceac.travel.record.city"
            labels = {
                first: (
                    "Shared Address [control=text; "
                    "human-approved value=FIRST]",
                ),
                duplicate: (
                    "Shared Address [control=text; "
                    "human-approved value=SECOND]",
                ),
            }
            hints = {
                first: ("shared-control",),
                duplicate: ("shared-control",),
            }

            actions, unresolved = driver.plan_fields(
                [first, duplicate],
                labels,
                hints,
            )

            self.assertEqual(
                [action.field_id for action in actions],
                [first],
            )
            self.assertEqual(unresolved, [duplicate])
            first_selector = driver._field_selectors[first]
            self.assertEqual(driver._page.locator(first_selector).count(), 1)
            self.assertNotIn(duplicate, driver._field_selectors)
            self.assertEqual(
                driver._page.locator("#shared-control").get_attribute(
                    "data-docflow-field-owner"
                ),
                first,
            )
            actions[0].value = "FIRST"
            self.assertIsNone(driver.constrain_action_value(actions[0]))
        finally:
            driver.close()

    def test_playwright_hint_ranking_separates_us_and_payer_city(self):
        driver = PlaywrightBrowserDriver(
            provider("playwright", "chromium-headless")
        )
        try:
            try:
                driver.start("about:blank")
            except ProviderNotConfigured as error:
                self.skipTest(str(error))
            driver._page.set_content(
                """
                <div style="margin:20px">
                  <label for="ADDR_US_CITY">U.S. Contact City</label>
                  <input id="ADDR_US_CITY" name="ADDR_US_CITY">
                </div>
                <div style="margin:20px">
                  <label for="PAYER_ADDRESS_CITY">Payer City</label>
                  <input id="PAYER_ADDRESS_CITY"
                         name="PAYER_ADDRESS_CITY">
                </div>
                """
            )
            us_city = "ceac.us_contact.address.city"
            payer_city = "ceac.travel.payeraddress.record.city"
            labels = {
                us_city: (
                    "U.S. Contact City [control=text; occurrence=1; "
                    "human-approved value=NEW YORK]",
                ),
                payer_city: (
                    "Payer City [control=text; occurrence=1; "
                    "human-approved value=SHENZHEN]",
                ),
            }
            hints = {
                us_city: ("ADDR_US_CITY", "US_CITY"),
                payer_city: ("PAYER", "ADDRESS", "CITY"),
            }

            actions, unresolved = driver.plan_fields(
                [us_city, payer_city],
                labels,
                hints,
            )

            self.assertEqual(unresolved, [])
            self.assertEqual(
                [action.field_id for action in actions],
                [us_city, payer_city],
            )
            self.assertEqual(
                driver._page.locator("#ADDR_US_CITY").get_attribute(
                    "data-docflow-field-owner"
                ),
                us_city,
            )
            self.assertEqual(
                driver._page.locator("#PAYER_ADDRESS_CITY").get_attribute(
                    "data-docflow-field-owner"
                ),
                payer_city,
            )
            self.assertNotEqual(
                driver._field_selectors[us_city],
                driver._field_selectors[payer_city],
            )
        finally:
            driver.close()

    def test_playwright_semantic_binding_outranks_wrong_model_coordinate(self):
        class FakeLocator:
            @property
            def first(self):
                return self

            def count(self):
                return 1

        class FakePage:
            def locator(self, _selector):
                return FakeLocator()

            def evaluate(self, *_args):
                raise AssertionError(
                    "a verified semantic binding must not consult coordinates"
                )

        driver = PlaywrightBrowserDriver(provider("playwright"))
        driver._page = FakePage()
        driver._field_selectors["personal.surname"] = "#surname"
        driver._semantic_field_bindings.add("personal.surname")
        action = ComputerAction(
            kind=ActionKind.TYPE,
            field_id="personal.surname",
            coordinate_x=999,
            coordinate_y=999,
        )

        self.assertTrue(driver.bind_visual_field(
            action,
            labels=("Surnames",),
            hints=("SURNAME",),
        ))
        driver._page = None
        driver._temporary.cleanup()

    def test_playwright_occurrence_selects_visual_order_and_rejects_ambiguity(self):
        class FakeItem:
            def __init__(self, name, top, left=40):
                self.name = name
                self.rect = {
                    "top": top,
                    "left": left,
                    "width": 180,
                    "height": 24,
                }

            def evaluate(self, _script):
                return {
                    "tag": "input",
                    "type": "text",
                    "disabled": False,
                    "readOnly": False,
                    "rect": self.rect,
                }

            def is_visible(self):
                return True

        class FakeLocator:
            def __init__(self, items):
                self.items = items

            def count(self):
                return len(self.items)

            def nth(self, index):
                return self.items[index]

        bottom = FakeItem("bottom", 300)
        top = FakeItem("top", 100)
        middle = FakeItem("middle", 200)
        locator = FakeLocator([bottom, top, middle])

        self.assertIs(
            PlaywrightBrowserDriver._unique_actionable_control(
                locator,
                occurrence=2,
            ),
            middle,
        )
        self.assertIsNone(
            PlaywrightBrowserDriver._unique_actionable_control(locator)
        )
        self.assertIsNone(
            PlaywrightBrowserDriver._unique_actionable_control(
                locator,
                occurrence=4,
            )
        )
        overlapping = FakeLocator([
            FakeItem("first", 100, 40),
            FakeItem("second", 100, 40),
        ])
        self.assertIsNone(
            PlaywrightBrowserDriver._unique_actionable_control(
                overlapping,
                occurrence=1,
            )
        )
        composite = FakeLocator([
            FakeItem("row-one-day", 100, 40),
            FakeItem("row-one-month", 100, 90),
            FakeItem("row-one-year", 100, 140),
            FakeItem("row-two-day", 200, 40),
            FakeItem("row-two-month", 200, 90),
            FakeItem("row-two-year", 200, 140),
        ])
        self.assertEqual(
            PlaywrightBrowserDriver._unique_actionable_control(
                composite,
                occurrence=2,
                group_visual_rows=True,
            ).name,
            "row-two-day",
        )

    def test_playwright_occurrence_resolves_real_duplicate_dom_controls(self):
        driver = PlaywrightBrowserDriver(
            provider("playwright", "chromium-headless")
        )
        try:
            try:
                driver.start("about:blank")
            except ProviderNotConfigured as error:
                self.skipTest(str(error))
            driver._page.set_content(
                """
                <style>
                  .stage { position: relative; height: 370px; width: 900px; }
                  .hint, .labelled, .legacy {
                    position: absolute; left: 20px; height: 30px;
                  }
                  .hint { left: 20px; }
                  .labelled { left: 300px; }
                  .legacy { left: 600px; width: 260px; }
                  .legacy span { display: inline-block; width: 105px; }
                  input { width: 130px; height: 22px; }
                </style>
                <div class="stage">
                  <input class="hint" id="hint-third-OCCUR"
                         style="top:260px" value="hint-third">
                  <input class="hint" id="hint-first-OCCUR"
                         style="top:60px" value="hint-first">
                  <input class="hint" id="hint-second-OCCUR"
                         style="top:160px" value="hint-second">

                  <div class="labelled" style="top:260px">
                    <label for="email-third">Repeated Email</label>
                    <input id="email-third" value="email-third">
                  </div>
                  <div class="labelled" style="top:60px">
                    <label for="email-first">Repeated Email</label>
                    <input id="email-first" value="email-first">
                  </div>
                  <div class="labelled" style="top:160px">
                    <label for="email-second">Repeated Email</label>
                    <input id="email-second" value="email-second">
                  </div>

                  <div class="legacy" style="top:260px">
                    <span>Legacy Field</span>
                    <input id="legacy-third" value="legacy-third">
                  </div>
                  <div class="legacy" style="top:60px">
                    <span>Legacy Field</span>
                    <input id="legacy-first" value="legacy-first">
                  </div>
                  <div class="legacy" style="top:160px">
                    <span>Legacy Field</span>
                    <input id="legacy-second" value="legacy-second">
                  </div>
                </div>
                """
            )

            hinted = driver._deterministic_control(
                "ceac.repeat.001.hint",
                ("Hint [control=text; occurrence=2]",),
                ("OCCUR",),
            )
            self.assertIsNotNone(hinted)
            self.assertEqual(hinted.get_attribute("id"), "hint-second-OCCUR")

            labelled = driver._deterministic_control(
                "ceac.repeat.002.email",
                ("Repeated Email [control=text; occurrence=2]",),
                (),
            )
            self.assertIsNotNone(labelled)
            self.assertEqual(labelled.get_attribute("id"), "email-second")

            legacy = driver._deterministic_control(
                "ceac.repeat.003.legacy",
                ("Legacy Field [control=text; occurrence=2]",),
                (),
            )
            self.assertIsNotNone(legacy)
            self.assertEqual(legacy.get_attribute("id"), "legacy-second")

            self.assertIsNone(driver._deterministic_control(
                "ceac.repeat.004.out-of-range",
                ("Repeated Email [control=text; occurrence=4]",),
                (),
            ))
        finally:
            driver.close()

    def test_playwright_next_dispatch_receipt_and_observation_pruning(self):
        driver = PlaywrightBrowserDriver(
            provider("playwright", "chromium-headless")
        )
        ceac_url = (
            "https://ceac.state.gov/GenNIV/General/complete/"
            "complete_personal.aspx?node=Personal1"
        )
        try:
            try:
                driver.start("about:blank")
            except ProviderNotConfigured as error:
                self.skipTest(str(error))
            driver._page.route(
                "https://ceac.state.gov/**",
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/html",
                    body="""
                      <html>
                        <head><title>Personal Information 1</title></head>
                        <body>
                          <input id="live" value="XIA"
                                 data-docflow-field="live">
                          <button id="next" type="button"
                            onclick="document.body.dataset.clicked='yes'">
                            Next: Personal 2
                          </button>
                        </body>
                      </html>
                    """,
                ),
            )
            driver._page.goto(ceac_url, wait_until="domcontentloaded")

            driver._field_selectors = {
                "stale": '[data-docflow-field="stale"]',
                "live": '[data-docflow-field="live"]',
            }
            lightweight = driver.observe_lightweight()
            self.assertNotIn("stale", driver._field_selectors)
            self.assertEqual(lightweight.control_values["live"], "XIA")

            driver._field_selectors["stale-again"] = (
                '[data-docflow-field="stale-again"]'
            )
            full = driver.observe()
            self.assertNotIn("stale-again", driver._field_selectors)
            self.assertEqual(full.control_values["live"], "XIA")

            action = driver.plan_next()
            self.assertIsNotNone(action)
            self.assertTrue(action.dispatch_receipt_required)
            self.assertTrue(action.dispatch_receipt_scope)
            action.id = "action-next-dispatch-proof"
            prepared = driver.observe_lightweight()
            self.assertTrue(prepared.dispatch_receipts_authoritative)
            self.assertEqual(
                prepared.dispatch_receipt_scope,
                action.dispatch_receipt_scope,
            )
            self.assertNotIn(
                action.id,
                prepared.dispatched_action_ids,
            )

            driver.execute(action)

            dispatched = driver.observe_lightweight()
            self.assertEqual(
                driver._page.locator("body").get_attribute("data-clicked"),
                "yes",
            )
            self.assertTrue(dispatched.dispatch_receipts_authoritative)
            self.assertEqual(
                dispatched.dispatch_receipt_scope,
                action.dispatch_receipt_scope,
            )
            self.assertIn(
                action.id,
                dispatched.dispatched_action_ids,
            )
        finally:
            driver.close()

    def test_persistent_profile_reopens_with_next_dispatch_receipt(self):
        ceac_url = (
            "https://ceac.state.gov/GenNIV/General/complete/"
            "complete_personal.aspx?node=Personal1"
        )
        body = """
          <html>
            <head><title>Personal Information 1</title></head>
            <body>
              <button id="next" type="button">Next: Personal 2</button>
            </body>
          </html>
        """
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "persistent-receipt-profile"
            first = PlaywrightBrowserDriver(
                provider("playwright", "chromium-headless")
            )
            first.set_profile_dir(profile)
            action = None
            try:
                try:
                    first.start("about:blank")
                except ProviderNotConfigured as error:
                    self.skipTest(str(error))
                first._page.route(
                    "https://ceac.state.gov/**",
                    lambda route: route.fulfill(
                        status=200,
                        content_type="text/html",
                        body=body,
                    ),
                )
                first._page.goto(ceac_url, wait_until="domcontentloaded")
                action = first.plan_next()
                self.assertIsNotNone(action)
                action.id = "action-persistent-next-receipt"
                first.execute(action)
                dispatched = first.observe_lightweight()
                self.assertIn(
                    action.id,
                    dispatched.dispatched_action_ids,
                )
            finally:
                first.close()

            second = PlaywrightBrowserDriver(
                provider("playwright", "chromium-headless")
            )
            second.set_profile_dir(profile)
            try:
                try:
                    second.start("about:blank")
                except ProviderNotConfigured as error:
                    self.skipTest(str(error))
                second._context.route(
                    "https://ceac.state.gov/**",
                    lambda route: route.fulfill(
                        status=200,
                        content_type="text/html",
                        body=body,
                    ),
                )
                second._page.goto(ceac_url, wait_until="domcontentloaded")
                restored = second.observe_lightweight()

                self.assertTrue(restored.dispatch_receipts_authoritative)
                self.assertEqual(
                    restored.dispatch_receipt_scope,
                    action.dispatch_receipt_scope,
                )
                self.assertIn(
                    action.id,
                    restored.dispatched_action_ids,
                )
                self.assertFalse(
                    ComputerUseAgent
                    ._pending_next_authoritatively_not_dispatched(
                        action,
                        restored,
                    )
                )
                self.assertTrue(
                    ComputerUseAgent
                    ._pending_next_authoritatively_dispatched(
                        action,
                        restored,
                    )
                )
            finally:
                second.close()

    def test_playwright_divergent_dispatch_ledgers_are_non_authoritative(self):
        driver = PlaywrightBrowserDriver(
            provider("playwright", "chromium-headless")
        )
        ceac_url = (
            "https://ceac.state.gov/GenNIV/General/complete/"
            "complete_personal.aspx?node=Personal1"
        )
        try:
            try:
                driver.start("about:blank")
            except ProviderNotConfigured as error:
                self.skipTest(str(error))
            driver._page.route(
                "https://ceac.state.gov/**",
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/html",
                    body=(
                        "<html><head><title>Personal Information 1</title>"
                        "</head><body><button id='next' type='button' "
                        "onclick=\"document.body.dataset.clicked='yes'\">"
                        "Next: Personal 2</button></body></html>"
                    ),
                ),
            )
            driver._page.goto(ceac_url, wait_until="domcontentloaded")
            driver._page.evaluate(
                """([key]) => {
                    sessionStorage.setItem(key, JSON.stringify({
                        scope: 'shared-scope',
                        ids: ['session-action']
                    }));
                    localStorage.setItem(key, JSON.stringify({
                        scope: 'shared-scope',
                        ids: ['durable-action']
                    }));
                }""",
                [driver.DISPATCH_LEDGER_KEY],
            )

            observed = driver.observe_lightweight()

            self.assertTrue(observed.dispatch_receipt_conflict)
            self.assertFalse(observed.dispatch_receipts_authoritative)
            self.assertEqual(observed.dispatch_receipt_scope, "")
            self.assertEqual(observed.dispatched_action_ids, [])
            with self.assertRaises(NextDispatchReceiptUnavailable):
                driver.plan_next()
            self.assertIsNone(
                driver._page.locator("body").get_attribute("data-clicked")
            )
        finally:
            driver.close()

    def test_playwright_choice_planner_matches_pending_subset_by_descriptor(self):
        driver = PlaywrightBrowserDriver(
            provider("playwright", "chromium-headless")
        )
        try:
            try:
                driver.start("about:blank")
            except ProviderNotConfigured as error:
                self.skipTest(str(error))
            driver._page.set_content(
                """
                <style>
                  .question {
                    margin: 24px; padding: 12px; width: 720px;
                    border: 1px solid #ddd;
                  }
                  .prompt { margin-bottom: 10px; }
                  .answers { display: flex; gap: 12px; }
                </style>
                <div class="question">
                  <div class="prompt">Have you ever been in the U.S.?</div>
                  <div class="answers">
                    <input id="visited-y" type="radio"
                           name="ctl00$PREV_US_TRAVEL_IND" value="Y"
                           checked><label for="visited-y">Yes</label>
                    <input id="visited-n" type="radio"
                           name="ctl00$PREV_US_TRAVEL_IND" value="N">
                    <label for="visited-n">No</label>
                  </div>
                </div>
                <div class="question">
                  <div class="prompt">
                    Have you ever been issued a U.S. Visa?
                  </div>
                  <div class="answers">
                    <input id="visa-y" type="radio"
                           name="ctl00$PREV_VISA_IND" value="Y">
                    <label for="visa-y">Yes</label>
                    <input id="visa-n" type="radio"
                           name="ctl00$PREV_VISA_IND" value="N">
                    <label for="visa-n">No</label>
                  </div>
                </div>
                <div class="question">
                  <div class="prompt">
                    Has anyone ever filed an immigrant petition on your
                    behalf?
                  </div>
                  <div class="answers">
                    <input id="petition-y" type="radio"
                           name="ctl00$IV_PETITION_IND" value="Y">
                    <label for="petition-y">Yes</label>
                    <input id="petition-n" type="radio"
                           name="ctl00$IV_PETITION_IND" value="N">
                    <label for="petition-n">No</label>
                  </div>
                </div>
                """
            )
            visa_field = "ceac.previous.002.us_history.previous_visa"
            petition_field = (
                "ceac.previous.005.us_history.immigrant_petition"
            )

            # The first group is already completed and remains visible. The
            # pending fields are deliberately supplied in reverse visual order
            # so a positional zip would select the wrong controls.
            actions, unresolved = driver.plan_choice_fields(
                [petition_field, visa_field],
                {
                    visa_field: (
                        "Have you ever been issued a U.S. Visa? "
                        "[control=yes_no; human-approved value=no]",
                    ),
                    petition_field: (
                        "Immigrant Petition "
                        "[control=yes_no; human-approved value=yes]",
                    ),
                },
                {
                    petition_field: ("IV_PETITION_IND",),
                    visa_field: (),
                },
            )

            self.assertEqual(unresolved, [])
            self.assertEqual(
                [action.field_id for action in actions],
                [petition_field, visa_field],
            )
            self.assertEqual(
                driver._page.locator(
                    driver._field_selectors[petition_field]
                ).get_attribute("name"),
                "ctl00$IV_PETITION_IND",
            )
            self.assertEqual(
                driver._page.locator(
                    driver._field_selectors[visa_field]
                ).get_attribute("name"),
                "ctl00$PREV_VISA_IND",
            )

            actions[0].value = "yes"
            actions[1].value = "no"
            driver.execute(actions[0])
            driver.execute(actions[1])
            self.assertTrue(
                driver._page.locator("#petition-y").is_checked()
            )
            self.assertTrue(driver._page.locator("#visa-n").is_checked())
            self.assertTrue(
                driver._page.locator("#visited-y").is_checked()
            )
        finally:
            driver.close()

    def test_playwright_choice_planner_rejects_ambiguous_question_groups(self):
        driver = PlaywrightBrowserDriver(
            provider("playwright", "chromium-headless")
        )
        try:
            try:
                driver.start("about:blank")
            except ProviderNotConfigured as error:
                self.skipTest(str(error))
            driver._page.set_content(
                """
                <div style="margin:20px">
                  <div>Repeated eligibility question</div>
                  <input type="radio" name="group-one" value="Y"> Yes
                  <input type="radio" name="group-one" value="N"> No
                </div>
                <div style="margin:20px">
                  <div>Repeated eligibility question</div>
                  <input type="radio" name="group-two" value="Y"> Yes
                  <input type="radio" name="group-two" value="N"> No
                </div>
                """
            )
            field_id = "ceac.security.ambiguous"
            actions, unresolved = driver.plan_choice_fields(
                [field_id],
                {
                    field_id: (
                        "Repeated eligibility question "
                        "[control=yes_no; human-approved value=no]",
                    ),
                },
                {field_id: ()},
            )
            self.assertEqual(actions, [])
            self.assertEqual(unresolved, [field_id])
        finally:
            driver.close()

    def test_visual_binding_resolves_multiple_yes_no_radio_groups(self):
        driver = PlaywrightBrowserDriver(
            provider("playwright", "chromium-headless")
        )
        try:
            try:
                driver.start("about:blank")
            except ProviderNotConfigured as error:
                self.skipTest(str(error))
            driver._page.set_content(
                """
                <label for="native-name">Full Name in Native Alphabet</label>
                <input id="native-name" value="夏意程">
                <section class="question">
                  <div>Have you ever used other names?</div>
                  <input id="other-y" type="radio"
                         name="ctl00$OtherNames" value="Y">
                  <label for="other-y">Yes</label>
                  <input id="other-n" type="radio"
                         name="ctl00$OtherNames" value="N">
                  <label for="other-n">No</label>
                </section>
                <section class="question">
                  <div>Do you have a telecode that represents your name?</div>
                  <input id="telecode-y" type="radio"
                         name="ctl00$TelecodeQuestion" value="Y">
                  <label for="telecode-y">Yes</label>
                  <input id="telecode-n" type="radio"
                         name="ctl00$TelecodeQuestion" value="N">
                  <label for="telecode-n">No</label>
                </section>
                """
            )
            cases = (
                (
                    "ceac.personal1.personal.other_names",
                    "Have you ever used other names?",
                    "OtherNames",
                    "#other-n",
                ),
                (
                    "ceac.personal1.personal.telecode",
                    "Do you have a telecode that represents your name?",
                    "TelecodeQuestion",
                    "#telecode-n",
                ),
            )
            for field_id, label, hint, expected in cases:
                action = ComputerAction(
                    kind=ActionKind.SELECT,
                    field_id=field_id,
                    coordinate_x=1,
                    coordinate_y=1,
                    reason="Gemini screenshot page batch",
                )
                labels = (
                    f"{label} [control=yes_no; "
                    "refresh_after_change=true; human-approved value=no]",
                )
                self.assertTrue(driver.bind_visual_field(
                    action,
                    labels=labels,
                    hints=(hint,),
                ))
                action.value = "no"
                driver.execute(action)
                self.assertTrue(driver._page.locator(expected).is_checked())
        finally:
            driver.close()

    def test_playwright_choice_planner_rebinds_after_radio_postback(self):
        driver = PlaywrightBrowserDriver(
            provider("playwright", "chromium-headless")
        )
        try:
            try:
                driver.start("about:blank")
            except ProviderNotConfigured as error:
                self.skipTest(str(error))
            driver._page.set_content(
                """
                <div id="host" style="margin:20px">
                  <div>Have you used another name?</div>
                  <input id="other-y" type="radio"
                         name="ctl00$OTHER_NAMES" value="Y">
                  <label for="other-y">Yes</label>
                  <input id="other-n" type="radio"
                         name="ctl00$OTHER_NAMES" value="N">
                  <label for="other-n">No</label>
                </div>
                <script>
                  document.querySelectorAll(
                    'input[name="ctl00$OTHER_NAMES"]'
                  ).forEach(item => item.addEventListener('change', () => {
                    setTimeout(() => {
                      document.getElementById('host').innerHTML = `
                        <div>Have you used another name?</div>
                        <input id="other-y-new" type="radio"
                               name="ctl00$OTHER_NAMES" value="Y">
                        <label for="other-y-new">Yes</label>
                        <input id="other-n-new" type="radio"
                               name="ctl00$OTHER_NAMES" value="N" checked>
                        <label for="other-n-new">No</label>
                      `;
                    }, 0);
                  }));
                </script>
                """
            )
            field_id = "ceac.personal1.009.personal.other_names"
            labels = (
                "Have you used another name? "
                "[control=yes_no; refresh_after_change=true; "
                "human-approved value=no]",
            )
            hints = ("OTHER_NAMES",)
            actions, unresolved = driver.plan_choice_fields(
                [field_id],
                {field_id: labels},
                {field_id: hints},
            )
            self.assertEqual(unresolved, [])
            actions[0].value = "no"

            started = time.monotonic()
            driver.execute(actions[0])
            self.assertLess(time.monotonic() - started, 2.0)
            self.assertTrue(driver.settle_after_dynamic_refresh(
                field_id,
                labels,
                hints,
            ))
            selector = driver._field_selectors[field_id]
            self.assertEqual(
                driver._live_control_value(field_id, selector, 800),
                "no",
            )
        finally:
            driver.close()

    def test_playwright_select_matches_missing_harmless_connector(self):
        driver = PlaywrightBrowserDriver(
            provider("playwright", "chromium-headless")
        )
        try:
            try:
                driver.start("about:blank")
            except ProviderNotConfigured as error:
                self.skipTest(str(error))
            driver._page.set_content(
                """
                <label for="purpose">Purpose of Trip to the U.S.</label>
                <select id="purpose" name="ctl00$PurposeOfTrip">
                  <option value="">PLEASE SELECT A VISA CLASS</option>
                  <option value="B">
                    TEMP. BUSINESS OR PLEASURE VISITOR (B)
                  </option>
                  <option value="F">STUDENT (F)</option>
                </select>
                """
            )
            field_id = "ceac.travel.travel.purpose.primary"
            labels = (
                "Purpose of Trip to the U.S. "
                "[control=select_text; "
                "label_terms=Purpose of Trip to the U.S.; "
                "control_hints=PurposeOfTrip; "
                "human-approved value="
                "TEMP. BUSINESS PLEASURE VISITOR (B)]",
            )
            action = ComputerAction(
                kind=ActionKind.SELECT,
                field_id=field_id,
                coordinate_x=1,
                coordinate_y=1,
                reason="Gemini screenshot page batch",
            )
            self.assertTrue(driver.bind_visual_field(
                action,
                labels=labels,
                hints=("PurposeOfTrip",),
            ))
            action.value = "TEMP. BUSINESS PLEASURE VISITOR (B)"
            driver.execute(action)
            self.assertEqual(
                driver._page.locator("#purpose").input_value(),
                "B",
            )
        finally:
            driver.close()

    def test_playwright_configures_bounded_action_and_navigation_timeouts(self):
        class TimeoutTarget:
            def __init__(self):
                self.action_timeout = None
                self.navigation_timeout = None

            def set_default_timeout(self, timeout):
                self.action_timeout = timeout

            def set_default_navigation_timeout(self, timeout):
                self.navigation_timeout = timeout

        driver = PlaywrightBrowserDriver(provider("playwright"))
        target = TimeoutTarget()

        driver._configure_timeout_target(target)

        self.assertEqual(target.action_timeout, 4000)
        self.assertGreaterEqual(target.navigation_timeout, 15000)
        self.assertLessEqual(target.navigation_timeout, 20000)
        self.assertEqual(driver.navigation_outcome_timeout_seconds, 20)
        driver._temporary.cleanup()

    def test_playwright_prunes_all_detached_batch_bindings_in_one_pass(self):
        class MarkerLocator:
            def evaluate_all(self, _script):
                return ["field.kept"]

        class MarkerPage:
            def locator(self, selector):
                self.selector = selector
                return MarkerLocator()

        driver = PlaywrightBrowserDriver(provider("playwright"))
        driver._page = MarkerPage()
        driver._field_selectors = {
            "field.kept": '[data-docflow-field="field.kept"]',
            "field.replaced": '[data-docflow-field="field.replaced"]',
        }
        driver._verified_field_values = {
            "field.kept": "kept",
            "field.replaced": "replaced",
        }

        detached = driver._prune_detached_field_bindings()

        self.assertEqual(detached, ["field.replaced"])
        self.assertIn("field.kept", driver._field_selectors)
        self.assertNotIn("field.replaced", driver._field_selectors)
        self.assertNotIn("field.replaced", driver._verified_field_values)
        driver._temporary.cleanup()

    def test_playwright_next_has_only_one_navigation_wait_owner(self):
        class NextLocator:
            def __init__(self):
                self.click_count = 0

            @property
            def first(self):
                return self

            def click(self):
                self.click_count += 1

        class NextPage:
            def __init__(self):
                self.next = NextLocator()

            def evaluate(self, _script, _argument=None, timeout=None):
                return {"generation": "document-1", "fields": []}

            def locator(self, _selector):
                return self.next

        driver = PlaywrightBrowserDriver(provider("playwright"))
        driver._page = NextPage()
        driver._target_selectors["Next: Continue"] = "#next"
        action = ComputerAction(
            kind=ActionKind.CLICK,
            target_hint="Next: Continue",
            reason="Deterministic fixed CEAC Next control",
        )

        def forbidden_wait(*_args):
            raise AssertionError("workflow must own the only outcome wait")

        driver._wait_for_page_transition = forbidden_wait

        started = time.monotonic()
        driver.execute(action)

        self.assertLess(time.monotonic() - started, 0.2)
        self.assertEqual(driver._page.next.click_count, 1)
        driver._temporary.cleanup()

    def test_playwright_stale_locator_fails_within_action_budget(self):
        driver = PlaywrightBrowserDriver(
            provider("playwright", "chromium-headless")
        )
        try:
            try:
                driver.start("about:blank")
            except ProviderNotConfigured as error:
                self.skipTest(str(error))
            action = ComputerAction(
                kind=ActionKind.TYPE,
                field_id="personal.surname",
                target_hint="Surname",
                value="XIA",
            )
            stale = driver._page.locator("#removed-before-action")

            started = time.monotonic()
            with self.assertRaises(Exception):
                driver._mark_field(stale, action)
            self.assertLess(time.monotonic() - started, 5.5)
        finally:
            driver.close()

    def test_playwright_select_rebinds_without_stale_locator_timeout(self):
        driver = PlaywrightBrowserDriver(
            provider("playwright", "chromium-headless")
        )
        try:
            try:
                driver.start("about:blank")
            except ProviderNotConfigured as error:
                self.skipTest(str(error))
            # A shorter page default makes this regression fast while still
            # proving the adapter does not inherit that default after the old
            # data-docflow marker is synchronously destroyed.
            driver._page.set_default_timeout(5000)
            driver._page.set_content(
                """
                <div id="country-host" style="margin:20px">
                  <label for="PassportIssueCountry">
                    Country/Authority that Issued Passport
                  </label>
                  <select id="PassportIssueCountry"
                          name="PassportIssueCountry">
                    <option value="">Select</option>
                    <option value="CHIN">CHINA</option>
                    <option value="JAPN">JAPAN</option>
                  </select>
                </div>
                <script>
                  document.getElementById(
                    'PassportIssueCountry'
                  ).addEventListener('change', () => {
                    document.getElementById('country-host').innerHTML = `
                      <label for="PassportIssueCountry">
                        Country/Authority that Issued Passport
                      </label>
                      <select id="PassportIssueCountry"
                              name="PassportIssueCountry">
                        <option value="CHIN" selected>CHINA</option>
                        <option value="JAPN">JAPAN</option>
                      </select>
                    `;
                  });
                </script>
                """
            )
            field_id = "passport.issueCountry"
            labels = (
                "Country/Authority that Issued Passport "
                "[control=select; human-approved value=CHINA]",
            )
            hints = ("PassportIssueCountry",)
            actions, unresolved = driver.plan_fields(
                [field_id],
                {field_id: labels},
                {field_id: hints},
            )
            self.assertEqual(unresolved, [])
            actions[0].value = "CHINA"

            started = time.monotonic()
            driver.execute(actions[0])
            self.assertLess(time.monotonic() - started, 2.0)
            # No refresh_after_change descriptor was supplied. The removed
            # marked select must still be recognized as a DOM generation
            # replacement and rebound from the new document state.
            self.assertTrue(driver.dynamic_refresh_detected(actions[0]))
            self.assertTrue(driver.settle_after_dynamic_refresh(
                field_id,
                labels,
                hints,
            ))
            selector = driver._field_selectors[field_id]
            self.assertEqual(
                driver._live_control_value(field_id, selector, 800),
                "CHINA",
            )
        finally:
            driver.close()

    def test_playwright_checkbox_rebinds_without_stale_locator_timeout(self):
        driver = PlaywrightBrowserDriver(
            provider("playwright", "chromium-headless")
        )
        try:
            try:
                driver.start("about:blank")
            except ProviderNotConfigured as error:
                self.skipTest(str(error))
            driver._page.set_default_timeout(5000)
            driver._page.set_content(
                """
                <div id="guard-host" style="margin:20px">
                  <input id="NationalIdDoesNotApply"
                         name="NationalIdDoesNotApply"
                         type="checkbox">
                  <label for="NationalIdDoesNotApply">
                    Does Not Apply
                  </label>
                </div>
                <script>
                  document.getElementById(
                    'NationalIdDoesNotApply'
                  ).addEventListener('change', () => {
                    document.getElementById('guard-host').innerHTML = `
                      <input id="NationalIdDoesNotApply"
                             name="NationalIdDoesNotApply"
                             type="checkbox" checked>
                      <label for="NationalIdDoesNotApply">
                        Does Not Apply
                      </label>
                    `;
                  });
                </script>
                """
            )
            field_id = "personal.nationalIdDoesNotApply"
            labels = (
                "Does Not Apply "
                "[control=checkbox; refresh_after_change=true; "
                "human-approved value=true]",
            )
            hints = ("NationalIdDoesNotApply",)
            actions, unresolved = driver.plan_fields(
                [field_id],
                {field_id: labels},
                {field_id: hints},
            )
            self.assertEqual(unresolved, [])
            actions[0].value = "true"

            started = time.monotonic()
            driver.execute(actions[0])
            self.assertLess(time.monotonic() - started, 2.0)
            self.assertTrue(driver.settle_after_dynamic_refresh(
                field_id,
                labels,
                hints,
            ))
            selector = driver._field_selectors[field_id]
            self.assertEqual(
                driver._live_control_value(field_id, selector, 800),
                "true",
            )
        finally:
            driver.close()

    def test_playwright_composite_date_rebinds_after_each_onchange(self):
        driver = PlaywrightBrowserDriver(
            provider("playwright", "chromium-headless")
        )
        try:
            try:
                driver.start("about:blank")
            except ProviderNotConfigured as error:
                self.skipTest(str(error))
            driver._page.set_content(
                """
                <div id="dob-host" style="display:flex;gap:8px;margin:20px"></div>
                <script>
                  window.dobState = {day: "", month: "", year: ""};
                  window.renderDob = () => {
                    const state = window.dobState;
                    const days = Array.from({length: 31}, (_, index) => {
                      const value = String(index + 1);
                      return `<option value="${value}" ${
                        state.day === value ? "selected" : ""
                      }>${value}</option>`;
                    }).join("");
                    const names = [
                      "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                      "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"
                    ];
                    const months = names.map((name, index) => {
                      const value = String(index + 1);
                      return `<option value="${value}" ${
                        state.month === value ? "selected" : ""
                      }>${name}</option>`;
                    }).join("");
                    const host = document.getElementById("dob-host");
                    host.innerHTML = `
                      <select
                        id="ctl00_SiteContentPlaceHolder_FormView1_ddlDOBDay"
                        name="ctl00$SiteContentPlaceHolder$FormView1$ddlDOBDay"
                      >${days}</select>
                      <select
                        id="ctl00_SiteContentPlaceHolder_FormView1_ddlDOBMonth"
                        name="ctl00$SiteContentPlaceHolder$FormView1$ddlDOBMonth"
                      >${months}</select>
                      <input
                        id="ctl00_SiteContentPlaceHolder_FormView1_tbxDOBYear"
                        name="ctl00$SiteContentPlaceHolder$FormView1$tbxDOBYear"
                             value="${state.year}" maxlength="4">
                    `;
                    host.querySelector("[id$='ddlDOBDay']").addEventListener(
                      "change",
                      event => {
                        state.day = event.target.value;
                        window.renderDob();
                      }
                    );
                    host.querySelector("[id$='ddlDOBMonth']").addEventListener(
                      "change",
                      event => {
                        state.month = event.target.value;
                        window.renderDob();
                      }
                    );
                    host.querySelector("[id$='tbxDOBYear']").addEventListener(
                      "input",
                      event => {
                        state.year = event.target.value;
                        window.renderDob();
                      }
                    );
                  };
                  window.renderDob();
                </script>
                """
            )
            field_id = "ceac.relatives.007.family.mother.dateOfBirth"
            labels = (
                "Mother Date of Birth "
                "[control=date; refresh_after_change=true; "
                "human-approved value=2004-10-29]",
            )
            hints = ("DOB",)
            actions, unresolved = driver.plan_fields(
                [field_id],
                {field_id: labels},
                {field_id: hints},
            )
            self.assertEqual(unresolved, [])
            actions[0].value = "2004-10-29"

            started = time.monotonic()
            driver.execute(actions[0])
            self.assertLess(time.monotonic() - started, 5.0)
            self.assertEqual(
                driver._page.evaluate("window.dobState"),
                {"day": "29", "month": "10", "year": "2004"},
            )
            self.assertTrue(driver.dynamic_refresh_detected(actions[0]))
            self.assertTrue(driver.settle_after_dynamic_refresh(
                field_id,
                labels,
                hints,
            ))
            selector = driver._field_selectors[field_id]
            self.assertEqual(
                driver._live_control_value(field_id, selector, 800),
                "2004-10-29",
            )
        finally:
            driver.close()

    def test_playwright_composite_date_does_not_mix_shared_passport_groups(self):
        driver = PlaywrightBrowserDriver(
            provider("playwright", "chromium-headless")
        )
        try:
            try:
                driver.start("about:blank")
            except ProviderNotConfigured as error:
                self.skipTest(str(error))
            driver._page.set_content(
                """
                <div id="passport-dates" style="display:flex;gap:8px"></div>
                <script>
                  const dayOptions = Array.from(
                    {length: 31},
                    (_, index) => `<option value="${index + 1}">${
                      index + 1
                    }</option>`
                  ).join("");
                  const monthNames = [
                    "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                    "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"
                  ];
                  const monthOptions = monthNames.map(
                    (name, index) => `<option value="${index + 1}">${
                      name
                    }</option>`
                  ).join("");
                  document.getElementById("passport-dates").innerHTML = `
                    <label for="PPT_ISSUED_DTEDay">Issuance Date</label>
                    <select id="PPT_ISSUED_DTEDay">
                      <option value="">DAY</option>${dayOptions}
                    </select>
                    <select id="PPT_ISSUED_DTEMonth">
                      <option value="">MONTH</option>${monthOptions}
                    </select>
                    <input id="PPT_ISSUEDYear" value="2020">
                    <label for="PPT_EXPIRE_DTEDay">Expiration Date</label>
                    <select id="PPT_EXPIRE_DTEDay">
                      <option value="">DAY</option>${dayOptions}
                    </select>
                    <select id="PPT_EXPIRE_DTEMonth">
                      <option value="">MONTH</option>${monthOptions}
                    </select>
                    <input id="PPT_EXPIREYear" value="">
                  `;
                  document.getElementById("PPT_ISSUED_DTEDay").value = "1";
                  document.getElementById("PPT_ISSUED_DTEMonth").value = "1";
                </script>
                """
            )
            field_id = "ceac.passport.passport.expiration"
            labels = (
                "Passport Expiration Date "
                "[control=date; human-approved value=2030-10-29]",
            )
            hints = ("PPT_EXPIRE",)
            actions, unresolved = driver.plan_fields(
                [field_id],
                {field_id: labels},
                {field_id: hints},
            )
            self.assertEqual(unresolved, [])
            actions[0].value = "2030-10-29"

            driver.execute(actions[0])

            values = driver._page.evaluate(
                """() => ({
                    issuedDay: document.querySelector(
                      '#PPT_ISSUED_DTEDay'
                    ).value,
                    issuedMonth: document.querySelector(
                      '#PPT_ISSUED_DTEMonth'
                    ).value,
                    issuedYear: document.querySelector(
                      '#PPT_ISSUEDYear'
                    ).value,
                    expireDay: document.querySelector(
                      '#PPT_EXPIRE_DTEDay'
                    ).value,
                    expireMonth: document.querySelector(
                      '#PPT_EXPIRE_DTEMonth'
                    ).value,
                    expireYear: document.querySelector(
                      '#PPT_EXPIREYear'
                    ).value
                })"""
            )
            self.assertEqual(
                values,
                {
                    "issuedDay": "1",
                    "issuedMonth": "1",
                    "issuedYear": "2020",
                    "expireDay": "29",
                    "expireMonth": "10",
                    "expireYear": "2030",
                },
            )
        finally:
            driver.close()

    def test_playwright_composite_duration_rebinds_after_input_refresh(self):
        driver = PlaywrightBrowserDriver(
            provider("playwright", "chromium-headless")
        )
        try:
            try:
                driver.start("about:blank")
            except ProviderNotConfigured as error:
                self.skipTest(str(error))
            driver._page.set_content(
                """
                <div id="duration-host"
                     style="display:flex;gap:8px;margin:20px"></div>
                <script>
                  window.durationState = {amount: "", unit: ""};
                  window.renderDuration = () => {
                    const state = window.durationState;
                    const host = document.getElementById("duration-host");
                    host.innerHTML = `
                      <input id="VISIT_DURATION_AMOUNT"
                             name="VISIT_DURATION_AMOUNT"
                             value="${state.amount}">
                      <select id="VISIT_DURATION_UNIT"
                              name="VISIT_DURATION_UNIT">
                        <option value="">Select</option>
                        <option value="MONTH" ${
                          state.unit === "MONTH" ? "selected" : ""
                        }>MONTHS</option>
                        <option value="YEAR" ${
                          state.unit === "YEAR" ? "selected" : ""
                        }>YEARS</option>
                      </select>
                    `;
                    host.querySelector("input").addEventListener(
                      "input",
                      event => {
                        state.amount = event.target.value;
                        window.renderDuration();
                      }
                    );
                    host.querySelector("select").addEventListener(
                      "change",
                      event => {
                        state.unit = event.target.value;
                        window.renderDuration();
                      }
                    );
                  };
                  window.renderDuration();
                </script>
                """
            )
            field_id = "ceac.previous.001.travel.duration"
            labels = (
                "Length of Stay "
                "[control=duration; refresh_after_change=true; "
                "human-approved value=3 MONTHS]",
            )
            hints = ("VISIT_DURATION",)
            actions, unresolved = driver.plan_fields(
                [field_id],
                {field_id: labels},
                {field_id: hints},
            )
            self.assertEqual(unresolved, [])
            actions[0].value = "3 MONTHS"

            started = time.monotonic()
            driver.execute(actions[0])
            self.assertLess(time.monotonic() - started, 5.0)
            self.assertEqual(
                driver._page.evaluate("window.durationState"),
                {"amount": "3", "unit": "MONTH"},
            )
            self.assertTrue(driver.dynamic_refresh_detected(actions[0]))
            self.assertTrue(driver.settle_after_dynamic_refresh(
                field_id,
                labels,
                hints,
            ))
            selector = driver._field_selectors[field_id]
            self.assertEqual(
                driver._live_control_value(field_id, selector, 800),
                "3 MONTHS",
            )
        finally:
            driver.close()

    def test_playwright_text_segments_fill_and_live_verify_real_dom(self):
        driver = PlaywrightBrowserDriver(
            provider("playwright", "chromium-headless")
        )
        try:
            try:
                driver.start("about:blank")
            except ProviderNotConfigured as error:
                self.skipTest(str(error))
            driver._page.set_content(
                """
                <label>Social Security Number</label>
                <div id="ssn-segments">
                  <input id="APP_SSN1" type="text" maxlength="3">
                  <span>-</span>
                  <input id="APP_SSN2" type="text" maxlength="2">
                  <span>-</span>
                  <input id="APP_SSN3" type="text" maxlength="4">
                </div>
                """
            )
            field_id = "ceac.personal2.001.personal.ssn"
            actions, unresolved = driver.plan_fields(
                [field_id],
                {
                    field_id: (
                        "Social Security Number "
                        "[control=text_segments; human-approved "
                        "value=123-45-6789]",
                    )
                },
                {field_id: ("APP_SSN",)},
            )
            self.assertEqual(unresolved, [])
            self.assertEqual(len(actions), 1)
            self.assertEqual(actions[0].kind, ActionKind.TYPE)

            actions[0].value = "123-45-6789"
            driver.execute(actions[0])
            values = driver._page.locator(
                "#ssn-segments input"
            ).evaluate_all("items => items.map(item => item.value)")
            self.assertEqual(values, ["123", "45", "6789"])
            selector = driver._field_selectors[field_id]
            self.assertEqual(
                driver._live_control_value(field_id, selector, 500),
                "123-45-6789",
            )

            driver._page.locator("#APP_SSN3").fill("0000")
            self.assertIsNone(
                driver._live_control_value(field_id, selector, 500)
            )

            driver._page.locator("#ssn-segments input").evaluate_all(
                "items => items.forEach(item => { item.value = ''; })"
            )
            driver._verified_field_values.pop(field_id, None)
            actions[0].value = "12345678"
            driver.execute(actions[0])
            values = driver._page.locator(
                "#ssn-segments input"
            ).evaluate_all("items => items.map(item => item.value)")
            self.assertEqual(values, ["", "", ""])
            self.assertIsNone(
                driver._live_control_value(field_id, selector, 500)
            )
        finally:
            driver.close()

    def test_playwright_text_segments_rebind_after_each_input_refresh(self):
        driver = PlaywrightBrowserDriver(
            provider("playwright", "chromium-headless")
        )
        try:
            try:
                driver.start("about:blank")
            except ProviderNotConfigured as error:
                self.skipTest(str(error))
            driver._page.set_content(
                """
                <div id="phone-host"
                     style="display:flex;gap:8px;margin:20px"></div>
                <script>
                  window.phoneParts = ["", "", ""];
                  window.renderPhone = () => {
                    const host = document.getElementById("phone-host");
                    host.innerHTML = window.phoneParts.map(
                      (value, index) => `
                        <input id="PHONE_${index + 1}"
                               name="PHONE_${index + 1}"
                               maxlength="${[3, 3, 4][index]}"
                               value="${value}">
                      `
                    ).join("");
                    host.querySelectorAll("input").forEach((item, index) => {
                      item.addEventListener("input", event => {
                        window.phoneParts[index] = event.target.value;
                        window.renderPhone();
                      });
                    });
                  };
                  window.renderPhone();
                </script>
                """
            )
            field_id = "ceac.address_phone.001.personal.homePhone"
            labels = (
                "Home Phone Number "
                "[control=text_segments; refresh_after_change=true; "
                "human-approved value=415-555-0123]",
            )
            hints = ("PHONE_",)
            actions, unresolved = driver.plan_fields(
                [field_id],
                {field_id: labels},
                {field_id: hints},
            )
            self.assertEqual(unresolved, [])
            actions[0].value = "415-555-0123"

            started = time.monotonic()
            driver.execute(actions[0])
            self.assertLess(time.monotonic() - started, 5.0)
            self.assertEqual(
                driver._page.evaluate("window.phoneParts"),
                ["415", "555", "0123"],
            )
            self.assertTrue(driver.dynamic_refresh_detected(actions[0]))
            self.assertTrue(driver.settle_after_dynamic_refresh(
                field_id,
                labels,
                hints,
            ))
            selector = driver._field_selectors[field_id]
            self.assertEqual(
                driver._live_control_value(field_id, selector, 800),
                "415-555-0123",
            )
        finally:
            driver.close()

    def test_playwright_observe_action_reads_only_requested_field(self):
        driver = PlaywrightBrowserDriver(
            provider("playwright", "chromium-headless")
        )
        try:
            try:
                driver.start("about:blank")
            except ProviderNotConfigured as error:
                self.skipTest(str(error))
            driver._page.set_content(
                """
                <input id="one" value="ONE">
                <input id="two" value="TWO">
                """
            )
            driver._field_selectors = {
                "field.one": "#one",
                "field.two": "#two",
            }
            original = driver._live_control_value
            calls = []

            def tracked(field_id, selector, timeout):
                calls.append(field_id)
                return original(field_id, selector, timeout)

            driver._live_control_value = tracked
            driver._validation_errors = lambda: [
                "[field_id=field.one] first",
                "[field_id=field.two] second",
            ]
            observation = driver.observe_action(ComputerAction(
                kind=ActionKind.TYPE,
                field_id="field.two",
                value="TWO",
            ))

            self.assertEqual(calls, ["field.two"])
            self.assertEqual(
                observation.control_values,
                {"field.two": "TWO"},
            )
            self.assertEqual(
                observation.errors,
                ["[field_id=field.two] second"],
            )
            next_observation = driver.observe_action(ComputerAction(
                kind=ActionKind.CLICK,
                target_hint="Next: Continue",
            ))
            self.assertEqual(
                next_observation.errors,
                [
                    "[field_id=field.one] first",
                    "[field_id=field.two] second",
                ],
            )
        finally:
            driver.close()

    def test_playwright_invalidates_poisoned_field_binding_and_cache(self):
        driver = PlaywrightBrowserDriver(
            provider("playwright", "chromium-headless")
        )
        try:
            try:
                driver.start("about:blank")
            except ProviderNotConfigured as error:
                self.skipTest(str(error))
            driver._page.set_content(
                """
                <label for="wrong">Wrong control</label>
                <input id="wrong" name="wrong">
                """
            )
            field_id = "passport.issueCountry"
            action = ComputerAction(
                kind=ActionKind.TYPE,
                field_id=field_id,
                target_hint="Wrong control",
            )
            wrong = driver._page.locator("#wrong")
            driver._mark_field(wrong, action)
            driver._verified_field_values[field_id] = "CHINA"
            selector = driver._field_selectors[field_id]
            self.assertEqual(driver._page.locator(selector).count(), 1)

            driver.invalidate_field_binding(field_id)

            self.assertNotIn(field_id, driver._field_selectors)
            self.assertNotIn(field_id, driver._verified_field_values)
            self.assertEqual(driver._page.locator(selector).count(), 0)
        finally:
            driver.close()

    def test_playwright_tags_nearest_field_validation_error(self):
        driver = PlaywrightBrowserDriver(
            provider("playwright", "chromium-headless")
        )
        try:
            try:
                driver.start("about:blank")
            except ProviderNotConfigured as error:
                self.skipTest(str(error))
            driver._page.set_content(
                """
                <div class="field-row">
                  <label for="surname">Surname</label>
                  <input id="surname" name="surname">
                  <span class="error">Surname is required</span>
                </div>
                """
            )
            field_id = "personal.surname"
            actions, unresolved = driver.plan_fields(
                [field_id],
                {field_id: ("Surname [control=text]",)},
                {field_id: ("surname",)},
            )
            self.assertEqual(unresolved, [])
            self.assertEqual(len(actions), 1)

            observation = driver.observe_lightweight()
            self.assertEqual(
                observation.errors,
                [
                    "[field_id=personal.surname] "
                    "Surname is required"
                ],
            )
        finally:
            driver.close()

    def test_playwright_lightweight_observes_visible_text_only_overlay_changes(
        self,
    ):
        html = """
        <html>
          <head><title>Application Checkpoint</title></head>
          <body>
            <main id="page-copy">Application form remains unchanged</main>
            <div id="site-challenge" role="dialog" style="display:none">
              Please complete CAPTCHA to continue
            </div>
          </body>
        </html>
        """
        url = (
            "data:text/html;base64,"
            + base64.b64encode(html.encode("utf-8")).decode("ascii")
        )
        driver = PlaywrightBrowserDriver(
            provider("playwright", "chromium-headless")
        )
        driver.set_execution_mode("visual")
        try:
            try:
                driver.start(url)
            except ProviderNotConfigured as error:
                self.skipTest(str(error))
            driver.set_visual_status(
                "paused",
                "DOCFLOW-OVERLAY-SENTINEL",
            )

            before = driver.observe_lightweight()
            self.assertIn(
                "Application form remains unchanged",
                before.visible_text,
            )
            self.assertNotIn("CAPTCHA", before.visible_text)
            self.assertNotIn("Gemini", before.visible_text)
            self.assertNotIn(
                "DOCFLOW-OVERLAY-SENTINEL",
                before.visible_text,
            )

            driver._page.locator("#site-challenge").evaluate(
                """element => {
                    element.style.display = "block";
                }"""
            )
            after = driver.observe_lightweight()

            # URL, title, controls, and validation state did not change.  The
            # real browser-rendered page text is the only observable delta.
            self.assertEqual(after.url, before.url)
            self.assertEqual(after.title, before.title)
            self.assertEqual(after.page_id, before.page_id)
            self.assertEqual(after.control_values, before.control_values)
            self.assertEqual(after.errors, before.errors)
            self.assertNotEqual(after.visible_text, before.visible_text)
            self.assertIn(
                "Please complete CAPTCHA to continue",
                after.visible_text,
            )
            self.assertNotIn("Gemini", after.visible_text)
            self.assertNotIn(
                "DOCFLOW-OVERLAY-SENTINEL",
                after.visible_text,
            )

            full = driver.observe()
            self.assertIn(
                "Please complete CAPTCHA to continue",
                full.visible_text,
            )
            self.assertNotIn("Gemini", full.visible_text)
            self.assertNotIn(
                "DOCFLOW-OVERLAY-SENTINEL",
                full.visible_text,
            )
            overlay = driver._page.locator(
                "#docflow-agent-visual-status"
            )
            self.assertTrue(overlay.is_visible())
            self.assertIn(
                "DOCFLOW-OVERLAY-SENTINEL",
                overlay.inner_text(),
            )
        finally:
            driver.close()

    def test_playwright_visible_text_read_is_bounded_and_exception_safe(self):
        class FakePage:
            def __init__(self):
                self.fail = False
                self.script = ""
                self.limit = 0

            def evaluate(self, script, limit):
                if self.fail:
                    raise RuntimeError("browser-owned page denied DOM access")
                self.script = script
                self.limit = limit
                return "X" * (limit + 100)

        driver = PlaywrightBrowserDriver(provider("playwright"))
        page = FakePage()
        driver._page = page
        try:
            visible_text = driver._visible_page_text()
            self.assertEqual(
                len(visible_text),
                driver.VISIBLE_TEXT_LIMIT,
            )
            self.assertEqual(page.limit, driver.VISIBLE_TEXT_LIMIT)
            self.assertIn(
                "#docflow-agent-visible-cursor",
                page.script,
            )
            self.assertIn(
                "#docflow-agent-visual-status",
                page.script,
            )

            page.fail = True
            self.assertEqual(driver._visible_page_text(), "")
        finally:
            driver._page = None
            driver.close()

    def test_playwright_pointer_path_is_curved_and_ends_exactly(self):
        points = PlaywrightBrowserDriver._human_pointer_path(
            20,
            20,
            820,
            220,
        )
        self.assertGreaterEqual(len(points), 10)
        self.assertEqual(points[-1], (820.0, 220.0))
        # At least one intermediate point must leave the straight segment.
        self.assertTrue(any(
            abs(
                (point_y - 20.0) * 800.0
                - (point_x - 20.0) * 200.0
            ) > 1.0
            for point_x, point_y in points[:-1]
        ))
        self.assertGreater(
            PlaywrightBrowserDriver._pointer_travel_ms(
                20,
                20,
                820,
                220,
            ),
            PlaywrightBrowserDriver._pointer_travel_ms(
                20,
                20,
                80,
                40,
            ),
        )

    def test_playwright_visible_pointer_dispatches_full_motion_path(self):
        class FakeMouse:
            def __init__(self):
                self.moves = []

            def move(self, x, y):
                self.moves.append((x, y))

        class FakePage:
            def __init__(self):
                self.mouse = FakeMouse()
                self.evaluations = []
                self.waits = []

            def evaluate(self, script, argument=None):
                self.evaluations.append((script, argument))

            def wait_for_timeout(self, milliseconds):
                self.waits.append(milliseconds)

        driver = PlaywrightBrowserDriver(provider("playwright"))
        driver._page = FakePage()
        driver.set_execution_mode("visual")
        driver._move_visible_pointer(900, 540, clicking=True)

        self.assertGreaterEqual(len(driver._page.mouse.moves), 10)
        self.assertEqual(driver._page.mouse.moves[-1], (900.0, 540.0))
        self.assertEqual(driver._cursor_x, 900.0)
        self.assertEqual(driver._cursor_y, 540.0)
        scripts = "\n".join(
            script for script, _argument in driver._page.evaluations
        )
        self.assertIn("__docflowAgentHeartbeatTimer", scripts)
        self.assertIn('document.addEventListener(\n                        "mousemove"', scripts)
        self.assertNotIn('content: "Gemini"', scripts)
        driver._temporary.cleanup()

    def test_playwright_visual_overlay_exists_on_new_document_init(self):
        driver = PlaywrightBrowserDriver(
            provider("playwright", "chromium-headless")
        )
        driver.set_execution_mode("visual")
        try:
            try:
                driver.start(
                    "data:text/html,<html><body>first</body></html>"
                )
            except ProviderNotConfigured as error:
                self.skipTest(str(error))
            driver.set_visual_status(
                "navigating",
                "正在进入下一页",
            )
            driver._page.goto(
                "data:text/html,<html><body>second</body></html>",
                wait_until="domcontentloaded",
            )
            self.assertEqual(
                driver._page.locator(
                    "#docflow-agent-visible-cursor"
                ).count(),
                1,
            )
            self.assertEqual(
                driver._page.locator(
                    "#docflow-agent-visual-status"
                ).count(),
                1,
            )
        finally:
            driver.close()

    def test_playwright_visual_overlay_installs_at_document_start_and_restores_xy(self):
        driver = PlaywrightBrowserDriver(
            provider("playwright", "chromium-headless")
        )
        driver.set_execution_mode("visual")
        try:
            try:
                driver.start("about:blank")
            except ProviderNotConfigured as error:
                self.skipTest(str(error))

            def fulfill(route):
                route.fulfill(
                    status=200,
                    content_type="text/html",
                    body="<html><head></head><body>page</body></html>",
                )

            driver._page.route("https://docflow.test/**", fulfill)
            driver._page.goto(
                "https://docflow.test/first",
                wait_until="domcontentloaded",
            )
            driver._move_visible_pointer(321, 222)
            driver.set_visual_status("navigating", "正在进入下一页")
            driver._page.goto(
                "https://docflow.test/second",
                wait_until="domcontentloaded",
            )

            state = driver._page.evaluate(
                """() => {
                    const cursor = document.getElementById(
                        'docflow-agent-visible-cursor'
                    );
                    return {
                        installedAt:
                            window.__docflowAgentOverlayInstalledReadyState,
                        left: cursor?.style.left || '',
                        top: cursor?.style.top || ''
                    };
                }"""
            )
            self.assertEqual(state["installedAt"], "loading")
            self.assertEqual(state["left"], "321px")
            self.assertEqual(state["top"], "222px")
        finally:
            driver.close()

    def test_playwright_visual_host_lease_expiry_is_visible(self):
        driver = PlaywrightBrowserDriver(
            provider("playwright", "chromium-headless")
        )
        driver.set_execution_mode("visual")
        try:
            try:
                driver.start(
                    "data:text/html,<html><body>lease</body></html>"
                )
            except ProviderNotConfigured as error:
                self.skipTest(str(error))
            driver.set_visual_status("working", "正在填写")
            expired = driver._page.evaluate(
                """() => {
                    const badge = document.getElementById(
                        'docflow-agent-visual-status'
                    );
                    badge.dataset.leaseUntil = '1';
                    window.__docflowAgentRenderHeartbeat();
                    return {
                        state: badge.dataset.state,
                        label: badge.querySelector(
                            '[data-docflow-status-label]'
                        )?.textContent || '',
                        detail: badge.querySelector(
                            '[data-docflow-status-detail]'
                        )?.textContent || ''
                    };
                }"""
            )
            self.assertEqual(expired["state"], "disconnected")
            self.assertIn("连接中断", expired["label"])
            self.assertIn("没有收到", expired["detail"])
        finally:
            driver.close()

    def test_playwright_visual_overlay_avoids_controls_and_never_intercepts(self):
        html = """
        <html><body>
          <button style="position:fixed;right:0;top:0;
            width:430px;height:220px">top controls</button>
          <button style="position:fixed;right:0;top:330px;
            width:430px;height:240px">middle controls</button>
          <button style="position:fixed;right:0;bottom:0;
            width:430px;height:220px">bottom controls</button>
          <main style="margin:260px 480px 0 320px">form content</main>
        </body></html>
        """
        url = (
            "data:text/html;base64,"
            + base64.b64encode(html.encode("utf-8")).decode("ascii")
        )
        driver = PlaywrightBrowserDriver(
            provider("playwright", "chromium-headless")
        )
        driver.set_execution_mode("visual")
        try:
            try:
                driver.start(url)
            except ProviderNotConfigured as error:
                self.skipTest(str(error))
            driver.set_visual_status(
                "working",
                "正在填写当前页面，鼠标停留时仍会显示计时",
            )
            state = driver._page.evaluate(
                """() => {
                  window.__docflowAgentPlaceStatus();
                  const badge = document.getElementById(
                    'docflow-agent-visual-status'
                  );
                  const cursor = document.getElementById(
                    'docflow-agent-visible-cursor'
                  );
                  const box = badge.getBoundingClientRect();
                  const overlaps = Array.from(
                    document.querySelectorAll('button')
                  ).some((button) => {
                    const target = button.getBoundingClientRect();
                    return Math.max(box.left, target.left)
                      < Math.min(box.right, target.right)
                      && Math.max(box.top, target.top)
                      < Math.min(box.bottom, target.bottom);
                  });
                  return {
                    badgePointerEvents: getComputedStyle(
                      badge
                    ).pointerEvents,
                    cursorPointerEvents: getComputedStyle(
                      cursor
                    ).pointerEvents,
                    placement: badge.dataset.placement || '',
                    overlaps
                  };
                }"""
            )
            self.assertEqual(state["badgePointerEvents"], "none")
            self.assertEqual(state["cursorPointerEvents"], "none")
            self.assertFalse(state["overlaps"])
            self.assertNotIn(
                state["placement"],
                {"top-right", "right-middle", "bottom-right"},
            )
        finally:
            driver.close()

    def test_playwright_visual_overlay_self_heals_and_capture_restores(self):
        driver = PlaywrightBrowserDriver(
            provider("playwright", "chromium-headless")
        )
        driver.set_execution_mode("visual")
        try:
            try:
                driver.start(
                    "data:text/html,<html><body>repair</body></html>"
                )
            except ProviderNotConfigured as error:
                self.skipTest(str(error))
            driver._set_visual_overlays_hidden(True)
            hidden = driver._page.evaluate(
                """() => Array.from(document.querySelectorAll(
                  '#docflow-agent-visible-cursor,'
                  + '#docflow-agent-visual-status'
                )).every((element) => (
                  element.dataset.docflowCaptureHidden === 'true'
                  && getComputedStyle(element).visibility === 'hidden'
                ))"""
            )
            self.assertTrue(hidden)

            driver._set_visual_overlays_hidden(False)
            restored = driver._page.evaluate(
                """() => Array.from(document.querySelectorAll(
                  '#docflow-agent-visible-cursor,'
                  + '#docflow-agent-visual-status'
                )).every((element) => (
                  !element.dataset.docflowCaptureHidden
                  && getComputedStyle(element).visibility === 'visible'
                ))"""
            )
            self.assertTrue(restored)

            driver._page.evaluate(
                """() => {
                  document.getElementById(
                    'docflow-agent-visible-cursor'
                  )?.remove();
                  document.getElementById(
                    'docflow-agent-visual-status'
                  )?.remove();
                }"""
            )
            driver._page.wait_for_function(
                """() => Boolean(
                  document.getElementById(
                    'docflow-agent-visible-cursor'
                  )
                  && document.getElementById(
                    'docflow-agent-visual-status'
                  )
                )""",
                timeout=1000,
            )
        finally:
            driver.close()

    def test_playwright_visual_click_uses_curved_multi_point_motion(self):
        driver = PlaywrightBrowserDriver(
            provider("playwright", "chromium-headless")
        )
        driver.set_execution_mode("visual")
        try:
            try:
                driver.start(
                    "data:text/html,<html><body>"
                    "<button id='continue' style='position:fixed;"
                    "left:980px;top:620px;width:160px;height:60px'>"
                    "Continue</button></body></html>"
                )
            except ProviderNotConfigured as error:
                self.skipTest(str(error))
            driver._page.evaluate(
                """() => {
                  window.__testPointerMoves = [];
                  document.addEventListener('mousemove', (event) => {
                    window.__testPointerMoves.push([
                      event.clientX, event.clientY
                    ]);
                  });
                }"""
            )
            driver._target_selectors["Continue"] = "#continue"
            driver.execute(ComputerAction(
                kind=ActionKind.CLICK,
                target_hint="Continue",
                reason="Synthetic visible click",
            ))
            moves = driver._page.evaluate(
                "() => window.__testPointerMoves"
            )
            self.assertGreaterEqual(len(moves), 10)
            start_x, start_y = moves[0]
            end_x, end_y = moves[-1]
            delta_x = end_x - start_x
            delta_y = end_y - start_y
            self.assertTrue(any(
                abs(
                    (point_y - start_y) * delta_x
                    - (point_x - start_x) * delta_y
                ) > 2
                for point_x, point_y in moves[1:-1]
            ))
        finally:
            driver.close()

    def test_playwright_visual_overlay_is_injected_in_new_tab(self):
        driver = PlaywrightBrowserDriver(
            provider("playwright", "chromium-headless")
        )
        driver.set_execution_mode("visual")
        try:
            try:
                driver.start(
                    "data:text/html,<html><body>first tab</body></html>"
                )
            except ProviderNotConfigured as error:
                self.skipTest(str(error))
            second = driver._context.new_page()
            second.goto(
                "data:text/html,<html><body>second tab</body></html>",
                wait_until="domcontentloaded",
            )
            self.assertEqual(
                second.locator(
                    "#docflow-agent-visible-cursor"
                ).count(),
                1,
            )
            self.assertEqual(
                second.locator(
                    "#docflow-agent-visual-status"
                ).count(),
                1,
            )
            self.assertEqual(
                second.locator(
                    "#docflow-agent-visual-status"
                ).evaluate("element => getComputedStyle(element).pointerEvents"),
                "none",
            )
        finally:
            driver.close()

    def test_playwright_profile_dir_is_private_and_purged_only_when_marked(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "agent-profile"
            driver = PlaywrightBrowserDriver(provider("playwright"))
            driver.set_profile_dir(profile)
            (profile / "sentinel").write_text("keep", encoding="utf-8")

            self.assertTrue(profile.is_dir())
            self.assertEqual(profile.stat().st_mode & 0o777, 0o700)
            driver.close()
            self.assertTrue(profile.exists())

            purge_driver = PlaywrightBrowserDriver(provider("playwright"))
            purge_driver.set_profile_dir(profile)
            purge_driver.purge_profile_on_close()
            purge_driver.close()
            self.assertFalse(profile.exists())

    def test_playwright_post_close_purge_intent_removes_private_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "agent-profile"
            driver = PlaywrightBrowserDriver(provider("playwright"))
            driver.set_profile_dir(profile)
            (profile / "sentinel").write_text("private", encoding="utf-8")

            driver.close()
            self.assertTrue(profile.exists())
            self.assertTrue(driver.purge_profile_on_close())

            self.assertFalse(profile.exists())
            self.assertIsNone(driver._profile_dir)
            self.assertFalse(driver._profile_dir_validated)
            self.assertFalse(driver._purge_profile_after_close)

    def test_playwright_profile_purge_never_follows_replacement_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / "agent-profile"
            victim = root / "must-survive"
            victim.mkdir()
            sentinel = victim / "sentinel"
            sentinel.write_text("keep", encoding="utf-8")

            driver = PlaywrightBrowserDriver(provider("playwright"))
            driver.set_profile_dir(profile)
            profile.rmdir()
            profile.symlink_to(victim, target_is_directory=True)

            self.assertTrue(driver.purge_profile_on_close())
            self.assertFalse(profile.exists())
            self.assertTrue(victim.is_dir())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

            supplied_link = root / "supplied-profile-link"
            supplied_link.symlink_to(victim, target_is_directory=True)
            rejecting_driver = PlaywrightBrowserDriver(
                provider("playwright")
            )
            try:
                with self.assertRaisesRegex(ValueError, "symlink"):
                    rejecting_driver.set_profile_dir(supplied_link)
                self.assertTrue(supplied_link.is_symlink())
                self.assertTrue(victim.is_dir())
            finally:
                rejecting_driver.close()

    def test_playwright_profile_purge_refuses_every_broad_path(self):
        driver = PlaywrightBrowserDriver(provider("playwright"))
        broad_paths = {
            Path("/").resolve(),
            Path.home().resolve(),
            Path.cwd().resolve(),
            Path(tempfile.gettempdir()).resolve(),
        }
        try:
            for broad_path in broad_paths:
                with self.subTest(path=str(broad_path)), mock.patch(
                    "visa_agent.profile_storage.shutil.rmtree",
                ) as remove_tree:
                    driver._profile_dir = broad_path
                    driver._profile_dir_validated = True
                    driver._purge_profile_after_close = True

                    self.assertFalse(driver._purge_private_profile())
                    remove_tree.assert_not_called()
        finally:
            driver._profile_dir = None
            driver._profile_dir_validated = False
            driver._purge_profile_after_close = False
            driver.close()

    def test_emergency_close_targets_only_the_profile_owned_chrome(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "agent-profile"
            driver = PlaywrightBrowserDriver(provider("playwright"))
            driver.set_profile_dir(profile)
            (profile / "SingletonLock").symlink_to("MacBook-43210")
            owned_command = (
                "/Applications/Google Chrome.app/Contents/MacOS/"
                f"Google Chrome --user-data-dir={profile.resolve()}"
            )
            process = mock.Mock(returncode=0, stdout=owned_command)

            with mock.patch(
                "visa_agent.adapters.subprocess.run",
                return_value=process,
            ) as inspect_process, mock.patch(
                "visa_agent.adapters.os.kill",
            ) as terminate:
                closed = driver.emergency_close()

            self.assertTrue(closed)
            inspect_process.assert_called_once_with(
                ["/bin/ps", "-p", "43210", "-o", "command="],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            terminate.assert_called_once_with(43210, signal.SIGTERM)
            driver.close()

    def test_emergency_close_refuses_unrelated_chrome_process(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "agent-profile"
            driver = PlaywrightBrowserDriver(provider("playwright"))
            driver.set_profile_dir(profile)
            (profile / "SingletonLock").symlink_to("MacBook-43210")
            unrelated = mock.Mock(
                returncode=0,
                stdout=(
                    "/Applications/Google Chrome.app/Contents/MacOS/"
                    "Google Chrome --user-data-dir=/tmp/other-profile"
                ),
            )

            with mock.patch(
                "visa_agent.adapters.subprocess.run",
                return_value=unrelated,
            ), mock.patch(
                "visa_agent.adapters.os.kill",
            ) as terminate:
                closed = driver.emergency_close()

            self.assertFalse(closed)
            terminate.assert_not_called()
            driver.close()

    def test_emergency_close_refuses_profile_path_prefix_collision(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "agent-profile"
            driver = PlaywrightBrowserDriver(provider("playwright"))
            driver.set_profile_dir(profile)
            (profile / "SingletonLock").symlink_to("MacBook-43210")
            prefix_collision = mock.Mock(
                returncode=0,
                stdout=(
                    "/Applications/Google Chrome.app/Contents/MacOS/"
                    "Google Chrome "
                    f"--user-data-dir={profile.resolve()}-unrelated"
                ),
            )

            with mock.patch(
                "visa_agent.adapters.subprocess.run",
                return_value=prefix_collision,
            ), mock.patch(
                "visa_agent.adapters.os.kill",
            ) as terminate:
                closed = driver.emergency_close()

            self.assertFalse(closed)
            terminate.assert_not_called()
            driver.close()

    def test_playwright_persistent_fallback_uses_local_chrome_and_restore_flag(self):
        class FakeEngine:
            def __init__(self):
                self.calls = []

            def launch_persistent_context(self, user_data_dir, **kwargs):
                self.calls.append((user_data_dir, kwargs))
                return "persistent-context"

        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "chrome"
            executable.touch()
            profile = Path(directory) / "profile"
            profile.mkdir()
            engine = FakeEngine()
            driver = PlaywrightBrowserDriver(provider("playwright"))

            context = driver._launch_local_persistent_chromium(
                engine,
                profile,
                RuntimeError("bundled browser missing"),
                environ={"BROWSER_EXECUTABLE_PATH": str(executable)},
            )

            self.assertEqual(context, "persistent-context")
            self.assertEqual(
                engine.calls[0][1]["executable_path"],
                str(executable),
            )
            self.assertIn(
                "--restore-last-session",
                engine.calls[0][1]["args"],
            )
            driver._temporary.cleanup()

    def test_playwright_restored_formal_ceac_tab_is_not_overwritten(self):
        class FakePage:
            def __init__(self, url, form_control_count=0):
                self.url = url
                self.form_control_count = form_control_count
                self.goto_calls = []
                self.front_count = 0

            def is_closed(self):
                return False

            def goto(self, url, **kwargs):
                self.goto_calls.append((url, kwargs))
                self.url = url

            def bring_to_front(self):
                self.front_count += 1

        class FakeContext:
            def __init__(self, pages):
                self.pages = pages

            def new_page(self):
                page = FakePage("about:blank")
                self.pages.append(page)
                return page

        landing = FakePage("https://ceac.state.gov/GenNIV/Default.aspx")
        formal = FakePage(
            "https://ceac.state.gov/GenNIV/General/complete/"
            "complete_personal.aspx?node=Personal1",
            form_control_count=3,
        )
        driver = PlaywrightBrowserDriver(provider("playwright"))
        driver._context = FakeContext([landing, formal])

        driver._reuse_restored_page_or_navigate(
            "https://ceac.state.gov/GenNIV/Default.aspx"
        )

        self.assertIs(driver._page, formal)
        self.assertEqual(formal.goto_calls, [])
        self.assertEqual(formal.front_count, 1)
        driver._temporary.cleanup()

    def test_playwright_local_chrome_fallback_prefers_explicit_executable(self):
        class FakeEngine:
            def __init__(self):
                self.calls = []

            def launch(self, **kwargs):
                self.calls.append(kwargs)
                return "local-browser"

        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "chrome"
            executable.touch()
            engine = FakeEngine()
            driver = PlaywrightBrowserDriver(provider("playwright"))
            browser = driver._launch_local_chromium(
                engine,
                RuntimeError("bundled browser missing"),
                {"BROWSER_EXECUTABLE_PATH": str(executable)},
            )

            self.assertEqual(browser, "local-browser")
            self.assertEqual(engine.calls, [{
                "headless": False,
                "executable_path": str(executable),
            }])
            self.assertEqual(
                driver.browser_launch_source,
                f"local-executable:{executable}",
            )
            driver._temporary.cleanup()

    def test_playwright_local_chrome_fallback_reports_every_stage(self):
        class FailingEngine:
            def launch(self, **kwargs):
                raise RuntimeError(
                    "named channel failed"
                    if kwargs.get("channel")
                    else "local executable failed"
                )

        driver = PlaywrightBrowserDriver(provider("playwright"))
        with self.assertRaises(ProviderNotConfigured) as raised:
            driver._launch_local_chromium(
                FailingEngine(),
                RuntimeError("bundled browser missing"),
                {},
            )

        message = str(raised.exception)
        self.assertIn("bundled=RuntimeError: bundled browser missing", message)
        self.assertIn("channel=chrome:RuntimeError: named channel failed", message)
        driver._temporary.cleanup()

    def test_playwright_repeater_counts_completed_rows_before_button_binding(self):
        class CountFirstDriver(PlaywrightBrowserDriver):
            def _count_repeater_records(self, record_labels):
                self.counted_labels = tuple(record_labels)
                return 2

            def _find_repeater_button(self, _label):
                raise AssertionError(
                    "A satisfied repeater must not resolve Add Another"
                )

        driver = CountFirstDriver(provider("playwright"))
        driver._page = object()
        action = driver._plan_repeater_field(
            "ceac.work_education3.additional.languages.ensure.2",
            (
                "Add Another [control=ensure_repeater; "
                "expected_count=2; record_labels=Language Name]",
            ),
        )

        self.assertIsNotNone(action)
        self.assertEqual(action.kind, ActionKind.CLICK)
        self.assertEqual(driver.counted_labels, ("Language Name",))
        self.assertIn("expected_count=2", action.reason)
        self.assertIn("current_count=2", action.reason)
        driver._page = None
        driver._temporary.cleanup()

    def test_playwright_repeater_remarking_keeps_locator_actionable(self):
        class Element:
            def __init__(self):
                self.field = "rep"
                self.owner = "rep"
                self.pin = ""
                self.clicks = 0

        class FakeLocator:
            def __init__(self, page, selector):
                self.page = page
                self.selector = selector

            @property
            def first(self):
                return self

            def _matches(self):
                if "data-docflow-field" in self.selector:
                    return self.page.element.field == "rep"
                if "data-docflow-mark-target" in self.selector:
                    return (
                        self.page.element.pin
                        and self.page.element.pin in self.selector
                    )
                return False

            def count(self):
                return int(bool(self._matches()))

            def evaluate(self, script, argument=None):
                if not self._matches():
                    raise RuntimeError("locator no longer resolves")
                if "const existingToken" in script:
                    marker, token, owner = argument
                    collision = bool(
                        (self.page.element.owner
                         and self.page.element.owner != owner)
                        or (
                            not self.page.element.owner
                            and self.page.element.field
                            and self.page.element.field != token
                        )
                    )
                    if not collision:
                        self.page.element.pin = marker
                    return {
                        "token": self.page.element.field,
                        "owner": self.page.element.owner,
                        "collision": collision,
                    }
                if "data-docflow-mark-target" in script:
                    self.page.element.pin = argument
                if "data-docflow-field" in script:
                    self.page.element.field = argument[0]
                    self.page.element.owner = argument[1]
                    self.page.element.pin = ""

            def evaluate_all(self, script, argument=None):
                if (
                    self._matches()
                    and self.page.element.pin != argument
                ):
                    self.page.element.field = ""

            def click(self):
                if not self._matches():
                    raise RuntimeError("stale repeater locator")
                self.page.element.clicks += 1

        class FakePage:
            def __init__(self):
                self.element = Element()

            def locator(self, selector):
                return FakeLocator(self, selector)

            def wait_for_timeout(self, _milliseconds):
                return None

        driver = PlaywrightBrowserDriver(provider("playwright"))
        driver._page = FakePage()
        driver._field_selectors["rep"] = '[data-docflow-field="rep"]'
        driver._semantic_field_bindings.add("rep")
        action = ComputerAction(
            kind=ActionKind.CLICK,
            field_id="rep",
            target_hint="Add Another",
            reason=(
                "Deterministic repeater ensure "
                "[expected_count=2; current_count=1; record_labels=]"
            ),
        )

        self.assertTrue(driver._execute_repeater(action))
        self.assertEqual(driver._page.element.clicks, 1)
        self.assertEqual(driver._page.element.field, "rep")
        driver._page = None
        driver._temporary.cleanup()

    def test_playwright_plans_checkbox_as_select_not_type(self):
        class FakeCheckbox:
            def evaluate(self, _script):
                return {"tag": "input", "type": "checkbox"}

        class StructuredPlanningDriver(PlaywrightBrowserDriver):
            def _deterministic_control(self, *_args):
                return FakeCheckbox()

            def _mark_field(self, *_args):
                return None

        driver = StructuredPlanningDriver(provider("playwright"))
        driver._page = object()
        actions, unresolved = driver.plan_fields(
            ["ceac.us_contact.001.us_contact.person.does_not_know"],
            {
                "ceac.us_contact.001.us_contact.person.does_not_know": (
                    "Contact Person [control=does_not_apply; "
                    "human-approved value=true]",
                )
            },
            {},
        )
        driver._page = None
        driver._temporary.cleanup()

        self.assertEqual(unresolved, [])
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].kind, ActionKind.SELECT)

    def test_checkbox_hint_filters_composite_text_segments_before_uniqueness(
        self,
    ):
        class CapturedLocator:
            def __init__(self, selector):
                self.selector = selector

        class FakePage:
            def locator(self, selector):
                return CapturedLocator(selector)

        class ShapeAwareDriver(PlaywrightBrowserDriver):
            def _unique_actionable_control(self, locator, **_kwargs):
                if 'input[type="checkbox"]' in locator.selector:
                    raise AssertionError(
                        "checkboxes must not pass through the text-actionable "
                        "resolver"
                    )
                return None

            def _unique_visible_form_control(self, locator, **_kwargs):
                if 'input[type="checkbox"]' in locator.selector:
                    return locator
                return None

            def _nearest_labeled_checkbox(self, *_args, **_kwargs):
                raise AssertionError(
                    "exact checkbox hint should resolve before geometry"
                )

        driver = ShapeAwareDriver(provider("playwright"))
        driver._page = FakePage()
        resolved = driver._deterministic_control(
            "ceac.personal2.personal.ssn.does_not_apply",
            (
                "Social Security Number [control=does_not_apply; "
                "human-approved value=true]",
            ),
            ("APP_SSN", "SSN"),
        )
        driver._page = None
        driver._temporary.cleanup()

        self.assertIsNotNone(resolved)
        self.assertIn('input[type="checkbox"]', resolved.selector)
        self.assertNotIn("textarea", resolved.selector)

    def test_mineru_posts_multipart_and_reads_markdown(self):
        transport = FakeTransport(raw_response=(
            json.dumps({
                "results": {
                    "passport.pdf": {"md_content": "# Passport\nZHANG"}
                }
            }).encode(),
            "application/json",
        ))
        adapter = MinerUAdapter(
            provider("mineru", "pipeline", "http://mineru.local:8000"),
            transport=transport,
        )
        text = adapter.parse(b"%PDF", "passport.pdf", "application/pdf")
        self.assertEqual(text, "# Passport\nZHANG")
        self.assertEqual(transport.calls[0][1], "http://mineru.local:8000/file_parse")
        self.assertIn(b'name="return_md"', transport.calls[0][2])
        self.assertIn(b'name="files"', transport.calls[0][2])

    def test_mineru_cloud_uploads_polls_and_downloads_zip(self):
        archive_buffer = io.BytesIO()
        with zipfile.ZipFile(archive_buffer, "w") as archive:
            archive.writestr("result/full.md", "# Cloud result")
        transport = FakeTransport(
            json_responses=[
                {
                    "code": 0,
                    "data": {
                        "batch_id": "batch-1",
                        "file_urls": ["https://upload.example/signed"],
                    },
                },
                {
                    "code": 0,
                    "data": {
                        "extract_result": [{
                            "state": "done",
                            "full_zip_url": "https://download.example/result.zip",
                        }]
                    },
                },
            ],
            raw_response=(
                archive_buffer.getvalue(), "application/zip"
            ),
        )
        adapter = MinerUAdapter(
            provider(
                "mineru",
                "pipeline",
                "https://mineru.net",
                "token",
            ),
            transport=transport,
        )
        self.assertEqual(
            adapter.parse(b"%PDF", "passport.pdf", "application/pdf"),
            "# Cloud result",
        )
        self.assertEqual(
            transport.calls[0][1],
            "https://mineru.net/api/v4/file-urls/batch",
        )
        self.assertEqual(transport.calls[1][0], "PUT")
        self.assertEqual(
            transport.calls[1][3].get("Content-Type"),
            "",
        )

    def test_paddle_posts_base64_and_reads_rec_texts(self):
        transport = FakeTransport(json_responses=[{
            "errorCode": 0,
            "result": {
                "ocrResults": [
                    {"prunedResult": {"rec_texts": ["Name", "ZHANG"]}}
                ]
            },
        }])
        adapter = PaddleOCRAdapter(
            provider("paddle", "PP-OCRv6", "http://paddle.local:8080"),
            transport=transport,
        )
        text = adapter.recognize(b"image-bytes", "passport.png", "image/png")
        self.assertEqual(text, "Name\nZHANG")
        payload = transport.calls[0][2]
        self.assertEqual(
            base64.b64decode(payload["file"]),
            b"image-bytes",
        )
        self.assertEqual(payload["fileType"], 1)

    def test_paddle_official_cloud_sdk_uses_token_and_model(self):
        clients = []

        def factory(**kwargs):
            client = FakePaddleCloudClient(**kwargs)
            clients.append(client)
            return client

        adapter = PaddleOCRAdapter(
            provider("paddle", "PP-OCRv6", "official", "access-token"),
            client_factory=factory,
        )
        self.assertEqual(
            adapter.recognize(b"cloud-image", "passport.png", "image/png"),
            "Cloud\nOCR",
        )
        self.assertEqual(clients[0].init_kwargs["token"], "access-token")
        self.assertEqual(clients[0].call["model"], "PP-OCRv6")
        self.assertEqual(clients[0].call["file_bytes"], b"cloud-image")
        self.assertTrue(clients[0].closed)
        self.assertFalse(Path(clients[0].call["file_path"]).exists())

    def test_deepseek_extraction_returns_untrusted_evidenced_fields(self):
        content = {
            "fields": [{
                "id": "personal.surname",
                "value": "ZHANG",
                "confidence": 0.93,
                "evidence": [{"excerpt": "Surname ZHANG", "page": 1}],
            }]
        }
        transport = FakeTransport(json_responses=[{
            "choices": [{"message": {"content": json.dumps(content)}}]
        }])
        adapter = DeepSeekAdapter(
            provider(
                "deepseek",
                "deepseek-v4-flash",
                "https://api.deepseek.com",
                "secret",
            ),
            transport=transport,
        )
        fields = adapter.extract(
            "Surname ZHANG", "passport", "passport.pdf"
        )
        self.assertEqual(fields[0].id, "personal.surname")
        self.assertEqual(fields[0].evidence[0].excerpt, "Surname ZHANG")
        request = transport.calls[0][2]
        self.assertEqual(request["model"], "deepseek-v4-flash")
        self.assertEqual(request["response_format"], {"type": "json_object"})
        self.assertNotIn("secret", json.dumps(request))

    def test_gemini_maps_native_type_call_to_approved_field(self):
        transport = FakeTransport(json_responses=[{
            "id": "interaction-1",
            "steps": [{
                "type": "function_call",
                "id": "call-1",
                "name": "type",
                "arguments": {
                    "text": "MODEL VALUE",
                    "intent": "Fill surname [field_id=personal.surname]",
                },
            }],
        }])
        adapter = GeminiComputerUseAdapter(
            provider(
                "google",
                "gemini-3.6-flash",
                "https://generativelanguage.googleapis.com",
                "secret",
            ),
            transport=transport,
        )
        action = adapter.propose_action(
            BrowserObservation(
                url="https://ceac.state.gov/GenNIV/form",
                title="Personal Information",
                visible_text="Surname",
            ),
            ["personal.surname"],
            [],
        )
        self.assertEqual(action.kind, ActionKind.TYPE)
        self.assertEqual(action.field_id, "personal.surname")
        self.assertEqual(action.value, "")
        self.assertEqual(
            transport.calls[0][1],
            "https://generativelanguage.googleapis.com/v1beta/interactions",
        )
        self.assertTrue(transport.calls[0][2]["store"])

    def test_gemini_observation_includes_full_rendered_text_and_scroll(self):
        blocks = GeminiComputerUseAdapter._observation_blocks(
            BrowserObservation(
                url="https://ceac.state.gov/GenNIV/form",
                title="Travel Information",
                visible_text=(
                    "Purpose of Trip\nHave you made specific travel plans?\n"
                    "Person/Entity Paying for Your Trip\nNext: Travel Companions"
                ),
                scroll_y=500,
                scroll_height=1400,
                viewport_height=900,
            )
        )

        summary = json.loads(blocks[0]["text"])
        self.assertIn(
            "Person/Entity Paying for Your Trip",
            summary["rendered_page_text"],
        )
        self.assertEqual(summary["scroll"]["y"], 500)
        self.assertEqual(summary["scroll"]["document_height"], 1400)

    def test_gemini_confirm_safety_decision_keeps_approved_field_action(self):
        transport = FakeTransport(json_responses=[{
            "safety_decision": "confirm",
            "steps": [{
                "type": "function_call",
                "name": "type",
                "arguments": {
                    "intent": (
                        "Fill criminal history answer "
                        "[field_id=security.criminal]"
                    ),
                    "safety_decision": {
                        "decision": "confirm",
                        "explanation": "User started the approved task",
                    },
                },
            }],
        }])
        adapter = GeminiComputerUseAdapter(
            provider(
                "google",
                "gemini-3.6-flash",
                "https://generativelanguage.googleapis.com",
                "secret",
            ),
            transport=transport,
        )

        action = adapter.propose_action(
            BrowserObservation(
                url="https://ceac.state.gov/GenNIV/form",
                title="Security and Background",
                visible_text="Have you ever been arrested?",
            ),
            ["security.criminal"],
            [],
        )

        self.assertEqual(action.kind, ActionKind.TYPE)
        self.assertEqual(action.field_id, "security.criminal")

    def test_gemini_continues_with_function_result_and_fresh_state(self):
        transport = FakeTransport(json_responses=[
            {
                "id": "interaction-1",
                "steps": [{
                    "type": "function_call",
                    "id": "call-1",
                    "name": "click",
                    "arguments": {
                        "x": 400,
                        "y": 500,
                        "intent": (
                            "Focus surname "
                            "[field_id=personal.surname]"
                        ),
                    },
                }],
            },
            {
                "id": "interaction-2",
                "steps": [{
                    "type": "function_call",
                    "id": "call-2",
                    "name": "type",
                    "arguments": {
                        "text": "MODEL VALUE",
                        "intent": (
                            "Type surname "
                            "[field_id=personal.surname]"
                        ),
                    },
                }],
            },
        ])
        adapter = GeminiComputerUseAdapter(
            provider(
                "google",
                "gemini-3.6-flash",
                "https://generativelanguage.googleapis.com",
                "secret",
            ),
            transport=transport,
        )
        before = BrowserObservation(
            url="https://ceac.state.gov/GenNIV/form",
            title="Personal Information",
            visible_text="Surname",
        )
        click = adapter.propose_action(
            before, ["personal.surname"], []
        )
        after = BrowserObservation(
            url=before.url,
            title=before.title,
            visible_text="Surname focused",
        )
        adapter.record_action_result(click, before, after)
        typed = adapter.propose_action(
            after, ["personal.surname"], []
        )
        self.assertEqual(typed.kind, ActionKind.TYPE)
        continuation = transport.calls[1][2]
        self.assertEqual(
            continuation["previous_interaction_id"],
            "interaction-1",
        )
        function_result = continuation["input"][0]
        self.assertEqual(function_result["type"], "function_result")
        self.assertEqual(function_result["call_id"], "call-1")
        self.assertEqual(function_result["name"], "click")

    def test_gemini_maps_safe_press_key_and_rejects_enter(self):
        transport = FakeTransport(json_responses=[
            {
                "steps": [{
                    "type": "function_call",
                    "name": "press_key",
                    "arguments": {
                        "key": "TAB",
                        "intent": "Move to the next date control",
                    },
                }],
            },
            {
                "steps": [{
                    "type": "function_call",
                    "name": "press_key",
                    "arguments": {
                        "key": "ENTER",
                        "intent": "Submit the focused control",
                    },
                }],
            },
        ])
        adapter = GeminiComputerUseAdapter(
            provider(
                "google",
                "gemini-3.6-flash",
                "https://generativelanguage.googleapis.com",
                "secret",
            ),
            transport=transport,
        )
        observation = BrowserObservation(
            url="https://ceac.state.gov/GenNIV/form",
            title="Family Information",
            visible_text="Date of Birth",
        )
        tab = adapter.propose_action(observation, ["family.father.dateOfBirth"], [])
        rejected = adapter.propose_action(
            observation, ["family.father.dateOfBirth"], []
        )
        self.assertEqual(tab.kind, ActionKind.PRESS_KEY)
        self.assertEqual(tab.value, "Tab")
        self.assertEqual(rejected.kind, ActionKind.PAUSE)
        self.assertIn("safe browser allowlist", rejected.reason)

    def test_gemini_retries_transient_provider_failures(self):
        transport = FlakyTransport(
            failures=1,
            json_responses=[{
                "steps": [{
                    "type": "function_call",
                    "name": "type",
                    "arguments": {
                        "intent": "Fill surname [field_id=personal.surname]",
                    },
                }],
            }],
        )
        adapter = GeminiComputerUseAdapter(
            provider(
                "google",
                "gemini-3.6-flash",
                "https://generativelanguage.googleapis.com",
                "secret",
            ),
            transport=transport,
        )
        statuses = []
        adapter.set_status_callback(
            lambda state, message: statuses.append((state, message))
        )
        action = adapter.propose_action(
            BrowserObservation(
                url="https://ceac.state.gov/GenNIV/form",
                title="Personal Information",
                visible_text="Surname",
            ),
            ["personal.surname"],
            [],
        )
        self.assertEqual(action.kind, ActionKind.TYPE)
        self.assertEqual(action.field_id, "personal.surname")
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(adapter.interaction_count, 1)
        self.assertEqual(adapter.request_count, 2)
        self.assertEqual(
            [state for state, _message in statuses],
            ["thinking", "thinking", "working"],
        )
        self.assertIn("2/2", statuses[-2][1])
        self.assertEqual(
            [call[-1] for call in transport.calls],
            [
                adapter.PRIMARY_PLANNING_TIMEOUT_SECONDS,
                adapter.RECOVERY_PLANNING_TIMEOUT_SECONDS,
            ],
        )
        self.assertLessEqual(adapter.PLANNING_TOTAL_BUDGET_SECONDS, 42)

    def test_gemini_does_not_restart_after_full_primary_timeout(self):
        transport = TimingOutTransport()
        adapter = GeminiComputerUseAdapter(
            provider(
                "google",
                "gemini-3.6-flash",
                "https://generativelanguage.googleapis.com",
                "secret",
            ),
            transport=transport,
        )
        statuses = []
        adapter.set_status_callback(
            lambda state, message: statuses.append((state, message))
        )

        with self.assertRaises(ProviderRequestError) as caught:
            adapter.propose_action(
                BrowserObservation(
                    url="https://ceac.state.gov/GenNIV/form",
                    title="Personal Information",
                    visible_text="Telecode",
                ),
                ["personal.hasTelecode"],
                [],
            )

        self.assertTrue(caught.exception.provider_retry_exhausted)
        self.assertEqual(adapter.request_count, 1)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(
            transport.calls[0][-1],
            adapter.PRIMARY_PLANNING_TIMEOUT_SECONDS,
        )
        self.assertEqual(
            [state for state, _message in statuses],
            ["thinking", "error"],
        )
        self.assertIn("网页保持不动", statuses[-1][1])

    def test_gemini_does_not_retry_nonretryable_http_4xx(self):
        transport = RejectingTransport(400)
        adapter = GeminiComputerUseAdapter(
            provider(
                "google",
                "gemini-3.6-flash",
                "https://generativelanguage.googleapis.com",
                "secret",
            ),
            transport=transport,
        )
        statuses = []
        adapter.set_status_callback(
            lambda state, message: statuses.append((state, message))
        )

        with self.assertRaises(ProviderRequestError):
            adapter.propose_action(
                BrowserObservation(
                    url="https://ceac.state.gov/GenNIV/form",
                    title="Personal Information",
                    visible_text="Surname",
                ),
                ["personal.surname"],
                [],
            )

        self.assertEqual(adapter.request_count, 1)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(
            transport.calls[0][-1],
            adapter.PRIMARY_PLANNING_TIMEOUT_SECONDS,
        )
        self.assertLess(
            adapter.PRIMARY_PLANNING_TIMEOUT_SECONDS,
            adapter.PLANNING_TOTAL_BUDGET_SECONDS,
        )
        self.assertEqual(
            [state for state, _message in statuses],
            ["thinking", "error"],
        )
        self.assertIn("停止无效重试", statuses[-1][1])

    def test_gemini_tells_next_turn_about_focused_field(self):
        transport = FakeTransport(json_responses=[
            {
                "steps": [{
                    "type": "function_call",
                    "name": "click",
                    "arguments": {
                        "x": 500,
                        "y": 500,
                        "intent": "Focus surname [field_id=personal.surname]",
                    },
                }],
            },
            {
                "steps": [{
                    "type": "function_call",
                    "name": "type",
                    "arguments": {
                        "intent": "Fill surname [field_id=personal.surname]",
                    },
                }],
            },
        ])
        adapter = GeminiComputerUseAdapter(
            provider(
                "google",
                "gemini-3.6-flash",
                "https://generativelanguage.googleapis.com",
                "secret",
            ),
            transport=transport,
        )
        observation = BrowserObservation(
            url="https://ceac.state.gov/GenNIV/form",
            title="Personal Information",
            visible_text="Surname",
        )
        click = adapter.propose_action(
            observation, ["personal.surname"], []
        )
        typed = adapter.propose_action(
            observation, ["personal.surname"], []
        )
        self.assertEqual(click.kind, ActionKind.CLICK)
        self.assertEqual(typed.kind, ActionKind.TYPE)
        prompt = transport.calls[1][2]["input"][0]["text"]
        self.assertIn(
            "Current focused field ID: personal.surname",
            prompt,
        )

    def test_gemini_completed_wait_maps_to_system_completion_check(self):
        transport = FakeTransport(json_responses=[{
            "steps": [{
                "type": "function_call",
                "name": "wait",
                "arguments": {
                    "intent": "All approved fields have been filled.",
                },
            }],
        }])
        adapter = GeminiComputerUseAdapter(
            provider(
                "google",
                "gemini-3.6-flash",
                "https://generativelanguage.googleapis.com",
                "secret",
            ),
            transport=transport,
        )
        action = adapter.propose_action(
            BrowserObservation(
                url="https://ceac.state.gov/GenNIV/form",
                title="Personal Information",
                visible_text="Surname ZHANG",
            ),
            ["personal.surname"],
            ["personal.surname"],
        )
        self.assertEqual(action.kind, ActionKind.COMPLETE)

    def test_gemini_plans_visible_page_fields_in_one_batch(self):
        transport = FakeTransport(json_responses=[{
            "steps": [{
                "type": "function_call",
                "name": "fill_page_fields",
                "arguments": {
                    "reason": "Fill all visible approved fields",
                    "fields": [
                        {
                            "field_id": "personal.surname",
                            "control_kind": "type",
                            "x": 250,
                            "y": 400,
                        },
                        {
                            "field_id": "personal.givenNames",
                            "control_kind": "type",
                            "x": 650,
                            "y": 400,
                        },
                        {
                            "field_id": "personal.sex",
                            "control_kind": "select",
                            "x": 250,
                            "y": 600,
                        },
                    ],
                },
            }],
        }])
        adapter = GeminiComputerUseAdapter(
            provider(
                "google",
                "gemini-3.6-flash",
                "https://generativelanguage.googleapis.com",
                "secret",
            ),
            transport=transport,
        )
        actions = adapter.propose_actions(
            BrowserObservation(
                url="https://ceac.state.gov/GenNIV/form",
                title="Personal Information",
                visible_text="Surname Given Names Sex",
            ),
            [
                "personal.surname",
                "personal.givenNames",
                "personal.sex",
            ],
            [],
            [
                "personal.surname",
                "personal.givenNames",
                "personal.sex",
            ],
        )
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(adapter.interaction_count, 1)
        self.assertEqual(adapter.request_count, 1)
        self.assertEqual(
            [action.kind for action in actions],
            [ActionKind.TYPE, ActionKind.TYPE, ActionKind.SELECT],
        )
        self.assertEqual(
            [action.field_id for action in actions],
            [
                "personal.surname",
                "personal.givenNames",
                "personal.sex",
            ],
        )
        self.assertTrue(all(action.value == "" for action in actions))
        request = transport.calls[0][2]
        self.assertEqual(
            request["tools"][1]["name"],
            "fill_page_fields",
        )
        excluded = request["tools"][0]["excluded_predefined_functions"]
        self.assertIn("click", excluded)
        self.assertIn("type", excluded)
        self.assertNotIn("ZHANG", json.dumps(request))

    def test_gemini_batch_allows_only_reviewed_repeater_clicks(self):
        transport = FakeTransport(json_responses=[{
            "steps": [{
                "type": "function_call",
                "name": "fill_page_fields",
                "arguments": {
                    "fields": [{
                        "field_id": "travel.companions.rows",
                        "control_kind": "click",
                        "x": 500,
                        "y": 700,
                    }],
                },
            }],
        }])
        adapter = GeminiComputerUseAdapter(
            provider(
                "google",
                "gemini-3.6-flash",
                "https://generativelanguage.googleapis.com",
                "secret",
            ),
            transport=transport,
        )
        adapter.set_page_context({
            "travel.companions.rows": {
                "label": (
                    "Add Another [control=ensure_repeater; "
                    "expected_count=2]"
                ),
            },
        })

        actions = adapter.propose_actions(
            BrowserObservation(
                url="https://ceac.state.gov/GenNIV/form",
                title="Travel Companions",
                visible_text="Add Another",
            ),
            ["travel.companions.rows"],
            [],
            ["travel.companions.rows"],
        )

        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].kind, ActionKind.CLICK)
        self.assertEqual(actions[0].field_id, "travel.companions.rows")
        control_kinds = (
            transport.calls[0][2]["tools"][1]["parameters"]
            ["properties"]["fields"]["items"]["properties"]
            ["control_kind"]["enum"]
        )
        self.assertIn("click", control_kinds)

    def test_gemini_batch_rejects_click_for_non_repeater_field(self):
        transport = FakeTransport(json_responses=[{
            "steps": [{
                "type": "function_call",
                "name": "fill_page_fields",
                "arguments": {
                    "fields": [{
                        "field_id": "personal.surname",
                        "control_kind": "click",
                        "x": 250,
                        "y": 400,
                    }],
                },
            }],
        }])
        adapter = GeminiComputerUseAdapter(
            provider(
                "google",
                "gemini-3.6-flash",
                "https://generativelanguage.googleapis.com",
                "secret",
            ),
            transport=transport,
        )
        adapter.set_page_context({
            "personal.surname": {
                "label": "Surnames [control=text]",
            },
        })

        actions = adapter.propose_actions(
            BrowserObservation(
                url="https://ceac.state.gov/GenNIV/form",
                title="Personal Information",
                visible_text="Surnames",
            ),
            ["personal.surname"],
            [],
            ["personal.surname"],
        )

        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].kind, ActionKind.PAUSE)
        self.assertIn("allowlist", actions[0].reason)

    def test_visual_repeater_binding_enriches_model_click_semantically(self):
        driver = PlaywrightBrowserDriver(provider("playwright"))
        driver._prune_detached_field_bindings = lambda: None
        driver._plan_repeater_field = lambda field_id, labels: ComputerAction(
            kind=ActionKind.CLICK,
            field_id=field_id,
            target_hint="Add Another",
            reason=(
                "Deterministic repeater ensure "
                "[expected_count=3; current_count=1; "
                "record_labels=Travel Companion]"
            ),
        )
        action = ComputerAction(
            kind=ActionKind.CLICK,
            field_id="travel.companions.rows",
            target_hint="travel.companions.rows",
            reason="Gemini screenshot page batch",
            coordinate_x=500,
            coordinate_y=700,
        )

        bound = driver.bind_visual_field(
            action,
            labels=(
                "Add Another [control=ensure_repeater; "
                "expected_count=3]",
            ),
        )

        self.assertTrue(bound)
        self.assertEqual(action.target_hint, "Add Another")
        self.assertTrue(action.reason.startswith(
            "Deterministic repeater ensure "
        ))
        self.assertIn("expected_count=3", action.reason)

    def test_openrouter_maps_tool_call_to_approved_field(self):
        transport = FakeTransport(json_responses=[{
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "type": "function",
                        "function": {
                            "name": "browser_action",
                            "arguments": json.dumps({
                                "kind": "type",
                                "field_id": "personal.surname",
                                "intent": "Fill the approved surname field",
                            }),
                        },
                    }],
                },
            }],
        }])
        adapter = OpenRouterComputerUseAdapter(
            provider(
                "openrouter",
                "google/gemini-3.6-flash",
                "https://openrouter.ai/api/v1",
                "secret",
            ),
            transport=transport,
        )
        action = adapter.propose_action(
            BrowserObservation(
                url="https://ceac.state.gov/GenNIV/form",
                title="Personal Information",
                visible_text="Surname",
            ),
            ["personal.surname"],
            [],
        )
        self.assertEqual(action.kind, ActionKind.TYPE)
        self.assertEqual(action.field_id, "personal.surname")
        self.assertEqual(action.value, "")
        request = transport.calls[0][2]
        self.assertEqual(
            transport.calls[0][1],
            "https://openrouter.ai/api/v1/chat/completions",
        )
        self.assertEqual(request["model"], "google/gemini-3.6-flash")
        self.assertFalse(request["parallel_tool_calls"])
        self.assertTrue(request["provider"]["require_parameters"])
        self.assertEqual(request["provider"]["data_collection"], "deny")
        self.assertNotIn("secret", json.dumps(request))

    def test_builtin_registry_and_factory_wire_full_route(self):
        registry = register_builtin_providers(ProviderRegistry())
        self.assertIsInstance(
            registry.create("document_parser", provider("mineru")),
            MinerUAdapter,
        )
        self.assertIsInstance(
            registry.create("browser", provider("playwright")),
            PlaywrightBrowserDriver,
        )
        with tempfile.TemporaryDirectory() as directory:
            config = AgentConfig(
                document_parser=provider("mineru"),
                ocr=provider("mineru"),
                ocr_fallback=provider("paddle"),
                extraction=provider("deepseek", key="key"),
                review=provider("deepseek", key="key"),
                translation=provider("deepseek", key="key"),
                computer_use=provider("openrouter", key="key"),
                browser=provider("playwright"),
                data_dir=Path(directory),
                allow_plaintext_checkpoints=True,
            )
            service = build_service(config)
            self.assertIsInstance(
                service.recognizer.ocr_provider, FallbackOCRProvider
            )
            self.assertIsInstance(
                service.recognizer.ocr_provider.primary, MinerUAdapter
            )
            self.assertIsInstance(
                service.recognizer.ocr_provider.fallback, PaddleOCRAdapter
            )
            self.assertIsInstance(
                service.translation_provider, DeepSeekAdapter
            )
            self.assertIsNotNone(service.runtime_factory)

    def test_factory_does_not_build_half_configured_computer_use_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            keyless = build_service(AgentConfig(
                computer_use=provider("gemini"),
                browser=provider("playwright"),
                data_dir=Path(directory),
                allow_plaintext_checkpoints=True,
            ))
            browser_only = build_service(AgentConfig(
                browser=provider("playwright"),
                data_dir=Path(directory),
                allow_plaintext_checkpoints=True,
            ))
            model_only = build_service(AgentConfig(
                computer_use=provider("gemini", key="configured-key"),
                data_dir=Path(directory),
                allow_plaintext_checkpoints=True,
            ))

            self.assertIsNone(keyless.runtime_factory)
            self.assertIsNone(browser_only.runtime_factory)
            self.assertIsNone(model_only.runtime_factory)

    def test_shared_vendor_keys_fill_capability_configs(self):
        config = load_config({
            "EXTRACTION_PROVIDER": "deepseek",
            "REVIEW_PROVIDER": "deepseek",
            "TRANSLATION_PROVIDER": "deepseek",
            "COMPUTER_USE_PROVIDER": "openrouter",
            "OCR_PROVIDER": "mineru",
            "OCR_FALLBACK_PROVIDER": "paddle",
            "MINERU_API_TOKEN": "mineru-key",
            "PADDLEOCR_ACCESS_TOKEN": "paddle-key",
            "DEEPSEEK_API_KEY": "deepseek-key",
            "OPENROUTER_API_KEY": "openrouter-key",
        })
        self.assertEqual(config.ocr.api_key, "mineru-key")
        self.assertEqual(config.ocr_fallback.api_key, "paddle-key")
        self.assertEqual(config.extraction.api_key, "deepseek-key")
        self.assertEqual(config.review.api_key, "deepseek-key")
        self.assertEqual(config.translation.api_key, "deepseek-key")
        self.assertEqual(config.computer_use.api_key, "openrouter-key")


if __name__ == "__main__":
    unittest.main()
