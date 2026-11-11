"""Offline regression tests for Yihop. No real Google credentials required."""
import hashlib
import unittest
from datetime import date, timedelta, timezone, datetime
from pathlib import Path

from auth_core import fingerprint, session_authorized, strong_secret, verify_pin
from order_core import (
    catalog_item_key, days_until_next_closed_gap, forecast_for_arrival,
    line_order_message, next_open_arrival, order_fingerprint,
    quantity_text, require_box_size, require_nonnegative_number, widget_key,
)
from order_storage import append_once, sync_order, OrderSyncError, REQUIRED_HEADERS


class WorksheetNotFound(Exception):
    pass


class AccessDenied(Exception):
    pass


class FakeWorksheet:
    def __init__(self, headers=None):
        self.headers = list(headers or [])
        self.col_count = max(10, len(self.headers))
        self.data = []
        self.fail = None
        self.append_calls = 0

    def row_values(self, row):
        assert row == 1
        return list(self.headers)

    def update(self, range_name=None, values=None):
        assert range_name == "A1"
        self.headers = list(values[0])

    def col_values(self, column):
        return [self.headers[column - 1]] + [r[column - 1] for r in self.data]

    def add_cols(self, extra):
        self.col_count += extra

    def append_rows(self, rows, value_input_option):
        assert value_input_option == "RAW"
        self.append_calls += 1
        if self.fail == "before":
            raise OSError("simulated disconnect before commit")
        self.data.extend(rows)
        if self.fail == "after":
            self.fail = None
            raise OSError("simulated disconnect after commit")


class FakeSpreadsheet:
    def __init__(self):
        self.sheets = {}
        self.denied = set()

    def worksheet(self, name):
        if name in self.denied:
            raise AccessDenied("permission denied")
        if name not in self.sheets:
            raise WorksheetNotFound(name)
        return self.sheets[name]

    def add_worksheet(self, title, rows, cols):
        result = FakeWorksheet()
        self.sheets[title] = result
        return result


def sample_rows():
    base = {
        "目標日期": "2026/11/12 (星期四)", "門市": "潮州店", "盤點人員": "測試員",
        "廠商": "供應商A", "剩餘量": 0, "系統建議量": 1, "實際叫貨量": 1.5,
        "涵蓋天數": 1, "均溫": "", "分類": "蔬菜", "送單編號": "ticket-01",
        "送單時間": "2026-11-11T12:00:00+08:00",
    }
    return [dict(base, 品項="豆腐", 品項鍵="one"), dict(base, 品項="青菜", 品項鍵="two")]


def sample_forecast():
    def element(name, first, second):
        return {
            "elementName": name,
            "time": [
                {"startTime": "2026-11-11 06:00:00", "endTime": "2026-11-11 18:00:00", "parameter": {"parameterName": first}},
                {"startTime": "2026-11-12T06:00:00+08:00", "endTime": "2026-11-12T18:00:00+08:00", "parameter": {"parameterName": second}},
            ],
        }
    return {"records": {"location": [{"weatherElement": [
        element("Wx", "晴", "雨"), element("MinT", "24", "15"),
        element("MaxT", "29", "19"), element("PoP", "10", "90"),
    ]}]}}


class WeatherAndUiTests(unittest.TestCase):
    def test_forecast_uses_actual_arrival_date(self):
        result = forecast_for_arrival(sample_forecast(), date(2026, 11, 12))
        self.assertEqual(result["wx"], "雨")
        self.assertEqual(result["avg_t"], 17)
        self.assertEqual(result["pop"], 90)

    def test_forecast_does_not_use_wrong_day(self):
        self.assertIsNone(forecast_for_arrival(sample_forecast(), date(2026, 11, 13)))
        self.assertIsNone(forecast_for_arrival({}, date(2026, 11, 12)))

    def test_skip_closed_arrival_and_limit_gap(self):
        # Friday => supplier closed Friday and Saturday => arrive Sunday.
        self.assertEqual(next_open_arrival(date(2026, 11, 13), {4, 5}), date(2026, 11, 15))
        self.assertEqual(days_until_next_closed_gap(date(2026, 11, 12), {4, 5}), 3)
        with self.assertRaises(ValueError):
            next_open_arrival(date(2026, 11, 12), set(range(7)))

    def test_item_state_isolated_by_store_vendor_category_and_date(self):
        a = catalog_item_key("供應商A", "蔬菜", "豆腐", 0)
        b = catalog_item_key("供應商B", "蔬菜", "豆腐", 0)
        self.assertNotEqual(a, b)
        self.assertNotEqual(a, catalog_item_key("供應商A", "加工品", "豆腐", 0))
        self.assertNotEqual(widget_key("潮州店", "A", date(2026, 11, 12), a, "inventory"),
                            widget_key("內埔店", "A", date(2026, 11, 12), a, "inventory"))
        self.assertNotEqual(widget_key("潮州店", "A", date(2026, 11, 12), a, "inventory"),
                            widget_key("潮州店", "A", date(2026, 11, 13), a, "inventory"))

    def test_fractional_line_quantities_preserved_without_script(self):
        self.assertEqual(quantity_text(1.5), "1.5")
        self.assertEqual(quantity_text("3.000"), "3")
        msg = line_order_message("供應商", "潮州店", "測試", [
            {"品項": "豆腐", "實際叫貨量": 1.5, "叫貨單位": "箱"},
            {"品項": "白菜", "實際叫貨量": 0, "叫貨單位": "箱"},
        ])
        self.assertIn("豆腐 1.5箱", msg)
        self.assertNotIn("白菜", msg)
        self.assertNotIn("豆腐 1箱", msg)

    def test_reject_invalid_catalog_units(self):
        for v in (0, -1, float("nan"), float("inf"), None):
            with self.subTest(v=v), self.assertRaises((ValueError, TypeError)):
                require_box_size(v)

    def test_order_fingerprint_changes_with_edit(self):
        rows = sample_rows()
        p1 = order_fingerprint(rows, "潮州店", "A", "測試", "2026/11/12")
        rows[0]["剩餘量"] = 9
        p2 = order_fingerprint(rows, "潮州店", "A", "測試", "2026/11/12")
        self.assertNotEqual(p1, p2)


class AuthorizationTests(unittest.TestCase):
    def test_missing_or_weak_password_fails_closed(self):
        for pin in (None, "1688", "123456789012", "admin123456789", "aaaaaaaaaaaa"):
            self.assertFalse(strong_secret(pin))
            self.assertFalse(verify_pin(str(pin), pin))

    def test_valid_role_secret_and_rotation(self):
        pin = "M9#azP7+v3Q@16"
        self.assertTrue(verify_pin(pin, pin))
        self.assertFalse(verify_pin(pin, "R5!ecB8~x4L@23"))
        self.assertTrue(session_authorized(fingerprint(pin), pin))
        self.assertFalse(session_authorized(fingerprint(pin), "R5!ecB8~x4L@23"))


class GoogleSheetsTests(unittest.TestCase):
    def test_two_sheet_commit_is_verified(self):
        ss = FakeSpreadsheet()
        result = sync_order(ss, "供應商A", sample_rows(), "ticket-01")
        self.assertTrue(result.fully_synced)
        self.assertEqual(len(ss.sheets["叫貨紀錄"].data), 2)
        self.assertEqual(len(ss.sheets["紀錄_供應商A"].data), 2)
        self.assertTrue(set(REQUIRED_HEADERS).issubset(ss.sheets["叫貨紀錄"].headers))

    def test_retry_same_id_does_not_duplicate_rows(self):
        ss = FakeSpreadsheet()
        first = sync_order(ss, "供應商A", sample_rows(), "ticket-01")
        again = sync_order(ss, "供應商A", sample_rows(), "ticket-01")
        self.assertTrue(first.fully_synced and again.fully_synced)
        self.assertEqual(len(ss.sheets["叫貨紀錄"].data), 2)
        self.assertEqual(ss.sheets["叫貨紀錄"].append_calls, 1)

    def test_mirror_failure_retries_only_mirror(self):
        ss = FakeSpreadsheet()
        ss.sheets["紀錄_供應商A"] = FakeWorksheet()
        ss.sheets["紀錄_供應商A"].fail = "before"
        first = sync_order(ss, "供應商A", sample_rows(), "ticket-01")
        self.assertTrue(first.master_ok)
        self.assertFalse(first.vendor_ok)
        self.assertEqual(len(ss.sheets["叫貨紀錄"].data), 2)
        ss.sheets["紀錄_供應商A"].fail = None
        second = sync_order(ss, "供應商A", sample_rows(), "ticket-01")
        self.assertTrue(second.fully_synced)
        self.assertEqual(ss.sheets["叫貨紀錄"].append_calls, 1)
        self.assertEqual(len(ss.sheets["紀錄_供應商A"].data), 2)

    def test_network_error_after_commit_is_detected(self):
        ss = FakeSpreadsheet()
        ss.sheets["叫貨紀錄"] = FakeWorksheet()
        ss.sheets["叫貨紀錄"].fail = "after"
        self.assertTrue(sync_order(ss, "供應商A", sample_rows(), "ticket-01").fully_synced)
        self.assertEqual(len(ss.sheets["叫貨紀錄"].data), 2)

    def test_partial_row_count_rejected(self):
        ss = FakeSpreadsheet()
        ws = FakeWorksheet()
        ws.update("A1", [list(REQUIRED_HEADERS)])
        ws.data.append([str(sample_rows()[0].get(h, "")) for h in REQUIRED_HEADERS])
        ss.sheets["叫貨紀錄"] = ws
        result = sync_order(ss, "供應商A", sample_rows(), "ticket-01")
        self.assertFalse(result.master_ok)
        self.assertIn("只有 1/2", result.message)
        self.assertEqual(len(ws.data), 1)

    def test_permissions_error_not_mistaken_for_missing_sheet(self):
        ss = FakeSpreadsheet()
        ss.denied.add("叫貨紀錄")
        result = sync_order(ss, "供應商A", sample_rows(), "ticket-01")
        self.assertFalse(result.master_ok)
        self.assertFalse(ss.sheets)

    def test_old_headers_are_extended_not_replaced(self):
        ss = FakeSpreadsheet()
        ss.sheets["叫貨紀錄"] = FakeWorksheet(["品項", "門市"])
        result = sync_order(ss, "供應商A", sample_rows(), "ticket-01")
        self.assertTrue(result.fully_synced)
        self.assertEqual(ss.sheets["叫貨紀錄"].headers[:2], ["品項", "門市"])
        self.assertGreaterEqual(ss.sheets["叫貨紀錄"].col_count, len(REQUIRED_HEADERS))


class AppSourceSmokeTests(unittest.TestCase):
    def test_staff_cannot_render_admin_tab_and_no_js_copy(self):
        source = (Path(__file__).resolve().parents[1] / "Yihop.py").read_text(encoding="utf-8")
        self.assertIn('if role == "admin":\n    with tab_analyze:', source)
        self.assertNotIn("components.html", source)
        self.assertNotIn("verify=False", source)
        self.assertIn("YIHOP_STAFF_PIN", source)
        self.assertIn("YIHOP_ADMIN_PIN", source)


if __name__ == "__main__":
    unittest.main()
