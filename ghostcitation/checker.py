"""Verify references by searching Google Scholar and resolving DOIs."""

import re
import time
import logging
from difflib import SequenceMatcher
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
})

SCHOLAR_SEARCH_URL = "https://scholar.google.com/scholar"
DOI_API_URL = "https://api.crossref.org/works/"
CROSSREF_SEARCH_URL = "https://api.crossref.org/works"


def _similarity(a: str, b: str) -> float:
    """Return similarity ratio between two strings (0-1)."""
    a = re.sub(r"[^\w\s]", "", a.lower())
    b = re.sub(r"[^\w\s]", "", b.lower())
    return SequenceMatcher(None, a, b).ratio()


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
            authors = []
            for a in data.get("author", []):
                name = f"{a.get('given', '')} {a.get('family', '')}".strip()
                if name:
                    authors.append(name)
            return {
                "found": True,
                "title": title,
                "authors": authors,
                "doi": data.get("DOI", doi),
                "source": "crossref_doi",
            }
    except Exception as e:
        logger.warning("DOI lookup failed for %s: %s", doi, e)
    return {"found": False, "source": "crossref_doi"}


def search_crossref(title: str, author: str = "") -> dict:
    """Search CrossRef by title (and optionally author)."""
    try:
        params = {"query.title": title, "rows": 3}
        if author:
            first_author = author.split(",")[0].split("&")[0].strip()
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
                if sim >= 0.75:
                    authors = []
                    for a in item.get("author", []):
                        name = f"{a.get('given', '')} {a.get('family', '')}".strip()
                        if name:
                            authors.append(name)
                    return {
                        "found": True,
                        "title": item_title,
                        "authors": authors,
                        "doi": item.get("DOI", ""),
                        "similarity": round(sim, 3),
                        "source": "crossref_search",
                    }
    except Exception as e:
        logger.warning("CrossRef search failed for '%s': %s", title, e)
    return {"found": False, "source": "crossref_search"}


def search_google_scholar(title: str, author: str = "") -> dict:
    """Search Google Scholar for a reference by title."""
    query = title
    if author:
        first_author = author.split(",")[0].split("&")[0].strip()
        query = f'"{title}" {first_author}'

    try:
        resp = _SESSION.get(
            SCHOLAR_SEARCH_URL,
            params={"q": query, "hl": "en"},
            timeout=15,
        )
        if resp.status_code == 429:
            return {"found": False, "error": "rate_limited", "source": "google_scholar"}

        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "lxml")
            results = soup.select(".gs_r.gs_or.gs_scl")
            for result in results[:3]:
                title_el = result.select_one(".gs_rt")
                if not title_el:
                    continue
                result_title = title_el.get_text(strip=True)
                # Remove leading [PDF], [HTML], etc.
                result_title = re.sub(r"^\[.*?\]\s*", "", result_title)
                sim = _similarity(title, result_title)
                if sim >= 0.65:
                    return {
                        "found": True,
                        "title": result_title,
                        "similarity": round(sim, 3),
                        "source": "google_scholar",
                    }
    except Exception as e:
        logger.warning("Google Scholar search failed for '%s': %s", title, e)

    return {"found": False, "source": "google_scholar"}


def check_reference(ref: dict) -> dict:
    """Check a single reference using multiple sources.

    Returns a result dict with verdict: 'verified', 'suspicious', or 'not_found'.
    """
    title = ref.get("title", "")
    authors = ref.get("authors", "")
    doi = ref.get("doi")
    raw = ref.get("raw", "")

    result = {
        "raw": raw,
        "title": title,
        "authors": authors,
        "doi": doi,
        "checks": [],
        "verdict": "not_found",
    }

    # 1. If DOI is present, verify it first
    if doi:
        doi_result = verify_doi(doi)
        result["checks"].append(doi_result)
        if doi_result["found"]:
            # Cross-check: does the DOI metadata match the claimed title?
            sim = _similarity(title, doi_result.get("title", ""))
            doi_result["title_match"] = round(sim, 3)
            if sim >= 0.6:
                result["verdict"] = "verified"
                result["verified_title"] = doi_result["title"]
                result["verified_authors"] = doi_result.get("authors", [])
                return result
            else:
                # DOI exists but title doesn't match — very suspicious
                result["verdict"] = "suspicious"
                result["note"] = "DOI exists but title does not match"
                return result

    # 2. Search CrossRef by title
    cr_result = search_crossref(title, authors)
    result["checks"].append(cr_result)
    if cr_result["found"]:
        result["verdict"] = "verified"
        result["verified_title"] = cr_result["title"]
        result["verified_authors"] = cr_result.get("authors", [])
        return result

    # 3. Search Google Scholar
    time.sleep(1)  # Rate limit courtesy
    gs_result = search_google_scholar(title, authors)
    result["checks"].append(gs_result)
    if gs_result["found"]:
        result["verdict"] = "verified"
        result["verified_title"] = gs_result["title"]
        return result

    # Nothing found
    result["verdict"] = "not_found"
    return result


def check_references(refs: list[dict], progress_callback=None) -> list[dict]:
    """Check a list of references. Returns results for each."""
    results = []
    for i, ref in enumerate(refs):
        result = check_reference(ref)
        results.append(result)
        if progress_callback:
            progress_callback(i + 1, len(refs), result)
        # Be polite with rate limiting
        if i < len(refs) - 1:
            time.sleep(0.5)
    return results
