# GhostCitation

Detect AI-fabricated references in academic papers.

Upload a PDF or DOCX academic paper, and GhostCitation will automatically extract the references section, then verify each reference against CrossRef, Google Scholar (via SerpAPI or ScraperAPI), and Google web search. It distinguishes between verified references, misattributed references (exist but with wrong metadata), and fabricated references (likely AI-generated).

## Features

- **File Parsing** — Supports PDF (including two-column layouts) and DOCX; automatically locates References / Bibliography sections
- **Chinese Reference Support** — Handles Chinese academic formats: `Author, Year,《Book》` and `Author, Year,〈Article〉`, including translated author names, sub-section headers, and `Year Month` date formats
- **DOI Verification** — Verifies DOIs via CrossRef API, comparing title, authors, and year
- **Multi-Source Search** — Searches CrossRef, Google Scholar (SerpAPI or ScraperAPI scraping), and Google web search
- **Reference Type Classification** — Distinguishes academic from non-academic references (media, government, think tanks) and routes to appropriate verification sources
- **Metadata Comparison** — Compares title similarity, author overlap, and publication year
- **Three Verdicts**:
  - **Verified** — Reference found and metadata matches
  - **Misattributed** — Reference exists but author, year, or title has discrepancies
  - **Fabricated** — No matching reference found in any source (likely AI-generated)
- **Bilingual UI** — English and Chinese interface with language toggle

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Start the server
py app.py
```

Open your browser to http://localhost:5000

### Google Scholar Search (Optional)

Google Scholar search supports two methods:

1. **ScraperAPI** (recommended) — Uses [ScraperAPI](https://www.scraperapi.com/) to scrape Google Scholar
   - Enter your ScraperAPI Key in the web UI
   - Or set the environment variable: `export SCRAPERAPI_KEY=your_key`

2. **SerpAPI** — Uses [SerpAPI](https://serpapi.com/) for Google Scholar API
   - Enter your SerpAPI Key in the web UI
   - Or set the environment variable: `export SERPAPI_KEY=your_key`

Without either key, only CrossRef verification is available.

## Verification Logic

1. If DOI is present → verify via CrossRef API
2. Academic references → Google Scholar → CrossRef → book title fallback → Google web search
3. Non-academic references → Google web search → CrossRef
4. Once a match is found, compare:
   - **Title similarity** < 80% → title mismatch
   - **Author overlap** < 50% → author mismatch
   - **Year mismatch** → year discrepancy
5. Any mismatch → "Misattributed"; no match at all → "Fabricated"

## Tech Stack

- **Backend**: Python / Flask
- **File Parsing**: pdfplumber (PDF with two-column detection), python-docx (DOCX)
- **Verification Sources**: CrossRef API, Google Scholar (SerpAPI / ScraperAPI), Google Web Search
- **Frontend**: Vanilla HTML/CSS/JS with i18n support (no framework dependencies)

---

# GhostCitation (中文說明)

偵測論文中 AI 生成的假參考文獻。

上傳 PDF 或 DOCX 格式的學術論文，GhostCitation 會自動擷取參考文獻，並逐筆透過 CrossRef、Google Scholar（SerpAPI 或 ScraperAPI）及 Google 網頁搜尋驗證其真實性，同時比對標題、作者與年份，區分「已驗證」、「引用有誤」與「疑似捏造」。

## 功能

- **檔案解析** — 支援 PDF（含雙欄排版）與 DOCX 格式，自動找到 References / 參考文獻段落
- **中文文獻支援** — 支援中文學術格式：`作者，年份，《書名》` 及 `作者，年份，〈篇名〉`，含譯者名、子分類標題、年月格式
- **DOI 驗證** — 透過 CrossRef API 比對 DOI 對應的標題、作者與年份
- **多來源搜尋** — 搜尋 CrossRef、Google Scholar（SerpAPI 或 ScraperAPI 爬蟲）、Google 網頁搜尋
- **文獻類型分類** — 區分學術文獻與非學術文獻（媒體、政府、智庫），導向適當的驗證來源
- **多欄位比對** — 比對標題相似度、作者重疊率、出版年份
- **三種結果分類**：
  - **已驗證 (Verified)** — 文獻存在且標題、作者、年份均正確
  - **引用有誤 (Misattributed)** — 文獻存在但作者、年份或標題有出入
  - **疑似捏造 (Fabricated)** — 所有來源均無匹配結果，可能為 AI 生成
- **雙語介面** — 支援英文與中文介面切換

## 快速開始

```bash
# 安裝相依套件
pip install -r requirements.txt

# 啟動伺服器
py app.py
```

開啟瀏覽器前往 http://localhost:5000

### Google Scholar 查詢（選填）

Google Scholar 搜尋支援兩種方式：

1. **ScraperAPI**（推薦）— 使用 [ScraperAPI](https://www.scraperapi.com/) 爬取 Google Scholar
   - 在網頁介面輸入 ScraperAPI Key
   - 或設定環境變數：`export SCRAPERAPI_KEY=your_key`

2. **SerpAPI** — 使用 [SerpAPI](https://serpapi.com/) 的 Google Scholar API
   - 在網頁介面輸入 SerpAPI Key
   - 或設定環境變數：`export SERPAPI_KEY=your_key`

不設定任何 Key 也可使用（僅透過 CrossRef 驗證）。

---

## License

This project is licensed under **AGPL-3.0**.

Academic and research use is welcome.

Commercial use requires a commercial license.

Please contact the author for licensing inquiries.
