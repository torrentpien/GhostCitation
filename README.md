# GhostCitation

偵測論文中 AI 生成的假參考文獻。

上傳 PDF 或 DOCX 格式的學術論文，GhostCitation 會自動擷取參考文獻，並逐筆透過 CrossRef API、Google Scholar (SerpAPI) 驗證其真實性，同時比對標題、作者與年份，區分「引用有誤」與「疑似捏造」。

## 功能

- **檔案解析** — 支援 PDF（含雙欄排版）與 DOCX 格式，自動找到 References / 參考文獻段落
- **DOI 驗證** — 透過 CrossRef API 比對 DOI 對應的標題、作者與年份
- **標題搜尋** — 透過 CrossRef 與 Google Scholar (SerpAPI) 搜尋標題
- **多欄位比對** — 除了標題，也比對作者名、出版年份
- **結果分類** — 區分三種狀態：
  - **已驗證 (verified)** — 文獻存在且標題、作者、年份均正確
  - **引用有誤 (misattributed)** — 文獻存在但作者、年份或標題有出入
  - **疑似捏造 (fabricated)** — 所有來源均無匹配結果，可能為 AI 生成

## 快速開始

```bash
# 安裝相依套件
pip install -r requirements.txt

# 啟動伺服器
python app.py
```

開啟瀏覽器前往 http://localhost:5000

### Google Scholar 查詢（選填）

Google Scholar 查詢使用 [SerpAPI](https://serpapi.com/)，需要 API Key：

- 可在網頁介面輸入
- 或設定環境變數：`export SERPAPI_KEY=your_key_here`
- 不設定也可使用（僅透過 CrossRef 驗證）

## 驗證邏輯

1. 若參考文獻包含 DOI → 透過 CrossRef API 驗證 DOI，比對標題、作者、年份
2. 若無 DOI 或 DOI 驗證失敗 → 透過 CrossRef 搜尋標題
3. 若 CrossRef 無結果 → 透過 Google Scholar (SerpAPI) 搜尋
4. 找到匹配後進行細部比對：
   - **標題相似度** < 80% → 標記標題不符
   - **作者匹配率** < 50% → 標記作者不符
   - **年份不一致** → 標記年份不符
5. 有任何不符 → 判定為「引用有誤」；完全找不到 → 判定為「疑似捏造」

## 技術架構

- **後端**: Python / Flask
- **文件解析**: pdfplumber (PDF, 含雙欄偵測), python-docx (DOCX)
- **驗證來源**: CrossRef API, Google Scholar (SerpAPI)
- **前端**: 原生 HTML/CSS/JS（無框架依賴）

## 授權

MIT License
