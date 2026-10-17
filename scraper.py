#!/usr/bin/env python3
"""
scraper.py — Multi-site literature scraper for systematic novelty search
Supports: Google Scholar, IEEE Xplore, ScienceDirect, Springer Nature,
          ACM Digital Library, AIAA, IFAC-PapersOnLine

Usage:
    python scraper.py \
        --scholar "bifurcation latent dynamics actuator fault" \
        --ieee "bifurcation actuator fault tolerant control" \
        --sciencedirect "latent dynamics bifurcation fault diagnosis" \
        --springer "bifurcation latent representation control" \
        --acm "latent dynamics fault diagnosis" \
        --aiaa "actuator fault bifurcation control" \
        --ifac "incipient actuator fault nonlinear control" \
        --limit 50 \
        --output results
"""

from __future__ import annotations

# ── stdlib ──────────────────────────────────────────────────────────────────
import argparse
import dataclasses
import hashlib
import io
import json
import logging
import os
import re
import sys
import time
import unicodedata
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# ── third-party ──────────────────────────────────────────────────────────────
try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("Missing deps: pip install requests beautifulsoup4 lxml")

try:
    import fitz  # PyMuPDF
    PYMUPDF_OK = True
except ImportError:
    PYMUPDF_OK = False
    logging.warning("PyMuPDF not found — PDF extraction disabled")

try:
    from thefuzz import fuzz as _fuzz
    FUZZY_OK = True
except ImportError:
    FUZZY_OK = False

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ────────────────────────────────────────────────────────────────────────────
# Logging
# ────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("scraper")

# ────────────────────────────────────────────────────────────────────────────
# Data model
# ────────────────────────────────────────────────────────────────────────────
@dataclass
class PaperRecord:
    """Canonical representation of a retrieved publication."""
    title: str = ""
    authors: list[str] = field(default_factory=list)
    year: Optional[str] = None
    venue: Optional[str] = None
    publisher: Optional[str] = None
    doi: Optional[str] = None
    doi_source: Optional[str] = None          # "site" | "api" | "crossref"
    abstract: Optional[str] = None
    abstract_source: Optional[str] = None     # "api"|"site"|"publisher_page"|"pdf"|"crossref"|"unavailable"
    abstract_status: str = "unavailable"      # "ok" | "unavailable"
    search_snippet: Optional[str] = None
    paper_url: Optional[str] = None
    pdf_url: Optional[str] = None
    source_sites: list[str] = field(default_factory=list)
    source_queries: list[str] = field(default_factory=list)
    source_urls: list[str] = field(default_factory=list)
    source_site: str = ""                     # primary site of first retrieval
    relevant_equations: list[dict] = field(default_factory=list)
    relevant_passages: list[dict] = field(default_factory=list)
    relevant_conclusions: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    # internal dedup key set after normalization
    _norm_title: str = field(default="", repr=False, compare=False)
    _norm_doi: str = field(default="", repr=False, compare=False)

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        for k in ("_norm_title", "_norm_doi"):
            d.pop(k, None)
        return d


# ────────────────────────────────────────────────────────────────────────────
# Shared HTTP session helpers
# ────────────────────────────────────────────────────────────────────────────
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

def make_session(extra_headers: dict | None = None) -> requests.Session:
    s = requests.Session()
    s.headers.update(DEFAULT_HEADERS)
    if extra_headers:
        s.headers.update(extra_headers)
    return s

def safe_get(session: requests.Session, url: str, *, timeout: int = 20,
             retries: int = 3, backoff: float = 2.0, **kwargs) -> Optional[requests.Response]:
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=timeout, **kwargs)
            if r.status_code == 200:
                return r
            if r.status_code in (403, 429, 503):
                wait = backoff * (2 ** attempt)
                log.warning("HTTP %d from %s — waiting %.1fs", r.status_code, url, wait)
                time.sleep(wait)
            else:
                log.warning("HTTP %d from %s", r.status_code, url)
                return None
        except requests.RequestException as exc:
            wait = backoff * (2 ** attempt)
            log.warning("Request error (%s) — retrying in %.1fs", exc, wait)
            time.sleep(wait)
    return None

def safe_get_json(session: requests.Session, url: str, **kwargs) -> Optional[dict]:
    r = safe_get(session, url, **kwargs)
    if r is None:
        return None
    try:
        return r.json()
    except Exception:
        return None


# ────────────────────────────────────────────────────────────────────────────
# Normalisation helpers
# ────────────────────────────────────────────────────────────────────────────
_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE    = re.compile(r"\s+")

def norm_title(t: str) -> str:
    t = unicodedata.normalize("NFKD", t)
    t = t.encode("ascii", "ignore").decode()
    t = t.lower()
    t = _PUNCT_RE.sub(" ", t)
    t = _WS_RE.sub(" ", t).strip()
    return t

def norm_doi(doi: str) -> str:
    doi = doi.lower().strip()
    for prefix in ("https://doi.org/", "http://doi.org/",
                   "https://dx.doi.org/", "http://dx.doi.org/", "doi:"):
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
    doi = doi.strip().rstrip(".,;")
    return doi

def extract_doi_from_text(text: str) -> Optional[str]:
    m = re.search(r'\b(10\.\d{4,9}/[^\s"<>]+)', text)
    if m:
        return norm_doi(m.group(1))
    return None

def extract_doi_from_url(url: str) -> Optional[str]:
    m = re.search(r'(?:doi\.org/|/doi/)(10\.\d{4,9}/[^\s"<>?&]+)', url)
    if m:
        return norm_doi(m.group(1))
    return None


# ────────────────────────────────────────────────────────────────────────────
# Abstract extraction from publisher pages
# ────────────────────────────────────────────────────────────────────────────
def extract_abstract_from_html(soup: BeautifulSoup) -> Optional[str]:
    """Generic structured extraction: JSON-LD → meta tags → common HTML patterns."""
    # 1. JSON-LD
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, list):
                data = data[0]
            for key in ("description", "abstract"):
                val = data.get(key)
                if val and len(val) > 80:
                    return val.strip()
        except Exception:
            pass

    # 2. Open Graph / Dublin Core / citation meta
    for name in ("description", "og:description", "dc.description",
                 "citation_abstract", "abstract", "DC.Description"):
        tag = soup.find("meta", attrs={"name": name}) or \
              soup.find("meta", attrs={"property": name})
        if tag:
            val = tag.get("content", "")
            if val and len(val) > 80:
                return val.strip()

    # 3. Common HTML structures
    candidates = []
    # class/id patterns
    for pattern in [
        {"class_": re.compile(r"abstract", re.I)},
        {"id": re.compile(r"abstract", re.I)},
        {"itemprop": "description"},
    ]:
        for tag in soup.find_all(True, **pattern):
            txt = tag.get_text(" ", strip=True)
            if len(txt) > 100:
                candidates.append(txt)
    if candidates:
        return max(candidates, key=len)

    # 4. Section with "Abstract" heading
    for h in soup.find_all(re.compile(r"^h[1-6]$")):
        if re.search(r"\babstract\b", h.get_text(), re.I):
            parts = []
            for sib in h.next_siblings:
                if hasattr(sib, "get_text"):
                    if re.match(r"^h[1-6]$", getattr(sib, "name", ""), re.I):
                        break
                    parts.append(sib.get_text(" ", strip=True))
            txt = " ".join(parts).strip()
            if len(txt) > 80:
                return txt

    return None


# ────────────────────────────────────────────────────────────────────────────
# PDF extraction
# ────────────────────────────────────────────────────────────────────────────
RELEVANCE_KEYWORDS = [
    "latent representation", "latent dynamics", "bifurcation", "early warning",
    "actuator fault", "actuator degradation", "actuator effectiveness",
    "fault diagnosis", "fault detection", "fault tolerant control",
    "reconfigurable control", "control allocation", "nonlinear control",
    "neural dynamics", "dynamical systems", "stability", "attractor",
    "manifold", "critical transition", "machine learning",
    "graph neural network", "neural ode", "representation learning",
    "eigenvalue", "jacobian", "floquet", "lyapunov", "state-space",
    "state space",
]

CONCLUSION_KEYWORDS = [
    r"\bconclusion\b", r"\bconclusions\b", r"\bdiscussion\b",
    r"\bmain contribution\b", r"\bcontribution\b", r"\bproposed method\b",
    r"\blimitation\b", r"\bfuture work\b", r"\bdemonstrated\b",
    r"\bshowed\b", r"\bfound that\b",
]

EQ_CONTEXT_KEYWORDS = [
    "state", "control", "fault", "actuator", "bifurcation", "lyapunov",
    "jacobian", "floquet", "eigenvalue", "stability", "neural", "latent",
    "dynamics", "transition", "attractor", "effectiveness", "degradation",
]

def _extract_abstract_from_pdf_text(full_text: str) -> Optional[str]:
    """Find abstract block in raw PDF text."""
    patterns = [
        # Abstract followed by Keywords / Introduction / numbered section
        r"[Aa][Bb][Ss][Tt][Rr][Aa][Cc][Tt]\s*[:\-]?\s*\n+([\s\S]+?)(?=\n\s*(?:[Kk]eywords?|[Ii]ndex [Tt]erms?|[Ii]ntroduction|\d+[\.\s]|[IVX]+[\.\s]))",
        # Abstract as standalone heading followed by paragraphs, terminated by blank line(s) then new heading
        r"[Aa][Bb][Ss][Tt][Rr][Aa][Cc][Tt]\s*\n+([\s\S]+?)(?=\n{2,}\s*[A-Z][A-Z\s]{3,}\n)",
        # Abstract: <text> on same or next lines, up to 3000 chars
        r"[Aa][Bb][Ss][Tt][Rr][Aa][Cc][Tt]\s*[:\-]?\s*((?:[^\n].+\n?){2,30})",
    ]
    for p in patterns:
        m = re.search(p, full_text, re.MULTILINE)
        if m:
            ab = m.group(1).strip()
            ab = re.sub(r"\s+", " ", ab)
            # Must be at least 80 chars, at most 4000
            if 80 <= len(ab) <= 4000:
                return ab
    return None

def _extract_equations_from_page(page_text: str, page_num: int) -> list[dict]:
    """Heuristic equation extraction: look for lines with math-like content."""
    results = []
    lines = page_text.split("\n")
    for i, line in enumerate(lines):
        stripped = line.strip()
        # Heuristic: contains =, Greek-like tokens, brackets with numbers
        if not stripped:
            continue
        score = 0
        if "=" in stripped:
            score += 1
        if re.search(r"[α-ωΑ-Ω]|\\[a-zA-Z]+|\b(?:dot|hat|bar|tilde)\b", stripped):
            score += 2
        if re.search(r"\([^)]{1,30}\)", stripped):
            score += 1
        if re.search(r"\d+\s*[×·\*]\s*\d+|\b\d+\.\d+\b", stripped):
            score += 1
        if len(stripped) < 8:
            score -= 1
        if score < 2:
            continue
        # Grab surrounding context
        ctx_start = max(0, i - 2)
        ctx_end   = min(len(lines), i + 3)
        context   = " ".join(lines[ctx_start:ctx_end]).strip()
        # Check relevance of context
        ctx_lower = context.lower()
        relevant  = any(k in ctx_lower for k in EQ_CONTEXT_KEYWORDS)
        if relevant:
            results.append({
                "equation": stripped,
                "context": context,
                "page": page_num,
            })
    return results

def _extract_relevant_passages(page_text: str, page_num: int) -> list[dict]:
    results = []
    paragraphs = re.split(r"\n{2,}", page_text)
    for para in paragraphs:
        para_clean = para.strip()
        if len(para_clean) < 60:
            continue
        lower = para_clean.lower()
        hits = [kw for kw in RELEVANCE_KEYWORDS if kw in lower]
        if hits:
            results.append({
                "page": page_num,
                "keywords": hits,
                "text": re.sub(r"\s+", " ", para_clean)[:1000],
            })
    return results

def _extract_conclusions(page_text: str, page_num: int) -> list[dict]:
    results = []
    paragraphs = re.split(r"\n{2,}", page_text)
    in_conclusion = False
    for para in paragraphs:
        para_clean = para.strip()
        lower = para_clean.lower()
        # Check if we enter a conclusion section
        if re.search(r"^\s*(?:\d+\.?\s+)?(?:conclusion|conclusions|discussion)", lower):
            in_conclusion = True
        if in_conclusion and len(para_clean) > 60:
            results.append({
                "page": page_num,
                "text": re.sub(r"\s+", " ", para_clean)[:1500],
            })
            if len(results) >= 6:
                break
        # Also grab any paragraph mentioning conclusion keywords inline
        elif not in_conclusion and any(re.search(p, lower) for p in CONCLUSION_KEYWORDS):
            results.append({
                "page": page_num,
                "text": re.sub(r"\s+", " ", para_clean)[:1000],
            })
    return results

def extract_from_pdf_bytes(pdf_bytes: bytes, paper: PaperRecord) -> None:
    """Run all PDF extractions and update paper in-place."""
    if not PYMUPDF_OK:
        paper.errors.append("PDF extraction skipped: PyMuPDF not installed")
        return
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        paper.errors.append(f"PDF open error: {exc}")
        return

    full_pages: list[tuple[int, str]] = []
    for i, page in enumerate(doc, start=1):
        try:
            full_pages.append((i, page.get_text("text")))
        except Exception:
            pass

    full_text = "\n".join(t for _, t in full_pages)

    # Abstract (only if not already obtained)
    if paper.abstract is None:
        ab = _extract_abstract_from_pdf_text(full_text)
        if ab:
            paper.abstract = ab
            paper.abstract_source = "pdf"
            paper.abstract_status = "ok"

    # Equations, passages, conclusions
    for page_num, page_text in full_pages:
        if not paper.relevant_equations:
            paper.relevant_equations.extend(
                _extract_equations_from_page(page_text, page_num)
            )
        paper.relevant_passages.extend(
            _extract_relevant_passages(page_text, page_num)
        )
        paper.relevant_conclusions.extend(
            _extract_conclusions(page_text, page_num)
        )

    # Deduplicate passages (by text prefix)
    seen: set[str] = set()
    deduped = []
    for p in paper.relevant_passages:
        key = p["text"][:80]
        if key not in seen:
            seen.add(key)
            deduped.append(p)
    paper.relevant_passages = deduped[:20]

    # Top equations (limit)
    paper.relevant_equations = paper.relevant_equations[:15]
    # Conclusions (limit)
    paper.relevant_conclusions = paper.relevant_conclusions[:8]

def download_and_extract_pdf(session: requests.Session, url: str,
                              paper: PaperRecord) -> None:
    """Download PDF and run extraction; skip if abstract already known."""
    log.info("Downloading PDF: %s", url)
    r = safe_get(session, url, timeout=30)
    if r is None or b"%PDF" not in r.content[:8]:
        paper.errors.append(f"PDF download failed or not a PDF: {url}")
        return
    extract_from_pdf_bytes(r.content, paper)


# ────────────────────────────────────────────────────────────────────────────
# Crossref DOI / abstract enrichment
# ────────────────────────────────────────────────────────────────────────────
_CROSSREF_URL = "https://api.crossref.org/works"
_CROSSREF_MAILTO = os.getenv("CROSSREF_MAILTO", "researcher@university.edu")

def crossref_lookup_by_doi(doi: str, session: requests.Session) -> Optional[dict]:
    url = f"{_CROSSREF_URL}/{urllib.parse.quote(doi)}"
    data = safe_get_json(session, url,
                         headers={"User-Agent": f"scraper/1.0 (mailto:{_CROSSREF_MAILTO})"},
                         timeout=15)
    if data and data.get("status") == "ok":
        return data.get("message", {})
    return None

def crossref_search(title: str, authors: list[str], year: Optional[str],
                    session: requests.Session) -> Optional[dict]:
    """Fuzzy bibliographic search via Crossref; returns work dict only if confident."""
    params: dict[str, Any] = {
        "query.title": title,
        "rows": 3,
        "select": "DOI,title,author,published,abstract",
        "mailto": _CROSSREF_MAILTO,
    }
    if authors:
        params["query.author"] = " ".join(authors[:2])
    r = safe_get_json(session, _CROSSREF_URL, params=params, timeout=15,
                      headers={"User-Agent": f"scraper/1.0 (mailto:{_CROSSREF_MAILTO})"})
    if not r or r.get("status") != "ok":
        return None
    items = r.get("message", {}).get("items", [])
    for item in items:
        item_titles = item.get("title", [])
        if not item_titles:
            continue
        item_title = item_titles[0]
        # Conservative match
        if FUZZY_OK:
            score = _fuzz.token_sort_ratio(norm_title(title), norm_title(item_title))
        else:
            score = 100 if norm_title(title) == norm_title(item_title) else 0
        if score < 90:
            continue
        # Year check
        item_year = str(item.get("published", {}).get("date-parts", [[None]])[0][0] or "")
        if year and item_year and abs(int(year) - int(item_year)) > 1:
            continue
        return item
    return None

def enrich_from_crossref(paper: PaperRecord, session: requests.Session) -> None:
    """Try to fill in missing DOI/abstract via Crossref."""
    work = None
    if paper.doi:
        work = crossref_lookup_by_doi(paper.doi, session)
    if work is None and paper.title:
        work = crossref_search(paper.title, paper.authors, paper.year, session)
    if work is None:
        return

    if not paper.doi and work.get("DOI"):
        paper.doi = norm_doi(work["DOI"])
        paper.doi_source = "crossref"
        log.info("DOI enriched via Crossref: %s", paper.doi)

    if paper.abstract is None and work.get("abstract"):
        ab = re.sub(r"<[^>]+>", "", work["abstract"]).strip()
        if len(ab) > 80:
            paper.abstract = ab
            paper.abstract_source = "crossref"
            paper.abstract_status = "ok"
            log.info("Abstract enriched via Crossref")

    time.sleep(1.0)


# ────────────────────────────────────────────────────────────────────────────
# Base adapter
# ────────────────────────────────────────────────────────────────────────────
class BaseAdapter:
    site_key: str = ""
    display_name: str = ""
    rate_limit: float = 2.0  # seconds between requests

    def __init__(self):
        self.session = make_session()
        self._last_request = 0.0
        self.stats: dict[str, Any] = {
            "queries": 0,
            "raw_results": 0,
            "errors": [],
        }

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_request
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        self._last_request = time.time()

    def search(self, query: str, limit: int) -> list[PaperRecord]:
        raise NotImplementedError

    def _get_publisher_abstract(self, paper: PaperRecord) -> None:
        """Try to fetch and parse the publisher page for the abstract."""
        if paper.abstract or not paper.paper_url:
            return
        self._throttle()
        r = safe_get(self.session, paper.paper_url)
        if r is None:
            return
        soup = BeautifulSoup(r.text, "lxml")
        ab = extract_abstract_from_html(soup)
        if ab:
            paper.abstract = ab
            paper.abstract_source = "publisher_page"
            paper.abstract_status = "ok"


# ────────────────────────────────────────────────────────────────────────────
# Google Scholar adapter
# ────────────────────────────────────────────────────────────────────────────
class ScholarAdapter(BaseAdapter):
    site_key = "google_scholar"
    display_name = "Google Scholar"
    rate_limit = 5.0

    _BASE = "https://scholar.google.com/scholar"

    def search(self, query: str, limit: int) -> list[PaperRecord]:
        results: list[PaperRecord] = []
        start = 0
        per_page = min(10, limit)

        while len(results) < limit:
            self._throttle()
            params = {
                "q": query,
                "start": start,
                "num": per_page,
                "hl": "en",
            }
            r = safe_get(self.session, self._BASE, params=params)
            if r is None:
                self.stats["errors"].append(f"Scholar request failed: {query}")
                break

            if "captcha" in r.text.lower() or "unusual traffic" in r.text.lower():
                self.stats["errors"].append(
                    "Google Scholar: CAPTCHA encountered — stopping Scholar retrieval"
                )
                log.warning("Google Scholar CAPTCHA — stopping Scholar queries")
                break

            soup = BeautifulSoup(r.text, "lxml")
            items = soup.select(".gs_r.gs_or.gs_scl")
            if not items:
                break

            for item in items:
                if len(results) >= limit:
                    break
                paper = self._parse_item(item, query)
                if paper:
                    results.append(paper)
                    self.stats["raw_results"] += 1

            if len(items) < per_page:
                break
            start += per_page
            if start >= 100:  # Scholar blocks beyond page 10
                break

        # Fetch publisher pages for missing abstracts (best-effort)
        for paper in results:
            if paper.abstract is None and paper.paper_url:
                self._get_publisher_abstract(paper)

        return results

    def _parse_item(self, item, query: str) -> Optional[PaperRecord]:
        paper = PaperRecord(source_site=self.site_key,
                            source_sites=[self.site_key],
                            source_queries=[query])

        # Title & URL
        title_tag = item.select_one(".gs_rt a")
        if title_tag:
            paper.title = title_tag.get_text(strip=True)
            paper.paper_url = title_tag.get("href", "")
        else:
            title_tag = item.select_one(".gs_rt")
            if title_tag:
                paper.title = title_tag.get_text(strip=True)
        if not paper.title:
            return None

        # Try DOI from URL
        if paper.paper_url:
            paper.doi = extract_doi_from_url(paper.paper_url)
            if paper.doi:
                paper.doi_source = "site"
            paper.source_urls.append(paper.paper_url)

        # Authors / year / venue
        meta = item.select_one(".gs_a")
        if meta:
            meta_text = meta.get_text(" ", strip=True)
            year_m = re.search(r"\b(19|20)\d{2}\b", meta_text)
            if year_m:
                paper.year = year_m.group(0)
            parts = meta_text.split("-")
            if parts:
                raw_authors = parts[0].strip()
                paper.authors = [a.strip() for a in raw_authors.split(",") if a.strip()]
            if len(parts) >= 2:
                paper.venue = parts[-1].strip()

        # Snippet stored separately — NEVER as abstract
        snippet_tag = item.select_one(".gs_rs")
        if snippet_tag:
            paper.search_snippet = snippet_tag.get_text(" ", strip=True)

        # PDF link
        pdf_tag = item.select_one(".gs_or_ggsm a")
        if pdf_tag and ".pdf" in pdf_tag.get("href", "").lower():
            paper.pdf_url = pdf_tag.get("href")

        return paper


# ────────────────────────────────────────────────────────────────────────────
# IEEE Xplore adapter  (official Metadata API)
# ────────────────────────────────────────────────────────────────────────────
class IEEEAdapter(BaseAdapter):
    site_key = "ieee"
    display_name = "IEEE Xplore"
    rate_limit = 2.0

    _API_BASE = "https://ieeexploreapi.ieee.org/api/v1/search/articles"
    _WEB_BASE = "https://ieeexplore.ieee.org/search/searchresult.jsp"

    def __init__(self):
        super().__init__()
        self.api_key = os.getenv("IEEE_XPLORE_API_KEY", "")
        if not self.api_key:
            log.warning("IEEE_XPLORE_API_KEY not set — falling back to web scrape")

    def search(self, query: str, limit: int) -> list[PaperRecord]:
        if self.api_key:
            return self._search_api(query, limit)
        return self._search_web(query, limit)

    def _search_api(self, query: str, limit: int) -> list[PaperRecord]:
        results: list[PaperRecord] = []
        start = 1
        per_page = min(25, limit)

        while len(results) < limit:
            self._throttle()
            params = {
                "apikey": self.api_key,
                "querytext": query,
                "max_records": per_page,
                "start_record": start,
                "datatype": "json",
            }
            data = safe_get_json(self.session, self._API_BASE, params=params)
            if not data:
                self.stats["errors"].append("IEEE API request failed")
                break
            articles = data.get("articles", [])
            if not articles:
                break
            for art in articles:
                if len(results) >= limit:
                    break
                paper = self._parse_api_article(art, query)
                if paper:
                    results.append(paper)
                    self.stats["raw_results"] += 1
            if len(articles) < per_page:
                break
            start += per_page

        return results

    def _parse_api_article(self, art: dict, query: str) -> Optional[PaperRecord]:
        paper = PaperRecord(source_site=self.site_key,
                            source_sites=[self.site_key],
                            source_queries=[query])
        paper.title = art.get("title", "").strip()
        if not paper.title:
            return None

        raw_authors = art.get("authors", {}).get("authors", [])
        paper.authors = [a.get("full_name", "") for a in raw_authors if a.get("full_name")]

        paper.year = str(art.get("publication_year", "") or "").strip() or None
        paper.venue = art.get("publication_title", "") or None
        paper.publisher = "IEEE"

        doi = art.get("doi", "")
        if doi:
            paper.doi = norm_doi(doi)
            paper.doi_source = "api"

        ab = art.get("abstract", "")
        if ab and len(ab) > 80:
            paper.abstract = ab.strip()
            paper.abstract_source = "api"
            paper.abstract_status = "ok"

        url = art.get("html_url", "") or art.get("pdf_url", "")
        if url:
            paper.paper_url = url
            paper.source_urls.append(url)
        paper.pdf_url = art.get("pdf_url", "") or None

        return paper

    def _search_web(self, query: str, limit: int) -> list[PaperRecord]:
        """Fallback: scrape IEEE Xplore search results page."""
        results: list[PaperRecord] = []
        page = 1
        while len(results) < limit:
            self._throttle()
            params = {
                "queryText": query,
                "newsearch": "true",
                "pageNumber": page,
                "rowsPerPage": 25,
            }
            r = safe_get(self.session, "https://ieeexplore.ieee.org/rest/search",
                         params=params,
                         headers={"Accept": "application/json",
                                  "Referer": "https://ieeexplore.ieee.org/"})
            if r is None:
                self.stats["errors"].append("IEEE web search failed — no API key configured")
                break
            try:
                data = r.json()
            except Exception:
                self.stats["errors"].append("IEEE web search: JSON parse error")
                break
            articles = data.get("records", [])
            if not articles:
                break
            for art in articles:
                if len(results) >= limit:
                    break
                paper = self._parse_web_record(art, query)
                if paper:
                    results.append(paper)
                    self.stats["raw_results"] += 1
            total = data.get("totalRecords", 0)
            if len(results) >= total or len(articles) == 0:
                break
            page += 1
        return results

    def _parse_web_record(self, rec: dict, query: str) -> Optional[PaperRecord]:
        paper = PaperRecord(source_site=self.site_key,
                            source_sites=[self.site_key],
                            source_queries=[query])
        paper.title = rec.get("title", "").strip()
        if not paper.title:
            return None
        paper.year = str(rec.get("publicationYear", "") or "").strip() or None
        paper.venue = rec.get("publicationTitle", "") or None
        paper.publisher = "IEEE"

        authors_raw = rec.get("authors", [])
        if isinstance(authors_raw, list):
            paper.authors = [a.get("preferredName", "") or a.get("fullName", "")
                             for a in authors_raw]

        doi = rec.get("doi", "")
        if doi:
            paper.doi = norm_doi(doi)
            paper.doi_source = "site"

        art_num = rec.get("articleNumber", "")
        if art_num:
            paper.paper_url = f"https://ieeexplore.ieee.org/document/{art_num}"
            paper.source_urls.append(paper.paper_url)

        ab = rec.get("abstract", "")
        if ab and len(ab) > 80:
            paper.abstract = ab.strip()
            paper.abstract_source = "site"
            paper.abstract_status = "ok"
        else:
            # Try fetching the article page
            self._get_publisher_abstract(paper)

        return paper


# ────────────────────────────────────────────────────────────────────────────
# ScienceDirect / Elsevier adapter  (official Search API v3)
# ────────────────────────────────────────────────────────────────────────────
class ScienceDirectAdapter(BaseAdapter):
    site_key = "sciencedirect"
    display_name = "ScienceDirect"
    rate_limit = 2.0

    _SEARCH_URL = "https://api.elsevier.com/content/search/sciencedirect"
    _ABSTRACT_URL = "https://api.elsevier.com/content/abstract/doi"

    def __init__(self):
        super().__init__()
        self.api_key = os.getenv("ELSEVIER_API_KEY", "")
        if not self.api_key:
            log.warning("ELSEVIER_API_KEY not set — ScienceDirect adapter will be limited")

    def search(self, query: str, limit: int) -> list[PaperRecord]:
        if not self.api_key:
            self.stats["errors"].append(
                "ScienceDirect: ELSEVIER_API_KEY not configured — skipping"
            )
            return []
        return self._search_api(query, limit)

    def _search_api(self, query: str, limit: int) -> list[PaperRecord]:
        results: list[PaperRecord] = []
        offset = 0
        per_page = min(25, limit)
        headers = {
            "X-ELS-APIKey": self.api_key,
            "Accept": "application/json",
        }

        while len(results) < limit:
            self._throttle()
            params = {
                "query": query,
                "count": per_page,
                "start": offset,
                "field": "dc:title,dc:creator,prism:publicationName,prism:doi,"
                         "dc:description,prism:url,prism:coverDate,openaccess",
            }
            r = safe_get(self.session, self._SEARCH_URL, params=params,
                         headers=headers)
            if r is None:
                self.stats["errors"].append("ScienceDirect API request failed")
                break
            try:
                data = r.json()
            except Exception:
                self.stats["errors"].append("ScienceDirect: JSON parse error")
                break
            entries = (data.get("search-results", {})
                           .get("entry", []))
            if not entries:
                break
            for entry in entries:
                if len(results) >= limit:
                    break
                paper = self._parse_entry(entry, query)
                if paper:
                    results.append(paper)
                    self.stats["raw_results"] += 1
            if len(entries) < per_page:
                break
            offset += per_page

        # Enrich abstracts where missing
        for paper in results:
            if paper.abstract is None and paper.doi:
                self._fetch_abstract_by_doi(paper, headers)

        return results

    def _parse_entry(self, entry: dict, query: str) -> Optional[PaperRecord]:
        paper = PaperRecord(source_site=self.site_key,
                            source_sites=[self.site_key],
                            source_queries=[query])
        paper.title = (entry.get("dc:title") or "").strip()
        if not paper.title:
            return None

        creator = entry.get("dc:creator") or ""
        paper.authors = [a.strip() for a in creator.split(";") if a.strip()]

        date = entry.get("prism:coverDate", "") or ""
        if date:
            paper.year = date[:4]

        paper.venue = entry.get("prism:publicationName") or None
        paper.publisher = "Elsevier"

        doi = entry.get("prism:doi") or ""
        if doi:
            paper.doi = norm_doi(doi)
            paper.doi_source = "api"

        # dc:description from search API is often a snippet — store as snippet
        desc = (entry.get("dc:description") or "").strip()
        if desc:
            paper.search_snippet = desc

        url = entry.get("prism:url") or entry.get("link", [{}])[0].get("@href", "") \
              if isinstance(entry.get("link"), list) else ""
        if url:
            paper.paper_url = url
            paper.source_urls.append(url)
        elif paper.doi:
            paper.paper_url = f"https://doi.org/{paper.doi}"
            paper.source_urls.append(paper.paper_url)

        return paper

    def _fetch_abstract_by_doi(self, paper: PaperRecord, headers: dict) -> None:
        self._throttle()
        url = f"{self._ABSTRACT_URL}/{urllib.parse.quote(paper.doi)}"
        r = safe_get(self.session, url,
                     headers={**headers, "Accept": "application/json"})
        if r is None:
            return
        try:
            data = r.json()
        except Exception:
            return
        ab = (data.get("abstracts-retrieval-response", {})
                  .get("coredata", {})
                  .get("dc:description") or "")
        if ab and len(ab) > 80:
            paper.abstract = ab.strip()
            paper.abstract_source = "api"
            paper.abstract_status = "ok"
        else:
            # Fall back to publisher page
            self._get_publisher_abstract(paper)


# ────────────────────────────────────────────────────────────────────────────
# Springer Nature adapter  (official Meta API)
# ────────────────────────────────────────────────────────────────────────────
class SpringerAdapter(BaseAdapter):
    site_key = "springer"
    display_name = "Springer Nature Link"
    rate_limit = 2.0

    _API_BASE = "https://api.springernature.com/meta/v2/json"

    def __init__(self):
        super().__init__()
        self.api_key = os.getenv("SPRINGER_API_KEY", "")
        if not self.api_key:
            log.warning("SPRINGER_API_KEY not set — Springer adapter will be limited")

    def search(self, query: str, limit: int) -> list[PaperRecord]:
        if not self.api_key:
            self.stats["errors"].append(
                "Springer: SPRINGER_API_KEY not configured — skipping"
            )
            return []
        return self._search_api(query, limit)

    def _search_api(self, query: str, limit: int) -> list[PaperRecord]:
        results: list[PaperRecord] = []
        start = 1
        per_page = min(25, limit)

        while len(results) < limit:
            self._throttle()
            params = {
                "api_key": self.api_key,
                "q": query,
                "s": start,
                "p": per_page,
            }
            data = safe_get_json(self.session, self._API_BASE, params=params)
            if not data:
                self.stats["errors"].append("Springer API request failed")
                break
            records = data.get("records", [])
            if not records:
                break
            for rec in records:
                if len(results) >= limit:
                    break
                paper = self._parse_record(rec, query)
                if paper:
                    results.append(paper)
                    self.stats["raw_results"] += 1
            if len(records) < per_page:
                break
            start += per_page

        return results

    def _parse_record(self, rec: dict, query: str) -> Optional[PaperRecord]:
        paper = PaperRecord(source_site=self.site_key,
                            source_sites=[self.site_key],
                            source_queries=[query])
        paper.title = (rec.get("title") or "").strip()
        if not paper.title:
            return None

        creators = rec.get("creators", [])
        paper.authors = [c.get("creator", "") for c in creators if c.get("creator")]

        date = rec.get("publicationDate") or rec.get("onlineDate") or ""
        if date:
            paper.year = date[:4]

        paper.venue = rec.get("publicationName") or None
        paper.publisher = rec.get("publisher") or "Springer"

        doi = rec.get("doi") or ""
        if doi:
            paper.doi = norm_doi(doi)
            paper.doi_source = "api"

        ab = (rec.get("abstract") or "").strip()
        if ab and len(ab) > 80:
            paper.abstract = ab
            paper.abstract_source = "api"
            paper.abstract_status = "ok"

        url = rec.get("url")
        if isinstance(url, list) and url:
            url = url[0].get("value", "")
        if url:
            paper.paper_url = str(url)
            paper.source_urls.append(paper.paper_url)
        elif paper.doi:
            paper.paper_url = f"https://link.springer.com/article/{urllib.parse.quote(paper.doi)}"

        return paper


# ────────────────────────────────────────────────────────────────────────────
# ACM Digital Library adapter  (web scrape — no public API)
# ────────────────────────────────────────────────────────────────────────────
class ACMAdapter(BaseAdapter):
    site_key = "acm"
    display_name = "ACM Digital Library"
    rate_limit = 4.0

    _SEARCH_URL = "https://dl.acm.org/action/doSearch"

    def search(self, query: str, limit: int) -> list[PaperRecord]:
        results: list[PaperRecord] = []
        page = 0
        per_page = min(20, limit)

        while len(results) < limit:
            self._throttle()
            params = {
                "query": query,
                "startPage": page,
                "pageSize": per_page,
                "AllField": query,
            }
            r = safe_get(self.session, self._SEARCH_URL, params=params)
            if r is None:
                self.stats["errors"].append(f"ACM search failed: {query}")
                break
            soup = BeautifulSoup(r.text, "lxml")
            items = soup.select("li.search__item")
            if not items:
                # Try alternate selector
                items = soup.select("div.issue-item")
            if not items:
                self.stats["errors"].append(
                    "ACM: no results found (page structure may have changed)"
                )
                break

            for item in items:
                if len(results) >= limit:
                    break
                paper = self._parse_item(item, query)
                if paper:
                    results.append(paper)
                    self.stats["raw_results"] += 1

            if len(items) < per_page:
                break
            page += 1

        # Fetch abstracts
        for paper in results:
            if paper.abstract is None and paper.paper_url:
                self._fetch_acm_abstract(paper)

        return results

    def _parse_item(self, item, query: str) -> Optional[PaperRecord]:
        paper = PaperRecord(source_site=self.site_key,
                            source_sites=[self.site_key],
                            source_queries=[query])

        title_tag = (item.select_one("h5.issue-item__title a") or
                     item.select_one(".hlFld-Title a") or
                     item.select_one("a.issue-item__title"))
        if title_tag:
            paper.title = title_tag.get_text(strip=True)
            href = title_tag.get("href", "")
            if href:
                paper.paper_url = ("https://dl.acm.org" + href
                                   if href.startswith("/") else href)
                paper.source_urls.append(paper.paper_url)
        if not paper.title:
            return None

        # DOI
        if paper.paper_url:
            doi_m = re.search(r"dl\.acm\.org/doi/(10\.\S+)", paper.paper_url)
            if doi_m:
                paper.doi = norm_doi(doi_m.group(1))
                paper.doi_source = "site"

        # Authors
        author_tags = item.select(".loa__author-name") or item.select(".author-name")
        paper.authors = [a.get_text(strip=True) for a in author_tags]

        # Year
        date_tag = item.select_one(".issue-item__detail span") or item.select_one(".epub-section__date")
        if date_tag:
            year_m = re.search(r"\b(19|20)\d{2}\b", date_tag.get_text())
            if year_m:
                paper.year = year_m.group(0)

        paper.publisher = "ACM"

        return paper

    def _fetch_acm_abstract(self, paper: PaperRecord) -> None:
        self._throttle()
        r = safe_get(self.session, paper.paper_url)
        if r is None:
            return
        soup = BeautifulSoup(r.text, "lxml")
        # ACM-specific: div with class "abstractSection"
        ab_div = (soup.select_one("div.abstractSection.abstractInFull") or
                  soup.select_one("div.abstractSection") or
                  soup.select_one("[data-abstract]"))
        if ab_div:
            ab = ab_div.get_text(" ", strip=True)
            # Remove "Abstract" heading if present at start
            ab = re.sub(r"^Abstract\s*", "", ab, flags=re.I).strip()
            if len(ab) > 80:
                paper.abstract = ab
                paper.abstract_source = "publisher_page"
                paper.abstract_status = "ok"
                return
        # Generic fallback
        ab = extract_abstract_from_html(soup)
        if ab:
            paper.abstract = ab
            paper.abstract_source = "publisher_page"
            paper.abstract_status = "ok"


# ────────────────────────────────────────────────────────────────────────────
# AIAA adapter  (web scrape of arc.aiaa.org)
# ────────────────────────────────────────────────────────────────────────────
class AIAAAdapter(BaseAdapter):
    site_key = "aiaa"
    display_name = "AIAA Aerospace Research Central"
    rate_limit = 4.0

    _SEARCH_URL = "https://arc.aiaa.org/action/doSearch"

    def search(self, query: str, limit: int) -> list[PaperRecord]:
        results: list[PaperRecord] = []
        page = 0
        per_page = min(20, limit)

        while len(results) < limit:
            self._throttle()
            params = {
                "AllField": query,
                "startPage": page,
                "pageSize": per_page,
            }
            r = safe_get(self.session, self._SEARCH_URL, params=params)
            if r is None:
                self.stats["errors"].append(f"AIAA search failed: {query}")
                break

            soup = BeautifulSoup(r.text, "lxml")
            items = soup.select("div.issue-item") or soup.select("li.search__item")
            if not items:
                self.stats["errors"].append(
                    "AIAA: no results found (page structure may have changed)"
                )
                break

            for item in items:
                if len(results) >= limit:
                    break
                paper = self._parse_item(item, query)
                if paper:
                    results.append(paper)
                    self.stats["raw_results"] += 1

            if len(items) < per_page:
                break
            page += 1

        for paper in results:
            if paper.abstract is None and paper.paper_url:
                self._get_publisher_abstract(paper)

        return results

    def _parse_item(self, item, query: str) -> Optional[PaperRecord]:
        paper = PaperRecord(source_site=self.site_key,
                            source_sites=[self.site_key],
                            source_queries=[query])

        title_tag = (item.select_one(".hlFld-Title a") or
                     item.select_one("h5.issue-item__title a") or
                     item.select_one("a.issue-item__title"))
        if title_tag:
            paper.title = title_tag.get_text(strip=True)
            href = title_tag.get("href", "")
            if href:
                base = "https://arc.aiaa.org"
                paper.paper_url = base + href if href.startswith("/") else href
                paper.source_urls.append(paper.paper_url)
        if not paper.title:
            return None

        if paper.paper_url:
            doi_m = re.search(r"doi/(10\.\S+)", paper.paper_url)
            if doi_m:
                paper.doi = norm_doi(doi_m.group(1))
                paper.doi_source = "site"

        author_tags = item.select(".loa__author-name") or item.select(".author-name")
        paper.authors = [a.get_text(strip=True) for a in author_tags]

        date_tag = item.select_one(".issue-item__detail")
        if date_tag:
            year_m = re.search(r"\b(19|20)\d{2}\b", date_tag.get_text())
            if year_m:
                paper.year = year_m.group(0)

        paper.publisher = "AIAA"
        return paper


# ────────────────────────────────────────────────────────────────────────────
# IFAC-PapersOnLine adapter  (ScienceDirect-hosted; also scrape ifac.org)
# ────────────────────────────────────────────────────────────────────────────
class IFACAdapter(BaseAdapter):
    """
    IFAC papers are hosted on ScienceDirect (Elsevier) for IFAC-PapersOnLine.
    We query via the ScienceDirect Elsevier API when available, else scrape
    the IFAC website search.
    """
    site_key = "ifac"
    display_name = "IFAC-PapersOnLine"
    rate_limit = 3.0

    _SD_SEARCH_URL = "https://api.elsevier.com/content/search/sciencedirect"
    _IFAC_SEARCH_URL = "https://www.ifac-control.org/publications/advanced-search"
    _SCIDIR_SEARCH_URL = "https://www.sciencedirect.com/search"

    def __init__(self):
        super().__init__()
        self.api_key = os.getenv("ELSEVIER_API_KEY", "")

    def search(self, query: str, limit: int) -> list[PaperRecord]:
        if self.api_key:
            return self._search_via_elsevier_api(query, limit)
        return self._search_web(query, limit)

    def _search_via_elsevier_api(self, query: str, limit: int) -> list[PaperRecord]:
        results: list[PaperRecord] = []
        offset = 0
        per_page = min(25, limit)
        headers = {
            "X-ELS-APIKey": self.api_key,
            "Accept": "application/json",
        }
        while len(results) < limit:
            self._throttle()
            # Restrict to IFAC-PapersOnLine ISSN: 2405-8963
            params = {
                "query": f'ISSN(2405-8963) AND ({query})',
                "count": per_page,
                "start": offset,
                "field": "dc:title,dc:creator,prism:publicationName,prism:doi,"
                         "dc:description,prism:url,prism:coverDate",
            }
            r = safe_get(self.session, self._SD_SEARCH_URL, params=params,
                         headers=headers)
            if r is None:
                self.stats["errors"].append("IFAC/ScienceDirect API request failed")
                break
            try:
                data = r.json()
            except Exception:
                self.stats["errors"].append("IFAC: JSON parse error")
                break
            entries = data.get("search-results", {}).get("entry", [])
            if not entries:
                break
            for entry in entries:
                if len(results) >= limit:
                    break
                paper = self._parse_sd_entry(entry, query)
                if paper:
                    results.append(paper)
                    self.stats["raw_results"] += 1
            if len(entries) < per_page:
                break
            offset += per_page
        return results

    def _parse_sd_entry(self, entry: dict, query: str) -> Optional[PaperRecord]:
        paper = PaperRecord(source_site=self.site_key,
                            source_sites=[self.site_key],
                            source_queries=[query])
        paper.title = (entry.get("dc:title") or "").strip()
        if not paper.title:
            return None
        creator = entry.get("dc:creator") or ""
        paper.authors = [a.strip() for a in creator.split(";") if a.strip()]
        date = entry.get("prism:coverDate", "") or ""
        if date:
            paper.year = date[:4]
        paper.venue = entry.get("prism:publicationName") or "IFAC-PapersOnLine"
        paper.publisher = "Elsevier / IFAC"
        doi = entry.get("prism:doi") or ""
        if doi:
            paper.doi = norm_doi(doi)
            paper.doi_source = "api"
        desc = (entry.get("dc:description") or "").strip()
        if desc:
            paper.search_snippet = desc
        url = entry.get("prism:url") or ""
        if not url and isinstance(entry.get("link"), list):
            url = entry["link"][0].get("@href", "") if entry["link"] else ""
        if url:
            paper.paper_url = url
            paper.source_urls.append(url)
        elif paper.doi:
            paper.paper_url = f"https://doi.org/{paper.doi}"
        return paper

    def _search_web(self, query: str, limit: int) -> list[PaperRecord]:
        """Web scrape ScienceDirect filtered to IFAC-PapersOnLine."""
        results: list[PaperRecord] = []
        self._throttle()
        params = {
            "qs": query,
            "pub": "IFAC-PapersOnLine",
        }
        r = safe_get(self.session, self._SCIDIR_SEARCH_URL, params=params)
        if r is None:
            self.stats["errors"].append(
                "IFAC web search failed — ELSEVIER_API_KEY not configured"
            )
            return []
        soup = BeautifulSoup(r.text, "lxml")
        items = soup.select("li.ResultItem") or soup.select("article.result-item-content")
        for item in items[:limit]:
            paper = self._parse_scidir_item(item, query)
            if paper:
                results.append(paper)
                self.stats["raw_results"] += 1
        for paper in results:
            if paper.abstract is None and paper.paper_url:
                self._get_publisher_abstract(paper)
        return results

    def _parse_scidir_item(self, item, query: str) -> Optional[PaperRecord]:
        paper = PaperRecord(source_site=self.site_key,
                            source_sites=[self.site_key],
                            source_queries=[query])
        title_tag = item.select_one("h2 a") or item.select_one(".result-list-title a")
        if title_tag:
            paper.title = title_tag.get_text(strip=True)
            href = title_tag.get("href", "")
            if href:
                paper.paper_url = ("https://www.sciencedirect.com" + href
                                   if href.startswith("/") else href)
                paper.source_urls.append(paper.paper_url)
        if not paper.title:
            return None
        if paper.paper_url:
            paper.doi = extract_doi_from_url(paper.paper_url)
            if paper.doi:
                paper.doi_source = "site"
        paper.venue = "IFAC-PapersOnLine"
        paper.publisher = "Elsevier / IFAC"
        return paper


# ────────────────────────────────────────────────────────────────────────────
# Deduplication engine
# ────────────────────────────────────────────────────────────────────────────
class Deduplicator:
    FUZZY_THRESHOLD = 92   # conservative

    def __init__(self):
        self._by_doi: dict[str, PaperRecord] = {}
        self._by_title: dict[str, PaperRecord] = {}
        self._all: list[PaperRecord] = []

    def add(self, paper: PaperRecord) -> tuple[bool, Optional[PaperRecord]]:
        """
        Add a paper, merging if duplicate found.
        Returns (is_new, canonical_record).
        """
        paper._norm_doi   = norm_doi(paper.doi) if paper.doi else ""
        paper._norm_title = norm_title(paper.title) if paper.title else ""

        # 1. DOI match
        if paper._norm_doi:
            existing = self._by_doi.get(paper._norm_doi)
            if existing:
                self._merge(existing, paper)
                return False, existing

        # 2. Exact normalised title
        if paper._norm_title:
            existing = self._by_title.get(paper._norm_title)
            if existing:
                self._merge(existing, paper)
                if paper._norm_doi and paper._norm_doi not in self._by_doi:
                    self._by_doi[paper._norm_doi] = existing
                return False, existing

        # 3. Fuzzy title (only if thefuzz available)
        if FUZZY_OK and paper._norm_title:
            for key, existing in self._by_title.items():
                score = _fuzz.token_sort_ratio(paper._norm_title, key)
                if score >= self.FUZZY_THRESHOLD:
                    # Extra checks
                    year_ok = (not paper.year or not existing.year or
                               paper.year == existing.year)
                    if year_ok:
                        self._merge(existing, paper)
                        return False, existing

        # New paper
        self._all.append(paper)
        if paper._norm_doi:
            self._by_doi[paper._norm_doi] = paper
        if paper._norm_title:
            self._by_title[paper._norm_title] = paper
        return True, paper

    def _merge(self, target: PaperRecord, source: PaperRecord) -> None:
        """Merge source into target, preserving best data."""
        # Sites / queries / URLs
        for site in source.source_sites:
            if site not in target.source_sites:
                target.source_sites.append(site)
        for q in source.source_queries:
            if q not in target.source_queries:
                target.source_queries.append(q)
        for u in source.source_urls:
            if u not in target.source_urls:
                target.source_urls.append(u)

        # DOI
        if not target.doi and source.doi:
            target.doi = source.doi
            target.doi_source = source.doi_source

        # Abstract (prefer api > site > publisher_page > pdf > crossref)
        _priority = ["api", "site", "publisher_page", "pdf", "crossref"]
        def _ab_rank(src: Optional[str]) -> int:
            try:
                return _priority.index(src)
            except (ValueError, TypeError):
                return 99

        if source.abstract and (
            target.abstract is None or
            _ab_rank(source.abstract_source) < _ab_rank(target.abstract_source)
        ):
            target.abstract = source.abstract
            target.abstract_source = source.abstract_source
            target.abstract_status = source.abstract_status

        # Fill gaps
        for attr in ("year", "venue", "publisher", "paper_url", "pdf_url", "search_snippet"):
            if not getattr(target, attr) and getattr(source, attr):
                setattr(target, attr, getattr(source, attr))

        if not target.authors and source.authors:
            target.authors = source.authors

        target.errors.extend(source.errors)

    def papers(self) -> list[PaperRecord]:
        return self._all


# ────────────────────────────────────────────────────────────────────────────
# Output
# ────────────────────────────────────────────────────────────────────────────
def write_json(papers: list[PaperRecord], path: Path) -> None:
    data = [p.to_dict() for p in papers]
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Wrote %d papers to %s", len(papers), path)

def write_raw_json(raw: list[dict], path: Path) -> None:
    path.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Wrote %d raw results to %s", len(raw), path)

def _md_field(label: str, value) -> str:
    if value is None or value == "" or value == []:
        return ""
    if isinstance(value, list):
        value = "; ".join(str(v) for v in value)
    return f"**{label}:** {value}\n\n"

def write_markdown(papers: list[PaperRecord], site_stats: dict,
                   search_config: dict, path: Path) -> None:
    lines: list[str] = []
    lines.append("# Semi-Manual Novelty Search\n")

    # Search configuration
    lines.append("## Search Configuration\n")
    for site, queries in search_config.items():
        if queries:
            lines.append(f"### {site}\n")
            for i, q in enumerate(queries, 1):
                lines.append(f"{i}. `{q}`\n")
    lines.append("\n")

    # Statistics
    lines.append("## Retrieval Statistics\n")
    total_unique = len(papers)
    total_with_abstract = sum(1 for p in papers if p.abstract)
    total_without = total_unique - total_with_abstract

    for site_key, stats in site_stats.items():
        lines.append(f"### {stats.get('display_name', site_key)}\n")
        for qstat in stats.get("query_stats", []):
            lines.append(f"- Query: `{qstat['query']}`\n")
            lines.append(f"  - Raw results: {qstat['raw']}\n")
            lines.append(f"  - Unique (within site): {qstat['unique']}\n")
        if stats.get("errors"):
            lines.append(f"- **Errors:** {'; '.join(stats['errors'])}\n")
        lines.append("\n")

    lines.append("## Summary\n")
    lines.append(f"- **Total deduplicated papers:** {total_unique}\n")
    lines.append(f"- **Papers with actual abstracts:** {total_with_abstract}\n")
    lines.append(f"- **Papers without abstracts:** {total_without}\n\n")

    # Papers
    lines.append("## Deduplicated Papers\n")
    for i, paper in enumerate(papers, 1):
        lines.append(f"---\n\n")
        lines.append(f"### {i}. {paper.title or '(no title)'}\n\n")
        lines.append(_md_field("Authors", paper.authors))
        lines.append(_md_field("Year", paper.year))
        lines.append(_md_field("Venue", paper.venue))
        lines.append(_md_field("Publisher", paper.publisher))
        lines.append(_md_field("DOI", paper.doi))
        if paper.doi:
            lines.append(f"**DOI source:** {paper.doi_source}\n\n")
        lines.append(_md_field("Sources", paper.source_sites))
        lines.append(_md_field("Queries", paper.source_queries))
        lines.append(_md_field("Paper URL", paper.paper_url))
        if paper.pdf_url:
            lines.append(_md_field("PDF URL", paper.pdf_url))

        lines.append("#### Abstract\n\n")
        if paper.abstract:
            lines.append(f"{paper.abstract}\n\n")
            lines.append(f"**Abstract source:** {paper.abstract_source}\n\n")
        else:
            lines.append(f"*Not obtained.*\n\n")
            lines.append(f"**Abstract status:** {paper.abstract_status}\n\n")

        if paper.search_snippet:
            lines.append("#### Search Snippet\n\n")
            lines.append(f"{paper.search_snippet}\n\n")

        if paper.relevant_equations:
            lines.append("#### Relevant Equations\n\n")
            for eq in paper.relevant_equations[:8]:
                lines.append(f"```\n{eq['equation']}\n```\n\n")
                lines.append(f"**Context:** {eq['context'][:300]}\n\n")
                lines.append(f"**PDF page:** {eq['page']}\n\n")

        if paper.relevant_passages:
            lines.append("#### Relevant Passages\n\n")
            for rp in paper.relevant_passages[:5]:
                kws = ", ".join(rp["keywords"][:4])
                lines.append(f"**Keywords:** {kws} | **Page:** {rp['page']}\n\n")
                lines.append(f"> {rp['text'][:600]}\n\n")

        if paper.relevant_conclusions:
            lines.append("#### Relevant Conclusions\n\n")
            for rc in paper.relevant_conclusions[:3]:
                lines.append(f"**Page:** {rc['page']}\n\n")
                lines.append(f"> {rc['text'][:800]}\n\n")

        if paper.errors:
            lines.append("#### Extraction Notes\n\n")
            for e in paper.errors:
                lines.append(f"- {e}\n")
            lines.append("\n")

    path.write_text("".join(lines), encoding="utf-8")
    log.info("Wrote Markdown to %s", path)


# ────────────────────────────────────────────────────────────────────────────
# Main orchestrator
# ────────────────────────────────────────────────────────────────────────────
ADAPTERS: dict[str, type] = {
    "scholar":       ScholarAdapter,
    "ieee":          IEEEAdapter,
    "sciencedirect": ScienceDirectAdapter,
    "springer":      SpringerAdapter,
    "acm":           ACMAdapter,
    "aiaa":          AIAAAdapter,
    "ifac":          IFACAdapter,
}

SITE_DISPLAY = {
    "scholar":       "Google Scholar",
    "ieee":          "IEEE Xplore",
    "sciencedirect": "ScienceDirect",
    "springer":      "Springer Nature Link",
    "acm":           "ACM Digital Library",
    "aiaa":          "AIAA Aerospace Research Central",
    "ifac":          "IFAC-PapersOnLine",
}


def run_search(site_key: str, queries: list[str], limit: int,
               raw_bucket: list[dict], site_stats_out: dict,
               enable_pdf: bool, enable_crossref: bool,
               crossref_session: requests.Session) -> list[PaperRecord]:
    """Run all queries for one site, deduplicate within site, return unique list."""
    AdapterCls = ADAPTERS[site_key]
    adapter = AdapterCls()
    adapter.stats["display_name"] = SITE_DISPLAY.get(site_key, site_key)

    site_dedup = Deduplicator()
    query_stats: list[dict] = []
    all_papers: list[PaperRecord] = []

    for query in queries:
        log.info("[%s] Running query: %s", SITE_DISPLAY.get(site_key, site_key), query)
        adapter.stats["queries"] += 1
        try:
            papers = adapter.search(query, limit)
        except Exception as exc:
            log.error("[%s] Query failed: %s — %s", site_key, query, exc)
            adapter.stats["errors"].append(f"Query '{query}' failed: {exc}")
            papers = []

        raw_count = len(papers)
        unique_count = 0

        for paper in papers:
            # Store raw entry before dedup
            raw_entry = paper.to_dict()
            raw_entry["_raw_query"] = query
            raw_entry["_raw_site"] = site_key
            raw_bucket.append(raw_entry)

            is_new, canonical = site_dedup.add(paper)
            if is_new:
                unique_count += 1
                all_papers.append(canonical)

        query_stats.append({
            "query": query,
            "raw": raw_count,
            "unique": unique_count,
        })
        log.info("[%s] Query done — raw: %d, unique: %d",
                 site_key, raw_count, unique_count)

    # Enforce per-site limit
    final_papers = all_papers[:limit]

    # Fetch PDFs if abstract still missing
    if enable_pdf:
        pdf_session = make_session()
        for paper in final_papers:
            if paper.abstract is None and paper.pdf_url:
                try:
                    download_and_extract_pdf(pdf_session, paper.pdf_url, paper)
                except Exception as exc:
                    paper.errors.append(f"PDF extraction error: {exc}")

    # Crossref enrichment
    if enable_crossref:
        for paper in final_papers:
            if paper.abstract is None or not paper.doi:
                try:
                    enrich_from_crossref(paper, crossref_session)
                except Exception as exc:
                    paper.errors.append(f"Crossref error: {exc}")

    # Set final abstract status
    for paper in final_papers:
        if paper.abstract is None:
            paper.abstract_status = "unavailable"
        else:
            paper.abstract_status = "ok"

    site_stats_out[site_key] = {
        "display_name": SITE_DISPLAY.get(site_key, site_key),
        "query_stats": query_stats,
        "total_raw": adapter.stats["raw_results"],
        "unique": len(final_papers),
        "with_abstract": sum(1 for p in final_papers if p.abstract),
        "errors": adapter.stats["errors"],
    }

    return final_papers


# ────────────────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Multi-site literature scraper for systematic novelty search",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--scholar", nargs="+", metavar="QUERY",
                   help="Google Scholar query/queries")
    p.add_argument("--ieee", nargs="+", metavar="QUERY",
                   help="IEEE Xplore query/queries")
    p.add_argument("--sciencedirect", nargs="+", metavar="QUERY",
                   help="ScienceDirect query/queries")
    p.add_argument("--springer", nargs="+", metavar="QUERY",
                   help="Springer Nature query/queries")
    p.add_argument("--acm", nargs="+", metavar="QUERY",
                   help="ACM Digital Library query/queries")
    p.add_argument("--aiaa", nargs="+", metavar="QUERY",
                   help="AIAA Aerospace Research Central query/queries")
    p.add_argument("--ifac", nargs="+", metavar="QUERY",
                   help="IFAC-PapersOnLine query/queries")
    p.add_argument("--limit", type=int, default=50,
                   help="Max unique papers per site (default: 50)")
    p.add_argument("--output", default="results",
                   help="Output filename stem (default: results)")
    p.add_argument("--no-pdf", action="store_true",
                   help="Disable PDF download/extraction")
    p.add_argument("--no-crossref", action="store_true",
                   help="Disable Crossref DOI/abstract enrichment")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Enable DEBUG logging")
    return p

def checkpoint(papers: list[PaperRecord], raw_bucket: list[dict], stem: str) -> None:
    Path(f"{stem}_checkpoint.json").write_text(
        json.dumps([p.to_dict() for p in papers], indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    Path(f"{stem}_raw_checkpoint.json").write_text(
        json.dumps(raw_bucket, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    log.info("Checkpoint saved (%d papers so far)", len(papers))


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Build search plan
    search_plan: dict[str, list[str]] = {}
    for site in ADAPTERS:
        queries = getattr(args, site.replace("-", "_"), None) or []
        if queries:
            search_plan[site] = queries

    if not search_plan:
        parser.print_help()
        sys.exit(0)

    log.info("Search plan:")
    for site, queries in search_plan.items():
        log.info("  %s: %d queries", SITE_DISPLAY.get(site, site), len(queries))

    output_stem = args.output
    enable_pdf       = not args.no_pdf
    enable_crossref  = not args.no_crossref
    limit            = args.limit

    # Shared state
    raw_bucket:   list[dict] = []
    site_stats:   dict[str, dict] = {}
    crossref_sess = make_session()

    # Cross-site deduplicator
    global_dedup = Deduplicator()
    all_site_papers: dict[str, list[PaperRecord]] = {}

    # Execute per-site
    for site_key, queries in search_plan.items():
        log.info("═" * 60)
        log.info("Site: %s", SITE_DISPLAY.get(site_key, site_key))
        try:
            papers = run_search(
                site_key, queries, limit,
                raw_bucket, site_stats,
                enable_pdf, enable_crossref,
                crossref_sess,
            )
            all_site_papers[site_key] = papers
            # Change for checkpointing:
            checkpoint(list(global_dedup.papers()) + [p for s, ps in all_site_papers.items() for p in ps], raw_bucket, output_stem)
        except Exception as exc:
            log.error("Site %s failed entirely: %s", site_key, exc)
            site_stats[site_key] = {
                "display_name": SITE_DISPLAY.get(site_key, site_key),
                "query_stats": [],
                "errors": [str(exc)],
            }
            all_site_papers[site_key] = []

    # Cross-site deduplication
    log.info("═" * 60)
    log.info("Running cross-site deduplication …")
    for site_key, papers in all_site_papers.items():
        for paper in papers:
            global_dedup.add(paper)

    final_papers = global_dedup.papers()

    # Write outputs
    out_json  = Path(f"{output_stem}.json")
    out_md    = Path(f"{output_stem}.md")
    out_raw   = Path(f"{output_stem}_raw.json")

    write_json(final_papers, out_json)
    write_raw_json(raw_bucket, out_raw)
    write_markdown(final_papers, site_stats,
                   {SITE_DISPLAY.get(k, k): v for k, v in search_plan.items()},
                   out_md)

    # Summary
    print("\n" + "═" * 60)
    print("RETRIEVAL SUMMARY")
    print("═" * 60)
    for site_key, stats in site_stats.items():
        display = stats.get("display_name", site_key)
        qstats = stats.get("query_stats", [])
        total_raw = sum(q["raw"] for q in qstats)
        total_unique = stats.get("unique", 0)
        with_ab = stats.get("with_abstract", 0)
        print(f"\n{display}")
        for qs in qstats:
            print(f"  Query: {qs['query']!r}")
            print(f"    raw: {qs['raw']}  unique: {qs['unique']}")
        print(f"  TOTAL raw: {total_raw}  unique: {total_unique}  with abstract: {with_ab}")
        if stats.get("errors"):
            for e in stats["errors"]:
                print(f"  ⚠  {e}")

    n_with_ab = sum(1 for p in final_papers if p.abstract)
    print(f"\n{'─'*60}")
    print(f"Cross-site unique papers : {len(final_papers)}")
    print(f"Papers with abstracts    : {n_with_ab}")
    print(f"Papers without abstracts : {len(final_papers) - n_with_ab}")
    print(f"\nOutputs:")
    print(f"  {out_json}")
    print(f"  {out_md}")
    print(f"  {out_raw}")
    print("═" * 60)


if __name__ == "__main__":
    main()
