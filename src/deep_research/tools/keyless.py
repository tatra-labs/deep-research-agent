"""Primary-source research clients that need no API credentials.

These are the backbone of the external research leg, not a fallback of last
resort. For the questions this report has to answer they are frequently a
better source than a general web search:

* **SEC full-text search** returns, alongside the matching filings, aggregations
  by filing entity, by industry code and by form type. One request therefore
  answers "which public companies are discussing this, in which industries" --
  which is most of "how are leaders innovating". S-1 registrations in the same
  response are new entrants coming to market.
* **arXiv** submission volume is a leading indicator of where technical effort
  is going, ahead of any commercial coverage.
* **Hacker News** surfaces launches and developer sentiment before trade press.
* **Google News** is the most current keyless feed for financing announcements.
* **Wikipedia** grounds the taxonomy and the incumbent set.

Every client returns plain dictionaries and never raises: a dead source
narrows the report rather than ending the run.

Noted honestly: the SEC full-text endpoint is undocumented and carries no
stability guarantee, and the Google News feed is not an official API. Both are
wrapped behind this interface precisely so they can be replaced.

See docs/DECISIONS.md, D-09.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import feedparser

from . import http

EDGAR_FTS = "https://efts.sec.gov/LATEST/search-index"
HN_SEARCH = "https://hn.algolia.com/api/v1/search"
WIKI_SEARCH = "https://en.wikipedia.org/w/rest.php/v1/search/page"
ARXIV_API = "http://export.arxiv.org/api/query"
NEWS_RSS = "https://news.google.com/rss/search"
CROSSREF = "https://api.crossref.org/works"

# Industry codes that recur in industrial-automation filings. Unknown codes are
# passed through as-is rather than guessed at.
SIC_NAMES = {
    "3510": "Engines and turbines",
    "3531": "Construction machinery",
    "3540": "Metalworking machinery",
    "3550": "Special industry machinery",
    "3559": "Special industry machinery, other",
    "3560": "General industrial machinery",
    "3569": "General industrial machinery, other",
    "3570": "Computer and office equipment",
    "3576": "Computer communications equipment",
    "3600": "Electronic and electrical equipment",
    "3612": "Power distribution and transformers",
    "3620": "Electrical industrial apparatus",
    "3670": "Electronic components and accessories",
    "3674": "Semiconductors",
    "3679": "Electronic components, other",
    "3690": "Electrical machinery and equipment",
    "3711": "Motor vehicles",
    "3721": "Aircraft",
    "3812": "Search, detection and navigation instruments",
    "3823": "Industrial process control instruments",
    "3826": "Laboratory analytical instruments",
    "3827": "Optical instruments and lenses",
    "3829": "Measuring and controlling devices",
    "3312": "Steel works and blast furnaces",
    "6331": "Fire, marine and casualty insurance",
    "7371": "Computer programming and data processing services",
    "7372": "Prepackaged software",
    "8711": "Engineering services",
}

_CIK = re.compile(r"\(CIK (\d{10})\)")
_TICKER = re.compile(r"\(([A-Z][A-Z0-9.\-]{0,6}(?:, [A-Z][A-Z0-9.\-]{0,6})*)\)")
_TAGS = re.compile(r"<[^>]+>")


def _clean_entity(display: str) -> dict:
    """Split an EDGAR display name into company, tickers and CIK."""
    m = _CIK.search(display)
    cik = m.group(1) if m else None
    name = _CIK.sub("", display)
    t = _TICKER.search(name)
    tickers = t.group(1).split(", ") if t else []
    name = _TICKER.sub("", name).strip(" ()")
    return {"company": " ".join(name.split()), "tickers": tickers, "cik": cik}


def _filing_url(hit_id: str | None, cik_plain: str) -> str:
    """Build the archive URL for one filing document from a search hit id."""
    if not hit_id or not cik_plain:
        return ""
    accession, _, filename = str(hit_id).partition(":")
    bare = accession.replace("-", "")
    if not bare or not filename:
        return ""
    return f"https://www.sec.gov/Archives/edgar/data/{cik_plain}/{bare}/{filename}"


def edgar_fulltext(query, months_back=12, forms=None, limit=10):
    """Search the full text of SEC filings; returns facet aggregations too.

    The query supports quoted phrases and OR.
    """
    end = date.today()
    start = end - timedelta(days=31 * months_back)
    params = {"q": query, "startdt": start.isoformat(), "enddt": end.isoformat()}
    if forms:
        params["forms"] = forms

    r = http.get(EDGAR_FTS, params=params, timeout=30)
    if r is None:
        return {"source": "sec_edgar", "query": query, "available": False, "filings": []}
    try:
        j = r.json()
    except Exception:
        return {"source": "sec_edgar", "query": query, "available": False, "filings": []}

    aggs = j.get("aggregations", {})

    def buckets(name):
        return aggs.get(name, {}).get("buckets", []) or []

    companies = []
    for b in buckets("entity_filter")[:12]:
        ent = _clean_entity(b.get("key", ""))
        ent["filing_count"] = b.get("doc_count")
        companies.append(ent)

    industries = [
        {
            "sic": b.get("key"),
            "industry": SIC_NAMES.get(str(b.get("key")), "SIC " + str(b.get("key"))),
            "filing_count": b.get("doc_count"),
        }
        for b in buckets("sic_filter")[:10]
    ]

    form_counts = [
        {"form": b.get("key"), "count": b.get("doc_count")} for b in buckets("form_filter")[:10]
    ]

    filings = []
    for h in j.get("hits", {}).get("hits", [])[:limit]:
        s = h.get("_source", {})
        ent = _clean_entity((s.get("display_names") or [""])[0])
        cik_plain = (ent["cik"] or "").lstrip("0")
        filings.append(
            {
                "company": ent["company"],
                "tickers": ent["tickers"],
                "cik": ent["cik"],
                "form": s.get("root_form") or s.get("file_type"),
                "filed": s.get("file_date"),
                "description": s.get("file_description") or "",
                # The document itself, not the company's filing history. The
                # first version of this linked to a browse-edgar listing for
                # the CIK, which resolves but does not contain the sentence
                # being cited -- a citation a reader cannot check in one click
                # is only half a citation. The hit id carries
                # "<accession>:<filename>", which is enough to address the
                # exact document in the EDGAR archive.
                "url": _filing_url(h.get("_id"), cik_plain),
                "company_filings_url": (
                    "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK="
                    + cik_plain
                    + "&type=&dateb=&owner=include&count=40"
                ),
            }
        )

    total = j.get("hits", {}).get("total", {})
    return {
        "source": "sec_edgar",
        "query": query,
        "available": True,
        "window": start.isoformat() + " to " + end.isoformat(),
        "total_filings": total.get("value"),
        "total_is_lower_bound": total.get("relation") == "gte",
        "companies_discussing": companies,
        "industries": industries,
        "form_types": form_counts,
        "filings": filings,
        "citation": "SEC EDGAR full-text search, https://www.sec.gov/edgar/search/",
    }


def hn_search(query, limit=10):
    """Hacker News stories -- an early launch and sentiment signal."""
    r = http.get(HN_SEARCH, params={"query": query, "tags": "story", "hitsPerPage": limit})
    if r is None:
        return {"source": "hacker_news", "query": query, "available": False, "stories": []}
    try:
        j = r.json()
    except Exception:
        return {"source": "hacker_news", "query": query, "available": False, "stories": []}
    stories = [
        {
            "title": h.get("title"),
            "url": h.get("url") or "https://news.ycombinator.com/item?id=" + str(h.get("objectID")),
            "points": h.get("points"),
            "comments": h.get("num_comments"),
            "created": (h.get("created_at") or "")[:10],
        }
        for h in j.get("hits", [])
        if h.get("title")
    ]
    return {
        "source": "hacker_news",
        "query": query,
        "available": True,
        "total_matches": j.get("nbHits"),
        "stories": stories,
        "citation": "Hacker News search (Algolia), https://hn.algolia.com",
    }


def wikipedia_search(query, limit=4):
    """Grounds taxonomy and the incumbent set."""
    r = http.get(WIKI_SEARCH, params={"q": query, "limit": limit})
    if r is None:
        return {"source": "wikipedia", "query": query, "available": False, "pages": []}
    try:
        pages = r.json().get("pages", [])
    except Exception:
        return {"source": "wikipedia", "query": query, "available": False, "pages": []}
    return {
        "source": "wikipedia",
        "query": query,
        "available": True,
        "pages": [
            {
                "title": p.get("title"),
                "description": p.get("description"),
                "excerpt": _TAGS.sub("", p.get("excerpt") or ""),
                "url": "https://en.wikipedia.org/wiki/" + (p.get("key") or "").replace(" ", "_"),
            }
            for p in pages
        ],
        "citation": "Wikipedia, https://en.wikipedia.org",
    }


def arxiv_search(query, limit=8):
    """Recent preprints, newest first. Volume here leads commercial activity."""
    r = http.get(
        ARXIV_API,
        params={
            # A loose unquoted query sorted by date returns the newest papers
            # in all of arXiv, not the most relevant ones. Quote the phrase and
            # let relevance rank; recency is then reported per paper.
            "search_query": 'all:"' + query.strip('"') + '"',
            "start": 0,
            "max_results": limit,
            "sortBy": "relevance",
            "sortOrder": "descending",
        },
        timeout=30,
    )
    if r is None:
        return {"source": "arxiv", "query": query, "available": False, "papers": []}
    feed = feedparser.parse(r.text)
    papers = [
        {
            "title": " ".join((e.get("title") or "").split()),
            "published": (e.get("published") or "")[:10],
            "url": e.get("link"),
            "summary": " ".join((e.get("summary") or "").split())[:340],
        }
        for e in feed.entries
    ]
    return {
        "source": "arxiv",
        "query": query,
        "available": True,
        "papers": papers,
        "citation": "arXiv, https://arxiv.org",
    }


def news_rss(query, limit=12):
    """Most current keyless feed -- the practical source for financing news."""
    r = http.get(NEWS_RSS, params={"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    if r is None:
        return {"source": "google_news", "query": query, "available": False, "articles": []}
    feed = feedparser.parse(r.text)
    articles = []
    for e in feed.entries[:limit]:
        src = e.get("source")
        publisher = None
        if isinstance(src, dict):
            publisher = src.get("title")
        elif src is not None:
            publisher = getattr(src, "title", None)
        articles.append(
            {
                "title": " ".join((e.get("title") or "").split()),
                "url": e.get("link"),
                "published": (e.get("published") or "")[:16],
                "publisher": publisher,
            }
        )
    return {
        "source": "google_news",
        "query": query,
        "available": True,
        "articles": articles,
        "citation": "Google News search feed, https://news.google.com",
    }


def crossref_search(query, limit=6):
    """Published research with funder metadata attached."""
    r = http.get(
        CROSSREF, params={"query": query, "rows": limit, "sort": "published", "order": "desc"}
    )
    if r is None:
        return {"source": "crossref", "query": query, "available": False, "works": []}
    try:
        items = r.json().get("message", {}).get("items", [])
    except Exception:
        return {"source": "crossref", "query": query, "available": False, "works": []}
    works = []
    for it in items:
        parts = (it.get("published", {}).get("date-parts") or [[None]])[0] or []
        works.append(
            {
                "title": (it.get("title") or [""])[0],
                "published": "-".join(str(x) for x in parts if x),
                "url": it.get("URL"),
                "funders": [f.get("name") for f in (it.get("funder") or [])][:4],
            }
        )
    return {
        "source": "crossref",
        "query": query,
        "available": True,
        "works": works,
        "citation": "Crossref, https://api.crossref.org",
    }
