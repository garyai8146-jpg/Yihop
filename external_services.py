"""Safe integration helpers: CWA API diagnostics and a valid Google Sheets link.

No credentials or private spreadsheet IDs are logged or returned in errors.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

import requests


class WeatherLookupError(RuntimeError):
    """An error suitable for displaying to an authenticated user."""

    def __init__(self, code: str, public_message: str):
        self.code = code
        super().__init__(public_message)


def normalize_spreadsheet_url(value: object) -> str | None:
    """Accept a Sheets URL or spreadsheet ID; reject unrelated / unsafe URLs."""
    raw = str(value or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{20,}", raw):
        return f"https://docs.google.com/spreadsheets/d/{raw}/edit"

    try:
        parts = urlsplit(raw)
    except ValueError:
        return None
    if parts.scheme != "https" or (parts.hostname or "").lower() != "docs.google.com":
        return None
    match = re.fullmatch(r"/spreadsheets/d/([A-Za-z0-9_-]{20,})(?:/.*)?", parts.path)
    if not match or parts.username or parts.password or parts.port is not None:
        return None

    url = f"https://docs.google.com/spreadsheets/d/{match.group(1)}/edit"
    # Keep a page/worksheet selection without keeping arbitrary query arguments.
    gid_values = parse_qs(parts.query).get("gid")
    if gid_values and gid_values[0].isdigit():
        url += f"?gid={gid_values[0]}"
    elif parts.fragment.startswith("gid=") and parts.fragment[4:].isdigit():
        url += f"#gid={parts.fragment[4:]}"
    return url


def request_cwa_forecast(county: str, api_key: str, requester=None) -> dict:
    """Fetch raw CWA data and distinguish missing data from authentication errors.

    Only successful payloads should be cached. Network failures and rejected
    credentials are raised and never include the Authorization parameter.
    """
    if not isinstance(api_key, str) or not api_key.strip():
        raise WeatherLookupError("no_key", "尚未設定中央氣象署 API 金鑰（Secrets 的 KEY）。")
    if requester is None:
        requester = requests.get
    try:
        resp = requester(
            "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001",
            params={"Authorization": api_key, "locationName": county, "format": "JSON"},
            timeout=10,
        )
    except requests.Timeout as exc:
        raise WeatherLookupError("timeout", "氣象署連線逾時，請稍後重新整理。") from exc
    except requests.RequestException as exc:
        raise WeatherLookupError("network", "無法連上中央氣象署，請檢查網路或稍後重試。") from exc

    code = getattr(resp, "status_code", None)
    if code in (401, 403):
        raise WeatherLookupError("auth", f"中央氣象署拒絕 API 金鑰（HTTP {code}），請檢查 KEY 是否仍有效。")
    if code == 429:
        raise WeatherLookupError("rate", "中央氣象署 API 請求頻率超出限制，請稍後重試。")
    if code != 200:
        raise WeatherLookupError("http", f"中央氣象署服務回應 HTTP {code}，目前無法載入預報。")

    try:
        payload = resp.json()
    except (ValueError, TypeError) as exc:
        raise WeatherLookupError("invalid_json", "氣象署回傳非預期的資料格式。") from exc
    if not isinstance(payload, dict):
        raise WeatherLookupError("invalid_json", "氣象署回傳的資料格式不正確。")
    if str(payload.get("success", "true")).lower() in ("false", "0"):
        raise WeatherLookupError("rejected", "氣象署 API 回傳查詢失敗，請檢查金鑰與縣市設定。")
    locations = payload.get("records", {}).get("location", []) if isinstance(payload.get("records"), dict) else []
    if not isinstance(locations, list) or not locations:
        raise WeatherLookupError("empty", "氣象署目前沒有提供所選縣市的預報資料。")
    return payload
