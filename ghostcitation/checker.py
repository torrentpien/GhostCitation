"""Verify references by searching Google Scholar (SerpAPI) and CrossRef.

Distinguishes between:
- verified: reference found and metadata matches
- misattributed: reference exists but author, year, or title has errors
- fabricated: no matching reference found in any source (likely AI-generated)
"""

import os
import re
import time
import logging
from difflib import SequenceMatcher
from urllib.parse import quote_plus

import requests

logger = logging.getLogger(__name__)

_SESSION = requests.Session()

DOI_API_URL = "https://api.crossref.org/works/"
CROSSREF_SEARCH_URL = "https://api.crossref.org/works"
SERPAPI_URL = "https://serpapi.com/search"

SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")


# ---------------------------------------------------------------------------
# Similarity helpers
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    """Lowercase and strip punctuation for comparison."""
    return re.sub(r"[^\w\s]", "", text.lower()).strip()


def _similarity(a: str, b: str) -> float:
    """Return similarity ratio between two strings (0-1)."""
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def _author_last_names(author_str: str) -> list[str]:
    """Extract last-name tokens from an author string."""
    # Remove connectors
    cleaned = re.sub(r"\b(and|&|et\s+al\.?)\b", ",", author_str, flags=re.IGNORECASE)
    parts = re.split(r"[,;]", cleaned)
    names = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # For "Lastname, F. M." style — first token is last name
        tokens = part.split()
        if tokens:
            # Skip single initials
            candidate = tokens[0].rstrip(".")
            if len(candidate) > 1:
                names.append(_normalize(candidate))
    return names


def _author_overlap(claimed: str, actual_authors: list[str]) -> float:
    """Compute overlap between claimed author string and actual author list.

    Returns a score from 0 to 1.
    """
    if not claimed or not actual_authors:
        return 0.0

    claimed_names = _author_last_names(claimed)
    if not claimed_names:
        return 0.0

    # Extract last names from actual authors (which are "Given Family" format)
    actual_names = []
    for a in actual_authors:
        tokens = a.strip().split()
        if tokens:
            actual_names.append(_normalize(tokens[-1]))

    if not actual_names:
        return 0.0

    # Count how many claimed names appear in actual names
    matches = sum(1 for c in claimed_names if any(_similarity(c, a) > 0.8 for a in actual_names))
    return matches / max(len(claimed_names), 1)


def _year_matches(claimed_year: str | None, actual_year: str | None) -> bool:
    """Check if claimed year matches actual year."""
    if not claimed_year or not actual_year:
        return True  # Can't verify — don't penalize
    return claimed_year.strip() == actual_year.strip()


def _extract_year_from_crossref(item: dict) -> str | None:
    """Extract publication year from CrossRef metadata."""
    for field in ["published-print", "published-online", "issued", "created"]:
        date_parts = item.get(field, {}).get("date-parts", [[]])
        if date_parts and date_parts[0] and date_parts[0][0]:
            return str(date_parts[0][0])
    return None


def _extract_authors_from_crossref(item: dict) -> list[str]:
    """Extract author names from CrossRef metadata."""
    authors = []
    for a in item.get("author", []):
        name = f"{a.get('given', '')} {a.get('family', '')}".strip()
        if name:
            authors.append(name)
    return authors


# ---------------------------------------------------------------------------
# DOI verification
# ---------------------------------------------------------------------------

def verify_doi(doi: str) -> dict:
    """Verify a DOI via CrossRef API. Returns metadata if found."""
    try:
        resp = _SESSION.get(
            DOI_API_URL + quote_plus(doi),
            timeout=15,
            headers={"Accept": "application/json"},
        )
        if resp.status_code == 200:
            data = resp.json().get("message", {})
            title_parts = data.get("title", [])
            title = title_parts[0] if title_parts else ""
            authors = _extract_authors_from_crossref(data)
            year = _extract_year_from_crossref(data)
            return {
                "found": True,
                "title": title,
                "authors": authors,
                "year": year,
                "doi": data.get("DOI", doi),
                "source": "crossref_doi",
            }
    except Exception as e:
        logger.warning("DOI lookup failed for %s: %s", doi, e)
    return {"found": False, "source": "crossref_doi"}


# ---------------------------------------------------------------------------
# CrossRef title search
# ---------------------------------------------------------------------------

def search_crossref(title: str, author: str = "") -> dict:
    """Search CrossRef by title (and optionally author)."""
    try:
        params = {"query.title": title, "rows": 3}
        if author:
            first_author = author.split(",")[0].split("&")[0].strip()
            if len(first_author) > 1:
                params["query.author"] = first_author
        resp = _SESSION.get(
            CROSSREF_SEARCH_URL,
            params=params,
            timeout=15,
            headers={"Accept": "application/json"},
        )
        if resp.status_code == 200:
            items = resp.json().get("message", {}).get("items", [])
            for item in items:
                item_title = (item.get("title") or [""])[0]
                sim = _similarity(title, item_title)
                if sim >= 0.65:
                    authors = _extract_authors_from_crossref(item)
                    year = _extract_year_from_crossref(item)
                    return {
                        "found": True,
                        "title": item_title,
                        "authors": authors,
                        "year": year,
                        "doi": item.get("DOI", ""),
                        "title_similarity": round(sim, 3),
                        "source": "crossref_search",
                    }
    except Exception as e:
        logger.warning("CrossRef search failed for '%s': %s", title, e)
    return {"found": False, "source": "crossref_search"}


# ---------------------------------------------------------------------------
# Google Scholar via SerpAPI
# ---------------------------------------------------------------------------

def search_google_scholar(title: str, author: str = "") -> dict:
    """Search Google Scholar using SerpAPI."""
    if not SERPAPI_KEY:
        return {"found": False, "source": "google_scholar", "error": "no_api_key"}

    query = title
    if author:
        first_author = author.split(",")[0].split("&")[0].strip()
        if len(first_author) > 1:
            query = f"{title} {first_author}"

    try:
        params = {
            "engine": "google_scholar",
            "q": query,
            "api_key": SERPAPI_KEY,
            "num": 3,
        }
        resp = _SESSION.get(SERPAPI_URL, params=params, timeout=20)

        if resp.status_code == 200:
            data = resp.json()
            results = data.get("organic_results", [])
            for result in results[:3]:
                result_title = result.get("title", "")
                sim = _similarity(title, result_title)
                if sim >= 0.60:
                    # Extract year from snippet or publication_info
                    pub_info = result.get("publication_info", {})
                    summary = pub_info.get("summary", "")
                    authors_str = pub_info.get("authors", [])
                    gs_authors = []
                    if isinstance(authors_str, list):
                        gs_authors = [a.get("name", "") for a in authors_str if isinstance(a, dict)]
                    elif isinstance(authors_str, str):
                        gs_authors = [a.strip() for a in authors_str.split(",")]

                    # Try to extract year from summary
                    year_match = re.search(r"\b((?:19|20)\d{2})\b", summary)
                    gs_year = year_match.group(1) if year_match else None

                    return {
                        "found": True,
                        "title": result_title,
                        "authors": gs_authors,
                        "year": gs_year,
                        "title_similarity": round(sim, 3),
                        "source": "google_scholar",
                    }
        elif resp.status_code == 429:
            return {"found": False, "error": "rate_limited", "source": "google_scholar"}
    except Exception as e:
        logger.warning("Google Scholar search failed for '%s': %s", title, e)

    return {"found": False, "source": "google_scholar"}


# ---------------------------------------------------------------------------
# Mismatch analysis — distinguish fabricated vs misattributed
# ---------------------------------------------------------------------------

def _analyze_match(ref: dict, match: dict) -> dict:
    """Analyze the quality of a match to distinguish verified vs misattributed.

    Returns a dict with:
    - verdict: 'verified' or 'misattributed'
    - mismatches: list of specific mismatches found
    - details: human-readable description
    """
    claimed_title = ref.get("title", "")
    claimed_authors = ref.get("authors", "")
    claimed_year = ref.get("year")

    matched_title = match.get("title", "")
    matched_authors = match.get("authors", [])
    matched_year = match.get("year")

    mismatches = []

    # 1. Title comparison
    title_sim = _similarity(claimed_title, matched_title)
    if title_sim < 0.80:
        mismatches.append({
            "field": "title",
            "claimed": claimed_title,
            "actual": matched_title,
            "similarity": round(title_sim, 3),
            "description": f"標題不完全匹配（相似度 {title_sim:.0%}）",
        })

    # 2. Author comparison
    if claimed_authors and matched_authors:
        author_score = _author_overlap(claimed_authors, matched_authors)
        if author_score < 0.5:
            actual_str = "; ".join(matched_authors[:3])
            if len(matched_authors) > 3:
                actual_str += " et al."
            mismatches.append({
                "field": "authors",
                "claimed": claimed_authors,
                "actual": actual_str,
                "overlap": round(author_score, 3),
                "description": f"作者不符（匹配率 {author_score:.0%}）",
            })

    # 3. Year comparison
    if claimed_year and matched_year and not _year_matches(claimed_year, matched_year):
        mismatches.append({
            "field": "year",
            "claimed": claimed_year,
            "actual": matched_year,
            "description": f"年份不符（標示 {claimed_year}，實際 {matched_year}）",
        })

    if mismatches:
        return {
            "verdict": "misattributed",
            "mismatches": mismatches,
        }
    return {"verdict": "verified", "mismatches": []}


# ---------------------------------------------------------------------------
# Main check logic
# ---------------------------------------------------------------------------

def check_reference(ref: dict) -> dict:
    """Check a single reference using multiple sources.

    Returns a result dict with verdict:
    - 'verified': found and metadata matches
    - 'misattributed': found but author/year/title has errors
    - 'fabricated': not found in any source (likely AI-generated)
    """
    title = ref.get("title", "")
    authors = ref.get("authors", "")
    doi = ref.get("doi")
    year = ref.get("year")
    raw = ref.get("raw", "")

    result = {
        "raw": raw,
        "title": title,
        "authors": authors,
        "year": year,
        "doi": doi,
        "checks": [],
        "mismatches": [],
        "verdict": "fabricated",
    }

    # 1. If DOI is present, verify it first
    if doi:
        doi_result = verify_doi(doi)
        result["checks"].append(doi_result)
        if doi_result["found"]:
            analysis = _analyze_match(ref, doi_result)
            result["verdict"] = analysis["verdict"]
            result["mismatches"] = analysis["mismatches"]
            result["verified_title"] = doi_result["title"]
            result["verified_authors"] = doi_result.get("authors", [])
            result["verified_year"] = doi_result.get("year")
            return result

    # 2. Search CrossRef by title
    cr_result = search_crossref(title, authors)
    result["checks"].append(cr_result)
    if cr_result["found"]:
        analysis = _analyze_match(ref, cr_result)
        result["verdict"] = analysis["verdict"]
        result["mismatches"] = analysis["mismatches"]
        result["verified_title"] = cr_result["title"]
        result["verified_authors"] = cr_result.get("authors", [])
        result["verified_year"] = cr_result.get("year")
        return result

    # 3. Search Google Scholar via SerpAPI
    time.sleep(0.5)
    gs_result = search_google_scholar(title, authors)
    result["checks"].append(gs_result)
    if gs_result["found"]:
        analysis = _analyze_match(ref, gs_result)
        result["verdict"] = analysis["verdict"]
        result["mismatches"] = analysis["mismatches"]
        result["verified_title"] = gs_result["title"]
        result["verified_authors"] = gs_result.get("authors", [])
        result["verified_year"] = gs_result.get("year")
        return result

    # Nothing found in any source
    result["verdict"] = "fabricated"
    return result


def check_references(refs: list[dict], progress_callback=None) -> list[dict]:
    """Check a list of references. Returns results for each."""
    results = []
    for i, ref in enumerate(refs):
        result = check_reference(ref)
        results.append(result)
        if progress_callback:
            progress_callback(i + 1, len(refs), result)
        if i < len(refs) - 1:
            time.sleep(0.3)
    return results
