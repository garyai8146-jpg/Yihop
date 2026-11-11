"""Streamlit startup smoke test (no secrets or Google Sheet needed)."""
import os
import unittest
from pathlib import Path
from unittest.mock import patch


class StreamlitSmokeTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("YIHOP_CI_STREAMLIT") == "1", "Run in CI with Streamlit installed")
    def test_app_fails_closed_without_credentials(self):
        from streamlit.testing.v1 import AppTest

        app_path = Path(__file__).resolve().parents[1] / "Yihop.py"
        at = AppTest.from_file(str(app_path), default_timeout=30)
        at.run()
        self.assertFalse(at.exception, [str(item.message) for item in at.exception])
        errors = [str(item.proto) for item in at.error]
        self.assertTrue(
            any("尚未完成安全設定" in text for text in errors),
            f"Expected fail-closed login config message, got: {errors}",
        )
        self.assertEqual(len(at.dataframe), 0)


    @unittest.skipUnless(os.environ.get("YIHOP_CI_STREAMLIT") == "1", "Run in CI with Streamlit installed")
    def test_configured_pins_still_require_login_before_any_data(self):
        from streamlit.testing.v1 import AppTest

        app_path = Path(__file__).resolve().parents[1] / "Yihop.py"
        with patch.dict(os.environ, {
            "YIHOP_STAFF_PIN": "x7#L28@wB5!qR9",
            "YIHOP_ADMIN_PIN": "A3?dN6&kQ8^pT1",
        }):
            at = AppTest.from_file(str(app_path), default_timeout=30).run()
        self.assertFalse(at.exception, [str(item.proto) for item in at.exception])
        self.assertTrue(any("門市營運系統登入" in x.value for x in at.title))
        self.assertEqual(len(at.tabs), 0)


if __name__ == "__main__":
    unittest.main()
