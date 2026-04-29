import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import math
import requests
from datetime import datetime, timedelta, timezone
import streamlit.components.v1 as components

# ----------------------------------------------------------------
# 1. 基礎設定與機密診斷 (資安與除錯強化版)
# ----------------------------------------------------------------
st.set_page_config(page_title="火鍋店智能營運系統", layout="wide")

# 🔍 診斷函數：確保 Secrets 讀取正常
def validate_secrets():
    missing = []
    if "CWA_API_KEY" not in st.secrets: missing.append("CWA_API_KEY")
    if "GOOGLE_SHEET_URL" not in st.secrets: missing.append("GOOGLE_SHEET_URL")
    
    if missing:
        st.error(f"❌ 偵測到機密設定缺失：{', '.join(missing)}")
        st.info("💡 請前往 Streamlit Cloud 的 Settings -> Secrets 檢查名稱是否正確 (需為大寫)。")
        st.stop()
    return st.secrets["CWA_API_KEY"], st.secrets["GOOGLE_SHEET_URL"]

# 取得 Secrets
MY_CWA_API_KEY, GOOGLE_SHEET_URL = validate_secrets()

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

@st.cache_data(ttl="1m")
def get_master_log():
    try: return conn.read(spreadsheet=GOOGLE_SHEET_URL, worksheet="叫貨紀錄", ttl="1m")
    except Exception as e:
        st.warning(f"無法讀取叫貨紀錄: {e}")
        return None

@st.cache_data(ttl="5m")
def get_vendor_catalog():
    try: return conn.read(spreadsheet=GOOGLE_SHEET_URL, worksheet="系統目錄", ttl="5m")
    except Exception as e:
        st.error(f"無法讀取系統目錄: {e}")
        return pd.DataFrame()

def copy_to_clipboard(text):
    html_code = f"""<script>function copyText() {{ const text = `{text}`; navigator.clipboard.writeText(text).then(() => {{ alert('訊息已複製！'); }}); }}</script>
    <button onclick="copyText()" style="background-color: #25D366; color: white; padding: 10px; border: none; border-radius: 5px; cursor: pointer; width: 100%; font-weight: bold;">📋 複製 LINE 叫貨訊息</button>"""
    components.html(html_code, height=50)

# ----------------------------------------------------------------
# 3. 邏輯計算與 UI
# ----------------------------------------------------------------
tz_tw = timezone(timedelta(hours=8))
taiwan_now = datetime.now(tz_tw)

st.sidebar.title("🛠️ 門市營運工具")
st.sidebar.link_button("📂 開啟 Google 試算表後台", GOOGLE_SHEET_URL, use_container_width=True)
if st.sidebar.button("🔄 重新整理資料 (清除快取)"):
    st.cache_data.clear()
    st.rerun()

with st.sidebar.expander("📍 門市與盤點人員", expanded=True):
    sel_store = st.selectbox("選擇門市：", STORE_LIST)
    staff_name = st.text_input("人員姓名/工號：")
    target_date_input = st.date_input("📅 盤點日期：", value=taiwan_now.date())
    lunar_str = get_lunar_date(target_date_input)
    st.info(f"📅 盤點基準：{target_date_input.strftime('%Y/%m/%d')} ({lunar_str})")

tab_order, tab_analyze = st.tabs(["📝 現場盤點作業", "📊 管理者數據分析"])

with tab_order:
    try:
        catalog_df = get_vendor_catalog()
        if not catalog_df.empty:
            vendors = catalog_df['廠商名稱'].dropna().tolist()
            selected_vendor = st.selectbox("🏢 選擇要盤點的廠商：", vendors)
            vendor_info = catalog_df[catalog_df['廠商名稱'] == selected_vendor].iloc[0]
            
            # 預計到貨日與公休判定
            lead_time = int(vendor_info['到貨天數']) if pd.notna(vendor_info['到貨天數']) else 1
            arrival_dt = datetime.combine(target_date_input, datetime.min.time()) + timedelta(days=lead_time)
            
            days_to_cover = 1
            if '公休日' in vendor_info and pd.notna(vendor_info['公休日']):
                closed_list = [int(d) - 1 for d in str(vendor_info['公休日']).split(",") if d.strip().isdigit()]
                while (arrival_dt + timedelta(days=days_to_cover-1)).weekday() in closed_list:
                    days_to_cover += 1
            
            arr_lunar = get_lunar_date(arrival_dt)
            is_veg_day = "初一" in (arr_lunar or "") or "十五" in (arr_lunar or "")
            st.warning(f"🚚 預計到貨日：{arrival_dt.strftime('%Y/%m/%d')} ({arr_lunar})")
            if is_veg_day: st.error("🚨 提醒：當天為素食大日子，請留意庫存！")

            # 讀取廠商庫存表
            df = conn.read(spreadsheet=GOOGLE_SHEET_URL, worksheet=selected_vendor, ttl="5m")
            
            # 天氣係數
            sel_county = st.sidebar.selectbox("天氣參考地點：", COUNTY_LIST)
            weather = get_weather_data(sel_county)
            t_mult = 1.2 if (weather and weather['avg_t'] <= 18) else (0.8 if weather and weather['avg_t'] > 28 else 1.0)
            
            vendor_tabs = st.tabs(list(df['分類'].unique()))
            for i, cat in enumerate(df['分類'].unique()):
                with vendor_tabs[i]:
                    cat_items = df[df['分類'] == cat]
                    for _, row in cat_items.iterrows():
                        base = row['基礎安全庫存']
                        # 簡易計算 (可依需求套用更多倍率)
                        target = math.ceil(base * days_to_cover * t_mult)
                        
                        st.write(f"**{row['品項']}**")
                        c1, c2 = st.columns(2)
                        with c1: cur_val = st.number_input("庫存", key=f"inv_{row['品項']}", min_value=0.0, step=1.0)
                        with c2:
                            sys_suggest = max(0, math.ceil((target - cur_val) / row['一箱數量']))
                            final_order = st.number_input(f"叫貨({row['叫貨單位']})", key=f"final_{row['品項']}", value=float(sys_suggest))
                            st.session_state[f"rec_{row['品項']}"] = {"sys": sys_suggest, "final": final_order, "cur": cur_val}

            st.divider()
            if st.button("🚀 確認送出叫貨單", type="primary", use_container_width=True):
                if not staff_name: st.error("⚠️ 請填寫人員姓名！")
                else:
                    # 準備寫入資料
                    order_rows = []
                    order_summary = ""
                    for _, row in df.iterrows():
                        d = st.session_state.get(f"rec_{row['品項']}")
                        if d and d['final'] > 0:
                            order_summary += f"{row['品項']} {int(d['final'])}{row['叫貨單位']}、"
                            order_rows.append({
                                "目標日期": arrival_dt.strftime("%Y/%m/%d"), "門市": sel_store,
                                "盤點人員": staff_name, "廠商": selected_vendor, "品項": row['品項'],
                                "剩餘量": d['cur'], "系統建議量": d['sys'], "實際叫貨量": d['final']
                            })
                    
                    if order_rows:
                        # ✅ 更新時也必須明確傳入 spreadsheet URL
                        hist_df = conn.read(spreadsheet=GOOGLE_SHEET_URL, worksheet="叫貨紀錄", ttl=0)
                        new_df = pd.concat([hist_df, pd.DataFrame(order_rows)], ignore_index=True)
                        conn.update(spreadsheet=GOOGLE_SHEET_URL, worksheet="叫貨紀錄", data=new_df)
                        st.success("✅ 資料已成功寫入後台！")
                        copy_to_clipboard(f"{selected_vendor}您好，我是{sel_store}的{staff_name}，今日訂單：{order_summary.rstrip('、')}，謝謝。")
                    else: st.info("目前庫存充足，未送出任何訂單。")
    except Exception as e: st.error(f"系統錯誤: {e}")

with tab_analyze:
    st.info("📊 管理者數據分析內容載入中...")
    hist = get_master_log()
    if hist is not None: st.dataframe(hist, use_container_width=True)
