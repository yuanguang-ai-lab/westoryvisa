import unittest

from visa_agent_v2.preflight import (
    job_preflight_issues,
    require_job_preflight,
)


def field(field_id, value="confirmed"):
    return {"id": field_id, "value": value}


class V2PreflightTests(unittest.TestCase):
    def test_missing_travel_branch_fields_are_reported_before_browser_open(self):
        payload = {
            "fields": [
                field("ceac.travel.travel.specific_plans", "no"),
                field("ceac.travel.travel.payer", "other_organization"),
                field("ceac.travel.travel.payerphone", "123"),
                field(
                    "ceac.travel.travel.payeraddress.record.line1.x",
                    "1 TEST ROAD",
                ),
                field(
                    "ceac.travel.travel.payeraddress.record.city.x",
                    "SHENZHEN",
                ),
                field(
                    "ceac.travel.travel.payeraddress.record.region.x",
                    "GUANGDONG",
                ),
            ],
        }

        issues = job_preflight_issues(payload)

        self.assertEqual(issues, [
            "预计抵达日期",
            "预计停留时长",
            "付款公司或机构名称",
            "付款机构地址邮编",
            "付款机构地址国家/地区",
        ])
        with self.assertRaisesRegex(
            ValueError,
            "V2 资料预检未通过",
        ):
            require_job_preflight(payload)

    def test_complete_travel_branch_passes(self):
        payload = {
            "fields": [
                field("ceac.travel.travel.specific_plans", "no"),
                field("ceac.travel.travel.arrivaldate", "2026-10-01"),
                field("ceac.travel.travel.stayduration", "10 DAY"),
                field("ceac.travel.travel.payer", "other_organization"),
                field(
                    "ceac.travel.travel.payerorganization",
                    "TEST COMPANY",
                ),
                field("ceac.travel.travel.payerphone", "123"),
                field(
                    "ceac.travel.travel.payeraddress.record.line1.x",
                    "1 TEST ROAD",
                ),
                field(
                    "ceac.travel.travel.payeraddress.record.city.x",
                    "SHENZHEN",
                ),
                field(
                    "ceac.travel.travel.payeraddress.record.region.x",
                    "GUANGDONG",
                ),
                field(
                    "ceac.travel.travel.payeraddress.record.postalcode.x",
                    "518000",
                ),
                field(
                    "ceac.travel.travel.payeraddress.record.country.x",
                    "CHINA",
                ),
            ],
        }

        self.assertEqual(job_preflight_issues(payload), [])
        require_job_preflight(payload)

    def test_self_paid_trip_does_not_require_payer_details(self):
        payload = {
            "fields": [
                field("ceac.travel.travel.specific_plans", "yes"),
                field("ceac.travel.travel.payer", "self"),
            ],
        }

        self.assertEqual(job_preflight_issues(payload), [])

    def test_present_employer_does_not_require_payer_details(self):
        payload = {
            "fields": [
                field("ceac.travel.travel.specific_plans", "yes"),
                field("ceac.travel.travel.payer", "present_employer"),
            ],
        }

        self.assertEqual(job_preflight_issues(payload), [])

    def test_passport_city_and_issue_date_are_required_before_browser_open(self):
        payload = {
            "fields": [
                field("ceac.passport.number", "E12345678"),
                field("ceac.passport.issuingauthority", "CHINA"),
                field("ceac.passport.issuecountry", "CHINA"),
                field("ceac.passport.expiration", "2034-07-11"),
            ],
        }

        self.assertEqual(
            job_preflight_issues(payload),
            ["护照签发城市", "护照签发日期"],
        )
        with self.assertRaisesRegex(ValueError, "护照签发城市"):
            require_job_preflight(payload)

    def test_complete_passport_required_fields_pass(self):
        payload = {
            "fields": [
                field("ceac.passport.number", "E12345678"),
                field("ceac.passport.issuingauthority", "CHINA"),
                field("ceac.passport.issuecity", "HANGZHOU"),
                field("ceac.passport.issuedate", "2024-07-12"),
                field("ceac.passport.expiration", "2034-07-11"),
            ],
        }

        self.assertEqual(job_preflight_issues(payload), [])

    def test_missing_education_address_is_reported_before_browser_open(self):
        payload = {
            "fields": [
                field(
                    "ceac.work_education2.work.education_secondary_or_above",
                    "yes",
                ),
                field(
                    "ceac.work_education2.work.education.record.school.abc",
                    "LEQING ACADEMY",
                ),
                field(
                    "ceac.work_education2.work.education.record.startdate.abc",
                    "2019-09-01",
                ),
            ],
        }

        self.assertEqual(job_preflight_issues(payload), [
            "教育机构地址第一行",
            "教育机构城市",
            "教育机构国家/地区",
        ])

    def test_complete_education_address_passes(self):
        payload = {
            "fields": [
                field(
                    "ceac.work_education2.work.education_secondary_or_above",
                    "yes",
                ),
                field(
                    "ceac.work_education2.work.education.record.line1.abc",
                    "88 EAST ROAD",
                ),
                field(
                    "ceac.work_education2.work.education.record.city.abc",
                    "LEQING",
                ),
                field(
                    "ceac.work_education2.work.education.record.country.abc",
                    "CHINA",
                ),
            ],
        }

        self.assertEqual(job_preflight_issues(payload), [])


if __name__ == "__main__":
    unittest.main()
