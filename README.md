# Yihop｜火鍋店備貨與叫貨工作台

> 將門市盤點、叫貨、廠商供貨日與氣象／農曆因素整合於 Streamlit 介面的流程工具。

[GitHub 面試作品導覽](https://github.com/garyai8146-jpg/Yihop_LineBOT/blob/main/PORTFOLIO.md)

## 解決的問題

門市每日叫貨需要參考庫存、廠商公休日、未來備貨天數、天氣變化與農曆節日。手工計算容易漏項，表格也難以協調多店同時操作。此專案將規則集中在單一工作台，提供建議量、視覺警示與可複製的 LINE 叫貨文字。

## 核心流程與技術取捨

- **資料存取：** 使用 `streamlit-gsheets`／`gspread` 連接 Google Sheets。
- **追加式紀錄：** 部分叫貨寫入使用 `append_rows`，讀取時按日期、門市、廠商與品項保留最近一筆，以降低整表覆寫衝突。**這不是資料庫交易或完整併發正確性的保證**；其他回寫與失敗重試仍需另外驗證。
- **動態備料參數：** 使用中央氣象署公開資料與 `lunar_python` 推算低溫、雨天及農曆特定日期的備貨加成。規則是營運輔助，並非經歷史訂單驗證的需求預測模型。
- **現場操作：** 將異常量、特殊節日、供貨間隔與叫貨草稿放入 Streamlit 介面。

## 技術棧

Python · Streamlit · pandas · Google Sheets API · gspread · CWA Open Data API · lunar_python

## 本機執行

```powershell
python -m pip install -r requirements.txt
streamlit run Yihop.py
```

執行前須於**本機不受 Git 追蹤**的 `.streamlit/secrets.toml` 或部署平台的私密設定中，提供 CWA API 金鑰 `KEY` 與 Google Sheets 連線設定 `[connections.gsheets]`。不要把服務帳戶私鑰、實際試算表 URL 或 Token 寫入 Git。

## 已知限制與正式部署前檢查

- **TLS 憑證驗證：** 目前 `Yihop.py` 的一處氣象 API 請求使用 `verify=False`，此設定不符合正式環境的安全要求，應修正並驗證 HTTPS 憑證鏈。
- Google Sheets 的追加寫入與讀取去重不能取代資料庫層唯一約束、版本控制與交易隔離。
- 缺乏可公開重現的多店競態壓力測試及完整自動化驗收；正式上線需另行檢查權限、資料保護及故障復原。
- 本專案展示**規則型備貨決策與流程整合**，不宣稱已達成任何精準預測或零掉單保證。

## 面試展示

可以聚焦在「為何採用 Sheets／Streamlit 快速建立原型」、「如何處理同品項的多次修正」、「外部氣象／節日因子的設計」，以及「何時需要將資料層升級為具交易能力的資料庫」。
