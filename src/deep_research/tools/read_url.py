"""Fetch a web page and extract just the article text.

The framework ships a scraping tool that returns every string on the page.
On the standard extraction benchmark that approach scores near-perfect recall
but roughly half precision: navigation, footers, cookie banners and inline
scripts all come back with the article. For an LLM pipeline that is primarily a
cost problem -- about twice the tokens for the same information, paid on every
source of every run -- so this tool uses a dedicated extraction library
instead. It also returns the publication date, which the report needs in order
to say what is *changing* rather than merely what is true.

See docs/DECISIONS.md, D-10.
"""

from __future__ import annotations

import json
from typing import Type

import trafilatura
from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from . import http

MAX_CHARS = 6000


def read_page(url: str) -> dict:
    r = http.get(url, timeout=25)
    if r is None:
        return {"url": url, "ok": False, "reason": "fetch failed or not in cache", "text": ""}

    html = r.text
    text = trafilatura.extract(
        html,
        output_format="markdown",
        include_comments=False,
        include_tables=True,
        favor_precision=True,
    )
    if not text:
        return {"url": url, "ok": False, "reason": "no article content extracted", "text": ""}

    title = author = published = None
    try:
        md = trafilatura.extract_metadata(html)
        if md is not None:
            title = md.title
            author = md.author
            published = md.date
    except Exception:
        pass

    truncated = len(text) > MAX_CHARS
    return {
        "url": url,
        "ok": True,
        "title": title,
        "author": author,
        "published": published,
        "chars": len(text),
        "truncated": truncated,
        "text": text[:MAX_CHARS],
    }


class ReadPageInput(BaseModel):
    url: str = Field(..., description="Full URL of the page to read.")


class ReadWebPageTool(BaseTool):
    name: str = "Read a web page"
    description: str = (
        "Fetch a URL and return its main article text plus title and publication date, with "
        "navigation and boilerplate stripped. Use this after a search to read a source properly "
        "before relying on it. Always cite the URL you read."
    )
    args_schema: Type[BaseModel] = ReadPageInput

    def _run(self, url: str) -> str:
        return json.dumps(read_page(url), indent=2)[: MAX_CHARS + 1200]
