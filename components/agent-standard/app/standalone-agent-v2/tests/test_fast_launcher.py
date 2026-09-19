import unittest
from unittest.mock import patch

import run_docflow


class V2LauncherTests(unittest.TestCase):
    def test_exact_requested_port_is_retained(self):
        with patch.object(run_docflow, "available_port", return_value=4175):
            resolved = run_docflow.require_exact_port(
                4175,
                "DocFlow V2 前端",
            )

        self.assertEqual(resolved, 4175)

    def test_occupied_port_never_shifts_to_second_stack(self):
        with patch.object(run_docflow, "available_port", return_value=4177):
            with self.assertRaisesRegex(
                RuntimeError,
                "拒绝启动第二套实例",
            ):
                run_docflow.require_exact_port(
                    4175,
                    "DocFlow V2 前端",
                )


if __name__ == "__main__":
    unittest.main()
