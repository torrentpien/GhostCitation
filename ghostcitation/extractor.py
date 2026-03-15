"""Extract references from PDF and DOCX files."""

import re
import logging

import pdfplumber
from docx import Document

logger = logging.getLogger(__name__)

# Section header patterns
_HEADER_PATTERNS = [
    r"(?i)(?:^|\n)\s*(References?|Bibliography|Works\s+Cited|Literature\s+Cited|參考文獻|參考書目|参考文献)\s*(?:\n|$)",
]


def _find_column_boundary(page) -> float | None:
    """Detect two-column layout by finding a consistent vertical gap
    between words near the middle of the page.

    Returns the x-coordinate of the column boundary, or None.
    """
    from collections import defaultdict

    words = page.extract_words(x_tolerance=3, y_tolerance=3)
    if not words or len(words) < 20:
        return None

    page_mid = page.width / 2

    # Group words into lines by y-position
    lines_by_y = defaultdict(list)
    for w in words:
        y_key = round(float(w["top"]) / 5) * 5
        lines_by_y[y_key].append(w)

    # For each line, find inter-word gaps near the page center
    gaps = []
    for y_key, line_words in lines_by_y.items():
        line_words.sort(key=lambda w: float(w["x0"]))
        for i in range(len(line_words) - 1):
            gap_start = float(line_words[i]["x1"])
            gap_end = float(line_words[i + 1]["x0"])
            gap_size = gap_end - gap_start
            gap_center = (gap_start + gap_end) / 2
            if gap_size > 20 and abs(gap_center - page_mid) < page_mid * 0.3:
                gaps.append((gap_start, gap_end))

    if len(gaps) < 5:
        return None

    avg_start = sum(g[0] for g in gaps) / len(gaps)
    avg_end = sum(g[1] for g in gaps) / len(gaps)
    return (avg_start + avg_end) / 2


def _extract_page_text(page) -> str:
    """Extract text from a page, handling two-column layout."""
    boundary = _find_column_boundary(page)
    if boundary is not None:
        left = page.crop((0, 0, boundary, page.height))
        right = page.crop((boundary, 0, page.width, page.height))
        left_text = left.extract_text() or ""
        right_text = right.extract_text() or ""
        return left_text + "\n" + right_text
    return page.extract_text() or ""


def extract_text_from_pdf(file_path: str) -> str:
    """Extract full text from a PDF file."""
    text_parts = []
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            page_text = _extract_page_text(page)
            if page_text:
                text_parts.append(page_text)
    return "\n".join(text_parts)


def extract_text_from_docx(file_path: str) -> str:
    """Extract full text from a DOCX file."""
    doc = Document(file_path)
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def find_references_section(text: str) -> str:
    """Locate the references/bibliography section in the text."""
    best_match = None
    best_pos = -1
    for pat in _HEADER_PATTERNS:
        for m in re.finditer(pat, text):
            if m.start() > best_pos:
                best_pos = m.start()
                best_match = m
    if best_match:
        section = text[best_match.end():]
        # Trim at appendix/table section if present
        end_match = re.search(
            r"\n\s*(?:附錄|附表|附圖|Appendix|Appendices)\s*(?:圖表|資料)?\s*(?:\n|$)",
            section,
            re.IGNORECASE,
        )
        if end_match:
            section = section[:end_match.start()]
        return section
    return ""


_REF_SUBHEADER = re.compile(
    r"^[\s\uf000-\uf0ff]*(?:中文專書|中文論文|英文專書|英文論文|中文期刊|英文期刊|"
    r"中文書目|英文書目|中文文獻|英文文獻|西文文獻|日文文獻|"
    r"專書|期刊論文|學位論文|研討會論文|網路資料|其他)\s*$"
)


def _clean_ref_section(ref_section: str) -> str:
    """Clean reference section by removing sub-headers and stray page numbers."""
    lines = ref_section.split("\n")
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if _REF_SUBHEADER.match(stripped):
            continue
        # Remove standalone page numbers (PDF artifacts)
        if re.match(r"^\d{1,3}$", stripped):
            continue
        cleaned.append(line)
    return "\n".join(cleaned)


def parse_references(ref_section: str) -> list[dict]:
    """Parse individual references from the references section.

    Handles numbered references like [1], (1), 1. and also
    author-year style references (APA, etc.).
    """
    ref_section = _clean_ref_section(ref_section)

    # Try numbered references first: [1], (1), 1.
    # Use bracket/paren patterns (unambiguous) and strict "N. " at line start
    bracket_refs = re.split(r"\n\s*(?:\[\d+\]|\(\d+\))\s+", "\n" + ref_section)
    bracket_refs = [r.strip() for r in bracket_refs if r.strip()]

    if len(bracket_refs) >= 3:
        raw_refs = bracket_refs
    else:
        # Try "1. " style but verify sequential numbering
        dot_matches = list(re.finditer(r"\n(\d+)\.\s+", "\n" + ref_section))
        numbers = [int(m.group(1)) for m in dot_matches]
        is_sequential = (
            len(numbers) >= 3
            and numbers[0] <= 2
            and all(numbers[i] == numbers[i - 1] + 1 for i in range(1, min(5, len(numbers))))
        )
        if is_sequential:
            raw_refs = re.split(r"\n\d+\.\s+", "\n" + ref_section)
            raw_refs = [r.strip() for r in raw_refs if r.strip()]
        else:
            raw_refs = _parse_author_year_refs(ref_section)

    # Post-process: split concatenated refs (two refs on same line)
    # e.g., "...73-114。王韻，2023，〈Title〉..."
    # Require the new ref to have a title marker (〈 or 《) to avoid over-splitting
    split_refs = []
    _INLINE_SPLIT = re.compile(
        r"(?<=[。.）)\d])\s*"  # end of previous ref
        r"(?=[\u4e00-\u9fff\u3400-\u4dbf]{2,}[^，,]{0,50}[，,]\s*(?:\d{4}\s*年\s*\d{1,2}\s*月|\d{4})\s*[，,。.]"
        r"[^$]*?[〈《「\u201c])"  # must have a title marker ahead
    )
    for raw in raw_refs:
        text = re.sub(r"\s+", " ", raw).strip()
        parts = _INLINE_SPLIT.split(text)
        split_refs.extend(parts)

    refs = []
    for text in split_refs:
        text = text.strip()
        if len(text) < 15:
            continue
        # Skip appendix/table content (not real references)
        if re.match(r"^(?:附錄|圖\s*\d|表\s*\d|資料來源)", text):
            continue
        # Skip content that looks like table data (no title markers at all)
        if not any(c in text for c in "《》〈〉\"\"「」()（）") and not re.search(r"[A-Z][a-z]", text):
            continue
        ref = {"raw": text}
        ref["title"] = _extract_title(text)
        ref["doi"] = _extract_doi(text)
        ref["authors"] = _extract_authors(text)
        ref["year"] = _extract_year(text)
        refs.append(ref)

    return refs


# Pattern matching the start of an author-year reference entry
_AUTHOR_YEAR_START = re.compile(
    r"^(?:"
    # APA style: "Lastname, F." or "Lastname, F. M.,"
    r"[A-Z\u00C0-\u024F][a-z\u00C0-\u024F]+(?:[-'][A-Z\u00C0-\u024F][a-z\u00C0-\u024F]+)*,\s+[A-Z]\."
    r"|"
    # ASA/Chicago style: "Lastname, Firstname" (full given name, not initial)
    r"[A-Z\u00C0-\u024F][a-z\u00C0-\u024F]+(?:[-'][A-Z\u00C0-\u024F][a-z\u00C0-\u024F]+)*,\s+[A-Z][a-z]{2,}"
    r"|"
    # CJK author: Chinese chars (2-4) followed by （year）or (year)
    r"[\u4e00-\u9fff\u3400-\u4dbf]{2,4}\s*[（(]\d{4}"
    r"|"
    # CJK author comma-year: 沈松僑，1997，  or  林正義，2016 年 3 月，
    r"[\u4e00-\u9fff\u3400-\u4dbf]{2,}[^，,\n]{0,50}[，,]\s*\d{4}\s*(?:年\s*\d{1,2}\s*月)?\s*[，,。.]"
    r"|"
    # CJK translated author: 高格孚（Stéphane Corcuff），2004，
    r"[\u4e00-\u9fff\u3400-\u4dbf][\u4e00-\u9fff\u3400-\u4dbf．·]*(?:（[^）]+）)\s*[，,]\s*\d{4}"
    r"|"
    # CJK author with 〈title〉 or 《title》 directly (no year before title)
    r"[\u4e00-\u9fff\u3400-\u4dbf][\u4e00-\u9fff\u3400-\u4dbf．·]*(?:（[^）]+）)\s*[〈《]"
    r")"
)


def _looks_like_complete_ref(text: str) -> bool:
    """Check if text looks like a complete reference entry.

    A complete entry has a year AND substantial content after the year,
    AND ends with something that looks like a reference ending:
    page numbers, DOI, URL, publisher, volume/issue, or closing punctuation.
    """
    # Try parenthesized year first, then bare year (ASA style: ". 2024. ")
    # or comma-year (Chinese style: "，2004，")
    m = re.search(r"[（(]\d{4}[a-z]?[）)]", text)
    if not m:
        m = re.search(r"\.\s+(\d{4}[a-z]?)\.\s", text)
    if not m:
        m = re.search(r"[，,]\s*\d{4}\s*(?:年\s*\d{1,2}\s*月)?\s*[，,。.]", text)
    if not m:
        return False
    after_year = text[m.end():]
    clean = re.sub(r"\s+", " ", after_year).strip()
    # Check for period (ASCII or Chinese full-width)
    has_period = "." in clean or "。" in clean
    if len(clean) < 20 or not has_period:
        return False

    trimmed = text.rstrip()
    # Ends with page numbers: 123-456, 123–456, 1-8
    if re.search(r"\d+\s*[-–]\s*\d+[.。]?\s*$", trimmed):
        return True
    # Ends with volume/issue: 22（9）, 30(5), etc.
    if re.search(r"\d+\s*[（(]\d+[）)]\s*[.。:：]?\s*$", trimmed):
        return True
    # Ends with DOI
    if re.search(r"10\.\d{4,9}/\S+\s*$", trimmed):
        return True
    # Ends with URL
    if re.search(r"https?://\S+\s*[）)。.]?\s*$", trimmed):
        return True
    # Ends with publisher/place pattern (English)
    if re.search(r"(?:Press|Publishing|Publisher|Publications|Routledge|Springer|Wiley|Elsevier|Cambridge|Oxford)\s*\.?\s*$", trimmed, re.IGNORECASE):
        return True
    # Ends with Chinese publisher pattern: 出版社。 出版中心。 出版。 文化。
    if re.search(r"(?:出版社|出版中心|出版公司|出版|文化|書局)\s*。?\s*$", trimmed):
        return True
    # Ends with "）" or "）。" (closing bracket for CJK references, e.g. 原著出版年)
    if re.search(r"[）)]\s*[.。]?\s*$", trimmed):
        return True
    # Ends with a single number (standalone page or issue)
    if re.search(r"[,，：:]\s*\d+\s*[.。]?\s*$", trimmed):
        return True
    # Ends with Chinese period 。(after any CJK character or closing bracket)
    if re.search(r"[\u4e00-\u9fff\u3400-\u4dbf》〉」】]。\s*$", trimmed):
        return True
    # Ends with 國史館出版。 or similar
    if re.search(r"[\u4e00-\u9fff]+出版\s*。?\s*$", trimmed):
        return True

    # Ends with a word followed by period (journal name, book title, etc.)
    if len(clean) > 50 and re.search(r"[A-Za-z\u4e00-\u9fff]{2,}[.。]\s*$", trimmed):
        return True

    # Fallback: if text is very long (>200 chars after year), likely complete
    return len(clean) > 200


def _has_year_anywhere(text: str) -> bool:
    """Check if text contains a year pattern (parenthesized, bare, or comma-year)."""
    return bool(re.search(
        r"(?:[（(]\d{4}[a-z]?[）)]|\.\s+\d{4}[a-z]?\.\s|[，,]\s*\d{4}\s*(?:年\s*\d{1,2}\s*月)?\s*[，,。.])",
        text,
    ))


# Secondary pattern: CJK author names that may span lines (year on next line)
# Matches lines starting with 2+ CJK chars followed by semicolons and more CJK names
_CJK_AUTHOR_LINE = re.compile(
    r"^[\u4e00-\u9fff\u3400-\u4dbf]{2,}[\u4e00-\u9fff\u3400-\u4dbf．·]*"
    r"(?:[；;、][\u4e00-\u9fff\u3400-\u4dbf．·\s]+)*\s*$"
)


def _parse_author_year_refs(ref_section: str) -> list[str]:
    """Parse author-year style references by detecting entry boundaries."""
    lines = ref_section.split("\n")
    non_blank = [l.strip() for l in lines if l.strip()]

    # Heuristic: if most non-blank lines individually contain a year
    # and start with an author pattern, treat each line as a separate reference
    # (common in DOCX and well-formatted documents)
    if len(non_blank) >= 3:
        lines_with_year = sum(1 for l in non_blank if _has_year_anywhere(l))
        lines_with_author = sum(1 for l in non_blank if _AUTHOR_YEAR_START.match(l))
        ratio_year = lines_with_year / len(non_blank)
        ratio_author = lines_with_author / len(non_blank)
        if ratio_year >= 0.7 and ratio_author >= 0.6:
            return non_blank

    # Otherwise: multi-line entries, use buffer-based parsing
    raw_refs = []
    current = []

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue

        is_new_entry = bool(_AUTHOR_YEAR_START.match(stripped))

        # Also detect CJK-only author lines (year will be on next line)
        # but only if the previous buffer looks complete
        if not is_new_entry and _CJK_AUTHOR_LINE.match(stripped) and current:
            combined = " ".join(current)
            if _looks_like_complete_ref(combined):
                raw_refs.append(combined)
                current = [stripped]
                continue

        if is_new_entry and current:
            combined = " ".join(current)
            if _looks_like_complete_ref(combined):
                raw_refs.append(combined)
                current = [stripped]
                continue

        current.append(stripped)

    if current:
        raw_refs.append(" ".join(current))

    return raw_refs


def _extract_doi(text: str) -> str | None:
    """Extract DOI from reference text."""
    m = re.search(r"(10\.\d{4,9}/[^\s,;\"'>\]]+)", text)
    if m:
        return m.group(1).rstrip(".")
    return None


def _extract_year(text: str) -> str | None:
    """Extract publication year from reference text."""
    # Chinese comma-year: ，2004，  or  ，2004。  or  ，2016 年 3 月，
    m = re.search(r"[，,]\s*((?:19|20)\d{2})\s*(?:年\s*\d{1,2}\s*月)?\s*[，,。.]", text)
    if m:
        return m.group(1)
    # Try full-width or half-width parenthesized year first
    m = re.search(r"[（(]((?:19|20)\d{2})[a-z]?[）)]", text)
    if m:
        return m.group(1)
    # ASA/Chicago style: ". 2024. " or ". 2024a. "
    m = re.search(r"\.\s+((?:19|20)\d{2})[a-z]?\.\s", text)
    if m:
        return m.group(1)
    # Bare year fallback
    m = re.search(r"\b((?:19|20)\d{2})\b", text)
    if m:
        return m.group(1)
    return None


def _extract_title(text: str) -> str:
    """Extract the likely title from a reference string."""
    # Chinese article title in〈〉 (prefer article title over book/journal title)
    m = re.search(r"〈(.+?)〉", text)
    if m and len(m.group(1)) > 2:
        return m.group(1).strip()

    # Chinese book title in《》
    m = re.search(r"《(.+?)》", text)
    if m and len(m.group(1)) > 2:
        return m.group(1).strip()

    # Try quoted title (English or CJK quotes)
    m = re.search(r'["\u201c「](.+?)["\u201d」]', text)
    if m and len(m.group(1)) > 10:
        return m.group(1).strip()

    # APA style: Author (Year). Title. Journal ...
    m = re.search(r"[）)]\.\s*(.+?)[\.\?]", text)
    if m and len(m.group(1)) > 10:
        return m.group(1).strip()

    # APA style: Author (Year) Title. Journal ...
    m = re.search(r"[）)]\s+(.+?)[\.\?]", text)
    if m and len(m.group(1)) > 10:
        return m.group(1).strip()

    # ASA/Chicago style: Author. Year. Title. Journal ...
    m = re.search(r"\.\s+\d{4}[a-z]?\.\s+(.+?)[\.\?]", text)
    if m and len(m.group(1)) > 10:
        return m.group(1).strip()

    # Chinese comma-year style: Author，Year，Title。
    m = re.search(r"[，,]\s*\d{4}\s*[，,]\s*(.+?)[。.]", text)
    if m and len(m.group(1)) > 2:
        return m.group(1).strip()

    # Try: after first period, take next sentence
    parts = text.split(". ")
    if len(parts) >= 2:
        candidate = parts[1].strip().rstrip(".")
        if len(candidate) > 10:
            return candidate

    # Fallback
    return text[:120]


def _extract_authors(text: str) -> str:
    """Extract author names (first author at minimum)."""
    # Chinese comma-year style: text before ，Year，
    m = re.match(r"(.+?)\s*[，,]\s*\d{4}\s*[，,。.]", text)
    if m:
        author = m.group(1).strip()
        # Remove trailing parenthesized western name for cleaner author field
        # but keep it if it's useful
        return author.rstrip(",").rstrip("，")

    # APA style: text before year in parentheses (full-width or half-width)
    m = re.match(r"(.+?)\s*[（(]\d{4}", text)
    if m:
        return m.group(1).strip().rstrip(",").rstrip(".")

    # ASA/Chicago style: text before ". Year."
    m = re.match(r"(.+?)\.\s+\d{4}[a-z]?\.", text)
    if m:
        return m.group(1).strip().rstrip(",").rstrip(".")

    # Text before first period
    parts = text.split(".")
    if parts:
        return parts[0].strip()

    return ""


def extract_references(file_path: str) -> list[dict]:
    """Main entry: extract references from a PDF or DOCX file."""
    ext = file_path.rsplit(".", 1)[-1].lower()
    if ext == "pdf":
        text = extract_text_from_pdf(file_path)
    elif ext in ("docx",):
        text = extract_text_from_docx(file_path)
    else:
        raise ValueError(f"Unsupported file type: .{ext}")

    ref_section = find_references_section(text)
    if not ref_section:
        return []

    return parse_references(ref_section)
