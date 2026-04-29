import sys
import os

# 🚀 第一重保障：強制設定系統語系環境變數 (針對 Linux 雲端環境)
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["LANG"] = "C.UTF-8"

import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import math
import requests
from datetime import datetime, timedelta, timezone
import streamlit.components.v1 as components

# ----------------------------------------------------------------
# 1. 基礎設定與機密診斷
# ----------------------------------------------------------------
st.set_page_config(page_title="火鍋店智能營運系統", layout="wide")

def validate_secrets():
    if "CWA_API_KEY" not in st.secrets or "GOOGLE_SHEET_URL" not in st.secrets:
        st.error("❌ 找不到機密設定 (Secrets)！")
        st.stop()
    return st.secrets["CWA_API_KEY"], st.secrets["GOOGLE_SHEET_URL"]

MY_CWA_API_KEY, GOOGLE_SHEET_URL = validate_secrets()

# 初始化連線
conn = st.connection("gsheets", type=GSheetsConnection)

STORE_LIST = ['潮州店', '內埔店']
COUNTY_LIST = ['屏東縣', '臺中市', '基隆市', '臺北市', '高雄市']
DAY_MAP = {0: "一", 1: "二", 2: "三", 3: "四", 4: "五", 5: "六", 6: "日"}

# ----------------------------------------------------------------
# 2. 核心輔助函數
# ----------------------------------------------------------------

def get_lunar_date(target_date):
    """內建農曆推算 (2026 全年)"""
    if not isinstance(target_date, (datetime, type(datetime.now().date()))): return ""
    check_date = target_date.date() if isinstance(target_date, datetime) else target_date
    anchors = [
        ("2026-01-19", "十二月", 29), ("2026-02-17", "正月", 30),
        ("2026-03-19", "二月", 29), ("2026-04-17", "三月", 30),
        ("2026-05-17", "四月", 29), ("2026-06-15", "五月", 30),
        ("2026-07-15", "六月", 29), ("2026-08-13", "七月", 29),
        ("2026-09-11", "八月", 29), ("2026-10-10", "九月", 30),
        ("2026-11-09", "十月", 30), ("2026-12-09", "十一月", 29), 
        ("2027-01-07", "十二月", 30)
    ]
    days_str = ["初一", "初二", "初三", "初四", "初五", "初六", "初七", "初八", "初九", "初十",
                "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十",
                "廿一", "廿二", "廿三", "廿四", "廿五", "廿六", "廿七", "廿八", "廿九", "三十"]
    for anchor_str, month_name, days_in_month in reversed(anchors):
        anchor_date = datetime.strptime(anchor_str, "%Y-%m-%d").date()
        if check_date >= anchor_date:
            delta = (check_date - anchor_date).days
            if delta < days_in_month: return f"{month_name}{days_str[delta]}"
    return ""

@st.cache_data(ttl="1h")
def get_weather_data(county_name):
    url = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001"
    params = {"Authorization": MY_CWA_API_KEY, "locationName": county_name, "format": "JSON"}
    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        loc = data['records']['location'][0]
        elements = loc['weatherElement']
        min_t = int(elements[2]['time'][2]['parameter']['parameterName'])
        max_t = int(elements[4]['time'][2]['parameter']['parameterName'])
        return {"avg_t": (min_t + max_t) / 2, "pop": int(elements[1]['time'][2]['parameter']['parameterName']), "wx": elements[0]['time'][2]['parameter']['parameterName']}
    except: return None

# 🚀 第二重保障：處理讀取時的字元問題
def safe_read_sheet(worksheet_name, ttl="1m"):
    try:
        # 使用 spreadsheet 參數，並確保名稱是乾淨的 Unicode 字串
        clean_name = str(worksheet_name).strip()
        return conn.read(spreadsheet=GOOGLE_SHEET_URL, worksheet=clean_name, ttl=ttl)
    except Exception as e:
        # 如果還是失敗，嘗試不帶 worksheet 名稱讀取（預設會讀取第一頁）
        if "ascii" in str(e).lower():
            try:
                st.warning(f"偵測到編碼問題，嘗試預設模式讀取...")
                return conn.read(spreadsheet=GOOGLE_SHEET_URL, ttl=ttl)
            except: pass
        st.error(f"❌ 無法讀取分頁 [{worksheet_name}]: {e}")
        return pd.DataFrame()

# ----------------------------------------------------------------
# 3. 主畫面邏輯
# ----------------------------------------------------------------
tz_tw = timezone(timedelta(hours=8))
taiwan_now = datetime.now(tz_tw)

st.sidebar.title("🛠️ 門市營運工具")
st.sidebar.link_button("📂 開啟 Google 試算表", GOOGLE_SHEET_URL)

with st.sidebar.expander("📍 基本資訊", expanded=True):
    sel_store = st.selectbox("選擇門市：", STORE_LIST)
    staff_name = st.text_input("人員姓名：")
    target_date_input = st.date_input("盤點日期：", value=taiwan_now.date())

tab_order, tab_analyze = st.tabs(["📝 現場盤點作業", "📊 數據分析"])

with tab_order:
    # 嘗試讀取
    catalog_df = safe_read_sheet("系統目錄")
    
    if not catalog_df.empty:
        vendors = catalog_df['廠商名稱'].dropna().tolist()
        selected_vendor = st.selectbox("🏢 選擇廠商：", vendors)
        
        # 讀取該廠商的分頁
        df = safe_read_sheet(selected_vendor)
        
        if not df.empty:
            st.success(f"✅ 已載入 {selected_vendor} 的資料")
            # 這裡可以繼續放你的品項顯示邏輯...
            st.dataframe(df, use_container_width=True)
    else:
        st.error("試算表讀取失敗。請確認：1. 網址正確 2. 試算表已設為『知道連結的任何人皆可編輯』")

with tab_analyze:
    st.write("數據分析開發中...")
