import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import school_directory


class SchoolDirectoryTests(unittest.TestCase):
    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")

    def test_lookup_splits_school_address_and_reuses_cache(self):
        candidate = {
            "display_name": (
                "Yingkou No. 1 Senior High School, Bohai Street, "
                "Yingkou, Liaoning, 115000, China"
            ),
            "category": "amenity",
            "type": "school",
            "importance": 0.7,
            "namedetails": {
                "name": "营口市第一高级中学",
                "name:en": "Yingkou No. 1 Senior High School",
            },
            "address": {
                "road": "Bohai Street",
                "city": "Yingkou",
                "state": "Liaoning",
                "postcode": "115000",
                "country": "China",
                "country_code": "cn",
            },
        }
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            os.environ,
            {
                "SCHOOL_LOOKUP_PROVIDER": "nominatim",
                "SCHOOL_LOOKUP_CACHE": str(Path(directory) / "schools.json"),
                "SCHOOL_VERIFIED_DIRECTORY": str(Path(directory) / "verified.json"),
            },
            clear=False,
        ), mock.patch(
            "school_directory._wait_for_rate_limit"
        ), mock.patch(
            "school_directory.url_request.urlopen",
            return_value=self.FakeResponse([candidate]),
        ) as urlopen:
            first = school_directory.lookup_school("营口市第一高级中学")
            second = school_directory.lookup_school("营口市第一高级中学")

        self.assertEqual(first["officialEnglishName"], "Yingkou No. 1 Senior High School")
        self.assertEqual(first["address"], "Bohai Street")
        self.assertEqual(first["city"], "Yingkou")
        self.assertEqual(first["region"], "Liaoning")
        self.assertEqual(first["postalCode"], "115000")
        self.assertEqual(first["country"], "CHINA")
        self.assertEqual(second, first)
        urlopen.assert_called_once()
        request = urlopen.call_args.args[0]
        self.assertIn("q=%E8%90%A5%E5%8F%A3", request.full_url)
        self.assertIn("DocFlow-DS160", request.headers["User-agent"])

    def test_enrichment_preserves_user_values_and_fills_missing_parts(self):
        updates = {
            "work.education_secondary_or_above": {
                "answer": "yes",
                "records": [{
                    "school": "辽宁理工学院",
                    "city": "JINZHOU",
                    "course": "FINANCIAL MANAGEMENT",
                }],
            }
        }
        lookup = {
            "school": "辽宁理工学院",
            "officialEnglishName": "Liaoning Institute of Science and Engineering",
            "address": "No. 169 Kunming Street",
            "city": "Jinzhou",
            "region": "Liaoning",
            "postalCode": "121000",
            "country": "CHINA",
            "provider": "nominatim_openstreetmap",
        }
        with mock.patch("school_directory.lookup_school", return_value=lookup):
            enriched, count = school_directory.enrich_education_updates(updates)

        record = enriched["work.education_secondary_or_above"]["records"][0]
        self.assertEqual(count, 1)
        self.assertEqual(
            record["school"], "LIAONING INSTITUTE OF SCIENCE AND ENGINEERING"
        )
        self.assertEqual(record["city"], "JINZHOU")
        self.assertEqual(record["address"], "NO. 169 KUNMING STREET")
        self.assertEqual(record["region"], "LIAONING")
        self.assertEqual(record["postalCode"], "121000")
        self.assertEqual(record["course"], "FINANCIAL MANAGEMENT")

    def test_verified_directory_replaces_legacy_pinyin_and_fills_all_components(self):
        with mock.patch.dict(os.environ, {"SCHOOL_LOOKUP_PROVIDER": "off"}):
            result = school_directory.lookup_school(
                "YING KOU SHI DI YI GAO JI MIDDLE SCHOOL"
            )

        self.assertEqual(result["officialEnglishName"], "YINGKOU SENIOR HIGH SCHOOL")
        self.assertEqual(result["address"], "NO. 1 JINXIU AVENUE, LAOBIAN DISTRICT")
        self.assertEqual(result["city"], "YINGKOU")
        self.assertEqual(result["region"], "LIAONING")
        self.assertEqual(result["postalCode"], "115005")
        self.assertEqual(result["provider"], "verified_local_directory")

    def test_original_chinese_school_upgrades_record_without_losing_dates(self):
        record = {
            "level": "college",
            "school": "LIAO NING LI GONG COLLEGE",
            "address": "LIAO NING SHENG JIN ZHOU SHI KUN MING JIE2HAO",
            "course": "FINANCIAL MANAGEMENT",
            "startDate": "2021.9.10",
            "endDate": "2025.6.10",
        }
        original = {"school": "辽宁理工学院"}

        enriched, changed = school_directory.enrich_education_record(record, original)

        self.assertTrue(changed)
        self.assertEqual(
            enriched["school"], "LIAONING INSTITUTE OF SCIENCE AND ENGINEERING"
        )
        self.assertEqual(
            enriched["address"], "NO. 2 KUNMING STREET, HIGH-TECH INDUSTRIAL PARK"
        )
        self.assertEqual(enriched["city"], "JINZHOU")
        self.assertEqual(enriched["region"], "LIAONING")
        self.assertEqual(enriched["postalCode"], "121013")
        self.assertEqual(enriched["startDate"], "2021.9.10")
        self.assertEqual(enriched["endDate"], "2025.6.10")

    def test_generic_school_uses_wikidata_name_and_reverse_geocoded_postcode(self):
        nominatim_candidate = {
            "display_name": "杭州市学军中学, 杭州市, 浙江省, 中国",
            "category": "amenity",
            "type": "school",
            "importance": 0.72,
            "lat": "30.278",
            "lon": "120.141",
            "osm_type": "way",
            "osm_id": 12345,
            "namedetails": {"name": "杭州市学军中学"},
            "extratags": {"wikidata": "Q123456"},
            "address": {
                "road": "文三路",
                "city": "杭州市",
                "state": "浙江省",
                "country": "中国",
                "country_code": "cn",
            },
        }
        wikidata_entity = {
            "labels": {
                "zh": {"value": "杭州市学军中学"},
                "en": {"value": "Hangzhou Xuejun High School"},
            },
            "descriptions": {"en": {"value": "secondary school in Hangzhou, China"}},
            "claims": {},
        }
        reverse_candidate = {
            "lat": "30.278",
            "lon": "120.141",
            "address": {
                "house_number": "188",
                "road": "Wensan Road",
                "city": "Hangzhou",
                "state": "Zhejiang",
                "postcode": "310012",
                "country": "China",
                "country_code": "cn",
            },
        }

        def response_for(request, timeout=0):
            del timeout
            if "action=wbgetentities" in request.full_url:
                return self.FakeResponse({"entities": {"Q123456": wikidata_entity}})
            if "/reverse?" in request.full_url:
                return self.FakeResponse(reverse_candidate)
            return self.FakeResponse([nominatim_candidate])

        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            os.environ,
            {
                "SCHOOL_LOOKUP_PROVIDER": "auto",
                "SCHOOL_LOOKUP_CACHE": str(Path(directory) / "schools.json"),
                "SCHOOL_VERIFIED_DIRECTORY": str(Path(directory) / "verified.json"),
            },
            clear=False,
        ), mock.patch(
            "school_directory._wait_for_rate_limit"
        ), mock.patch(
            "school_directory.structure_address",
            return_value={
                "line1": "WENSAN ROAD",
                "line2": "",
                "city": "HANGZHOU",
                "region": "ZHEJIANG",
                "postalCode": "",
                "country": "CHINA",
            },
        ), mock.patch(
            "school_directory.url_request.urlopen", side_effect=response_for
        ) as urlopen:
            result = school_directory.lookup_school("杭州市学军中学")

        self.assertEqual(result["officialEnglishName"], "Hangzhou Xuejun High School")
        self.assertEqual(result["address"], "WENSAN ROAD")
        self.assertEqual(result["city"], "HANGZHOU")
        self.assertEqual(result["region"], "ZHEJIANG")
        self.assertEqual(result["postalCode"], "310012")
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["nameProvider"], "wikidata")
        self.assertEqual(len(result["sources"]), 2)
        self.assertEqual(urlopen.call_count, 3)

    def test_wikidata_coordinates_can_resolve_school_missing_from_nominatim_search(self):
        search_item = {
            "id": "Q999001",
            "label": "虚构测试大学",
            "description": "中国的大学",
        }
        entity = {
            "labels": {
                "zh": {"value": "虚构测试大学"},
                "en": {"value": "Example Test University"},
            },
            "descriptions": {"en": {"value": "university in China"}},
            "claims": {
                "P625": [{
                    "mainsnak": {
                        "datavalue": {
                            "value": {"latitude": 31.2, "longitude": 121.5}
                        }
                    }
                }]
            },
        }
        reverse = {
            "lat": "31.2",
            "lon": "121.5",
            "address": {
                "house_number": "20",
                "road": "Test Road",
                "city": "Shanghai",
                "state": "Shanghai",
                "postcode": "200000",
                "country": "China",
                "country_code": "cn",
            },
        }

        def response_for(request, timeout=0):
            del timeout
            if "action=wbsearchentities" in request.full_url:
                return self.FakeResponse({"search": [search_item]})
            if "action=wbgetentities" in request.full_url:
                return self.FakeResponse({"entities": {"Q999001": entity}})
            if "/reverse?" in request.full_url:
                return self.FakeResponse(reverse)
            return self.FakeResponse([])

        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            os.environ,
            {
                "SCHOOL_LOOKUP_PROVIDER": "auto",
                "SCHOOL_LOOKUP_CACHE": str(Path(directory) / "schools.json"),
                "SCHOOL_VERIFIED_DIRECTORY": str(Path(directory) / "verified.json"),
            },
            clear=False,
        ), mock.patch(
            "school_directory._wait_for_rate_limit"
        ), mock.patch(
            "school_directory.url_request.urlopen", side_effect=response_for
        ):
            result = school_directory.lookup_school("虚构测试大学")

        self.assertEqual(result["officialEnglishName"], "Example Test University")
        self.assertEqual(result["address"], "20 Test Road")
        self.assertEqual(result["postalCode"], "200000")
        self.assertEqual(result["provider"], "wikidata")

    def test_non_school_and_ambiguous_results_are_not_auto_selected(self):
        restaurant = {
            "display_name": "实验中学餐厅, Beijing, China",
            "category": "amenity",
            "type": "restaurant",
            "namedetails": {"name": "实验中学"},
            "address": {"country_code": "cn"},
        }
        ambiguous = [
            {
                "display_name": "北京实验中学, Beijing, China",
                "category": "amenity",
                "type": "school",
                "namedetails": {"name": "北京实验中学"},
                "address": {"country_code": "cn"},
            },
            {
                "display_name": "上海实验中学, Shanghai, China",
                "category": "amenity",
                "type": "school",
                "namedetails": {"name": "上海实验中学"},
                "address": {"country_code": "cn"},
            },
        ]
        self.assertEqual(school_directory._candidate_score(restaurant, "实验中学"), -100)
        selected, confidence = school_directory._select_nominatim_candidate(
            ambiguous, "实验中学"
        )
        self.assertIsNone(selected)
        self.assertEqual(confidence, 0)

    def test_unresolved_school_is_marked_without_inventing_address(self):
        record = {"school": "MYSTERY PINYIN XUE XIAO", "startDate": "2020-09-01"}
        original = {"school": "不存在的测试学校"}
        with mock.patch.dict(os.environ, {"SCHOOL_LOOKUP_PROVIDER": "off"}):
            enriched, changed = school_directory.enrich_education_record(record, original)

        self.assertFalse(changed)
        self.assertEqual(enriched["schoolLookupStatus"], "unresolved")
        self.assertNotIn("address", enriched)
        self.assertEqual(enriched["startDate"], "2020-09-01")

    def test_foreign_school_search_is_not_hard_limited_to_china(self):
        candidate = {
            "display_name": "Lincoln High School, Seattle, Washington, United States",
            "category": "amenity",
            "type": "school",
            "importance": 0.6,
            "namedetails": {"name": "Lincoln High School", "name:en": "Lincoln High School"},
            "address": {
                "house_number": "4400",
                "road": "Interlake Avenue North",
                "city": "Seattle",
                "state": "Washington",
                "postcode": "98103",
                "country": "United States",
                "country_code": "us",
            },
        }
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            os.environ,
            {
                "SCHOOL_LOOKUP_PROVIDER": "auto",
                "SCHOOL_LOOKUP_COUNTRYCODES": "",
                "SCHOOL_LOOKUP_CACHE": str(Path(directory) / "schools.json"),
                "SCHOOL_VERIFIED_DIRECTORY": str(Path(directory) / "verified.json"),
            },
            clear=False,
        ), mock.patch(
            "school_directory._wait_for_rate_limit"
        ), mock.patch(
            "school_directory.url_request.urlopen",
            return_value=self.FakeResponse([candidate]),
        ) as urlopen:
            result = school_directory.lookup_school("Lincoln High School")

        self.assertEqual(result["officialEnglishName"], "Lincoln High School")
        self.assertEqual(result["city"], "Seattle")
        self.assertEqual(result["country"], "United States")
        self.assertNotIn("countrycodes=", urlopen.call_args.args[0].full_url)


if __name__ == "__main__":
    unittest.main()
