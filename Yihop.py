import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import math
import requests
from datetime import datetime, timedelta, timezone
import streamlit.components.v1 as components

# ----------------------------------------------------------------
# 1. 基礎設定與參數 (從 Secrets 讀取)
# ----------------------------------------------------------------
# 這裡會從 Streamlit Cloud 的 Secrets 分頁讀取
MY_CWA_API_KEY = st.secrets["CWA_API_KEY"]
GOOGLE_SHEET_URL = st.secrets["GOOGLE_SHEET_URL"]

st.set_page_config(page_title="火鍋店智能營運系統", layout="wide")

# 初始化連線
conn = st.connection("gsheets", type=GSheetsConnection)

NON_FOOD_CATEGORIES = ['外帶塑膠提袋', '氣體與耗材', '耗材', '廚房清潔用品', '清潔用品', '外帶包材', '雜貨']
STORE_LIST = ['潮州店', '內埔店']
COUNTY_LIST = ['屏東縣', '臺中市', '基隆市', '臺北市', '高雄市']

DAY_MAP = {0: "一", 1: "二", 2: "三", 3: "四", 4: "五", 5: "六", 6: "日"}

# ----------------------------------------------------------------
# 2. 核心 AI 與輔助函數
# ----------------------------------------------------------------

def get_lunar_date(target_date):
    """內建農曆推算 (精準校正 2026 全年)"""
    if not isinstance(target_date, (datetime, type(datetime.now().date()))):
        return ""
    
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
            if delta < days_in_month:
                return f"{month_name}{days_str[delta]}"
    return ""

@st.cache_data(ttl="1h")
def get_weather_data(county_name):
    url = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001"
    params = {"Authorization": MY_CWA_API_KEY, "locationName": county_name, "format": "JSON"}
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        loc = data['records']['location'][0]
        elements = loc['weatherElement']
        idx = 2 
        min_t = int(elements[2]['time'][idx]['parameter']['parameterName'])
        max_t = int(elements[4]['time'][idx]['parameter']['parameterName'])
        return {"avg_t": (min_t + max_t) / 2, "min_t": min_t, "max_t": max_t, "pop": int(elements[1]['time'][idx]['parameter']['parameterName']), "wx": elements[0]['time'][idx]['parameter']['parameterName']}
    except: return None

@st.cache_data(ttl="1m")
def get_master_log():
    try: 
        # ✅ 重點修正：必須傳入 spreadsheet 參數
        return conn.read(spreadsheet=GOOGLE_SHEET_URL, worksheet="叫貨紀錄", ttl="1m")
    except: return None

@st.cache_data(ttl="5m")
def get_vendor_catalog():
    try:
        # ✅ 重點修正：必須傳入 spreadsheet 參數
        return conn.read(spreadsheet=GOOGLE_SHEET_URL, worksheet="系統目錄", ttl="5m")
    except: return pd.DataFrame()

def copy_to_clipboard(text):
    html_code = f"""
    <script>
    function copyText() {{
        const text = `{text}`;
        navigator.clipboard.writeText(text).then(() => {{ alert('訊息已複製！'); }});
    }}
    </script>
    <button onclick="copyText()" style="background-color: #25D366; color: white; padding: 10px; border: none; border-radius: 5px; cursor: pointer; width: 100%; font-weight: bold;">📋 複製 LINE 叫貨訊息</button>
    """
    components.html(html_code, height=50)

# ----------------------------------------------------------------
# 3. 逐日精算大腦 (邏輯維持不變)
# ----------------------------------------------------------------
def calculate_target_precise(base, cat, tag, cover_days, start_date, ui_wknd, auto_wknd, ui_veg, auto_veg, w_m, v_m, t_mult, r_mult):
    total_target = 0.0
    for i in range(cover_days):
        current_calc_date = start_date + timedelta(days=i)
        cal_is_wknd = (current_calc_date.weekday() >= 4)
        curr_lunar = get_lunar_date(current_calc_date)
        cal_is_veg = curr_lunar and ("初一" in curr_lunar or "十五" in curr_lunar)
        apply_wknd = cal_is_wknd if (ui_wknd and auto_wknd) else ui_wknd
        apply_veg = cal_is_veg if (ui_veg and auto_veg) else ui_veg
        daily_m = 1.0
        if apply_wknd: daily_m *= w_m
        if apply_veg and tag == '素食': daily_m *= v_m
        if cat not in NON_FOOD_CATEGORIES: daily_m *= (t_mult * r_mult)
        total_target += (base * daily_m)
    return math.ceil(total_target)

# ----------------------------------------------------------------
# 4. 主畫面邏輯
# ----------------------------------------------------------------
tz_tw = timezone(timedelta(hours=8))
taiwan_now = datetime.now(tz_tw)

st.sidebar.title("🛠️ 門市營運工具")
st.sidebar.link_button("📂 開啟 Google 試算表後台", GOOGLE_SHEET_URL, use_container_width=True)
st.sidebar.divider()

with st.sidebar.expander("📍 門市與盤點人員", expanded=True):
    sel_store = st.selectbox("選擇盤點門市：", STORE_LIST, index=0)
    staff_name = st.text_input("人員姓名/工號：", placeholder="必填")
    target_date_input = st.date_input("📅 盤點日期：", value=taiwan_now.date())
    base_dt = datetime.combine(target_date_input, datetime.min.time())
    lunar_str = get_lunar_date(target_date_input)
    lunar_display = f"({lunar_str})" if lunar_str else ""
    st.info(f"📅 盤點基準：**{target_date_input.strftime('%Y/%m/%d')} {lunar_display}**")

tab_order, tab_analyze = st.tabs(["📝 現場盤點作業", "📊 管理者數據分析"])

with tab_order:
    try:
        catalog_df = get_vendor_catalog()
        if catalog_df.empty:
            st.error("⚠️ 無法讀取系統目錄，請檢查 Secrets 中的 GOOGLE_SHEET_URL 是否正確，以及試算表是否已共用。")
            st.stop()
            
        vendors = catalog_df['廠商名稱'].dropna().tolist()
        selected_vendor = st.selectbox("🏢 選擇要盤點的廠商：", vendors)
        vendor_info = catalog_df[catalog_df['廠商名稱'] == selected_vendor].iloc[0]
        
        lead_time = int(vendor_info['到貨天數']) if '到貨天數' in vendor_info and pd.notna(vendor_info['到貨天數']) else 1
        arrival_dt = base_dt + timedelta(days=lead_time)
        arrival_str = arrival_dt.strftime("%Y/%m/%d")
        arrival_lunar = get_lunar_date(arrival_dt)
        
        days_to_cover = 1
        if '公休日' in vendor_info and pd.notna(vendor_info['公休日']):
            closed_list = [int(d) - 1 for d in str(vendor_info['公休日']).replace(" ", "").split(",") if d.isdigit()]
            check_day = arrival_dt + timedelta(days=1)
            while check_day.weekday() in closed_list:
                days_to_cover += 1
                check_day += timedelta(days=1)

        auto_has_weekend = any((arrival_dt + timedelta(days=i)).weekday() >= 4 for i in range(days_to_cover))
        auto_has_veg = any("初一" in (get_lunar_date(arrival_dt + timedelta(days=i)) or "") or "十五" in (get_lunar_date(arrival_dt + timedelta(days=i)) or "") for i in range(days_to_cover))
        
        db_target_date = f"{arrival_str} (星期{DAY_MAP[arrival_dt.weekday()]}){f' ({arrival_lunar})' if arrival_lunar else ''}"
        if auto_has_veg: st.error(f"🚨 預計到貨日：{db_target_date}\n\n💡 提醒：當天為素食大日子！")
        else: st.success(f"🚚 預計到貨日：{db_target_date}")

        with st.sidebar.expander("🌤️ 天氣預報", expanded=True):
            sel_county = st.selectbox("天氣參考地點：", COUNTY_LIST, index=0)
            weather = get_weather_data(sel_county)
            t_mult = 1.2 if (weather and weather['avg_t'] <= 18) else (0.8 if weather and weather['avg_t'] > 28 else 1.0)
            r_mult = 1.5 if (weather and weather['pop'] > 70) else 1.0
            if weather: st.write(f"🌡️ 均溫：{weather['avg_t']}°C | 🌧️ 降雨：{weather['pop']}%")

        with st.sidebar.expander("📅 加成倍率", expanded=False):
            weekend_m = st.number_input("週末加成", value=1.2)
            is_wknd_ui = st.checkbox("啟用週末加成", value=auto_has_weekend)
            veg_m = st.number_input("素食加成", value=1.5)
            is_veg_ui = st.checkbox("啟用素食加成", value=auto_has_veg)

        # 讀取個別廠商分頁
        # ✅ 重點修正：必須傳入 spreadsheet 參數
        df = conn.read(spreadsheet=GOOGLE_SHEET_URL, worksheet=selected_vendor, ttl="5m")
        
        vendor_tabs = st.tabs(list(df['分類'].unique()))
        for i, cat in enumerate(df['分類'].unique()):
            with vendor_tabs[i]:
                cat_items = df[df['分類'] == cat]
                for _, row in cat_items.iterrows():
                    target = calculate_target_precise(row['基礎安全庫存'], row['分類'], row.get('係數標籤', '一般'), days_to_cover, arrival_dt, is_wknd_ui, auto_has_weekend, is_veg_ui, auto_has_veg, weekend_m, veg_m, t_mult, r_mult)
                    st.write(f"**{row['品項']}** (目標: {target})")
                    c1, c2 = st.columns(2)
                    with c1: cur_val = st.number_input("庫存", key=f"inv_{row['品項']}", min_value=0.0, step=1.0)
                    with c2:
                        sys_suggest = max(0, math.ceil((target - cur_val) / row['一箱數量']))
                        final_order = st.number_input(f"叫貨({row['叫貨單位']})", key=f"final_{row['品項']}", value=float(sys_suggest))
                        st.session_state[f"rec_{row['品項']}"] = {"sys": sys_suggest, "final": final_order, "cur": cur_val}

        if st.button("🚀 確認送出叫貨單", type="primary", use_container_width=True):
            if not staff_name: st.error("⚠️ 請填寫人員姓名！")
            else:
                # 這裡可以加入寫入資料表的邏輯
                st.success("✅ 叫貨資料已處理！(請串接更新邏輯)")

    except Exception as e: st.error(f"系統錯誤: {e}")

# 分析分頁 (簡略示意)
with tab_analyze:
    st.write("📊 數據載入中...")
