"""Pure helpers for inventory identifiers, CWA forecasts, and LINE output.

Keeping these routines independent of Streamlit makes the business rules
repeatable without access to an actual shop or its private Google Sheet.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation

TAIPEI = timezone(timedelta(hours=8))


def catalog_item_key(vendor: str, category: str, item: str, occurrence: int = 0) -> str:
    """Stable per-row identity within a catalog ordering; position is a fallback ID."""
    raw = json.dumps([vendor, category, item, int(occurrence)], ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def widget_key(store: str, vendor: str, arrival: date, item_key: str, field: str) -> str:
    raw = json.dumps([store, vendor, arrival.isoformat(), item_key, field], ensure_ascii=False)
    return "order_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def quantity_text(value: object) -> str:
    """Human-readable exact decimal formatting (1.5 stays 1.5, not 1)."""
    try:
        number = Decimal(str(value))
        if not number.is_finite():
            raise ValueError("數量不是有效數字")
        return format(number, "f").rstrip("0").rstrip(".") if "." in format(number, "f") else format(number, "f")
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("數量不是有效數字") from exc


def require_nonnegative_number(value: object, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 必須是數字") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{label} 不能是負數或無效數值")
    return number


def require_box_size(value: object) -> float:
    result = require_nonnegative_number(value, "一箱數量")
    if result <= 0:
        raise ValueError("一箱數量必須大於 0，請先修正系統目錄")
    return result


def line_order_message(vendor: str, store: str, staff: str, items: list[dict]) -> str:
    details = [
        f"{row['品項']} {quantity_text(row['實際叫貨量'])}{row['叫貨單位']}"
        for row in items if require_nonnegative_number(row["實際叫貨量"], "實際叫貨量") > 0
    ]
    body = "、".join(details) if details else "本次無需補貨"
    return f"{vendor}您好，我是{store}的{staff}，今日訂單：{body}，謝謝。"


def order_fingerprint(rows: list[dict], store: str, vendor: str, staff: str, target_date: str) -> str:
    data = json.dumps(
        [store, vendor, staff, target_date, rows],
        sort_keys=True, ensure_ascii=False, default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _parse_cwa_datetime(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    return result.replace(tzinfo=TAIPEI) if result.tzinfo is None else result.astimezone(TAIPEI)


def _forecast_value(element: dict, target: datetime):
    for slot in element.get("time", []):
        try:
            begin = _parse_cwa_datetime(slot["startTime"])
            end = _parse_cwa_datetime(slot["endTime"])
            if begin <= target < end:
                return slot.get("parameter", {}).get("parameterName")
        except (KeyError, TypeError, ValueError):
            continue
    return None


def forecast_for_arrival(payload: dict, arrival: date):
    """Use the forecast that actually covers noon on the requested arrival day.

    CWA F-C0032-001 is limited to the coming 36 hours. Do not silently apply
    an unrelated slot to later dates, or treat API failures as known weather.
    """
    try:
        locations = payload["records"]["location"]
        if not locations:
            return None
        elements = {entry["elementName"]: entry for entry in locations[0]["weatherElement"]}
        target = datetime.combine(arrival, time(12, 0), tzinfo=TAIPEI)
        vals = {name: _forecast_value(elements[name], target) for name in ("Wx", "MinT", "MaxT", "PoP")}
        if any(value is None for value in vals.values()):
            return None
        min_t = int(vals["MinT"])
        max_t = int(vals["MaxT"])
        pop = int(vals["PoP"])
        if min_t > max_t or not 0 <= pop <= 100:
            return None
        return {"avg_t": (min_t + max_t) / 2, "min_t": min_t, "max_t": max_t, "pop": pop, "wx": str(vals["Wx"])}
    except (KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def next_open_arrival(base_arrival: date, closed_weekdays: set[int]) -> date:
    """Advance a supplier's nominal delivery date to the next open weekday."""
    if len(closed_weekdays) >= 7:
        raise ValueError("廠商一週七天都設定公休，無法推算到貨日")
    result = base_arrival
    for _ in range(7):
        if result.weekday() not in closed_weekdays:
            return result
        result += timedelta(days=1)
    raise ValueError("找不到可到貨日期")


def days_until_next_closed_gap(arrival: date, closed_weekdays: set[int]) -> int:
    """Number of days that a delivery must cover, including following closure days."""
    covered = 1
    for i in range(1, 8):
        if (arrival + timedelta(days=i)).weekday() not in closed_weekdays:
            break
        covered += 1
    return covered
