"""Extract references from PDF and DOCX files."""

import re

import pdfplumber
from docx import Document


def extract_text_from_pdf(file_path: str) -> str:
    """Extract full text from a PDF file."""
    text_parts = []
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
    return "\n".join(text_parts)


def extract_text_from_docx(file_path: str) -> str:
    """Extract full text from a DOCX file."""
    doc = Document(file_path)
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def find_references_section(text: str) -> str:
    """Locate the references/bibliography section in the text."""
    patterns = [
        r"(?i)\n\s*(References|Bibliography|Works\s+Cited|Literature\s+Cited|參考文獻|參考書目|参考文献)\s*\n",
    ]
    best_match = None
    best_pos = -1
    for pat in patterns:
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
    unnumbered references separated by blank lines.
    """
    refs = []

    # Try numbered references first: [1], (1), 1.
    numbered = re.split(r"\n\s*(?:\[\d+\]|\(\d+\)|\d+\.)\s+", "\n" + ref_section)
    numbered = [r.strip() for r in numbered if r.strip()]

    if len(numbered) >= 2:
        raw_refs = numbered
    else:
        # Fall back: split by blank lines
        raw_refs = [r.strip() for r in re.split(r"\n\s*\n", ref_section) if r.strip()]

    for raw in raw_refs:
        # Collapse multi-line into single line
        text = re.sub(r"\s+", " ", raw).strip()
        if len(text) < 10:
            continue
        ref = {"raw": text}
        ref["title"] = _extract_title(text)
        ref["doi"] = _extract_doi(text)
        ref["authors"] = _extract_authors(text)
        refs.append(ref)

    return refs


def _extract_doi(text: str) -> str | None:
    """Extract DOI from reference text."""
    m = re.search(r"(10\.\d{4,9}/[^\s,;\"'>\]]+)", text)
    if m:
        doi = m.group(1).rstrip(".")
        return doi
    return None


def _extract_title(text: str) -> str:
    """Extract the likely title from a reference string.

    Heuristic: look for quoted title, or text after year in parentheses,
    or the longest sentence-like fragment.
    """
    # Try quoted title first
    m = re.search(r'["\u201c](.+?)["\u201d]', text)
    if m and len(m.group(1)) > 10:
        return m.group(1).strip()

    # Try: Author (Year). Title. Journal ...
    m = re.search(r"\(\d{4}[a-z]?\)\.\s*(.+?)[\.\?]", text)
    if m and len(m.group(1)) > 10:
        return m.group(1).strip()

    # Try: Author (Year) Title. Journal ...
    m = re.search(r"\(\d{4}[a-z]?\)\s+(.+?)[\.\?]", text)
    if m and len(m.group(1)) > 10:
        return m.group(1).strip()

    # Try: after first period (skip author block), take next sentence
    parts = text.split(". ")
    if len(parts) >= 2:
        candidate = parts[1].strip().rstrip(".")
        if len(candidate) > 10:
            return candidate

    # Fallback: return first 120 chars
    return text[:120]


def _extract_authors(text: str) -> str:
    """Extract author names (first author at minimum)."""
    # Take text before first year in parentheses
    m = re.match(r"(.+?)\s*\(\d{4}", text)
    if m:
        return m.group(1).strip().rstrip(",").rstrip(".")

    # Take text before first period
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
