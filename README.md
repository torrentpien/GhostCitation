# GhostCitation

偵測論文中 AI 生成的假參考文獻。

上傳 PDF 或 DOCX 格式的學術論文，GhostCitation 會自動擷取參考文獻，並逐筆透過 CrossRef API、Google Scholar 驗證其真實性。

## 功能

- **檔案解析** — 支援 PDF 與 DOCX 格式，自動找到 References / 參考文獻段落
- **DOI 驗證** — 透過 CrossRef API 比對 DOI 對應的標題與作者
- **標題搜尋** — 透過 CrossRef 與 Google Scholar 搜尋標題，比對相似度
- **結果分類** — 每筆文獻標示為「已驗證」、「可疑」或「未找到」

## 快速開始

```bash
# 安裝相依套件
pip install -r requirements.txt

# 啟動伺服器
python app.py
```

開啟瀏覽器前往 http://localhost:5000

## 驗證邏輯

1. 若參考文獻包含 DOI → 透過 CrossRef API 驗證，並比對標題相似度
2. 若無 DOI 或 DOI 驗證失敗 → 透過 CrossRef 搜尋標題
3. 若 CrossRef 無結果 → 透過 Google Scholar 搜尋
4. 根據查詢結果標示：
   - **已驗證** — 在資料庫中找到高度匹配的文獻
   - **可疑** — DOI 存在但標題不符
   - **未找到** — 所有來源均無匹配結果（可能為假文獻）

## 技術架構

- **後端**: Python / Flask
- **文件解析**: pdfplumber (PDF), python-docx (DOCX)
- **驗證來源**: CrossRef API, Google Scholar
- **前端**: 原生 HTML/CSS/JS（無框架依賴）

## 授權

MIT License
