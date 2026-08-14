import unittest

from visa_agent.models import BrowserObservation

from visa_agent_v2.safety import FastVisaFormSafetyPolicy


ADDRESS_PHONE_URL = (
    "https://ceac.state.gov/GenNIV/General/complete/"
    "complete_contact.aspx?node=AddressPhone"
)


class V2SafetyTests(unittest.TestCase):
    def test_social_media_username_text_is_not_a_login_checkpoint(self):
        observation = BrowserObservation(
            url=ADDRESS_PHONE_URL,
            title="Address and Phone Information",
            visible_text=(
                "Address and Phone Information\n"
                "Social Media Username or Identifier"
            ),
            form_control_count=12,
        )

        decision = FastVisaFormSafetyPolicy().inspect_page(observation)

        self.assertTrue(decision.allowed)
        self.assertFalse(decision.requires_human)

    def test_real_hard_boundary_is_still_blocked(self):
        observation = BrowserObservation(
            url=ADDRESS_PHONE_URL,
            title="Address and Phone Information",
            visible_text="Please complete CAPTCHA",
            form_control_count=12,
        )

        decision = FastVisaFormSafetyPolicy().inspect_page(observation)

        self.assertFalse(decision.allowed)
        self.assertTrue(decision.requires_human)
        self.assertIn("captcha", decision.reason.casefold())


if __name__ == "__main__":
    unittest.main()
