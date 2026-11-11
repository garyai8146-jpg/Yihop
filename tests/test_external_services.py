"""Offline tests for CWA error handling and Google Sheets navigation."""
import unittest
from datetime import datetime, date, timezone, timedelta
from unittest.mock import Mock

import requests

from external_services import (
    WeatherLookupError, normalize_spreadsheet_url, request_cwa_forecast,
)
from order_core import forecast_for_arrival, forecast_for_now


def sample_cwa():
    def element(name, values):
        return {"elementName": name, "time": [
            {"startTime": "2026-11-11T12:00:00+08:00",
             "endTime": "2026-11-11T18:00:00+08:00",
             "parameter": {"parameterName": values[0]}},
            {"startTime": "2026-11-11T18:00:00+08:00",
             "endTime": "2026-11-12T06:00:00+08:00",
             "parameter": {"parameterName": values[1]}},
            {"startTime": "2026-11-12T06:00:00+08:00",
             "endTime": "2026-11-12T18:00:00+08:00",
             "parameter": {"parameterName": values[2]}},
        ]}
    return {"success": "true", "records": {"location": [{"weatherElement": [
        element("Wx", ["晴", "陰", "雨"]),
        element("MinT", ["25", "20", "16"]),
        element("MaxT", ["30", "24", "18"]),
        element("PoP", ["10", "30", "80"]),
    ]}]}}


class FakeResponse:
    def __init__(self, payload=None, status=200):
        self.payload = payload
        self.status_code = status

    def json(self):
        return self.payload


class WeatherVisibilityTests(unittest.TestCase):
    def test_current_weather_visible_even_if_arrival_outside_36h(self):
        payload = sample_cwa()
        now = datetime(2026, 11, 11, 20, 0, tzinfo=timezone(timedelta(hours=8)))
        current = forecast_for_now(payload, now)
        self.assertEqual(current["wx"], "陰")
        self.assertEqual(current["min_t"], 20)
        self.assertIn("start_time", current)
        self.assertIsNone(forecast_for_arrival(payload, date(2026, 11, 14)))

    def test_soonest_upcoming_period_if_current_has_no_forecast(self):
        payload = sample_cwa()
        now = datetime(2026, 11, 11, 10, 0, tzinfo=timezone(timedelta(hours=8)))
        available = forecast_for_now(payload, now)
        self.assertEqual(available["wx"], "晴")

    def test_arrival_still_only_matches_actual_noon_forecast(self):
        payload = sample_cwa()
        self.assertEqual(forecast_for_arrival(payload, date(2026, 11, 12))["wx"], "雨")
        self.assertIsNone(forecast_for_arrival(payload, date(2026, 11, 13)))

    def test_no_stale_fallback_after_last_interval(self):
        now = datetime(2026, 11, 13, 19, tzinfo=timezone(timedelta(hours=8)))
        self.assertIsNone(forecast_for_now(sample_cwa(), now))


class ApiDiagnosticsTests(unittest.TestCase):
    def test_valid_data_is_accepted_without_leaking_key(self):
        requester = Mock(return_value=FakeResponse(sample_cwa()))
        self.assertEqual(request_cwa_forecast("屏東縣", "a-secret", requester)["success"], "true")
        args, kwargs = requester.call_args
        self.assertEqual(kwargs["params"]["Authorization"], "a-secret")
        self.assertEqual(kwargs["params"]["locationName"], "屏東縣")
        self.assertEqual(kwargs["timeout"], 10)

    def test_missing_key_is_diagnosed(self):
        with self.assertRaises(WeatherLookupError) as caught:
            request_cwa_forecast("屏東縣", "", Mock())
        self.assertEqual(caught.exception.code, "no_key")

    def test_rejected_key_is_diagnosed_and_not_echoed(self):
        for status in (401, 403):
            with self.subTest(status=status), self.assertRaises(WeatherLookupError) as caught:
                request_cwa_forecast("屏東縣", "never-display-this", Mock(return_value=FakeResponse(status=status)))
            self.assertEqual(caught.exception.code, "auth")
            self.assertNotIn("never-display-this", str(caught.exception))

    def test_rate_limit_and_outage_report_different_causes(self):
        for status, expected in ((429, "rate"), (500, "http")):
            with self.subTest(status=status), self.assertRaises(WeatherLookupError) as caught:
                request_cwa_forecast("屏東縣", "safekey", Mock(return_value=FakeResponse(status=status)))
            self.assertEqual(caught.exception.code, expected)

    def test_json_declared_failure_not_mistaken_for_no_forecast(self):
        payload = {"success": "false", "result": {"message": "invalid API key"}}
        with self.assertRaises(WeatherLookupError) as caught:
            request_cwa_forecast("屏東縣", "safekey", Mock(return_value=FakeResponse(payload)))
        self.assertEqual(caught.exception.code, "rejected")

    def test_timeout_does_not_leak_sensitive_request_url(self):
        with self.assertRaises(WeatherLookupError) as caught:
            request_cwa_forecast("屏東縣", "PRIVATE123", Mock(side_effect=requests.Timeout("url?Authorization=PRIVATE123")))
        self.assertEqual(caught.exception.code, "timeout")
        self.assertNotIn("PRIVATE123", str(caught.exception))


class GoogleSheetsLinkTests(unittest.TestCase):
    SHEET_ID = "1ABCabcEFG0987_-1234567890abcde"

    def test_full_url_normalized(self):
        url = f"https://docs.google.com/spreadsheets/d/{self.SHEET_ID}/edit#gid=123"
        self.assertEqual(normalize_spreadsheet_url(url), url)

    def test_id_only_becomes_valid_google_url(self):
        self.assertEqual(
            normalize_spreadsheet_url(self.SHEET_ID),
            f"https://docs.google.com/spreadsheets/d/{self.SHEET_ID}/edit",
        )

    def test_gid_query_retained(self):
        src = f"https://docs.google.com/spreadsheets/d/{self.SHEET_ID}/edit?gid=4&token=SECRET"
        value = normalize_spreadsheet_url(src)
        self.assertEqual(value, f"https://docs.google.com/spreadsheets/d/{self.SHEET_ID}/edit?gid=4")
        self.assertNotIn("SECRET", value)

    def test_reject_bad_domains_or_links(self):
        for candidate in (
            "http://docs.google.com/spreadsheets/d/" + self.SHEET_ID + "/edit",
            "https://docs.google.com.evil.invalid/spreadsheets/d/" + self.SHEET_ID,
            "https://evil.example/", "javascript:alert(1)", "",
            "this-is-not-an-id", "https://docs.google.com/document/d/" + self.SHEET_ID,
        ):
            with self.subTest(candidate=candidate):
                self.assertIsNone(normalize_spreadsheet_url(candidate))


if __name__ == "__main__":
    unittest.main()
