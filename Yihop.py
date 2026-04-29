import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import math
import requests
import urllib3
from datetime import datetime, timedelta, timezone
import streamlit.components.v1 as components

# 關閉 SSL 驗證警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ----------------------------------------------------------------
# 1. 基礎設定與寫死參數
# ----------------------------------------------------------------
MY_CWA_API_KEY = st.secrets["KEY"]
# 直接綁定老闆專屬試算表
GOOGLE_SHEET_URL = st.secrets["connections"]["gsheets"]["spreadsheet"]

st.set_page_config(page_title="火鍋店智能營運系統", layout="wide")
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
    if not isinstance(target_date, datetime) and not isinstance(target_date, type(datetime.now().date())):
        return ""
        
    # 統一轉換為 date 物件處理
    if isinstance(target_date, datetime):
        check_date = target_date.date()
    else:
        check_date = target_date
        
    # 2026 全年精準農曆初一基準點與天數 (大月30, 小月29)
    # 已根據 2026 年(丙午年)真實農民曆校正
    anchors = [
        ("2026-01-19", "十二月", 29), # 乙巳年十二月
        ("2026-02-17", "正月", 30),   # 丙午年正月 (春節)
        ("2026-03-19", "二月", 29),
        ("2026-04-17", "三月", 30),
        ("2026-05-17", "四月", 29),
        ("2026-06-15", "五月", 30),
        ("2026-07-15", "六月", 29),
        ("2026-08-13", "七月", 29),
        ("2026-09-11", "八月", 29),
        ("2026-10-10", "九月", 30),
        ("2026-11-09", "十月", 30),   
        ("2026-12-09", "十一月", 29), 
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
        resp = requests.get(url, params=params, timeout=10, verify=False)
        data = resp.json()
        loc = data['records']['location'][0]
        elements = loc['weatherElement']
        idx = 2 
        min_t, max_t = int(elements[2]['time'][idx]['parameter']['parameterName']), int(elements[4]['time'][idx]['parameter']['parameterName'])
        return {"avg_t": (min_t + max_t) / 2, "min_t": min_t, "max_t": max_t, "pop": int(elements[1]['time'][idx]['parameter']['parameterName']), "wx": elements[0]['time'][idx]['parameter']['parameterName']}
    except: return None

@st.cache_data(ttl="1m")
def get_master_log():
    try: return conn.read(worksheet="叫貨紀錄", ttl="1m")
    except: return None

@st.cache_data(ttl="5m")
def get_vendor_catalog():
    return conn.read(worksheet="系統目錄", ttl="5m")

def copy_to_clipboard(text):
    html_code = f"""
    <script>
    function copyText() {{
        const text = `{text}`;
        navigator.clipboard.writeText(text).then(() => {{
            alert('訊息已複製！請貼上至 LINE。');
        }});
    }}
    </script>
    <button onclick="copyText()" style="background-color: #25D366; color: white; padding: 10px; border: none; border-radius: 5px; cursor: pointer; width: 100%; font-weight: bold;">📋 複製 LINE 叫貨訊息</button>
    """
    components.html(html_code, height=50)

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
# 4. 側邊欄頂部：基本資訊
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

# ----------------------------------------------------------------
# 5. 主畫面與動態側邊欄連動
# ----------------------------------------------------------------

tab_order, tab_analyze = st.tabs(["📝 現場盤點作業", "📊 管理者數據分析"])

with tab_order:
    try:
        catalog_df = get_vendor_catalog()
        vendors = catalog_df['廠商名稱'].dropna().tolist()
        selected_vendor = st.selectbox("🏢 選擇要盤點的廠商：", vendors)
        
        vendor_info = catalog_df[catalog_df['廠商名稱'] == selected_vendor].iloc[0]
        
        lead_time = 1
        if '到貨天數' in vendor_info and pd.notna(vendor_info['到貨天數']):
            try: lead_time = int(vendor_info['到貨天數'])
            except: pass
            
        arrival_dt = base_dt + timedelta(days=lead_time)
        arrival_str = arrival_dt.strftime("%Y/%m/%d")
        arrival_lunar = get_lunar_date(arrival_dt)
        
        days_to_cover = 1
        if '公休日' in vendor_info and pd.notna(vendor_info['公休日']):
            closed_str = str(vendor_info['公休日']).replace(" ", "")
            closed_list_int = [int(d) - 1 for d in closed_str.split(",") if d.isdigit() and 1 <= int(d) <= 7]
            
            if arrival_dt.weekday() in closed_list_int:
                st.error(f"⚠️ 警告：預計到貨日 (星期{DAY_MAP[arrival_dt.weekday()]}) 是 {selected_vendor} 的公休日，請確認廠商是否能送貨！")
            
            check_day = arrival_dt + timedelta(days=1)
            closed_names = []
            while check_day.weekday() in closed_list_int:
                days_to_cover += 1
                closed_names.append(f"星期{DAY_MAP[check_day.weekday()]}")
                check_day += timedelta(days=1)
                
            if days_to_cover > 1:
                st.info(f"💡 **公休判定**：因廠商 {', '.join(closed_names)} 公休，本次叫貨需涵蓋 **{days_to_cover} 天** 庫存。")

        auto_has_weekend = False
        auto_has_veg = False
        
        for i in range(days_to_cover):
            chk_dt = arrival_dt + timedelta(days=i)
            if chk_dt.weekday() >= 4: auto_has_weekend = True
            chk_lunar = get_lunar_date(chk_dt)
            if chk_lunar and ("初一" in chk_lunar or "十五" in chk_lunar): auto_has_veg = True
        
        lunar_disp = f" ({arrival_lunar})" if arrival_lunar else ""
        date_header = f"預計到貨日：**{arrival_str} (星期{DAY_MAP[arrival_dt.weekday()]}){lunar_disp}**"
        
        # 定義準備寫入資料庫的目標日期字串 (包含星期與農曆)
        db_target_date = f"{arrival_str} (星期{DAY_MAP[arrival_dt.weekday()]}){lunar_disp}"
        
        # 移除依賴性文字，僅保留客觀警示
        if auto_has_veg:
            st.error(f"🚨 {date_header}\n\n💡 提醒：當天為素食大日子，請留意叫貨量！")
        else:
            st.success(f"🚚 {date_header}")

        # --- 動態側邊欄 ---
        with st.sidebar.expander("🌤️ 地點與天氣預報 (可手動覆蓋)", expanded=True):
            sel_county = st.selectbox("天氣參考地點：", COUNTY_LIST, index=0)
            weather = get_weather_data(sel_county)
            
            auto_t_mult, auto_r_mult = 1.0, 1.0
            if weather:
                st.write(f"🌥️ 預報：**{weather['wx']}**")
                st.write(f"🌡️ 營業均溫：**{weather['avg_t']:.1f}°C**")
                st.caption(f"(區間：{weather['min_t']}°C ~ {weather['max_t']}°C)")
                auto_t_mult = 1.2 if weather['avg_t'] <= 18 else (0.8 if weather['avg_t'] > 28 else 1.0)
                auto_r_mult = 1.5 if weather['pop'] > 70 else 1.0
            else: st.warning("⚠️ 氣象資料同步中...")

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

        # --- 繼續盤點畫面邏輯 ---
        in_transit_boxes = {}
        if lead_time > 1:
            in_transit_dates = [(base_dt + timedelta(days=d)).strftime("%Y/%m/%d") for d in range(1, lead_time)]
            hist = get_master_log()
            if hist is not None and not hist.empty:
                # 擷取純日期 (前10碼 YYYY/MM/DD) 以相容新舊格式的精準比對
                hist['純日期'] = hist['目標日期'].astype(str).str[:10]
                match = hist[(hist['門市'] == sel_store) & (hist['廠商'] == selected_vendor) & (hist['純日期'].isin(in_transit_dates))]
                if not match.empty:
                    match['實際叫貨量'] = pd.to_numeric(match['實際叫貨量'], errors='coerce')
                    in_transit_boxes = match.groupby('品項')['實際叫貨量'].sum().to_dict()
                    st.info("📦 系統已自動偵測並扣除『在途庫存』，防止爆倉。")

        df = conn.read(worksheet=selected_vendor, ttl="5m")
        hist_today = get_master_log()
        history_counts = {}
        if hist_today is not None and not hist_today.empty:
            # 同樣擷取純日期做預設庫存抓取
            hist_today['純日期'] = hist_today['目標日期'].astype(str).str[:10]
            match_today = hist_today[(hist_today['純日期'] == arrival_str) & (hist_today['廠商'] == selected_vendor) & (hist_today['門市'] == sel_store)]
            if not match_today.empty: history_counts = dict(zip(match_today['品項'], match_today['剩餘量']))

        vendor_tabs = st.tabs(list(df['分類'].unique()))
        for i, cat in enumerate(df['分類'].unique()):
            with vendor_tabs[i]:
                cat_items = df[df['分類'] == cat]
                for _, row in cat_items.iterrows():
                    tag = row['係數標籤'] if '係數標籤' in df.columns else '一般'
                    
                    target = calculate_target_precise(
                        row['基礎安全庫存'], row['分類'], tag, days_to_cover, arrival_dt,
                        is_wknd_ui, auto_has_weekend, is_veg_ui, auto_has_veg, 
                        weekend_m, veg_m, t_mult, r_mult
                    )
                    
                    default_val = float(history_counts.get(row['品項'], 0.0))
                    in_transit_box_qty = float(in_transit_boxes.get(row['品項'], 0.0))
                    in_transit_pieces = in_transit_box_qty * row['一箱數量']
                    
                    st.write(f"**{row['品項']}**")
                    transit_text = f" | 🚚在途: {int(in_transit_box_qty)}{row['叫貨單位']}" if in_transit_box_qty > 0 else ""
                    st.caption(f"單位: {row['盤點單位']}{transit_text}")
                    
                    c1, c2 = st.columns(2)
                    with c1:
                        cur_val = st.number_input("庫存", key=f"inv_{row['品項']}", min_value=0.0, step=1.0, value=default_val, format="%.1f")
                    with c2:
                        sys_suggest = math.ceil((target - cur_val - in_transit_pieces) / row['一箱數量'])
                        if sys_suggest < 0: sys_suggest = 0
                        
                        final_order = st.number_input(f"叫貨({row['叫貨單位']})", key=f"final_{row['品項']}", value=0.0, step=1.0, format="%.1f")
                        
                        st.session_state[f"rec_{row['品項']}"] = {
                            "sys": sys_suggest, "final": final_order, "target": target, 
                            "cur": cur_val
                        }

        st.divider()

        if st.button("🚀 確認送出叫貨單", type="primary", use_container_width=True):
            if not staff_name: st.error("⚠️ 請填寫左側的『人員姓名』！")
            else:
                order_rows = []
                order_text = ""
                
                for _, row in df.iterrows():
                    d = st.session_state.get(f"rec_{row['品項']}")
                    if d and d['final'] > 0:
                        order_text += f"{row['品項']} {int(d['final'])}{row['叫貨單位']}、"
                        order_rows.append({
                            "目標日期": db_target_date, # <--- 這裡改為寫入包含農曆與星期的完整字串
                            "門市": sel_store, 
                            "盤點人員": staff_name, "廠商": selected_vendor, "品項": row['品項'], 
                            "剩餘量": d['cur'], "系統建議量": d['sys'], "實際叫貨量": d['final'],
                            "涵蓋天數": days_to_cover,
                            "均溫": weather['avg_t'] if weather else 25
                        })
                
                if order_rows:
                    for sheet in ["叫貨紀錄", f"紀錄_{selected_vendor}"]:
                        try:
                            ex = conn.read(worksheet=sheet, ttl=0)
                            if ex is not None and not ex.empty:
                                cb = pd.concat([ex, pd.DataFrame(order_rows)], ignore_index=True)
                                # 建立純日期作為防呆比對基準，確保使用者覆蓋資料時不會產生重複紀錄
                                cb['純日期'] = cb['目標日期'].astype(str).str[:10]
                                sub = ['純日期', '門市', '廠商', '品項'] if sheet == "叫貨紀錄" else ['純日期', '門市', '品項']
                                up = cb.drop_duplicates(subset=sub, keep='last').drop(columns=['純日期'])
                            else: up = pd.DataFrame(order_rows)
                            
                            if '紀錄時間' in up.columns: up = up.drop(columns=['紀錄時間'])
                            if '昨日報廢' in up.columns: up = up.drop(columns=['昨日報廢'])
                                
                            conn.update(worksheet=sheet, data=up)
                        except: pass
                    get_master_log.clear()

                    st.success("✅ 盤點資料已上傳至老闆後台！")
                    final_msg = f"{selected_vendor}您好，我是{sel_store}的{staff_name}，今日訂單：{order_text.rstrip('、')}，謝謝。"
                    st.text_area("📋 LINE 訊息確認：", value=final_msg, height=100)
                    copy_to_clipboard(final_msg)
                else:
                    st.success("🎉 目前庫存(含在途)已充足，無需叫貨！")

    except Exception as e: st.error(f"系統錯誤: {e}")

# ==========================================
# 分頁 2: 儀表板淨化與彩虹高亮版
# ==========================================
with tab_analyze:
    st.header("📊 營運數據分析 (管理端)")
    st.info("💡 建議點擊側邊欄的『開啟 Google 試算表後台』以獲取最完整的報表數據。")
    try:
        hist_df = get_master_log()
        if hist_df is not None and not hist_df.empty:
            if '系統建議量' not in hist_df.columns: hist_df['系統建議量'] = 0
            if '實際叫貨量' not in hist_df.columns: hist_df['實際叫貨量'] = 0
            
            hist_df['系統建議量'] = pd.to_numeric(hist_df['系統建議量'], errors='coerce').fillna(0)
            hist_df['實際叫貨量'] = pd.to_numeric(hist_df['實際叫貨量'], errors='coerce').fillna(0)
            
            filter_store = st.radio("篩選檢視門市：", ["全部門市"] + STORE_LIST, horizontal=True)
            if filter_store != "全部門市": hist_df = hist_df[hist_df['門市'] == filter_store]

            if not hist_df.empty:
                c1, c2 = st.columns(2)
                with c1:
                    st.subheader("🤖 系統建議 vs 🧑‍🌾 實際叫貨差異")
                    # 圖表分組改用純日期，確保折線圖不會因為資料庫混雜新舊格式而斷裂
                    hist_df['純日期'] = hist_df['目標日期'].astype(str).str[:10]
                    chart_data = hist_df.groupby('純日期').agg({'系統建議量': 'sum', '實際叫貨量': 'sum'}).reset_index()
                    st.line_chart(chart_data.set_index('純日期'))
                    hist_df = hist_df.drop(columns=['純日期']) # 用完即丟，不影響下方表格顯示
                    
                with c2:
                    st.subheader("📦 各品項實際補貨總量")
                    st.bar_chart(hist_df.groupby('品項')['實際叫貨量'].sum().sort_values(ascending=False).head(10))
                
                st.subheader("📋 歷史叫貨明細")
                
                if '紀錄時間' in hist_df.columns: hist_df = hist_df.drop(columns=['紀錄時間'])
                if '昨日報廢' in hist_df.columns: hist_df = hist_df.drop(columns=['昨日報廢'])
                
                hist_df = hist_df.iloc[::-1]
                
                def format_target_date(date_str):
                    try:
                        raw_str = str(date_str)
                        clean_date = raw_str[:10] # 只抓前10碼 YYYY/MM/DD
                        dt = datetime.strptime(clean_date, "%Y/%m/%d")
                        is_wknd = dt.weekday() >= 4 
                        
                        # 重新推算並覆寫，統一所有歷史資料的顯示格式
                        weekday_str = DAY_MAP[dt.weekday()]
                        lunar_str = get_lunar_date(dt)
                        is_veg = lunar_str and ("初一" in lunar_str or "十五" in lunar_str)
                        
                        prefix = ""
                        if is_veg: prefix += "🔥"
                        if is_wknd: prefix += "🌟"
                        if prefix: prefix += " "
                        
                        lunar_disp_fmt = f" ({lunar_str})" if lunar_str else ""
                        
                        return f"{prefix}{clean_date} (星期{weekday_str}){lunar_disp_fmt}"
                    except: return date_str

                hist_df['目標日期'] = hist_df['目標日期'].apply(format_target_date)
                
                def highlight_special_days(row):
                    date_val = str(row['目標日期'])
                    if '🔥' in date_val and '🌟' in date_val:
                        return ['background-color: rgba(255, 165, 0, 0.3); color: #d97700; font-weight: bold'] * len(row)
                    elif '🔥' in date_val:
                        return ['background-color: rgba(255, 75, 75, 0.2); color: #ff4b4b; font-weight: bold'] * len(row)
                    elif '🌟' in date_val:
                        return ['background-color: rgba(0, 150, 255, 0.15); color: #0066cc; font-weight: bold'] * len(row)
                    return [''] * len(row)

                styled_df = hist_df.style.apply(highlight_special_days, axis=1)
                st.dataframe(styled_df, use_container_width=True)
                
            else: st.write("該門市尚無數據。")
        else: st.write("尚無歷史數據。")
    except Exception as e: 
        st.error(f"⚠️ 讀取失敗。錯誤詳情：{str(e)}")
