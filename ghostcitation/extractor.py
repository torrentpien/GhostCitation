"""Extract references from PDF and DOCX files."""

import re
import logging

import pdfplumber
from docx import Document

logger = logging.getLogger(__name__)

# Section header patterns
_HEADER_PATTERNS = [
    r"(?i)(?:^|\n)\s*(References|Bibliography|Works\s+Cited|Literature\s+Cited|參考文獻|參考書目|参考文献)\s*(?:\n|$)",
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
        return text[best_match.end():]
    return ""


def parse_references(ref_section: str) -> list[dict]:
    """Parse individual references from the references section.

    Handles numbered references like [1], (1), 1. and also
    author-year style references (APA, etc.).
    """
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

    refs = []
    for raw in raw_refs:
        text = re.sub(r"\s+", " ", raw).strip()
        if len(text) < 15:
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
    # English: "Lastname, F." or "Lastname, F. M.,"
    r"[A-Z\u00C0-\u024F][a-z\u00C0-\u024F]+(?:[-'][A-Z\u00C0-\u024F][a-z\u00C0-\u024F]+)*,\s+[A-Z]\."
    r"|"
    # CJK author: Chinese chars (2-4) followed by （year）or (year)
    r"[\u4e00-\u9fff\u3400-\u4dbf]{2,4}\s*[（(]\d{4}"
    r"|"
    # Organization/all-caps start
    r"[A-Z][A-Za-z]+\s+[A-Z][a-z]+\s+[A-Z]"  # e.g. "Council for Economic..."
    r")"
)


def _looks_like_complete_ref(text: str) -> bool:
    """Check if text looks like a complete reference entry.

    A complete entry has a year AND substantial content after the year
    (title + journal/publisher info).
    """
    m = re.search(r"[（(]\d{4}[a-z]?[）)]", text)
    if not m:
        return False
    after_year = text[m.end():]
    # Should have meaningful content after year (title, journal, etc.)
    # At least ~30 chars of content with a period (sentence end)
    clean = re.sub(r"\s+", " ", after_year).strip()
    return len(clean) > 30 and "." in clean


def _parse_author_year_refs(ref_section: str) -> list[str]:
    """Parse author-year style references by detecting entry boundaries."""
    lines = ref_section.split("\n")
    raw_refs = []
    current = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # Check if this line starts a new reference entry
        is_new_entry = bool(_AUTHOR_YEAR_START.match(stripped))

        if is_new_entry and current:
            combined = " ".join(current)
            # Only split if the current buffer is a complete reference
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
    # Try full-width or half-width parenthesized year first
    m = re.search(r"[（(](\d{4})[a-z]?[）)]", text)
    if m:
        return m.group(1)
    # Bare year
    m = re.search(r"\b((?:19|20)\d{2})\b", text)
    if m:
        return m.group(1)
    return None


def _extract_title(text: str) -> str:
    """Extract the likely title from a reference string."""
    # Try quoted title (English or CJK quotes)
    m = re.search(r'["\u201c「](.+?)["\u201d」]', text)
    if m and len(m.group(1)) > 10:
        return m.group(1).strip()

    # Try: Author (Year). Title. Journal ...
    m = re.search(r"[）)]\.\s*(.+?)[\.\?]", text)
    if m and len(m.group(1)) > 10:
        return m.group(1).strip()

    # Try: Author (Year) Title. Journal ...
    m = re.search(r"[）)]\s+(.+?)[\.\?]", text)
    if m and len(m.group(1)) > 10:
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
    # Text before first year in parentheses (full-width or half-width)
    m = re.match(r"(.+?)\s*[（(]\d{4}", text)
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
