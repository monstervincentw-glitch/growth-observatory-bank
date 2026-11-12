# 成長股觀測站｜銀行版

網站 https://growth-observatory-bank.streamlit.app/

研究資料中心 https://docs.google.com/spreadsheets/d/1I9Alvyhg4UZXi-AdylJN0pjiLHSMzZUleFK0vsXG1K0/edit

## 資料與部署

- GitHub main 部署至 Streamlit Community Cloud。入口是 streamlit_app.py，Python 套件由 requirements.txt 安裝。公開頁面只包含共用研究。
- Streamlit 後端從 data/sheets-url.txt 所設定的 Apps Script 唯讀介面讀取 bank_snapshot。也可在 Streamlit Secrets 設定 google_service_account，以 Viewer 分享研究試算表並使用 Sheets API。
- Apps Script 的公開介面只讀指定的 bank_snapshot 範圍。私人 MVP 部署固定於原版。六個既有分頁沿用原格式，同一批次更新候選、燈號與來源日期，並保留測試標記、回測和顧問結果。
- bank_snapshot 包含版本、分段數、SHA-256 與經 gzip 壓縮的研究資料。讀取端驗證完整批次、研究池、燈號與評分。bank_runs 保存更新嘗試。
- 研究回測與顧問報告沿用 2026-11-04 已驗證封存輸出。台股歷史結果屬研究版。策略模擬部位是封存模擬帳戶，延伸日期僅估值。

## 每日更新

GitHub Actions 在台北時間週二至週六約 06:17 處理美股，週一至週五約 21:27 處理台股。各市場以既有基準日曆識別實際收盤日。排程時間可能受 GitHub 等候隊列影響。原策略因子、財報來源與 ALFRED 版本判定由 engine 的第 29 版模組執行。

工作流程把已驗證批次及最新嘗試寫入 research-data 分支。失敗會保留最後批次並輸出紀錄。可從 Actions 手動選 US 或 TW 重跑。

研究資料中心的同步由獨立 Apps Script 執行。將 apps-script/Writer.gs 及 apps-script/appsscript.json 放在新的私人 Apps Script 專案，開啟 Sheets v4 進階服務，以研究資料中心擁有者執行 installBankSync。首次執行需要 Google 試算表寫入、外部請求與觸發條件權限。觸發條件每小時檢查發布分支，正常執行時會在同一次 Sheets 批次更新快照與寫入紀錄。此 Writer 不需要發布成網頁。

## 維護

- 更新失敗時，先看 GitHub Actions 日誌及 bank_runs。檢查基準交易日期、財報來源及 ALFRED 各序列。保留上一次成功批次直到資料通過。
- bank_snapshot 出現混批或校驗不符，網站會顯示已驗證備援。復原時應整批寫回經過校驗的發布資料。
- 私人帳戶仍由原第 29 版管理。銀行版程式只引用公開研究投影及模擬資料。銀行版部署資訊和權限獨立於私人資料來源。
