"""External research tools exposed to the agents.

Search resolves through tiers so that a missing credential or a dead provider
narrows the report instead of ending the run:

    SERPER_API_KEY -> TAVILY_API_KEY -> keyless metasearch -> keyless news feed

Every tool returns a JSON string. Each result carries the URL and the source
name it came from, because the citation audit downstream can only verify what
the tools bothered to record.

See docs/DECISIONS.md, D-08 and D-09.
"""

from __future__ import annotations

import json
import os
from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from . import http, keyless, search_cache

SERPER_URL = "https://google.serper.dev/search"
TAVILY_URL = "https://api.tavily.com/search"


# --------------------------------------------------------------------------
# Provider tiers
# --------------------------------------------------------------------------
def _serper(query: str, limit: int = 8) -> list[dict] | None:
    key = os.getenv("SERPER_API_KEY")
    if not key:
        return None
    r = http.post(
        SERPER_URL,
        json={"q": query, "num": min(limit, 10)},
        headers={"X-API-KEY": key, "Content-Type": "application/json"},
    )
    if r is None:
        return None
    try:
        payload = r.json()
    except Exception:
        return None
    out = []
    for item in (payload.get("organic") or [])[:limit]:
        out.append(
            {
                "title": item.get("title"),
                "url": item.get("link"),
                "snippet": item.get("snippet"),
                "published": item.get("date"),
                "via": "serper",
            }
        )
    return out or None


def _tavily(query: str, limit: int = 8) -> list[dict] | None:
    key = os.getenv("TAVILY_API_KEY")
    if not key:
        return None
    r = http.post(
        TAVILY_URL,
        json={"query": query, "max_results": limit, "search_depth": "basic"},
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    if r is None:
        return None
    try:
        payload = r.json()
    except Exception:
        return None
    out = []
    for item in (payload.get("results") or [])[:limit]:
        out.append(
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "snippet": (item.get("content") or "")[:300],
                "published": item.get("published_date"),
                "via": "tavily",
            }
        )
    return out or None


def _ddgs(query: str, limit: int = 8) -> list[dict] | None:
    """Keyless metasearch. Rate-limits hard, so failure is treated as normal."""
    if http.offline():
        return None
    try:
        from ddgs import DDGS

        rows = DDGS().text(
            query,
            max_results=limit,
            backend="duckduckgo,mojeek,brave,bing",
        )
    except Exception:
        return None
    out = []
    for row in rows or []:
        out.append(
            {
                "title": row.get("title"),
                "url": row.get("href") or row.get("url"),
                "snippet": row.get("body"),
                "published": None,
                "via": "keyless-metasearch",
            }
        )
    return out or None


def _keyless_fanout(query: str, limit: int = 8) -> list[dict]:
    """Last resort before the cache: news feed plus reference grounding."""
    out = []
    news = keyless.news_rss(query, limit=limit)
    for a in news.get("articles", []):
        out.append(
            {
                "title": a["title"],
                "url": a["url"],
                "snippet": None,
                "published": a.get("published"),
                "publisher": a.get("publisher"),
                "via": "google-news",
            }
        )
    if len(out) < 4:
        wiki = keyless.wikipedia_search(query, limit=3)
        for p in wiki.get("pages", []):
            out.append(
                {
                    "title": p["title"],
                    "url": p["url"],
                    "snippet": p.get("excerpt"),
                    "published": None,
                    "via": "wikipedia",
                }
            )
    return out[:limit]


def web_search(query: str, limit: int = 8) -> dict:
    """Tiered search. Reports which tier answered so the run is auditable.

    Offline, the recorded result set is consulted first: the metasearch tier
    runs on a third-party HTTP client that the response cache cannot see, so
    replaying a previous run means replaying this record instead.
    """
    attempted = []
    if http.offline():
        recalled = search_cache.recall(query)
        if recalled:
            return {
                "query": query,
                "provider": f"recorded:{search_cache.provider_for(query)}",
                "attempted": ["recorded-search-results"],
                "results": recalled[:limit],
            }
    for name, fn in (("serper", _serper), ("tavily", _tavily), ("keyless-metasearch", _ddgs)):
        attempted.append(name)
        results = fn(query, limit)
        if results:
            search_cache.remember(query, name, results)
            return {"query": query, "provider": name, "attempted": attempted, "results": results}
    attempted.append("keyless-fanout")
    results = _keyless_fanout(query, limit)
    if results:
        search_cache.remember(query, "keyless-fanout", results)
        return {
            "query": query,
            "provider": "keyless-fanout",
            "attempted": attempted,
            "results": results,
        }
    # Every live tier failed. A previously recorded answer beats no answer, and
    # saying which is which is what keeps the report honest.
    recalled = search_cache.recall(query)
    if recalled:
        attempted.append("recorded-search-results")
        return {
            "query": query,
            "provider": f"recorded:{search_cache.provider_for(query)}",
            "attempted": attempted,
            "results": recalled[:limit],
        }
    return {"query": query, "provider": "none", "attempted": attempted, "results": []}


# --------------------------------------------------------------------------
# Agent-facing tools
# --------------------------------------------------------------------------
class QueryInput(BaseModel):
    query: str = Field(..., description="A specific, focused search query.")


class WebSearchTool(BaseTool):
    name: str = "Search the public web"
    description: str = (
        "Search the public web for market news, company announcements, funding rounds and "
        "analyst commentary. Input a single focused query. Returns titles, URLs, publishers "
        "and dates. Use this for current events and commercial signal. Always record the URL "
        "of anything you rely on."
    )
    args_schema: Type[BaseModel] = QueryInput

    def _run(self, query: str) -> str:
        return json.dumps(web_search(query), indent=2)[:6000]


class FilingSearchInput(BaseModel):
    query: str = Field(
        ...,
        description=(
            'Full-text query over SEC filings. Supports quoted phrases and OR, e.g. '
            '\'"industrial autonomy" OR "autonomous manufacturing"\'.'
        ),
    )
    forms: str = Field(
        "",
        description=(
            "Optional filing-type filter, e.g. 'S-1' for companies newly registering to go "
            "public, '8-K' for material events, '10-K' for annual reports. Leave empty for all."
        ),
    )


class FilingSearchTool(BaseTool):
    name: str = "Search regulatory filings"
    description: str = (
        "Search the full text of US SEC filings and get back not just matching filings but "
        "aggregate counts of WHICH COMPANIES are discussing the topic and in WHICH INDUSTRIES. "
        "This is a primary source: it is evidence of what public companies actually tell "
        "regulators, not commentary about them. Filter forms='S-1' to find companies newly "
        "registering to go public, which is a strong new-entrant signal."
    )
    args_schema: Type[BaseModel] = FilingSearchInput

    def _run(self, query: str, forms: str = "") -> str:
        data = keyless.edgar_fulltext(query, forms=forms or None)
        return json.dumps(data, indent=2)[:6000]


class ResearchSignalTool(BaseTool):
    name: str = "Search research preprints"
    description: str = (
        "Search recent scientific preprints on a topic. Research volume and recency are a "
        "leading indicator of where technical effort is going, typically 12-24 months ahead of "
        "commercial availability. Use this to judge whether a capability is becoming feasible."
    )
    args_schema: Type[BaseModel] = QueryInput

    def _run(self, query: str) -> str:
        return json.dumps(keyless.arxiv_search(query, limit=6), indent=2)[:5000]


class CommunitySignalTool(BaseTool):
    name: str = "Search developer community signal"
    description: str = (
        "Search technical community discussion for product launches and practitioner sentiment. "
        "Useful for spotting new entrants and gauging whether a product is respected by the "
        "engineers who would have to operate it, often before trade press covers it."
    )
    args_schema: Type[BaseModel] = QueryInput

    def _run(self, query: str) -> str:
        return json.dumps(keyless.hn_search(query, limit=8), indent=2)[:5000]
