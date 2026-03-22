"""Verify references by searching Google Scholar (SerpAPI), CrossRef, and Google.

Classifies references as academic or non-academic (media, government, think tank),
then routes to the appropriate verification source.

Distinguishes between:
- verified: reference found and metadata matches
- misattributed: reference exists but author, year, or title has errors
- fabricated: no matching reference found in any source (likely AI-generated)
"""

import os
import re
import time
import unicodedata
import logging
from difflib import SequenceMatcher
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_SESSION = requests.Session()

DOI_API_URL = "https://api.crossref.org/works/"
CROSSREF_SEARCH_URL = "https://api.crossref.org/works"
SERPAPI_URL = "https://serpapi.com/search"

SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")
SCRAPERAPI_KEY = os.environ.get("SCRAPERAPI_KEY", "")
APIFY_KEY = os.environ.get("APIFY_KEY", "")


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

def _strip_accents(text: str) -> str:
    """Remove diacritical marks from text for API compatibility."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _crossref_query(title: str, author: str = "") -> dict | None:
    """Execute a single CrossRef query and return the best matching item."""
    try:
        params = {"query.title": title, "rows": 5}
        if author:
            params["query.author"] = _strip_accents(author)
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
                    authors_list = _extract_authors_from_crossref(item)
                    year = _extract_year_from_crossref(item)
                    return {
                        "found": True,
                        "title": item_title,
                        "authors": authors_list,
                        "year": year,
                        "doi": item.get("DOI", ""),
                        "title_similarity": round(sim, 3),
                        "source": "crossref_search",
                    }
    except Exception as e:
        logger.warning("CrossRef search failed for '%s': %s", title, e)
    return None


def search_crossref(title: str, author: str = "") -> dict:
    """Search CrossRef by title (and optionally author).

    Tries with author first; if no match, retries without author filter
    since CrossRef author data can be unreliable.
    """
    if author:
        first_author = author.split(",")[0].split("&")[0].strip()
        if len(first_author) > 1:
            result = _crossref_query(title, first_author)
            if result:
                return result

    # Retry without author filter
    result = _crossref_query(title)
    if result:
        return result

    return {"found": False, "source": "crossref_search"}


# ---------------------------------------------------------------------------
# Web scraping fallbacks (used when no SerpAPI key)
# ---------------------------------------------------------------------------

_SCRAPE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,zh-TW;q=0.8",
}


def _scrape_google_scholar(title: str, author: str = "") -> dict:
    """Scrape Google Scholar via ScraperAPI proxy or direct fallback."""
    if not SCRAPERAPI_KEY:
        return {"found": False, "source": "google_scholar", "error": "no_api_key"}

    query = title
    if author:
        first_author = author.split(",")[0].split("&")[0].strip()
        if len(first_author) > 1:
            query = f"{title} {first_author}"

    try:
        # Use ScraperAPI as proxy to avoid blocks
        target_url = f"https://scholar.google.com/scholar?q={quote_plus(query)}&hl=en&num=5"
        api_url = "https://api.scraperapi.com"
        params = {
            "api_key": SCRAPERAPI_KEY,
            "url": target_url,
        }
        resp = _SESSION.get(
            api_url, params=params, headers=_SCRAPE_HEADERS, timeout=60,
        )

        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            entries = soup.select("div.gs_ri")

            for entry in entries[:5]:
                # Title
                title_el = entry.select_one("h3.gs_rt")
                if not title_el:
                    continue
                # Remove [PDF], [HTML] etc. link prefixes
                for span in title_el.select("span"):
                    span.decompose()
                result_title = title_el.get_text(strip=True)

                sim = _similarity(title, result_title)
                if sim < 0.60:
                    continue

                # Author/year info line
                info_el = entry.select_one("div.gs_a")
                info_text = info_el.get_text(strip=True) if info_el else ""

                # Parse authors (before " - " separator)
                gs_authors = []
                year = None
                if info_text:
                    # Format: "Author1, Author2 - Journal, Year - Publisher"
                    parts = info_text.split(" - ")
                    if parts:
                        author_part = parts[0].strip()
                        gs_authors = [
                            a.strip() for a in author_part.split(",")
                            if a.strip() and not re.match(r"^\d{4}$", a.strip())
                        ]
                    year_match = re.search(r"\b((?:19|20)\d{2})\b", info_text)
                    year = year_match.group(1) if year_match else None

                # URL
                link_el = title_el.select_one("a")
                result_url = link_el["href"] if link_el and link_el.has_attr("href") else ""

                return {
                    "found": True,
                    "title": result_title,
                    "authors": gs_authors,
                    "year": year,
                    "url": result_url,
                    "title_similarity": round(sim, 3),
                    "source": "google_scholar_scrape",
                }

        elif resp.status_code == 429:
            logger.warning("ScraperAPI rate-limited for Google Scholar")
            return {"found": False, "error": "rate_limited", "source": "google_scholar_scrape"}

    except Exception as e:
        logger.warning("ScraperAPI Google Scholar failed for '%s': %s", title, e)

    return {"found": False, "source": "google_scholar_scrape"}


def _scrape_google(title: str, author: str = "") -> dict:
    """Scrape Google web search via ScraperAPI proxy or direct fallback."""
    if not SCRAPERAPI_KEY:
        return {"found": False, "source": "google", "error": "no_api_key"}

    query = f'"{title}"'
    if author and len(author) > 2:
        first_author = author.split(",")[0].split("&")[0].strip()
        if len(first_author) > 1:
            query += f" {first_author}"

    try:
        target_url = f"https://www.google.com/search?q={quote_plus(query)}&hl=zh-TW&num=5"
        api_url = "https://api.scraperapi.com"
        params = {
            "api_key": SCRAPERAPI_KEY,
            "url": target_url,
        }
        resp = _SESSION.get(
            api_url, params=params, headers=_SCRAPE_HEADERS, timeout=60,
        )

        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")

            # Google search results are in divs with class "g" or data-sokoban
            for g in soup.select("div.g"):
                # Title
                title_el = g.select_one("h3")
                if not title_el:
                    continue
                result_title = title_el.get_text(strip=True)

                # Link
                link_el = g.select_one("a")
                link = link_el["href"] if link_el and link_el.has_attr("href") else ""

                # Snippet
                snippet_el = (
                    g.select_one("div.VwiC3b")
                    or g.select_one("span.st")
                    or g.select_one("div[data-sncf]")
                )
                snippet = snippet_el.get_text(strip=True) if snippet_el else ""

                title_sim = _similarity(title, result_title)
                snippet_sim = _similarity(title, snippet) if snippet else 0

                if title_sim >= 0.50 or snippet_sim >= 0.40:
                    year_match = re.search(r"\b((?:19|20)\d{2})\b", snippet)
                    found_year = year_match.group(1) if year_match else None

                    return {
                        "found": True,
                        "title": result_title,
                        "authors": [],
                        "year": found_year,
                        "url": link,
                        "title_similarity": round(max(title_sim, snippet_sim), 3),
                        "source": "google_scrape",
                    }

        elif resp.status_code == 429:
            logger.warning("Google scrape rate-limited")
            return {"found": False, "error": "rate_limited", "source": "google_scrape"}

    except Exception as e:
        logger.warning("Google scrape failed for '%s': %s", title, e)

    return {"found": False, "source": "google_scrape"}


# ---------------------------------------------------------------------------
# Apify Google Scholar scraper
# ---------------------------------------------------------------------------

def _apify_google_scholar(title: str, author: str = "") -> dict:
    """Search Google Scholar via Apify actor marco.gullo/google-scholar-scraper."""
    if not APIFY_KEY:
        return {"found": False, "source": "apify_google_scholar", "error": "no_api_key"}

    query = title
    if author:
        first_author = author.split(",")[0].split("&")[0].strip()
        if len(first_author) > 1:
            query = f"{title} {first_author}"

    try:
        run_url = (
            f"https://api.apify.com/v2/acts/marco.gullo~google-scholar-scraper"
            f"/run-sync-get-dataset-items?token={APIFY_KEY}"
        )
        payload = {
            "enableDebugDumps": False,
            "filter": "all",
            "keyword": query,
            "maxItems": 3,
            "proxyOptions": {
                "useApifyProxy": True,
            },
        }
        resp = _SESSION.post(
            run_url,
            json=payload,
            timeout=120,
            headers={"Content-Type": "application/json"},
        )

        if resp.status_code == 200:
            items = resp.json()
            if not isinstance(items, list):
                items = []

            for item in items[:3]:
                result_title = item.get("title", "")
                sim = _similarity(title, result_title)
                if sim < 0.60:
                    continue

                # Parse authors
                authors_raw = item.get("authors", "")
                gs_authors = []
                if isinstance(authors_raw, str) and authors_raw:
                    gs_authors = [a.strip() for a in authors_raw.split(",") if a.strip()]
                elif isinstance(authors_raw, list):
                    gs_authors = [str(a).strip() for a in authors_raw if a]

                # Year
                year = None
                pub_info = item.get("publicationInfo", "") or item.get("year", "")
                if isinstance(pub_info, str):
                    year_match = re.search(r"\b((?:19|20)\d{2})\b", pub_info)
                    year = year_match.group(1) if year_match else None
                if not year:
                    year_match = re.search(r"\b((?:19|20)\d{2})\b", str(item))
                    year = year_match.group(1) if year_match else None

                result_url = item.get("url", "") or item.get("link", "")

                return {
                    "found": True,
                    "title": result_title,
                    "authors": gs_authors,
                    "year": year,
                    "url": result_url,
                    "title_similarity": round(sim, 3),
                    "source": "apify_google_scholar",
                }

        elif resp.status_code == 402:
            logger.warning("Apify usage limit reached")
            return {"found": False, "error": "usage_limit", "source": "apify_google_scholar"}

    except Exception as e:
        logger.warning("Apify Google Scholar failed for '%s': %s", title, e)

    return {"found": False, "source": "apify_google_scholar"}


# ---------------------------------------------------------------------------
# Google Scholar via SerpAPI
# ---------------------------------------------------------------------------

def search_google_scholar(title: str, author: str = "") -> dict:
    """Search Google Scholar using SerpAPI, with scraping fallback."""
    if not SERPAPI_KEY:
        return _scrape_google_scholar(title, author)

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
# Google web search via SerpAPI (for non-academic references)
# ---------------------------------------------------------------------------

def search_google(title: str, author: str = "", raw: str = "") -> dict:
    """Search Google (web) using SerpAPI, with scraping fallback."""
    if not SERPAPI_KEY:
        return _scrape_google(title, author)

    # Build a targeted query
    query = f'"{title}"'
    if author and len(author) > 2:
        query += f" {author}"

    try:
        params = {
            "engine": "google",
            "q": query,
            "api_key": SERPAPI_KEY,
            "num": 5,
            "hl": "zh-TW",
        }
        resp = _SESSION.get(SERPAPI_URL, params=params, timeout=20)

        if resp.status_code == 200:
            data = resp.json()
            results = data.get("organic_results", [])
            for result in results[:5]:
                result_title = result.get("title", "")
                snippet = result.get("snippet", "")
                link = result.get("link", "")

                # Check if the title or snippet matches
                title_sim = _similarity(title, result_title)
                snippet_sim = _similarity(title, snippet) if snippet else 0

                if title_sim >= 0.50 or snippet_sim >= 0.40:
                    # Try to extract year from snippet
                    year_match = re.search(r"\b((?:19|20)\d{2})\b", snippet)
                    found_year = year_match.group(1) if year_match else None

                    return {
                        "found": True,
                        "title": result_title,
                        "authors": [],
                        "year": found_year,
                        "url": link,
                        "title_similarity": round(max(title_sim, snippet_sim), 3),
                        "source": "google",
                    }
        elif resp.status_code == 429:
            return {"found": False, "error": "rate_limited", "source": "google"}
    except Exception as e:
        logger.warning("Google search failed for '%s': %s", title, e)

    return {"found": False, "source": "google"}


# ---------------------------------------------------------------------------
# Reference type classification
# ---------------------------------------------------------------------------

# Keywords that indicate non-academic references
_NON_ACADEMIC_INDICATORS = [
    # Media / news
    r"未來商務", r"環境資訊中心", r"商業周刊", r"天下雜誌", r"聯合新聞網", r"自由時報",
    r"新聞", r"報導", r"搜尋日期", r"Accessed",
    r"Forbes", r"BBC", r"CNN", r"Reuters", r"Bloomberg", r"The Guardian",
    r"New York Times", r"Washington Post",
    # Government / institutional
    r"環境部", r"行政院", r"Ministry of", r"Administration",
    r"Government", r"Agency", r"Bureau", r"Department of",
    r"Executive Yuan", r"Legislative Yuan",
    # Think tanks / organizations
    r"World Bank", r"OECD", r"United Nations", r"IPCC", r"WHO",
    r"交易所", r"Exchange",
    # Websites / platforms
    r"https?://(?!doi\.org)",
]

_NON_ACADEMIC_PATTERN = re.compile(
    "|".join(_NON_ACADEMIC_INDICATORS),
    re.IGNORECASE,
)

# Strong indicators of academic references
_ACADEMIC_INDICATORS = [
    r"Journal\b", r"Review\b", r"Quarterly\b", r"Proceedings\b",
    r"Transactions\b", r"Letters\b", r"Annals\b", r"Research\b",
    r"Economics\b", r"Science\b", r"Psychology\b", r"Sociology\b",
    r"Vol\.", r"pp\.", r"doi:", r"10\.\d{4,}",
    # Volume/issue pattern: "30（3）" or "30(3)" or "vol. 3"
    r"\d+\s*[（(]\d+[）)]",
    r"Springer", r"Elsevier", r"Wiley", r"Cambridge University Press",
    r"Oxford University Press", r"Routledge", r"SAGE", r"Academic Press",
]

_ACADEMIC_PATTERN = re.compile(
    "|".join(_ACADEMIC_INDICATORS),
    re.IGNORECASE,
)


def classify_reference(ref: dict) -> str:
    """Classify a reference as 'academic' or 'non_academic'.

    Uses heuristics based on the raw text content.
    """
    raw = ref.get("raw", "")
    title = ref.get("title", "")

    # If it has a DOI, it's almost certainly academic
    if ref.get("doi"):
        return "academic"

    has_academic = bool(_ACADEMIC_PATTERN.search(raw))
    has_non_academic = bool(_NON_ACADEMIC_PATTERN.search(raw))

    # If it has both indicators, check the primary content (before URLs)
    # to decide — academic journal/volume patterns are strong signals
    if has_academic:
        return "academic"

    if has_non_academic:
        # Check if the non-academic signal is only in a trailing URL/accessed section
        # that might belong to a merged next reference
        first_url_pos = len(raw)
        for m in re.finditer(r"https?://", raw):
            first_url_pos = m.start()
            break
        primary = raw[:first_url_pos]
        # Re-check: does the primary part (before URLs) still have non-academic signals?
        if _NON_ACADEMIC_PATTERN.search(primary):
            return "non_academic"
        # Also check if primary part has academic signals
        if _ACADEMIC_PATTERN.search(primary):
            return "academic"
        return "non_academic"

    # Default to academic
    return "academic"


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

def _extract_book_title(raw: str) -> str | None:
    """Extract a book title from a book chapter reference.

    Looks for patterns like:
    - "in Book Title" (APA/ASA style)
    - "In: Book Title" (various styles)
    - "In Book Title, edited by ..."
    """
    # "in" or "In" followed by the book title
    m = re.search(
        r"\b[Ii]n[:：]?\s+(.+?)(?:\.\s|,\s*(?:edited|ed\.|pp\.|Vol\.)|\s*[（(]\s*pp\.)",
        raw,
    )
    if m:
        book = m.group(1).strip().rstrip(".")
        if len(book) > 10:
            return book
    return None


def _apply_match(result: dict, ref: dict, match: dict) -> dict:
    """Apply a match result to the output, running mismatch analysis."""
    analysis = _analyze_match(ref, match)
    result["verdict"] = analysis["verdict"]
    result["mismatches"] = analysis["mismatches"]
    result["verified_title"] = match.get("title", "")
    result["verified_authors"] = match.get("authors", [])
    result["verified_year"] = match.get("year")
    if match.get("url"):
        result["verified_url"] = match["url"]
    return result


def check_reference(ref: dict, step_callback=None) -> dict:
    """Check a single reference using multiple sources.

    Routes non-academic references (media, government, think tank) through
    Google web search first, and academic references through CrossRef /
    Google Scholar.

    step_callback: optional callable(source_name, status) called before/after
    each verification step. status is 'trying' or 'found' or 'not_found'.

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

    ref_type = classify_reference(ref)

    result = {
        "raw": raw,
        "title": title,
        "authors": authors,
        "year": year,
        "doi": doi,
        "ref_type": ref_type,
        "checks": [],
        "mismatches": [],
        "verdict": "fabricated",
    }

    def _notify(source, status):
        if step_callback:
            step_callback(source, status)

    # 1. If DOI is present, verify it first (always academic)
    if doi:
        _notify("crossref_doi", "trying")
        doi_result = verify_doi(doi)
        result["checks"].append(doi_result)
        _notify("crossref_doi", "found" if doi_result["found"] else "not_found")
        if doi_result["found"]:
            return _apply_match(result, ref, doi_result)

    # 2. Route based on reference type
    if ref_type == "non_academic":
        # Non-academic: try Google web search first
        _notify("google", "trying")
        google_result = search_google(title, authors, raw)
        result["checks"].append(google_result)
        _notify("google", "found" if google_result["found"] else "not_found")
        if google_result["found"]:
            return _apply_match(result, ref, google_result)

        # Fallback: also try CrossRef (some reports have DOIs)
        _notify("crossref", "trying")
        cr_result = search_crossref(title, authors)
        result["checks"].append(cr_result)
        _notify("crossref", "found" if cr_result["found"] else "not_found")
        if cr_result["found"]:
            return _apply_match(result, ref, cr_result)
    else:
        # Academic: Google Scholar first (better coverage), then CrossRef
        _notify("google_scholar", "trying")
        gs_result = search_google_scholar(title, authors)
        result["checks"].append(gs_result)
        _notify("google_scholar", "found" if gs_result["found"] else "not_found")
        if gs_result["found"]:
            return _apply_match(result, ref, gs_result)

        step_delay = 1.0 if not SERPAPI_KEY else 0.3

        # Try Apify Google Scholar scraper if available
        if APIFY_KEY:
            time.sleep(step_delay)
            _notify("apify_google_scholar", "trying")
            apify_result = _apify_google_scholar(title, authors)
            result["checks"].append(apify_result)
            _notify("apify_google_scholar", "found" if apify_result["found"] else "not_found")
            if apify_result["found"]:
                return _apply_match(result, ref, apify_result)

        time.sleep(step_delay)
        _notify("crossref", "trying")
        cr_result = search_crossref(title, authors)
        result["checks"].append(cr_result)
        _notify("crossref", "found" if cr_result["found"] else "not_found")
        if cr_result["found"]:
            return _apply_match(result, ref, cr_result)

        # For book chapters ("in Book Title"), also try searching the book title
        book_title = _extract_book_title(raw)
        if book_title:
            time.sleep(step_delay)
            _notify("google_scholar_book", "trying")
            book_result = search_google_scholar(book_title, authors)
            book_result["source"] = "google_scholar_book"
            result["checks"].append(book_result)
            _notify("google_scholar_book", "found" if book_result["found"] else "not_found")
            if book_result["found"]:
                return _apply_match(result, ref, book_result)

        # Last resort: Google web search
        time.sleep(step_delay)
        _notify("google", "trying")
        google_result = search_google(title, authors, raw)
        result["checks"].append(google_result)
        _notify("google", "found" if google_result["found"] else "not_found")
        if google_result["found"]:
            return _apply_match(result, ref, google_result)

    # Nothing found in any source
    result["verdict"] = "fabricated"
    return result


def check_references(refs: list[dict], progress_callback=None,
                     step_callback=None) -> list[dict]:
    """Check a list of references. Returns results for each.

    progress_callback: called(index, total, result) after each ref is done.
    step_callback: called(ref_index, source, status) for each verification step.
    """
    # Use longer delay when scraping (no API key) to avoid rate limits
    delay = 1.5 if not SERPAPI_KEY else 0.3
    results = []
    for i, ref in enumerate(refs):
        ref_step_cb = None
        if step_callback:
            ref_step_cb = lambda source, status, idx=i: step_callback(idx, source, status)
        result = check_reference(ref, step_callback=ref_step_cb)
        results.append(result)
        if progress_callback:
            progress_callback(i + 1, len(refs), result)
        if i < len(refs) - 1:
            time.sleep(delay)
    return results
