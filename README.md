# 🍲 火鍋店智能營運系統 (Hotpot Smart Operations System)

## 🎯 專案背景
本專案為針對實體火鍋店量身打造的 Serverless 營運自動化工具。透過 Streamlit 結合 Google Sheets 作為輕量級資料庫，取代傳統紙本盤點與人工叫貨流程。系統導入了氣象 API 與農曆演算法，能根據「氣候變化」與「特殊節日」自動計算安全庫存，並已具備防禦多門市高併發寫入的資料庫架構。
---

## 🛠️ 技術核心與架構亮點

### 1. 具備事件溯源概念的原子性寫入 (Append-Only & Read-Time Deduplication)
* **解決併發衝突 (Race Condition)**：捨棄傳統的整表覆寫模式，底層改採 `gspread` 的 `append_rows` 進行原子性追加寫入，徹底根絕多門市於交接班尖峰同時送出所導致的「掉單」與「覆蓋」事故。
* **無損資料修改**：讀取階段 (`get_master_log`) 實作動態去重邏輯 (`drop_duplicates(keep='last')`)。這不僅保留了完整的稽核軌跡 (Audit Trail)，更確保門市人員能隨時重開網頁修正盤點失誤。

### 2. 動態環境參數加成引擎 (Dynamic Multiplier Engine)
* **氣象連動 (CWA API)**：介接中央氣象署 API，動態抓取門市當地氣溫與降雨機率。系統依此自動觸發「低溫加成 (x1.2)」或「降雨加成 (x1.5)」，精準預測氣候對餐飲業的備料影響。
* **永久農曆推算 (Lunar Algorithm)**：導入 `lunar_python` 演算法引擎，精準推算農曆初一、十五。徹底汰除硬編碼 (Hardcoded) 的時間節點，實現永久免維護的「素食大日 (x1.5)」自動加成警示。

### 3. Mobile-First 現場作業體驗 (Mobile UX/UI)
* **防呆警示機制**：系統自動偵測廠商公休日並推算需「涵蓋天數」；對於特殊節日（如素食日）與超重/爆倉風險，皆具備搶眼的視覺化 Alert 高亮提示。
* **零斷層通訊對接**：盤點完成後，系統自動統整缺貨品項，並提供「一鍵複製 LINE 叫貨訊息」功能，無縫對接現有的供應商溝通渠道。

---

## ⚙️ 技術棧 (Tech Stack)
* **Frontend / Framework**: Streamlit (Python)
* **Database**: Google Sheets API (`streamlit-gsheets`, `gspread`)
* **External APIs**: 交通部中央氣象署 (CWA) 開放資料 API
* **Algorithms**: `lunar_python` (農曆與節氣推算)
* **Security**: Streamlit Secrets Management (`.streamlit/secrets.toml`)

---

## 🚀 部署與資安規範

本專案採嚴格的配置與代碼分離原則。執行前請確保於專案根目錄建立 `.streamlit/secrets.toml`，並設定以下環境變數：

```toml
KEY = "CWA-YOUR-API-KEY"

[connections.gsheets]
spreadsheet = "[https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit](https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit)"
