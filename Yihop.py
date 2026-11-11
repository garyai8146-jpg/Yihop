# 腳本功能：火鍋店智能營運系統 (完整還原 UI 分頁、高亮提示與分析模組，結合防掉單與動態農曆)
import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import math
import os
import uuid
import requests
from datetime import date, datetime, timedelta, timezone
from lunar_python import Solar
from auth_core import strong_secret, verify_pin, fingerprint, session_authorized
from order_core import (
    catalog_item_key, widget_key, quantity_text, require_nonnegative_number,
    require_box_size, line_order_message, order_fingerprint,
    forecast_for_arrival, next_open_arrival, days_until_next_closed_gap,
)
from order_storage import sync_order

# ----------------------------------------------------------------
# 1. 基礎設定與祕密參數
# ----------------------------------------------------------------
st.set_page_config(page_title="火鍋店智能營運系統", layout="wide")


def secret(name):
    value = os.environ.get(name, "")
    if value:
        return value
    try:
        return str(st.secrets.get(name, ""))
    except Exception:
        return ""


STAFF_PIN = secret("YIHOP_STAFF_PIN")
ADMIN_PIN = secret("YIHOP_ADMIN_PIN")
if not strong_secret(STAFF_PIN) or not strong_secret(ADMIN_PIN) or STAFF_PIN == ADMIN_PIN:
    st.error("尚未完成安全設定：請在 Streamlit Secrets 設定兩組不同、至少 12 字元的 YIHOP_STAFF_PIN 和 YIHOP_ADMIN_PIN。")
    st.info("為保護門市訂貨與管理資料，未設定密碼前無法使用本系統。")
    st.stop()

saved_role = st.session_state.get("_yihop_role")
saved_auth = st.session_state.get("_yihop_auth")
expected_pin = ADMIN_PIN if saved_role == "admin" else STAFF_PIN
if saved_role not in ("staff", "admin") or not session_authorized(saved_auth, expected_pin):
    st.title("🔐 門市營運系統登入")
    role_choice = st.radio("登入身分", ["門市人員", "管理者"], horizontal=True)
    with st.form("yihop_login"):
        entered_pin = st.text_input("登入 PIN", type="password")
        login = st.form_submit_button("登入", type="primary")
    if login:
        chosen = ADMIN_PIN if role_choice == "管理者" else STAFF_PIN
        if verify_pin(entered_pin, chosen):
            st.session_state["_yihop_role"] = "admin" if role_choice == "管理者" else "staff"
            st.session_state["_yihop_auth"] = fingerprint(chosen)
            st.rerun()
        else:
            st.error("驗證失敗，請確認身分與 PIN。")
    st.stop()

role = saved_role
MY_CWA_API_KEY = secret("KEY")
try:
    GOOGLE_SHEET_URL = st.secrets["connections"]["gsheets"]["spreadsheet"]
except Exception:
    st.error("缺少 Google Sheets 連線設定：[connections.gsheets].spreadsheet")
    st.stop()
conn = st.connection("gsheets", type=GSheetsConnection)

NON_FOOD_CATEGORIES = ['外帶塑膠提袋', '氣體與耗材', '耗材', '廚房清潔用品', '清潔用品', '外帶包材', '雜貨']
STORE_LIST = ['潮州店', '內埔店']
COUNTY_LIST = ['屏東縣', '臺中市', '基隆市', '臺北市', '高雄市']

DAY_MAP = {0: "一", 1: "二", 2: "三", 3: "四", 4: "五", 5: "六", 6: "日"}

# ----------------------------------------------------------------
# 2. 核心 AI 與輔助函數
# ----------------------------------------------------------------

def get_lunar_date(target_date):
    """使用 lunar_python 動態推算農曆，永久免維護"""
    try:
        dt = target_date.date() if isinstance(target_date, datetime) else target_date
        solar = Solar.fromYmd(dt.year, dt.month, dt.day)
        lunar = solar.getLunar()
        return f"{lunar.getMonthInChinese()}月{lunar.getDayInChinese()}"
    except:
        return ""

@st.cache_data(ttl=3600)
def get_weather_data(county_name, arrival_iso):
    """Only apply a 36-hour forecast to an actually covered arrival day."""
    if not MY_CWA_API_KEY:
        return None
    url = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001"
    params = {"Authorization": MY_CWA_API_KEY, "locationName": county_name, "format": "JSON"}
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        return forecast_for_arrival(resp.json(), date.fromisoformat(arrival_iso))
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return None


@st.cache_data(ttl=60)
def get_master_log():
    """Load a backward-compatible latest inventory snapshot per catalog item."""
    try:
        df = conn.read(worksheet="叫貨紀錄", ttl=0)
        if df is None or df.empty:
            return df
        df = df.copy()
        required = {"目標日期", "門市", "廠商", "品項"}
        if not required.issubset(df.columns):
            raise ValueError("叫貨紀錄缺少必要欄位：" + "、".join(sorted(required - set(df.columns))))
        df["純日期"] = df["目標日期"].astype(str).str[:10]
        if "品項鍵" in df.columns:
            item_keys = df["品項鍵"].fillna("").astype(str).str.strip()
            df["_item_identity"] = item_keys.where(item_keys.ne(""), df["品項"].astype(str))
        else:
            df["_item_identity"] = df["品項"].astype(str)
        return df.drop_duplicates(subset=["純日期", "門市", "廠商", "_item_identity"], keep="last")
    except Exception as exc:
        st.error(f"無法讀取叫貨紀錄，已停止自動帶入與送單，避免使用錯誤的庫存：{exc}")
        return None


@st.cache_data(ttl="5m")
def get_vendor_catalog():
    return conn.read(worksheet="系統目錄", ttl="5m")

def copy_to_clipboard(text):
    """Streamlit's built-in copy button avoids injecting order text as JS."""
    st.code(text, language=None)
    st.caption("可使用上方程式碼區塊的複製按鈕，再貼到 LINE。")


# ----------------------------------------------------------------
# 3. 逐日精算大腦
# ----------------------------------------------------------------
def calculate_target_precise(base, cat, tag, cover_days, start_date, ui_wknd, auto_wknd, ui_veg, auto_veg, w_m, v_m, t_mult, r_mult):
    total_target = 0.0
    for i in range(cover_days):
        current_calc_date = start_date + timedelta(days=i)
        cal_is_wknd = (current_calc_date.weekday() >= 4)
        curr_lunar = get_lunar_date(current_calc_date)
        cal_is_veg = curr_lunar and ("初一" in curr_lunar or "十五" in curr_lunar)
        
        apply_wknd = False
        if ui_wknd: apply_wknd = cal_is_wknd if auto_wknd else True
            
        apply_veg = False
        if ui_veg: apply_veg = cal_is_veg if auto_veg else True
            
        daily_m = 1.0
        if apply_wknd: daily_m *= w_m
        if apply_veg and tag == '素食': daily_m *= v_m
        if cat not in NON_FOOD_CATEGORIES: daily_m *= (t_mult * r_mult)
        
        total_target += (base * daily_m)
        
    return math.ceil(total_target)

# ----------------------------------------------------------------
# 4. 側邊欄與盤點設定
# ----------------------------------------------------------------

tz_tw = timezone(timedelta(hours=8))
taiwan_now = datetime.now(tz_tw)

st.sidebar.title("🛠️ 門市營運工具")
st.sidebar.caption("登入身分：" + ("管理者" if role == "admin" else "門市人員"))
if st.sidebar.button("登出系統"):
    for key in ("_yihop_role", "_yihop_auth", "_yihop_pending_order", "_yihop_last_fingerprint", "_yihop_last_line"):
        st.session_state.pop(key, None)
    st.rerun()
if role == "admin":
    st.sidebar.link_button("📂 開啟 Google 試算表後台", GOOGLE_SHEET_URL, use_container_width=True)
st.sidebar.divider()

with st.sidebar.expander("📍 門市與盤點人員", expanded=True):
    sel_store = st.selectbox("選擇盤點門市：", STORE_LIST, index=STORE_LIST.index(st.session_state.get('last_store', '潮州店')) if 'last_store' in st.session_state else 0)
    staff_name = st.text_input("人員姓名/工號：", placeholder="必填")
    target_date_input = st.date_input("📅 盤點日期：", value=taiwan_now.date())
    base_dt = datetime.combine(target_date_input, datetime.min.time())
    
    lunar_str = get_lunar_date(target_date_input)
    lunar_display = f"({lunar_str})" if lunar_str else ""
    st.info(f"📅 盤點基準：**{target_date_input.strftime('%Y/%m/%d')} {lunar_display}**")

# ----------------------------------------------------------------
# 5. 主畫面流程
# ----------------------------------------------------------------

if role == "admin":
    tab_order, tab_analyze = st.tabs(["📝 現場盤點作業", "📊 管理者數據分析"])
else:
    tab_order = st.container()
    tab_analyze = None

with tab_order:
    try:
        catalog_df = get_vendor_catalog()
        vendors = catalog_df['廠商名稱'].dropna().tolist()
        selected_vendor = st.selectbox("🏢 選擇要盤點的廠商：", vendors)
        
        vendor_info = catalog_df[catalog_df['廠商名稱'] == selected_vendor].iloc[0]
        lead_time = 1
        if '到貨天數' in vendor_info and pd.notna(vendor_info['到貨天數']):
            lead_time = int(vendor_info['到貨天數'])
        if lead_time < 0 or lead_time > 60:
            raise ValueError("廠商到貨天數需介於 0 到 60 天")

        closed_days = set()
        if '公休日' in vendor_info and pd.notna(vendor_info['公休日']):
            text_days = str(vendor_info['公休日']).replace(" ", "")
            closed_days = {
                int(day) - 1 for day in text_days.split(",")
                if day.isdigit() and 1 <= int(day) <= 7
            }

        nominal_arrival = (base_dt + timedelta(days=lead_time)).date()
        corrected_arrival = next_open_arrival(nominal_arrival, closed_days)
        if corrected_arrival != nominal_arrival:
            st.warning(
                f"⚠️ 原預計到貨日 {nominal_arrival:%Y/%m/%d} 遇廠商公休，"
                f"已順延至下一個營業日 {corrected_arrival:%Y/%m/%d}；請再向廠商確認。"
            )
        arrival_dt = datetime.combine(corrected_arrival, datetime.min.time())
        arrival_str = arrival_dt.strftime("%Y/%m/%d")
        arrival_lunar = get_lunar_date(arrival_dt)
        days_to_cover = days_until_next_closed_gap(corrected_arrival, closed_days)
        if days_to_cover > 1:
            covered_closed = [
                f"星期{DAY_MAP[(corrected_arrival + timedelta(days=i)).weekday()]}"
                for i in range(1, days_to_cover)
            ]
            st.info(f"💡 廠商接下來 {', '.join(covered_closed)} 公休，本次備貨需涵蓋 {days_to_cover} 天。")

        auto_has_weekend = any((arrival_dt + timedelta(days=i)).weekday() >= 4 for i in range(days_to_cover))
        auto_has_veg = any("初一" in get_lunar_date(arrival_dt + timedelta(days=i)) or "十五" in get_lunar_date(arrival_dt + timedelta(days=i)) for i in range(days_to_cover))
        
        lunar_disp = f" ({arrival_lunar})" if arrival_lunar else ""
        date_header = f"預計到貨日：{arrival_str} (星期{DAY_MAP[arrival_dt.weekday()]}){lunar_disp}"
        db_target_date = f"{arrival_str} (星期{DAY_MAP[arrival_dt.weekday()]}){lunar_disp}"

        # 恢復：高亮提示到貨日
        if auto_has_veg:
            st.error(f"🚨 **{date_header}**\n\n💡 提醒：當天為素食大日子，請留意叫貨量！")
        else:
            st.success(f"🚚 **{date_header}**")

        # --- 動態側邊欄設定 ---
        with st.sidebar.expander("🌤️ 地點與天氣預報 (可手動覆蓋)", expanded=True):
            sel_county = st.selectbox("天氣參考地點：", COUNTY_LIST, index=0)
            weather = get_weather_data(sel_county, corrected_arrival.isoformat())
            
            auto_t_mult, auto_r_mult = 1.0, 1.0
            if weather:
                st.write(f"🌥️ 預報：**{weather['wx']}**")
                st.write(f"🌡️ 營業均溫：**{weather['avg_t']:.1f}°C**")
                st.caption(f"(區間：{weather['min_t']}°C ~ {weather['max_t']}°C)")
                auto_t_mult = 1.2 if weather['avg_t'] <= 18 else (0.8 if weather['avg_t'] > 28 else 1.0)
                auto_r_mult = 1.5 if weather['pop'] > 70 else 1.0
            else: st.warning("⚠️ 預計到貨日沒有可用的 36 小時天氣預報；氣象加成先用 x1.0，可手動調整。")

            st.divider()
            is_cold_override = st.checkbox("❄️ 強制啟用低溫加成 (x1.2)")
            is_rain_override = st.checkbox("🌧️ 強制啟用降雨加成 (x1.5)")
            
            t_mult = 1.2 if is_cold_override else auto_t_mult
            r_mult = 1.5 if is_rain_override else auto_r_mult
            st.caption(f"🎯 當前套用係數：溫度 x{t_mult} | 降雨 x{r_mult}")

        with st.sidebar.expander("📅 系統加成倍率設定 (後台控制)", expanded=False):
            weekend_m = st.number_input("週末/連假加成倍率", value=1.2, step=0.1)
            is_wknd_ui = st.checkbox("啟用週末/連假加成", value=auto_has_weekend)
            veg_m = st.number_input("初一十五(素食)加成倍率", value=1.5, step=0.1)
            is_veg_ui = st.checkbox("啟用初一十五(素食)加成", value=auto_has_veg)

        # 新舊紀錄相容：新資料用「品項鍵」，舊資料退回以品項名稱尋找。
        hist_today = get_master_log()
        if hist_today is None:
            st.stop()
        history_counts = {}
        in_transit_boxes = {}
        if not hist_today.empty:
            today_rows = hist_today[
                (hist_today["純日期"] == arrival_str)
                & (hist_today["廠商"] == selected_vendor)
                & (hist_today["門市"] == sel_store)
            ]
            for record in today_rows.to_dict("records"):
                identity = str(record.get("_item_identity", record["品項"]))
                history_counts[identity] = record.get("剩餘量", 0)

            # 在途的日期以實際到貨日為準（包含因公休日順延的天數）。
            travel_days = max(0, (corrected_arrival - base_dt.date()).days)
            if travel_days > 1:
                transit_dates = {
                    (base_dt + timedelta(days=i)).strftime("%Y/%m/%d")
                    for i in range(1, travel_days)
                }
                transit_rows = hist_today[
                    (hist_today["門市"] == sel_store)
                    & (hist_today["廠商"] == selected_vendor)
                    & (hist_today["純日期"].isin(transit_dates))
                ].copy()
                if not transit_rows.empty:
                    transit_rows["實際叫貨量"] = pd.to_numeric(
                        transit_rows["實際叫貨量"], errors="coerce"
                    ).fillna(0)
                    in_transit_boxes = transit_rows.groupby("_item_identity")["實際叫貨量"].sum().to_dict()
                    st.info("📦 已扣除這段期間預計到貨的在途庫存；請與實際配送狀態核對。")

        df = conn.read(worksheet=selected_vendor, ttl="5m")
        required_columns = {"分類", "品項", "基礎安全庫存", "一箱數量", "叫貨單位", "盤點單位"}
        if df is None or df.empty or not required_columns.issubset(df.columns):
            raise ValueError(
                "廠商品項清單為空或缺少必要欄位：" +
                "、".join(sorted(required_columns - set(df.columns if df is not None else [])))
            )
        df = df.reset_index(drop=True)
        categories = df["分類"].fillna("未分類").astype(str).unique().tolist()
        visible_items = []
        duplicate_counts = {}
        for _, catalog_row in df.iterrows():
            grouping = (str(catalog_row["分類"]), str(catalog_row["品項"]))
            duplicate_counts[grouping] = duplicate_counts.get(grouping, 0) + 1
        if any(count > 1 for count in duplicate_counts.values()):
            st.error("商品目錄中有相同分類且同名的品項。請在表格內區分名稱，避免庫存互相混用。")
            st.stop()

        category_tabs = st.tabs(categories)
        for category_index, cat in enumerate(categories):
            with category_tabs[category_index]:
                for position, row in df[df["分類"].fillna("未分類").astype(str) == cat].iterrows():
                    name = str(row["品項"]).strip()
                    if not name:
                        raise ValueError("廠商品項清單中有未填名稱的品項")
                    # Unique category/name pair remains stable if catalog rows are reordered.
                    item_key = catalog_item_key(selected_vendor, cat, name, 0)
                    box_size = require_box_size(row["一箱數量"])
                    base_amount = require_nonnegative_number(row["基礎安全庫存"], f"{name} 基礎安全庫存")
                    target = calculate_target_precise(
                        base_amount, cat, row.get("係數標籤", "一般"), days_to_cover, arrival_dt,
                        is_wknd_ui, auto_has_weekend, is_veg_ui, auto_has_veg,
                        weekend_m, veg_m, t_mult, r_mult,
                    )

                    previous = history_counts.get(item_key, history_counts.get(name, 0.0))
                    transit = in_transit_boxes.get(item_key, in_transit_boxes.get(name, 0.0))
                    try:
                        default_stock = require_nonnegative_number(previous, f"{name} 歷史庫存")
                        transit_boxes = require_nonnegative_number(transit, f"{name} 在途數量")
                    except ValueError:
                        st.warning(f"⚠️ {name} 的歷史數值不正確，請重新盤點。")
                        default_stock, transit_boxes = 0.0, 0.0

                    st.write(f"**{name}**")
                    suffix = f" | 🚚在途: {quantity_text(transit_boxes)}{row['叫貨單位']}" if transit_boxes else ""
                    st.caption(f"單位: {row['盤點單位']}{suffix}")
                    left, right = st.columns(2)
                    with left:
                        counted_stock = st.number_input(
                            "庫存", key=widget_key(sel_store, selected_vendor, corrected_arrival, item_key, "inventory"),
                            min_value=0.0, step=1.0, value=float(default_stock), format="%.1f",
                        )
                    suggested_boxes = max(0, math.ceil((target - counted_stock - transit_boxes * box_size) / box_size))
                    with right:
                        final_boxes = st.number_input(
                            f"叫貨({row['叫貨單位']})",
                            key=widget_key(sel_store, selected_vendor, corrected_arrival, item_key, "order"),
                            min_value=0.0, value=float(suggested_boxes), step=0.5, format="%.1f",
                        )
                    visible_items.append({
                        "目標日期": db_target_date,
                        "門市": sel_store,
                        "盤點人員": staff_name.strip(),
                        "廠商": selected_vendor,
                        "分類": cat,
                        "品項": name,
                        "品項鍵": item_key,
                        "叫貨單位": str(row["叫貨單位"]),
                        "剩餘量": counted_stock,
                        "系統建議量": suggested_boxes,
                        "實際叫貨量": final_boxes,
                        "涵蓋天數": days_to_cover,
                        "均溫": weather["avg_t"] if weather else "",
                    })

        st.divider()

        def finalize_submission(pending):
            """Retain the same ID and frozen rows until both worksheets confirm."""
            try:
                # st-gsheets-connection wraps the underlying gspread client.
                # Calling conn.client.open_by_url() directly is invalid on its wrapper.
                gsheets_client = conn.client
                if hasattr(gsheets_client, "_open_spreadsheet"):
                    spreadsheet = gsheets_client._open_spreadsheet(spreadsheet=GOOGLE_SHEET_URL)
                else:
                    spreadsheet = gsheets_client.open_by_url(GOOGLE_SHEET_URL)
                result = sync_order(spreadsheet, pending["vendor"], pending["rows"], pending["id"])
            except Exception as exc:
                st.session_state["_yihop_pending_order"] = pending
                st.error(f"無法連線 Google Sheets，已保留送單編號，請重試：{exc}")
                return
            if result.fully_synced:
                st.session_state.pop("_yihop_pending_order", None)
                st.session_state["_yihop_last_fingerprint"] = pending["fingerprint"]
                st.session_state["_yihop_last_line"] = pending["line"]
                get_master_log.clear()
                st.success(f"✅ 兩份紀錄均已核對完成。送單編號：{pending['id']}")
                st.caption(result.message)
            else:
                st.session_state["_yihop_pending_order"] = pending
                st.error(f"⚠️ {result.status}：{result.message}")
                st.code(pending["id"], language=None)

        pending = st.session_state.get("_yihop_pending_order")
        if pending:
            st.warning(
                f"上一筆送單 {pending['id']} 尚未全部同步。"
                "請先補送；系統會用相同編號核對，避免把已成功的部分重複寫入。"
            )
            if st.button("🔄 補送上一筆未完成的叫貨單", type="primary"):
                finalize_submission(pending)

        if st.button(
            "🚀 確認送出叫貨單", type="primary",
            use_container_width=True, disabled=bool(pending),
        ):
            if not staff_name.strip():
                st.error("⚠️ 請填寫人員姓名／工號")
            elif not visible_items:
                st.error("⚠️ 目前沒有可儲存的盤點品項")
            else:
                rows = [{name: value for name, value in item.items()} for item in visible_items]
                check = order_fingerprint(rows, sel_store, selected_vendor, staff_name.strip(), arrival_str)
                if check == st.session_state.get("_yihop_last_fingerprint"):
                    st.info("此工作階段已成功提交相同內容；為防止重複送單，已略過本次操作。")
                else:
                    submission_id = uuid.uuid4().hex
                    submitted_at = datetime.now(tz_tw).isoformat(timespec="seconds")
                    for row in rows:
                        row["送單編號"] = submission_id
                        row["送單時間"] = submitted_at
                    payload = {
                        "id": submission_id,
                        "vendor": selected_vendor,
                        "rows": rows,
                        "fingerprint": check,
                        "line": line_order_message(selected_vendor, sel_store, staff_name.strip(), rows),
                    }
                    st.session_state["_yihop_pending_order"] = payload
                    finalize_submission(payload)

        if st.session_state.get("_yihop_last_line"):
            st.markdown("### 📋 LINE 叫貨訊息")
            st.text_area(
                "核對後複製到 LINE", value=st.session_state["_yihop_last_line"],
                height=100, key="_yihop_line_preview",
            )
            copy_to_clipboard(st.session_state["_yihop_last_line"])
            if st.button("➕ 我確定要再建立一筆新叫貨單"):
                st.session_state.pop("_yihop_last_fingerprint", None)
                st.session_state.pop("_yihop_last_line", None)
                st.rerun()

    except Exception as e: st.error(f"系統錯誤: {e}")

# ==========================================
# 管理者的營運數據分析（門市人員完全不執行這段）
# ==========================================
if role == "admin":
    with tab_analyze:
        st.header("📊 營運數據分析")
        st.caption("如需完整原始紀錄，可開啟側邊欄的 Google 試算表。")
        try:
            history = get_master_log()
            if history is None:
                st.error("歷史資料目前不可讀取。")
            elif history.empty:
                st.info("目前沒有歷史叫貨紀錄。")
            else:
                history = history.copy()
                for column in ("系統建議量", "實際叫貨量"):
                    if column not in history.columns:
                        history[column] = 0
                    history[column] = pd.to_numeric(
                        history[column], errors="coerce"
                    ).fillna(0)
                if "純日期" not in history.columns:
                    history["純日期"] = history["目標日期"].astype(str).str[:10]
                if "分類" not in history.columns:
                    history["分類"] = "舊版未分類"
                else:
                    history["分類"] = history["分類"].fillna("舊版未分類").replace("", "舊版未分類")

                selected_store = st.radio(
                    "查看門市", ["全部門市"] + STORE_LIST, horizontal=True, key="_yihop_report_store"
                )
                if selected_store != "全部門市":
                    history = history[history["門市"] == selected_store]

                if history.empty:
                    st.info("這間門市目前沒有紀錄。")
                else:
                    vendors = sorted(history["廠商"].dropna().astype(str).unique().tolist())
                    chosen_vendor = st.selectbox("選擇廠商", vendors, key="_yihop_report_vendor")
                    vendor_history = history[history["廠商"].astype(str) == chosen_vendor]
                    choices = sorted({
                        (str(record["分類"]), str(record["品項"]))
                        for record in vendor_history.to_dict("records")
                    })
                    if choices:
                        chosen = st.selectbox(
                            "比較品項", choices,
                            format_func=lambda value: f"{value[0]} / {value[1]}",
                            key="_yihop_report_item",
                        )
                        selected = vendor_history[
                            (vendor_history["分類"].astype(str) == chosen[0])
                            & (vendor_history["品項"].astype(str) == chosen[1])
                        ]
                        left, right = st.columns(2)
                        with left:
                            st.subheader("📈 同品項：建議與實際叫貨")
                            st.caption("不同品項的箱、包、公斤等單位不能直接加總。")
                            chart = selected.groupby("純日期")[["系統建議量", "實際叫貨量"]].sum().sort_index()
                            st.line_chart(chart)
                        with right:
                            st.subheader("📦 叫貨出現次數")
                            st.caption("這裡是有叫貨的紀錄數，不是混合單位的總貨量。")
                            positive = vendor_history[vendor_history["實際叫貨量"] > 0]
                            if positive.empty:
                                st.info("尚無正數叫貨紀錄。")
                            else:
                                st.bar_chart(positive.groupby("品項").size().sort_values(ascending=False).head(10))

                    st.subheader("📋 歷史叫貨明細")
                    visible = history.drop(
                        columns=["_item_identity", "純日期", "昨日報廢"],
                        errors="ignore",
                    )

                    def highlight_date(value):
                        try:
                            raw = str(value)
                            dt = datetime.strptime(raw[:10], "%Y/%m/%d")
                            weekend = dt.weekday() >= 4
                            lunar = get_lunar_date(dt)
                            vegetarian = "初一" in lunar or "十五" in lunar
                            prefix = ("🔥" if vegetarian else "") + ("🌟" if weekend else "")
                            return f"{prefix} {raw}" if prefix else raw
                        except (ValueError, TypeError):
                            return value

                    visible["目標日期"] = visible["目標日期"].apply(highlight_date)
                    st.dataframe(visible.iloc[::-1], use_container_width=True)
        except Exception as exc:
            st.error(f"管理分析無法讀取：{exc}")
