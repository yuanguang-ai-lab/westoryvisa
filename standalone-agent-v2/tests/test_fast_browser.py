import time
import unittest
from unittest.mock import patch

from visa_agent.adapters import PlaywrightBrowserDriver
from visa_agent.config import ProviderConfig
from visa_agent.models import ActionKind, ComputerAction

from visa_agent_v2.browser import (
    ControlPostbackTimeout,
    FastVisiblePlaywrightBrowser,
)


class FakePage:
    def __init__(self):
        self.wait_calls = 0

    def evaluate(self, _script, _argument=None):
        return {
            "generation": "same-generation",
            "fields": ["field-token"],
            "async": {
                "available": True,
                "begun": 0,
                "ended": 0,
                "inflight": 0,
            },
        }

    def wait_for_timeout(self, milliseconds):
        self.wait_calls += 1
        time.sleep(milliseconds / 1000)


class FakeRequestImplementation:
    def __init__(self, guid):
        self._guid = guid


class FakeRequest:
    resource_type = "document"
    method = "POST"

    def __init__(self, guid="request-guid"):
        self._impl_obj = FakeRequestImplementation(guid)

    @staticmethod
    def is_navigation_request():
        return True


class DelayedNetworkPage(FakePage):
    def __init__(self):
        super().__init__()
        self.handlers = {}
        self.elapsed_ms = 0
        self.request = FakeRequest("delayed-postback")
        self.finished_request = FakeRequest("delayed-postback")
        self.request_started = False
        self.request_finished = False
        self.generation = "same-generation"

    def on(self, event_name, callback):
        self.handlers[event_name] = callback

    def evaluate(self, script, _argument=None):
        if "manager.add_beginRequest" in script:
            return {
                "available": False,
                "begun": 0,
                "ended": 0,
                "inflight": 0,
            }
        return {
            "generation": self.generation,
            "fields": ["field-token"],
            "async": {
                "available": False,
                "begun": 0,
                "ended": 0,
                "inflight": 0,
            },
        }

    def wait_for_timeout(self, milliseconds):
        super().wait_for_timeout(milliseconds)
        self.elapsed_ms += milliseconds
        if self.elapsed_ms >= 960 and not self.request_started:
            self.request_started = True
            self.handlers["request"](self.request)
        if self.elapsed_ms >= 1200 and not self.request_finished:
            self.request_finished = True
            # Playwright may surface a different Python wrapper for the same
            # protocol request in its completion callback.
            self.handlers["requestfinished"](self.finished_request)
            self.generation = "new-generation"


class SelectedOptionLocator:
    def evaluate(self, _script, _argument=None):
        return {
            "tag": "select",
            "value": "B1-B2",
            "text": "BUSINESS OR TOURISM (TEMPORARY VISITOR) (B1/B2)",
        }


class ContactControlLocator:
    def __init__(self, kind):
        self.kind = kind

    def evaluate(self, _script):
        return {
            "tag": "select" if self.kind in {"select", "select_text"}
            else "input",
            "type": "checkbox" if self.kind == "does_not_apply"
            else "text",
        }


class ContactRelationshipLocator:
    def evaluate(self, _script):
        return {
            "tag": "select",
            "value": "P",
            "text": "EMPLOYER",
            "options": [
                {
                    "index": 0,
                    "value": "",
                    "text": "- SELECT ONE -",
                    "disabled": False,
                },
                {
                    "index": 1,
                    "value": "P",
                    "text": "EMPLOYER",
                    "disabled": False,
                },
            ],
        }


class ContactPlaceholderRelationshipLocator(ContactRelationshipLocator):
    def evaluate(self, _script):
        snapshot = super().evaluate(_script)
        snapshot["value"] = ""
        snapshot["text"] = "- SELECT ONE -"
        return snapshot


class ContactHotelRelationshipLocator(ContactPlaceholderRelationshipLocator):
    def evaluate(self, _script):
        snapshot = super().evaluate(_script)
        snapshot["options"].append({
            "index": 2,
            "value": "O",
            "text": "OTHER",
            "disabled": False,
        })
        return snapshot


class ContactUnknownLocator:
    def __init__(self, page):
        self.page = page
        self.clicks = 0

    @property
    def first(self):
        return self

    @staticmethod
    def count():
        return 1

    @staticmethod
    def is_visible():
        return True

    def is_checked(self, timeout=None):
        return self.page.checked

    def click(self, timeout=None):
        self.clicks += 1
        self.page.checked = not self.page.checked
        self.page.names_unavailable = self.page.checked


class ContactUnknownPage:
    url = (
        "https://ceac.state.gov/GenNIV/General/complete/"
        "complete_uscontact.aspx?node=USContact"
    )

    def __init__(self, checked=True, names_unavailable=False):
        self.checked = checked
        self.names_unavailable = names_unavailable
        self.checkbox = ContactUnknownLocator(self)

    def locator(self, _selector):
        return self.checkbox

    def evaluate(self, _script):
        return {
            "checked": self.checked,
            "surnameUnavailable": self.names_unavailable,
            "givenUnavailable": self.names_unavailable,
        }

    @staticmethod
    def wait_for_timeout(_milliseconds):
        return None


class AddressPostalDnaLocator:
    def __init__(self, page):
        self.page = page
        self.clicks = 0

    @property
    def first(self):
        return self

    @staticmethod
    def count():
        return 1

    @staticmethod
    def is_visible(timeout=None):
        return True

    def click(self, timeout=None):
        self.clicks += 1
        self.page.checked = not self.page.checked
        self.page.hidden_value = "Y" if self.page.checked else "N"
        self.page.text_disabled = self.page.checked


class AddressPostalDnaPage:
    url = (
        "https://ceac.state.gov/GenNIV/General/complete/"
        "complete_contact.aspx?node=AddressPhone"
    )

    def __init__(self):
        self.checked = False
        self.hidden_value = "N"
        self.text_disabled = False
        self.checkbox = AddressPostalDnaLocator(self)

    def locator(self, _selector):
        return self.checkbox

    def evaluate(self, _script, _argument=None):
        return {
            "found": True,
            "checked": self.checked,
            "hiddenFound": True,
            "hiddenValue": self.hidden_value,
            "textFound": True,
            "textDisabled": self.text_disabled,
            "textValue": "",
        }

    @staticmethod
    def wait_for_timeout(_milliseconds):
        return None


class ContactTextLocator:
    def __init__(self, value=""):
        self.value = value

    @property
    def first(self):
        return self

    @staticmethod
    def count():
        return 1

    @staticmethod
    def is_visible():
        return True

    def input_value(self, timeout=None):
        return self.value


class ContactResetPage(ContactUnknownPage):
    def __init__(self):
        super().__init__(checked=False, names_unavailable=False)
        self.organization = ContactTextLocator("")

    def locator(self, selector):
        if "tbxUS_POC_ORGANIZATION" in selector:
            return self.organization
        return self.checkbox


class EducationControlLocator:
    def __init__(self, kind):
        self.kind = kind

    def evaluate(self, _script):
        return {
            "tag": "select" if self.kind in {"select", "select_text"}
            else "input",
            "type": "checkbox" if self.kind == "does_not_apply"
            else "text",
        }


class ExactEducationPage:
    def __init__(self):
        self.selectors = []

    def locator(self, selector):
        self.selectors.append(selector)
        return SingleLocator()


class ContactPage:
    url = (
        "https://ceac.state.gov/GenNIV/General/complete/"
        "complete_uscontact.aspx?node=USContact"
    )


class ReloadableContactPage(ContactPage):
    def __init__(self):
        self.reload_count = 0

    def reload(self, **_kwargs):
        self.reload_count += 1

    @staticmethod
    def wait_for_timeout(_milliseconds):
        return None


class SingleLocator:
    @property
    def first(self):
        return self

    @staticmethod
    def count():
        return 1


class FamilyIdentityPage:
    url = (
        "https://ceac.state.gov/GenNIV/General/complete/"
        "complete_family1.aspx?node=Relatives"
    )

    def evaluate(self, script, _argument=None):
        # Simulate production's ambiguous prompt container: prompt geometry
        # fails, but the exact OtherRelatives radio identity is unique.
        return "otherrelativefollowup" in script

    @staticmethod
    def locator(_selector):
        return SingleLocator()


class UncheckedFamilyRadioLocator:
    @staticmethod
    def evaluate(_script):
        return {"checked": False, "candidate": ""}


class CheckedChoiceLocator:
    def __init__(self):
        self.clicks = 0

    @staticmethod
    def is_checked(timeout=None):
        return True

    def click(self, timeout=None):
        self.clicks += 1


class FastBrowserTests(unittest.TestCase):
    def test_only_photo_stage_allows_confirm_photo_next(self):
        allow = FastVisiblePlaywrightBrowser._safe_next_control_text
        photo_url = (
            "https://ceac.state.gov/GenNIV/General/photo/"
            "photo_uploadthephoto.aspx?node=UploadPhoto"
        )

        self.assertTrue(allow("Next: Confirm Photo", photo_url))
        self.assertFalse(allow(
            "Next: Confirm Application",
            photo_url,
        ))
        self.assertFalse(allow(
            "Next: Confirm Photo",
            "https://ceac.state.gov/GenNIV/General/review/review.aspx",
        ))
        self.assertFalse(allow("Next: Submit", photo_url))

    @staticmethod
    def _us_contact_fields():
        return {
            "ceac.us_contact.us_contact.phone": (
                "Phone Number [control=text]",
            ),
            "ceac.us_contact.us_contact.email": (
                "Email Address [control=text]",
            ),
            "ceac.us_contact.us_contact.address.street1": (
                "U.S. Contact Address Line 1 [control=text]",
            ),
            "ceac.us_contact.us_contact.address.street2": (
                "U.S. Contact Address Line 2 [control=text]",
            ),
            "ceac.us_contact.us_contact.address.city": (
                "U.S. Contact City [control=text]",
            ),
            "ceac.us_contact.us_contact.address.state": (
                "U.S. Contact State [control=select_text]",
            ),
            "ceac.us_contact.us_contact.address.postalcode": (
                "U.S. Contact ZIP Code [control=text]",
            ),
        }

    def test_us_contact_required_block_cannot_be_classified_absent(self):
        browser = FastVisiblePlaywrightBrowser(
            ProviderConfig(provider="playwright", model="chromium-headless")
        )
        browser._page = ContactPage()
        labels = self._us_contact_fields()
        field_ids = list(labels)
        browser._travel_semantic_control = (
            lambda _terms, kind, **_kwargs: ContactControlLocator(kind)
        )

        presence = browser._classify_us_contact_field_presence(
            {"present": [], "absent": field_ids, "unresolved": []},
            field_ids,
            labels,
        )

        self.assertEqual(presence["present"], field_ids)
        self.assertEqual(presence["absent"], [])
        self.assertEqual(presence["unresolved"], [])

        browser._travel_semantic_control = lambda *_args, **_kwargs: None
        missing = browser._classify_us_contact_field_presence(
            {"present": [], "absent": field_ids, "unresolved": []},
            field_ids,
            labels,
        )
        self.assertEqual(missing["present"], [])
        self.assertEqual(missing["absent"], [])
        self.assertEqual(missing["unresolved"], field_ids)

    def test_us_contact_required_block_uses_exact_semantic_actions(self):
        browser = FastVisiblePlaywrightBrowser(
            ProviderConfig(provider="playwright", model="chromium-headless")
        )
        browser._page = ContactPage()
        labels = self._us_contact_fields()
        browser._travel_semantic_control = (
            lambda _terms, kind, **_kwargs: ContactControlLocator(kind)
        )
        browser._mark_field = lambda _locator, _action: None

        actions, unresolved = browser._plan_us_contact_semantic_fallback(
            list(labels),
            labels,
        )

        self.assertEqual(
            [action.field_id for action in actions],
            list(labels),
        )
        self.assertEqual(unresolved, [])
        self.assertEqual(
            [action.kind for action in actions],
            [
                ActionKind.TYPE,
                ActionKind.TYPE,
                ActionKind.TYPE,
                ActionKind.TYPE,
                ActionKind.TYPE,
                ActionKind.SELECT,
                ActionKind.TYPE,
            ],
        )

    def test_us_contact_missing_email_plans_exact_dna_checkbox(self):
        browser = FastVisiblePlaywrightBrowser(
            ProviderConfig(provider="playwright", model="chromium-headless")
        )
        browser._page = ContactPage()
        field_id = "ceac.us_contact.us_contact.email"
        labels = {
            field_id: (
                "Email Address [control=does_not_apply; "
                "human-approved value=true]",
            ),
        }
        browser._us_contact_email_dna_control = (
            lambda: ContactControlLocator("does_not_apply")
        )
        browser._mark_field = lambda _locator, _action: None

        actions, unresolved = browser._plan_us_contact_semantic_fallback(
            [field_id],
            labels,
        )

        self.assertEqual(unresolved, [])
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].field_id, field_id)
        self.assertEqual(actions[0].kind, ActionKind.SELECT)
        self.assertTrue(browser._us_contact_email_dna_consistent({
            "found": True,
            "checked": True,
            "hiddenValue": "Y",
            "textDisabled": True,
            "textValue": "",
        }))

    def test_us_contact_missing_address_replays_selected_relationship(self):
        browser = FastVisiblePlaywrightBrowser(
            ProviderConfig(provider="playwright", model="chromium-headless")
        )
        browser._page = ContactPage()
        browser._us_contact_address_rendered = lambda: False
        relationship = ContactRelationshipLocator()
        browser._us_contact_relationship_control = lambda: relationship
        browser._mark_field = lambda _locator, _action: None
        pending = ["ceac.us_contact.us_contact.address.street1"]

        actions = browser._plan_missing_us_contact_branch_replay(pending)

        self.assertEqual(len(actions), 1)
        self.assertEqual(
            actions[0].field_id,
            "ceac.us_contact.us_contact.relationship",
        )
        self.assertEqual(actions[0].value, "EMPLOYER")
        self.assertIn(
            actions[0].field_id,
            browser._v2_forced_us_contact_relationship_ids,
        )
        self.assertEqual(
            browser._plan_missing_us_contact_branch_replay(pending),
            [],
        )

    def test_us_contact_legacy_hotel_relationship_plans_ceac_other(self):
        browser = FastVisiblePlaywrightBrowser(
            ProviderConfig(provider="playwright", model="chromium-headless")
        )
        browser._page = ContactPage()
        relationship = ContactHotelRelationshipLocator()
        browser._us_contact_relationship_control = lambda: relationship
        browser._mark_field = lambda _locator, _action: None
        field_id = "ceac.us_contact.us_contact.relationship"
        labels = {
            field_id: (
                "Relationship to You [control=select_text; "
                "human-approved value=Hotel Hotel Hostel]",
            ),
        }

        actions, unresolved = browser._plan_us_contact_relationship_stage(
            [field_id],
            [field_id, "ceac.us_contact.us_contact.address.street1"],
            labels,
        )

        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].value, "OTHER")
        self.assertEqual(
            unresolved,
            ["ceac.us_contact.us_contact.address.street1"],
        )
        self.assertIn(
            field_id,
            browser._v2_forced_us_contact_relationship_ids,
        )

    def test_us_contact_other_matches_legacy_hotel_approval(self):
        browser = FastVisiblePlaywrightBrowser(
            ProviderConfig(provider="playwright", model="chromium-headless")
        )
        browser._page = ContactPage()
        locator = ContactHotelRelationshipLocator()
        locator.evaluate = lambda _script: {
            "tag": "select",
            "value": "O",
            "text": "OTHER",
            "options": [],
        }
        browser._us_contact_relationship_control = lambda: locator

        self.assertTrue(browser.us_contact_relationship_matches_approved(
            "ceac.us_contact.us_contact.relationship",
            "Hotel Hotel Hostel",
        ))

    def test_us_contact_inconsistent_unknown_checkbox_uses_trusted_replay(self):
        browser = FastVisiblePlaywrightBrowser(
            ProviderConfig(provider="playwright", model="chromium-headless")
        )
        page = ContactUnknownPage(
            checked=True,
            names_unavailable=False,
        )
        browser._page = page
        browser._require_page = lambda: None
        browser._mark_field = lambda _locator, _action: None
        field_id = "ceac.us_contact.us_contact.person.does_not_know"
        action = ComputerAction(
            kind=ActionKind.SELECT,
            field_id=field_id,
            target_hint=field_id,
            value="true",
            reason="fixture",
        )

        browser._execute_us_contact_person_unknown(action)

        self.assertEqual(page.checkbox.clicks, 2)
        self.assertTrue(page.checked)
        self.assertTrue(page.names_unavailable)
        self.assertIn(action.id, browser._acknowledged)

    def test_address_phone_postal_dna_uses_trusted_click_and_hidden_proof(self):
        browser = FastVisiblePlaywrightBrowser(
            ProviderConfig(provider="playwright", model="chromium-headless")
        )
        page = AddressPostalDnaPage()
        browser._page = page
        browser._require_page = lambda: None
        browser._mark_field = lambda _locator, _action: None
        field_id = "ceac.address_phone.contact.homepostalcode"
        action = ComputerAction(
            kind=ActionKind.SELECT,
            field_id=field_id,
            target_hint=field_id,
            value="true",
            reason="fixture",
        )

        browser._execute_address_phone_postal_dna(action)

        self.assertEqual(page.checkbox.clicks, 1)
        self.assertTrue(page.checked)
        self.assertEqual(page.hidden_value, "Y")
        self.assertTrue(
            browser.address_phone_dna_value_matches(field_id, "true")
        )
        self.assertIn(action.id, browser._acknowledged)

    def test_address_phone_hidden_y_and_disabled_text_is_authoritative_dna(self):
        browser = FastVisiblePlaywrightBrowser(
            ProviderConfig(provider="playwright", model="chromium-headless")
        )
        page = AddressPostalDnaPage()
        page.checked = False
        page.hidden_value = "Y"
        page.text_disabled = True
        browser._page = page
        browser._require_page = lambda: None
        browser._mark_field = lambda _locator, _action: None
        field_id = "ceac.address_phone.contact.workphone"
        action = ComputerAction(
            kind=ActionKind.SELECT,
            field_id=field_id,
            target_hint=field_id,
            value="DNA",
            reason="fixture",
        )

        browser._execute_address_phone_dna(action)

        self.assertEqual(page.checkbox.clicks, 0)
        self.assertTrue(
            browser.address_phone_exact_value_matches(field_id, "DNA")
        )
        self.assertIn(action.id, browser._acknowledged)

    def test_address_phone_checked_n_replays_off_then_on_once(self):
        browser = FastVisiblePlaywrightBrowser(
            ProviderConfig(provider="playwright", model="chromium-headless")
        )
        page = AddressPostalDnaPage()
        page.checked = True
        page.hidden_value = "N"
        page.text_disabled = False
        browser._page = page
        browser._require_page = lambda: None
        browser._mark_field = lambda _locator, _action: None
        field_id = "ceac.address_phone.contact.secondaryphone"
        action = ComputerAction(
            kind=ActionKind.SELECT,
            field_id=field_id,
            target_hint=field_id,
            value="DNA",
            reason="fixture",
        )

        browser._execute_address_phone_dna(action)

        self.assertEqual(page.checkbox.clicks, 2)
        self.assertTrue(page.checked)
        self.assertEqual(page.hidden_value, "Y")
        self.assertTrue(page.text_disabled)
        self.assertIn(action.id, browser._acknowledged)

    def test_address_phone_stable_ids_plan_postal_text_and_phone_dna(self):
        browser = FastVisiblePlaywrightBrowser(
            ProviderConfig(provider="playwright", model="chromium-headless")
        )
        browser._page = AddressPostalDnaPage()
        bindings = []
        browser._address_phone_exact_control = (
            lambda field_id, dna=False: (field_id, dna)
        )
        browser._mark_field = (
            lambda locator, action: bindings.append((locator, action.field_id))
        )
        postal_id = "ceac.address_phone.contact.homepostalcode"
        secondary_id = "ceac.address_phone.contact.secondaryphone"

        actions, unresolved = browser._plan_address_phone_semantic_fallback(
            [postal_id, secondary_id],
            {
                postal_id: (
                    "Home Postal Zone/ZIP Code [control=text; "
                    "human-approved value=325000]",
                ),
                secondary_id: (
                    "Secondary Phone Number [control=text; "
                    "human-approved value=DNA]",
                ),
            },
        )

        self.assertEqual(unresolved, [])
        self.assertEqual(
            [(action.field_id, action.kind) for action in actions],
            [
                (postal_id, ActionKind.TYPE),
                (secondary_id, ActionKind.SELECT),
            ],
        )
        self.assertEqual(bindings[0][0], (postal_id, False))
        self.assertEqual(bindings[1][0], (secondary_id, True))

    def test_us_contact_synthetic_replay_rebinds_replacement_select(self):
        browser = FastVisiblePlaywrightBrowser(
            ProviderConfig(provider="playwright", model="chromium-headless")
        )
        browser._page = ContactPage()
        browser._us_contact_address_rendered = lambda: True
        relationship = ContactRelationshipLocator()
        browser._us_contact_relationship_control = lambda: relationship
        field_id = "ceac.us_contact.us_contact.relationship"
        browser._verified_field_values[field_id] = "EMPLOYER"
        rebound = []
        browser._mark_field = (
            lambda locator, action: rebound.append((locator, action))
        )

        settled = browser.settle_after_dynamic_refresh(field_id, (), ())

        self.assertTrue(settled)
        self.assertEqual(rebound[0][0], relationship)
        self.assertEqual(rebound[0][1].field_id, field_id)

    def test_us_contact_replay_accepts_existing_reviewed_branch(self):
        browser = FastVisiblePlaywrightBrowser(
            ProviderConfig(provider="playwright", model="chromium-headless")
        )
        browser._page = ContactPage()
        browser._us_contact_address_rendered = lambda: True
        relationship = ContactRelationshipLocator()
        browser._us_contact_relationship_control = lambda: relationship
        browser._us_contact_person_unknown_control = lambda: None
        rebound = []
        browser._mark_field = (
            lambda locator, action: rebound.append((locator, action))
        )
        field_id = "ceac.us_contact.us_contact.relationship"
        browser._v2_forced_us_contact_relationship_ids.add(field_id)
        action = ComputerAction(
            kind=ActionKind.SELECT,
            field_id=field_id,
            target_hint=field_id,
            value="EMPLOYER",
        )

        browser._execute_us_contact_branch_reset(action)

        self.assertEqual(rebound[0][0], relationship)
        self.assertIn(action.id, browser._acknowledged)
        self.assertNotIn(
            field_id,
            browser._v2_forced_us_contact_relationship_ids,
        )
        self.assertIn(
            field_id,
            browser._v2_forced_refresh_receipt_field_ids,
        )

    def test_us_contact_timeout_reloads_before_bounded_retry(self):
        browser = FastVisiblePlaywrightBrowser(
            ProviderConfig(provider="playwright", model="chromium-headless")
        )
        page = ReloadableContactPage()
        browser._page = page
        relationship = ContactPlaceholderRelationshipLocator()
        browser._us_contact_relationship_control = lambda: relationship
        browser._us_contact_person_unknown_control = lambda: None
        browser._mark_field = lambda _locator, _action: None
        browser._require_page = lambda: None
        browser._begin_action_dom_watch = lambda: None
        browser._configure_timeout_target = lambda _page: None
        browser._prune_detached_field_bindings = lambda: None
        browser._activate_select_option = lambda _locator, _option: True
        browser._ensure_travel_control_postback = (
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                ControlPostbackTimeout("fixture uncertain POST")
            )
        )
        field_id = "ceac.us_contact.us_contact.relationship"
        browser._v2_forced_us_contact_relationship_ids.add(field_id)
        action = ComputerAction(
            kind=ActionKind.SELECT,
            field_id=field_id,
            target_hint=field_id,
            value="EMPLOYER",
        )

        with self.assertRaises(RuntimeError) as raised:
            browser._execute_us_contact_branch_reset(action)

        self.assertNotIsInstance(
            raised.exception,
            ControlPostbackTimeout,
        )
        self.assertIn(
            "safely reloaded for bounded re-verification",
            str(raised.exception),
        )
        self.assertTrue(
            browser.interrupted_action_retry_safe(action, raised.exception)
        )
        self.assertEqual(page.reload_count, 1)
        self.assertTrue(
            browser._last_control_postback_diagnostic.get(
                "safeReloadAfterUnknownPostback"
            )
        )

    def test_us_contact_reconnect_reopens_all_three_controllers(self):
        browser = FastVisiblePlaywrightBrowser(
            ProviderConfig(provider="playwright", model="chromium-headless")
        )
        browser._page = ContactResetPage()
        browser._us_contact_relationship_control = (
            lambda: ContactPlaceholderRelationshipLocator()
        )
        browser._us_contact_address_rendered = lambda: False
        unknown = "ceac.us_contact.us_contact.person.does_not_know"
        organization = "ceac.us_contact.us_contact.organization"
        relationship = "ceac.us_contact.us_contact.relationship"

        stale = browser._stale_us_contact_controllers(
            [relationship, organization, unknown],
            {
                unknown: "true",
                organization: "XINZHUOSHIYE",
                relationship: "EMPLOYER",
            },
            {},
        )

        self.assertEqual(stale, [unknown, organization, relationship])

    def test_us_contact_controllers_are_staged_unknown_first(self):
        browser = FastVisiblePlaywrightBrowser(
            ProviderConfig(provider="playwright", model="chromium-headless")
        )
        browser._page = ContactPage()
        unknown = "ceac.us_contact.us_contact.person.does_not_know"
        organization = "ceac.us_contact.us_contact.organization"
        relationship = "ceac.us_contact.us_contact.relationship"
        address = "ceac.us_contact.us_contact.address.street1"
        action = ComputerAction(
            kind=ActionKind.SELECT,
            field_id=unknown,
            target_hint=unknown,
            reason="fixture",
        )

        with patch.object(
            PlaywrightBrowserDriver,
            "plan_fields",
            return_value=([action], []),
        ) as generic_plan:
            actions, unresolved = browser._plan_semantic_fields_once(
                [address, relationship, organization, unknown],
                {},
                {},
            )

        self.assertEqual(actions, [action])
        self.assertEqual(
            unresolved,
            [address, relationship, organization],
        )
        self.assertEqual(generic_plan.call_args.args[0], [unknown])

    def test_family_other_relatives_uses_exact_group_identity_fallback(self):
        browser = FastVisiblePlaywrightBrowser(
            ProviderConfig(provider="playwright", model="chromium-headless")
        )
        browser._page = FamilyIdentityPage()

        locator = browser._family_choice_group(
            "ceac.relatives.family.other_relatives_us",
            ("do you have any other relatives in the united states",),
        )

        self.assertIsNotNone(locator)

    def test_family_restored_unchecked_controllers_are_reopened(self):
        browser = FastVisiblePlaywrightBrowser(
            ProviderConfig(provider="playwright", model="chromium-headless")
        )
        browser._is_relatives_page = lambda: True
        fields = [
            "ceac.relatives.family.father_in_us",
            "ceac.relatives.family.mother_in_us",
            "ceac.relatives.family.immediate_relatives_us",
            "ceac.relatives.family.other_relatives_us",
        ]
        browser._family_choice_group = (
            lambda field_id, _terms: (
                None
                if field_id.endswith(".other_relatives_us")
                else UncheckedFamilyRadioLocator()
            )
        )

        stale = browser.stale_completed_branch_controller_fields(
            fields,
            {field_id: "no" for field_id in fields},
            {},
        )

        self.assertEqual(stale, fields[:3])

    def test_work_education2_restored_unchecked_controllers_are_reopened(self):
        browser = FastVisiblePlaywrightBrowser(
            ProviderConfig(provider="playwright", model="chromium-headless")
        )
        browser._is_relatives_page = lambda: False
        browser._is_work_education2_page = lambda: True
        fields = [
            "ceac.work_education2.work.previously_employed",
            "ceac.work_education2.work.education_secondary_or_above",
        ]
        browser._prompt_scoped_choice_group = (
            lambda _terms: UncheckedFamilyRadioLocator()
        )

        stale = browser.stale_completed_branch_controller_fields(
            fields,
            {fields[0]: "no", fields[1]: "yes"},
            {},
        )

        self.assertEqual(stale, fields)

    def test_work_education2_controllers_are_staged_employment_first(self):
        browser = FastVisiblePlaywrightBrowser(
            ProviderConfig(provider="playwright", model="chromium-headless")
        )
        education = (
            "ceac.work_education2.work.education_secondary_or_above"
        )
        employment = "ceac.work_education2.work.previously_employed"
        school = (
            "ceac.work_education2.work.education.record.school.key"
        )
        action = ComputerAction(
            kind=ActionKind.SELECT,
            field_id=employment,
            target_hint=employment,
            reason="fixture",
        )

        with patch.object(
            PlaywrightBrowserDriver,
            "plan_fields",
            return_value=([action], []),
        ) as generic_plan:
            actions, unresolved = browser._plan_work_education2_page_once(
                [education, school, employment],
                {},
                {},
            )

        self.assertEqual(actions, [action])
        self.assertEqual(unresolved, [education, school])
        self.assertEqual(generic_plan.call_args.args[0], [employment])

    def test_work_education2_education_yes_requires_school_panel(self):
        browser = FastVisiblePlaywrightBrowser(
            ProviderConfig(provider="playwright", model="chromium-headless")
        )
        browser._is_work_education2_page = lambda: True
        action = ComputerAction(
            kind=ActionKind.SELECT,
            field_id=(
                "ceac.work_education2.work.education_secondary_or_above"
            ),
            target_hint="education",
            value="yes",
            reason="fixture",
        )
        browser._work_education2_school_rendered = lambda: False

        failed = browser.action_postcondition(action)
        browser._work_education2_school_rendered = lambda: True
        passed = browser.action_postcondition(action)

        self.assertFalse(failed[0])
        self.assertIn("Name of Institution", failed[1])
        self.assertEqual(passed, (True, ""))

    def test_checked_education_yes_reposts_until_school_panel_exists(self):
        browser = FastVisiblePlaywrightBrowser(
            ProviderConfig(provider="playwright", model="chromium-headless")
        )
        field_id = (
            "ceac.work_education2.work.education_secondary_or_above"
        )
        action = ComputerAction(
            kind=ActionKind.SELECT,
            field_id=field_id,
            target_hint=field_id,
            value="yes",
            reason="fixture",
        )
        target = CheckedChoiceLocator()
        panel = {"rendered": False}
        postbacks = []
        browser._action_locator = lambda _action: target
        browser._travel_specific_plans_choice_control = (
            lambda _group, _value: target
        )
        browser._work_education2_school_rendered = (
            lambda: panel["rendered"]
        )
        browser._require_page = lambda: None
        browser._mark_field = lambda _locator, _action: None
        browser._begin_action_dom_watch = lambda: None
        browser._prune_detached_field_bindings = lambda: None
        browser._prompt_scoped_choice_group = lambda _terms: target

        def ensure_postback(_target, _action, **kwargs):
            postbacks.append(kwargs)
            panel["rendered"] = True
            return True

        browser._ensure_travel_control_postback = ensure_postback

        browser._execute_work_education2_choice(action)

        self.assertEqual(target.clicks, 0)
        self.assertEqual(len(postbacks), 1)
        self.assertTrue(postbacks[0]["require_dependent"])
        self.assertIn(action.id, browser._acknowledged)
        self.assertEqual(browser._verified_field_values[field_id], "yes")

    def test_work_education2_revalidation_rebinds_school_fields(self):
        browser = FastVisiblePlaywrightBrowser(
            ProviderConfig(provider="playwright", model="chromium-headless")
        )
        browser._is_travel_page = lambda: False
        browser._is_passport_page = lambda: False
        browser._is_relatives_page = lambda: False
        browser._is_work_education1_page = lambda: False
        browser._is_work_education2_page = lambda: True
        course = (
            "ceac.work_education2.work.education.record.course.key"
        )
        controller = (
            "ceac.work_education2.work.education_secondary_or_above"
        )
        rebound = []

        def bind_school(field_ids, _labels):
            rebound.extend(field_ids)
            return [], []

        browser._plan_work_education2_semantic_fallback = bind_school

        unresolved = browser.rebind_page_fields_for_revalidation(
            [course, controller],
            {
                course: ("Course of Study [control=text]",),
                controller: ("Education [control=yes_no]",),
            },
        )

        self.assertEqual(rebound, [course])
        self.assertEqual(unresolved, [controller])

    def test_work_education2_school_record_uses_exact_semantic_actions(self):
        browser = FastVisiblePlaywrightBrowser(
            ProviderConfig(provider="playwright", model="chromium-headless")
        )
        browser._is_work_education2_page = lambda: True
        suffixes = (
            ("school", "text"),
            ("course", "text"),
            ("startdate", "date"),
            ("enddate", "date"),
            ("line1", "text"),
            ("city", "text"),
            ("region", "does_not_apply"),
            ("postalcode", "does_not_apply"),
            ("country", "select_text"),
        )
        fields = {
            "ceac.work_education2.work.education.record."
            f"{suffix}.key": (f"fixture [control={kind}]",)
            for suffix, kind in suffixes
        }
        browser._work_education2_semantic_control = (
            lambda _rule, kind: EducationControlLocator(kind)
        )
        browser._mark_field = lambda _locator, _action: None

        actions, unresolved = (
            browser._plan_work_education2_semantic_fallback(
                list(fields),
                fields,
            )
        )

        structural = [
            field_id for field_id, label in fields.items()
            if "control=does_not_apply" in label[0]
        ]
        ordinary = [
            field_id for field_id in fields
            if field_id not in structural
        ]
        self.assertEqual(
            [action.field_id for action in actions],
            [*structural, *ordinary],
        )
        self.assertEqual(unresolved, [])
        self.assertEqual(
            [action.kind for action in actions[:2]],
            [ActionKind.SELECT, ActionKind.SELECT],
        )

    def test_work_education2_course_and_region_use_native_ceac_ids(self):
        browser = FastVisiblePlaywrightBrowser(
            ProviderConfig(provider="playwright", model="chromium-headless")
        )
        page = ExactEducationPage()
        browser._page = page

        course_rule = browser._work_education2_semantic_rule(
            "ceac.work_education2.work.education.record.course.key"
        )
        region_rule = browser._work_education2_semantic_rule(
            "ceac.work_education2.work.education.record.region.key"
        )

        self.assertIsNotNone(
            browser._work_education2_semantic_control(course_rule, "text")
        )
        self.assertIsNotNone(
            browser._work_education2_semantic_control(region_rule, "text")
        )
        self.assertIn("text", region_rule["kinds"])
        self.assertEqual(
            page.selectors,
            [
                'input[id$="_tbxSchoolCourseOfStudy"]',
                'input[id$="_tbxEDUC_INST_ADDR_STATE"]',
            ],
        )

    def test_absent_family_followup_is_inapplicable_after_controller(self):
        browser = FastVisiblePlaywrightBrowser(
            ProviderConfig(provider="playwright", model="chromium-headless")
        )
        browser._page = FakePage()
        field_id = "ceac.relatives.family.other_relatives_us"
        browser._family_choice_group = lambda _field_id, _terms: None
        browser._family_immediate_choice_answered = lambda: True

        presence = browser._classify_relatives_field_presence(
            {"present": [], "absent": [], "unresolved": [field_id]},
            [field_id],
            {field_id: ("Other relatives [control=yes_no]",)},
        )

        self.assertEqual(presence["present"], [])
        self.assertEqual(presence["absent"], [field_id])
        self.assertEqual(presence["unresolved"], [])
        self.assertEqual(browser._page.wait_calls, 4)

    def test_travel_secondary_revalidation_accepts_connector_equivalence(self):
        browser = FastVisiblePlaywrightBrowser(
            ProviderConfig(provider="playwright", model="chromium-headless")
        )
        browser._is_travel_page = lambda: True
        browser._travel_purpose_control = (
            lambda _part, _terms: SelectedOptionLocator()
        )

        self.assertTrue(browser.travel_purpose_matches_approved(
            "ceac.travel.travel.purpose.secondary",
            "BUSINESS & TOURISM (TEMPORARY VISITOR) (B1/B2)",
        ))

    def test_headless_specific_plans_requires_complete_server_branch(self):
        browser = FastVisiblePlaywrightBrowser(
            ProviderConfig(provider="playwright", model="chromium-headless")
        )
        playwright = None
        chromium = None
        try:
            try:
                from playwright.sync_api import sync_playwright

                playwright = sync_playwright().start()
                chromium = playwright.chromium.launch(
                    headless=True,
                    executable_path=(
                        "/Applications/Google Chrome.app/Contents/MacOS/"
                        "Google Chrome"
                    ),
                )
                context = chromium.new_context()
                browser._page = context.new_page()
            except Exception as error:
                self.skipTest(f"Headless Chromium unavailable: {error}")
            browser._page.set_content(
                """
                <style>
                  label, h3, span { display: block; }
                  input, select { width: 280px; height: 28px; }
                </style>
                <form>
                  <span>Have you made specific travel plans?</span>
                  <label><input id="yes" type="radio" name="plans"
                    value="Y">Yes</label>
                  <label><input id="no" type="radio" name="plans"
                    value="N">No</label>
                  <div id="branch"></div>
                </form>
                <script>
                  document.querySelector('#no').addEventListener(
                    'change', () => {
                      document.querySelector('#branch').innerHTML = `
                        <span>Intended Date of Arrival</span>
                        <select><option>06</option></select>
                        <select><option>FEB</option></select>
                        <input>
                        <span>Intended Length of Stay in U.S.</span>
                        <input id="stay-amount"><select id="stay-unit">
                          <option value="">-SELECT ONE-</option>
                          <option value="DAY">DAY(S)</option>
                        </select>
                        <div id="address"></div>
                        <label>Person/Entity Paying for Your Trip</label>
                        <select><option>PRESENT EMPLOYER</option></select>`;
                      document.querySelector('#stay-unit').addEventListener(
                        'change', () => {
                          if (document.querySelector('#stay-unit').value !== 'DAY') {
                            document.querySelector('#address').innerHTML = '';
                            return;
                          }
                          document.querySelector('#address').innerHTML = `
                            <h3>Address Where You Will Stay in the U.S.</h3>
                            <input id="line1"><input><input>
                            <select><option>CALIFORNIA</option></select>
                            <input>`;
                        }
                      );
                    }
                  );
                </script>
                """
            )
            browser._is_travel_page = lambda: True
            field_id = "ceac.travel.travel.specific_plans"
            action = ComputerAction(
                kind=ActionKind.SELECT,
                field_id=field_id,
                target_hint=field_id,
                value="no",
            )
            browser._mark_field(browser._page.locator("#yes"), action)

            browser.execute(action)

            self.assertTrue(browser._page.locator("#no").is_checked())
            self.assertTrue(
                browser._travel_specific_plans_branch_rendered("no")
            )
            self.assertFalse(browser._travel_us_address_rendered())
            self.assertIn(action.id, browser._acknowledged)

            # Keep this test headless: emulate the already separately tested
            # native option selection while exercising the controller's
            # postback/dependent-branch contract.
            def activate_option(locator, selected):
                locator.select_option(value=str(selected.get("value") or ""))
                return True

            browser._activate_select_option = activate_option
            stay_id = "ceac.travel.travel.stayduration"
            stay = ComputerAction(
                # Production planning anchors this composite on its amount
                # input, so the action kind is TYPE even though the writer
                # also selects the sibling unit.
                kind=ActionKind.TYPE,
                field_id=stay_id,
                target_hint=stay_id,
                value="7 DAY",
            )
            browser._mark_field(
                browser._page.locator("#stay-amount"), stay
            )

            browser.execute(stay)

            self.assertEqual(
                browser._page.locator("#stay-amount").input_value(), "7"
            )
            self.assertEqual(
                browser._page.locator("#stay-unit").input_value(), "DAY"
            )
            self.assertTrue(browser._travel_us_address_rendered())
            self.assertIn(stay.id, browser._acknowledged)

            # Model a retained local value whose dependent address panel was
            # lost by a later postback.  The repair must transition through
            # the placeholder before reselecting the same reviewed unit.
            browser._page.locator("#address").evaluate(
                "el => { el.innerHTML = ''; }"
            )
            repeated = ComputerAction(
                kind=ActionKind.TYPE,
                field_id=stay_id,
                target_hint=stay_id,
                value="7 DAY",
            )
            browser._mark_field(
                browser._page.locator("#stay-amount"), repeated
            )

            browser.execute(repeated)

            self.assertEqual(
                browser._page.locator("#stay-unit").input_value(), "DAY"
            )
            self.assertTrue(browser._travel_us_address_rendered())
            self.assertIn(repeated.id, browser._acknowledged)
        finally:
            browser._page = None
            if chromium is not None:
                chromium.close()
            if playwright is not None:
                playwright.stop()

    def test_headless_fresh_travel_page_reopens_old_controllers(self):
        browser = FastVisiblePlaywrightBrowser(
            ProviderConfig(provider="playwright", model="chromium-headless")
        )
        playwright = None
        chromium = None
        try:
            try:
                from playwright.sync_api import sync_playwright

                playwright = sync_playwright().start()
                chromium = playwright.chromium.launch(
                    headless=True,
                    executable_path=(
                        "/Applications/Google Chrome.app/Contents/MacOS/"
                        "Google Chrome"
                    ),
                )
                context = chromium.new_context()
                browser._page = context.new_page()
            except Exception as error:
                self.skipTest(f"Headless Chromium unavailable: {error}")
            travel_url = (
                "https://ceac.state.gov/GenNIV/General/complete/"
                "complete_travel.aspx?node=Travel"
            )
            browser._page.route(
                travel_url,
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/html",
                    body="""
                    <style>
                      label, span { display:block; }
                      input, select { width:280px; height:28px; }
                    </style>
                    <label>Purpose of Trip to the U.S.</label>
                    <select><option value="">PLEASE SELECT A VISA CLASS</option>
                      <option value="B">TEMP. BUSINESS OR PLEASURE VISITOR (B)
                      </option></select>
                    <span>Have you made specific travel plans?</span>
                    <label><input type="radio" name="plans" value="Y">Yes</label>
                    <label><input type="radio" name="plans" value="N">No</label>
                    <span>Intended Date of Arrival</span>
                    <select><option value="">DAY</option></select>
                    <select><option value="">MONTH</option></select>
                    <input id="arrival-year">
                    <span>Intended Length of Stay in U.S.</span>
                    <input id="stay-amount"><select id="stay-unit">
                      <option value="">-SELECT ONE-</option>
                      <option value="DAY">DAY(S)</option>
                    </select>
                    <label>Person/Entity Paying for Your Trip</label>
                    <select><option value="">-SELECT ONE-</option>
                      <option>PRESENT EMPLOYER</option></select>
                    """,
                ),
            )
            browser._page.goto(travel_url, wait_until="domcontentloaded")
            primary = "ceac.travel.travel.purpose.primary"
            specific = "ceac.travel.travel.specific_plans"
            payer = "ceac.travel.travel.payer"
            arrival = "ceac.travel.travel.arrivaldate"
            stay = "ceac.travel.travel.stayduration"

            stale = browser.stale_completed_branch_controller_fields(
                [primary, specific, payer, arrival, stay],
                {
                    primary: "TEMP. BUSINESS OR PLEASURE VISITOR (B)",
                    specific: "no",
                    payer: "PRESENT EMPLOYER",
                    arrival: "2027-02-06",
                    stay: "7 DAY",
                },
                {
                    arrival: (
                        "Intended Date of Arrival [control=date; "
                        "human-approved value=2027-02-06]",
                    ),
                    stay: (
                        "Intended Length of Stay [control=duration; "
                        "human-approved value=7 DAY]",
                    ),
                },
            )

            self.assertEqual(
                set(stale),
                {primary, specific, payer, arrival, stay},
            )
        finally:
            browser._page = None
            if chromium is not None:
                chromium.close()
            if playwright is not None:
                playwright.stop()

    def test_headless_travel_us_address_order_fallback(self):
        browser = FastVisiblePlaywrightBrowser(
            ProviderConfig(provider="playwright", model="chromium-headless")
        )
        playwright = None
        chromium = None
        try:
            try:
                from playwright.sync_api import sync_playwright

                playwright = sync_playwright().start()
                chromium = playwright.chromium.launch(
                    headless=True,
                    executable_path=(
                        "/Applications/Google Chrome.app/Contents/MacOS/"
                        "Google Chrome"
                    ),
                )
                context = chromium.new_context()
                browser._page = context.new_page()
            except Exception as error:
                self.skipTest(f"Headless Chromium unavailable: {error}")
            browser._page.set_content(
                """
                <style>
                  label, h3 { display: block; }
                  input, select { display: block; width: 300px; height: 30px; }
                </style>
                <h3>Address Where You Will Stay in the U.S.</h3>
                <label>Street Address (Line 1)<img alt="required"></label>
                <input id="us-line1">
                <label>Street Address (Line 2) *Optional</label>
                <input id="us-line2">
                <label>City<img alt="required"></label>
                <input id="us-city">
                <label>State<img alt="required"></label>
                <select id="us-state"><option>- SELECT ONE -</option></select>
                <label>ZIP Code (if known)</label>
                <input id="us-zip">
                <label>Person/Entity Paying for Your Trip</label>
                <select id="payer"><option>PRESENT EMPLOYER</option></select>
                """
            )
            expected = {
                "ceac.travel.travel.usstreet1": "us-line1",
                "ceac.travel.travel.usstreet2": "us-line2",
                "ceac.travel.travel.uscity": "us-city",
                "ceac.travel.travel.usstate": "us-state",
                "ceac.travel.travel.uspostalcode": "us-zip",
            }
            for field_id, control_id in expected.items():
                locator = browser._travel_us_address_control_by_order(
                    field_id
                )
                self.assertIsNotNone(locator)
                self.assertEqual(locator.get_attribute("id"), control_id)
        finally:
            browser._page = None
            if chromium is not None:
                chromium.close()
            if playwright is not None:
                playwright.stop()

    def test_only_external_payer_choices_require_detail_branch(self):
        requires = FastVisiblePlaywrightBrowser._travel_payer_requires_details

        self.assertFalse(requires("SELF"))
        self.assertFalse(requires("present_employer"))
        self.assertFalse(requires("EMPLOYER IN THE U.S."))
        self.assertTrue(requires("OTHER PERSON"))
        self.assertTrue(requires("other_company_organization"))

    def test_network_receipt_ignores_unrelated_get(self):
        post = FakeRequest("post")
        get = FakeRequest("get")
        get.method = "GET"

        self.assertTrue(
            FastVisiblePlaywrightBrowser._is_dynamic_request(post)
        )
        self.assertFalse(
            FastVisiblePlaywrightBrowser._is_dynamic_request(get)
        )

    def test_completed_post_is_not_success_without_dependent_branch(self):
        browser = FastVisiblePlaywrightBrowser(
            ProviderConfig(provider="playwright", model="chromium")
        )
        browser._page = FakePage()
        browser.FALSE_POSTBACK_GRACE_SECONDS = 0.05
        browser._v2_network_before = {
            "started": 0,
            "ended": 0,
            "inflightTokens": set(),
        }
        browser._v2_network_started = 1
        browser._v2_network_ended = 1
        browser.dynamic_refresh_detected = lambda _action: False

        received = browser._await_control_postback_receipt(
            object(),
            dispatch_kind="native-change",
            require_dependent=True,
            dependent_probe=lambda: False,
        )

        self.assertFalse(received)
        self.assertTrue(
            browser._last_dynamic_refresh_evidence.get(
                "postbackCompletedWithoutDependentBranch"
            )
        )
        browser._page = None

    def test_dependent_branch_wins_over_stuck_network_bookkeeping(self):
        browser = FastVisiblePlaywrightBrowser(
            ProviderConfig(provider="playwright", model="chromium")
        )
        browser._page = FakePage()
        browser.FALSE_POSTBACK_GRACE_SECONDS = 0.1
        browser.CONTROL_POSTBACK_SETTLE_TIMEOUT_SECONDS = 0.2
        browser._v2_network_before = {
            "started": 0,
            "ended": 0,
            "inflightTokens": set(),
        }
        browser._v2_network_started = 1
        browser._v2_network_ended = 0
        browser._v2_network_inflight = {"guid:stale-post-wrapper"}
        browser.dynamic_refresh_detected = lambda _action: False
        probes = {"count": 0}

        def dependent_probe():
            probes["count"] += 1
            return probes["count"] >= 3

        received = browser._await_control_postback_receipt(
            object(),
            dispatch_kind="native-change",
            require_dependent=True,
            dependent_probe=dependent_probe,
        )

        self.assertTrue(received)
        self.assertTrue(
            browser._last_dynamic_refresh_evidence.get(
                "dependentBranchRendered"
            )
        )
        self.assertFalse(
            browser._last_dynamic_refresh_evidence.get(
                "networkRequestCompleted",
                False,
            )
        )
        browser._page = None

    def test_late_dependent_branch_wins_at_network_timeout_boundary(self):
        browser = FastVisiblePlaywrightBrowser(
            ProviderConfig(provider="playwright", model="chromium")
        )
        browser._page = FakePage()
        browser.FALSE_POSTBACK_GRACE_SECONDS = 0.05
        browser._v2_network_before = {
            "started": 0,
            "ended": 0,
            "inflightTokens": set(),
        }
        browser._v2_network_started = 1
        browser._v2_network_ended = 0
        browser.dynamic_refresh_detected = lambda _action: False
        branch = {"rendered": False}

        def finish_on_timeout(*_args, **_kwargs):
            branch["rendered"] = True
            return "network-timeout"

        browser._wait_for_dynamic_network_idle = finish_on_timeout
        received = browser._await_control_postback_receipt(
            object(),
            dispatch_kind="native-change",
            require_dependent=True,
            dependent_probe=lambda: branch["rendered"],
        )

        self.assertTrue(received)
        self.assertTrue(
            browser._last_dynamic_refresh_evidence.get(
                "dependentBranchRendered"
            )
        )
        browser._page = None

    def test_no_detail_payer_accepts_real_post_start_without_idle(self):
        browser = FastVisiblePlaywrightBrowser(
            ProviderConfig(provider="playwright", model="chromium")
        )
        browser._page = FakePage()
        browser._v2_network_before = {
            "started": 0,
            "ended": 0,
            "inflightTokens": set(),
        }
        browser._v2_network_started = 1
        browser._v2_network_ended = 0
        browser._v2_network_inflight = {"guid:present-employer-post"}
        browser.dynamic_refresh_detected = lambda _action: False

        received = browser._await_control_postback_receipt(
            object(),
            dispatch_kind="native-change",
            accept_post_start=True,
        )

        self.assertTrue(received)
        self.assertTrue(
            browser._last_dynamic_refresh_evidence.get(
                "noDependentBranchExpected"
            )
        )
        self.assertFalse(
            browser._last_dynamic_refresh_evidence.get(
                "networkRequestCompleted",
                False,
            )
        )
        browser._page = None

    def test_travel_duration_accepts_stale_single_letter_unit(self):
        self.assertEqual(
            FastVisiblePlaywrightBrowser._travel_stay_duration_parts("7 D"),
            ("7", "DAY"),
        )

    def test_false_postback_signal_does_not_wait_eight_seconds(self):
        browser = FastVisiblePlaywrightBrowser(
            ProviderConfig(provider="playwright", model="chromium")
        )
        browser._page = FakePage()
        browser._last_dynamic_refresh_evidence = {
            "postbackStarted": True,
        }
        browser._action_dom_generation_before = "same-generation"
        browser._action_field_tokens_before = {"field-token"}
        browser._v2_async_before = {
            "available": True,
            "begun": 0,
            "ended": 0,
            "inflight": 0,
        }

        started = time.monotonic()
        browser._wait_for_watched_dom_replacement()
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 1.5)
        self.assertTrue(
            browser._last_dynamic_refresh_evidence.get(
                "v2FalsePostbackSignal"
            )
        )
        # Avoid the destructor treating this fake as a real browser page.
        browser._page = None

    def test_delayed_full_postback_is_not_mistaken_for_false_signal(self):
        browser = FastVisiblePlaywrightBrowser(
            ProviderConfig(provider="playwright", model="chromium")
        )
        page = DelayedNetworkPage()
        browser._page = page
        # A request that predates this field action must not prevent this
        # action's own postback from settling.
        browser._v2_network_inflight.add("guid:earlier-page-request")
        browser._begin_action_dom_watch()
        browser._last_dynamic_refresh_evidence = {
            "postbackStarted": True,
        }

        started = time.monotonic()
        browser._wait_for_watched_dom_replacement()
        elapsed = time.monotonic() - started

        self.assertGreater(elapsed, 0.9)
        self.assertLess(elapsed, 2.0)
        self.assertTrue(page.request_started)
        self.assertTrue(page.request_finished)
        self.assertEqual(
            browser._v2_network_inflight,
            {"guid:earlier-page-request"},
        )
        self.assertFalse(
            browser._last_dynamic_refresh_evidence.get(
                "v2FalsePostbackSignal",
                False,
            )
        )
        browser._page = None


if __name__ == "__main__":
    unittest.main()
