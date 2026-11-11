"""Retryable, best-effort idempotent Google Sheets order submission.

Sheets cannot offer a cross-worksheet transaction. We write the master ledger
first, verify its rows, and then write the optional vendor mirror. Retrying the
same submission ID skips any worksheet already fully written. Concurrent
processes can still race; do not claim database-grade exactly-once semantics.
"""
from __future__ import annotations

from dataclasses import dataclass

REQUIRED_HEADERS = (
    "目標日期", "門市", "盤點人員", "廠商", "品項",
    "剩餘量", "系統建議量", "實際叫貨量", "涵蓋天數", "均溫",
    "分類", "品項鍵", "送單編號", "送單時間",
)


class OrderSyncError(RuntimeError):
    pass


@dataclass(frozen=True)
class SyncResult:
    master_ok: bool
    vendor_ok: bool
    status: str
    message: str

    @property
    def fully_synced(self) -> bool:
        return self.master_ok and self.vendor_ok


def get_or_create_worksheet(spreadsheet, name: str, row_count: int):
    try:
        return spreadsheet.worksheet(name)
    except Exception as exc:
        # Only a genuinely missing worksheet may be created. Permission and
        # network failures must never be mistaken for missing sheets.
        if exc.__class__.__name__ != "WorksheetNotFound":
            raise
    return spreadsheet.add_worksheet(title=name, rows=max(200, row_count + 20), cols=len(REQUIRED_HEADERS) + 6)


def ensure_headers(worksheet) -> list[str]:
    headers = worksheet.row_values(1)
    if len(headers) != len(set(headers)):
        raise OrderSyncError("試算表第一列有重複欄名，請先整理欄名再送單")
    updated = headers + [name for name in REQUIRED_HEADERS if name not in headers]
    if updated != headers:
        worksheet.update(range_name="A1", values=[updated])
    return updated


def submission_row_count(worksheet, headers: list[str], submission_id: str) -> int:
    index = headers.index("送單編號") + 1
    return sum(str(value).strip() == submission_id for value in worksheet.col_values(index)[1:])


def append_once(spreadsheet, sheet_name: str, rows: list[dict], submission_id: str) -> str:
    if not rows or not submission_id:
        raise OrderSyncError("送單內容或編號不可為空")

    ws = get_or_create_worksheet(spreadsheet, sheet_name, len(rows))
    headers = ensure_headers(ws)
    existing = submission_row_count(ws, headers, submission_id)
    if existing == len(rows):
        return "已存在，略過重複寫入"
    if existing:
        raise OrderSyncError(
            f"{sheet_name} 的送單編號 {submission_id} 只有 {existing}/{len(rows)} 筆，"
            "已停止自動寫入以免資料重複；請先檢查試算表"
        )
    ordered_rows = [[str(row.get(name, "")) for name in headers] for row in rows]
    try:
        ws.append_rows(ordered_rows, value_input_option="RAW")
    except Exception as exc:
        # A request can fail *after* Sheets has committed the batch.
        # Re-query by submission ID before telling the user to retry.
        try:
            if submission_row_count(ws, headers, submission_id) == len(rows):
                return "確認遠端已有完整紀錄"
        except Exception:
            pass
        raise OrderSyncError(f"{sheet_name} 寫入失敗：{exc}") from exc

    actual = submission_row_count(ws, headers, submission_id)
    if actual != len(rows):
        raise OrderSyncError(
            f"{sheet_name} 寫入後僅確認 {actual}/{len(rows)} 筆；"
            "請檢查試算表後再重送"
        )
    return "已寫入且核對完成"


def sync_order(spreadsheet, vendor: str, rows: list[dict], submission_id: str) -> SyncResult:
    try:
        master_status = append_once(spreadsheet, "叫貨紀錄", rows, submission_id)
    except Exception as exc:
        return SyncResult(False, False, "主紀錄失敗", str(exc))
    try:
        mirror_status = append_once(spreadsheet, f"紀錄_{vendor}", rows, submission_id)
    except Exception as exc:
        return SyncResult(
            True, False, "部分完成",
            f"主叫貨紀錄已確認（{master_status}），但廠商備份頁未完成：{exc}。"
            "請保留原送單編號並重按送出，系統會跳過已完整寫入的主紀錄。"
        )
    return SyncResult(True, True, "全部完成", f"主紀錄：{master_status}；廠商紀錄：{mirror_status}")
