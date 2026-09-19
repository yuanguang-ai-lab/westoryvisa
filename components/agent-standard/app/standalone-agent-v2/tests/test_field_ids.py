import unittest

from visa_agent_v2.field_ids import (
    semantic_field_id,
    semantic_field_id_endswith,
)


class SemanticFieldIdTests(unittest.TestCase):
    def test_production_instance_hash_is_removed(self):
        field_id = "ceac.travel.travel.purpose.secondary.45f2572d8486"

        self.assertEqual(
            semantic_field_id(field_id),
            "ceac.travel.travel.purpose.secondary",
        )
        self.assertTrue(semantic_field_id_endswith(
            field_id,
            ".travel.purpose.secondary",
        ))

    def test_legacy_unhashed_id_still_matches(self):
        self.assertTrue(semantic_field_id_endswith(
            "ceac.travel.travel.purpose.primary",
            (
                ".travel.purpose.primary",
                ".travel.purpose.secondary",
            ),
        ))

    def test_non_hash_suffix_is_not_removed(self):
        field_id = "ceac.travel.travel.purpose.secondary.not-a-hash"

        self.assertEqual(semantic_field_id(field_id), field_id)
        self.assertFalse(semantic_field_id_endswith(
            field_id,
            ".travel.purpose.secondary",
        ))


if __name__ == "__main__":
    unittest.main()
