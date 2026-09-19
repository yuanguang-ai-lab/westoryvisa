import unittest

from visa_agent.adapters import PlaywrightBrowserDriver
from visa_agent.config import ProviderConfig
from visa_agent.models import BrowserObservation
from visa_agent.page_plans import (
    PagePlanRegistry,
    classify_ceac_page,
)
from visa_agent.safety import VisaFormSafetyPolicy


TRAVEL_URL = (
    "https://ceac.state.gov/GenNIV/General/complete/"
    "complete_travel.aspx?node=Travel"
)
PERSONAL_URL = (
    "https://ceac.state.gov/GenNIV/General/complete/"
    "complete_personal.aspx?node=Personal1"
)


class FakePage:
    def __init__(
        self,
        url,
        *,
        title="",
        visible_text="",
        form_control_count=0,
    ):
        self.url = url
        self.title = title
        self.visible_text = visible_text
        self.form_control_count = form_control_count
        self.front_count = 0
        self.goto_calls = []

    def is_closed(self):
        return False

    def bring_to_front(self):
        self.front_count += 1

    def goto(self, url, **kwargs):
        self.goto_calls.append((url, kwargs))
        self.url = url


class FakeContext:
    def __init__(self, pages):
        self.pages = pages

    def new_page(self):
        page = FakePage("about:blank")
        self.pages.append(page)
        return page


class CEACPageClassificationTests(unittest.TestCase):
    def observation(self, url, title="", visible_text="", controls=0):
        return BrowserObservation(
            url=url,
            title=title,
            visible_text=visible_text,
            form_control_count=controls,
        )

    def test_formal_route_requires_live_structure(self):
        empty_shell = self.observation(
            TRAVEL_URL,
            title="Nonimmigrant Visa - Instructions Page",
        )
        live_form = self.observation(
            TRAVEL_URL,
            title="任意本地化标题",
            controls=7,
        )

        self.assertEqual(
            classify_ceac_page(empty_shell).kind,
            "unsupported",
        )
        self.assertEqual(
            classify_ceac_page(live_form).kind,
            "formal",
        )
        self.assertIsNone(PagePlanRegistry.default().match(empty_shell))
        self.assertEqual(
            PagePlanRegistry.default().match(live_form).id,
            "ceac-plan-travel",
        )

    def test_exact_live_plans_inherit_field_owned_legacy_descriptors(self):
        registry = PagePlanRegistry.default()
        personal1 = registry.match(self.observation(
            PERSONAL_URL,
            title="Personal Information 1",
            controls=4,
        ))
        personal2 = registry.match(self.observation(
            "https://ceac.state.gov/GenNIV/General/complete/"
            "complete_personalcont.aspx?node=Personal2",
            title="Personal Information 2",
            controls=4,
        ))
        passport = registry.match(self.observation(
            "https://ceac.state.gov/GenNIV/General/complete/"
            "complete_pptvisa.aspx?node=PptVisa",
            title="Passport Information",
            controls=4,
        ))
        sevis = registry.match(self.observation(
            "https://ceac.state.gov/GenNIV/General/complete/"
            "complete_sevis.aspx?node=SEVIS",
            title="Student and Exchange Visitor Information",
            controls=4,
        ))

        self.assertEqual(personal1.id, "ceac-plan-personal1")
        self.assertIn("Surnames", personal1.field_labels["personal.surname"])
        self.assertIn(
            "APP_SURNAME",
            personal1.control_hints["personal.surname"],
        )
        # Nationality was historically described by the broad Personal plan,
        # but its canonical physical owner is Personal 2.
        self.assertEqual(personal2.id, "ceac-plan-personal2")
        self.assertIn(
            "Nationality",
            personal2.field_labels["personal.nationality"],
        )
        self.assertIn(
            "APP_NATL",
            personal2.control_hints["personal.nationality"],
        )
        self.assertIn(
            "PPT_NUM",
            passport.control_hints["passport.number"],
        )
        self.assertIn(
            "SCHOOL_NAME",
            sevis.control_hints["education.schoolName"],
        )

    def test_formal_url_rendering_timeout_is_never_formal(self):
        expired = self.observation(
            TRAVEL_URL,
            title=(
                "Consular Electronic Application Center - Session Timed Out"
            ),
            visible_text="Your session has timed out.",
            controls=12,
        )

        classification = classify_ceac_page(expired)

        self.assertEqual(classification.kind, "session_timeout")
        self.assertIsNone(PagePlanRegistry.default().match(expired))
        decision = VisaFormSafetyPolicy().inspect_page(expired)
        self.assertFalse(decision.allowed)
        self.assertTrue(decision.requires_human)
        self.assertIn("CEAC 会话已超时", decision.reason)

    def test_classifier_distinguishes_every_runtime_boundary(self):
        cases = {
            "default": self.observation(
                "https://ceac.state.gov/GenNIV/Default.aspx",
                visible_text="Start an Application Retrieve an Application",
            ),
            "recovery": self.observation(
                "https://ceac.state.gov/GenNIV/Common/Recovery.aspx",
                title="Recover Your Application",
            ),
            "captcha": self.observation(
                PERSONAL_URL,
                title="Personal Information 1",
                visible_text="Please complete CAPTCHA",
                controls=4,
            ),
            "sign": self.observation(
                "https://ceac.state.gov/GenNIV/General/Sign/"
                "SignReview.aspx?node=Sign",
                title="Sign and Submit",
            ),
            "final_submit": self.observation(
                "https://ceac.state.gov/GenNIV/General/Review/"
                "ReviewReview.aspx?node=Review",
                title="Review Application",
            ),
            "unsupported": self.observation(
                "https://ceac.state.gov/GenNIV/Common/Unknown.aspx",
                title="Unknown",
            ),
        }

        for expected, observation in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(
                    classify_ceac_page(observation).kind,
                    expected,
                )

    def test_restored_timeout_tab_cannot_beat_live_form(self):
        expired = FakePage(
            TRAVEL_URL,
            title="Session Timed Out",
            visible_text="Session Timed Out",
            form_control_count=10,
        )
        live = FakePage(
            PERSONAL_URL,
            title="Personal Information 1",
            form_control_count=3,
        )
        driver = PlaywrightBrowserDriver(ProviderConfig(provider="playwright"))
        driver._page = expired
        driver._context = FakeContext([expired, live])

        driver._select_best_page()

        self.assertIs(driver._page, live)
        self.assertEqual(live.front_count, 1)
        driver._temporary.cleanup()

    def test_recovery_page_is_preserved_instead_of_overwritten_by_default(self):
        recovery = FakePage(
            "https://ceac.state.gov/GenNIV/Common/Recovery.aspx",
            title="Recover Your Application",
        )
        driver = PlaywrightBrowserDriver(ProviderConfig(provider="playwright"))
        driver._context = FakeContext([recovery])

        driver._reuse_restored_page_or_navigate(
            "https://ceac.state.gov/GenNIV/Default.aspx"
        )

        self.assertIs(driver._page, recovery)
        self.assertEqual(recovery.goto_calls, [])
        driver._temporary.cleanup()

    def test_loading_formal_route_is_not_clobbered_before_dom_settles(self):
        loading = FakePage(
            TRAVEL_URL,
            title="",
            visible_text="",
            form_control_count=0,
        )
        driver = PlaywrightBrowserDriver(ProviderConfig(provider="playwright"))
        driver._context = FakeContext([loading])

        driver._reuse_restored_page_or_navigate(
            "https://ceac.state.gov/GenNIV/Default.aspx"
        )

        self.assertEqual(
            driver._classify_live_page(loading).kind,
            "unsupported",
        )
        self.assertEqual(loading.goto_calls, [])
        driver._temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
