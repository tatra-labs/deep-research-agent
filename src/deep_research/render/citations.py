"""Making citations readable without making them weaker.

The writing agent cites an external claim by putting the URL where the claim
is, which is the correct instinct and produces unreadable output: a Google
News link is several hundred characters of base64, and two in a paragraph
make the paragraph unreadable on a page an executive is meant to skim.

Nothing here drops a citation. Every URL keeps its position in the sentence
and stays clickable; only the address moves to a list at the end. The same
applies to record identifiers: an executive-summary bullet trailing
twenty-two deal identifiers is not more traceable than one showing five and a
count, because nobody reads the twenty-two. The complete set stays in the
evidence ledger and in the typed output, which is where verification actually
happens.
"""

from __future__ import annotations

import re

# A URL inside prose. Stops at whitespace and at the punctuation that normally
# closes a citation, so a trailing bracket or full stop is not swallowed.
URL_IN_TEXT = re.compile(r"https?://[^\s\]\),]+")

# A bracketed citation whose contents are one or more bare URLs, e.g.
# "[https://a.com, https://b.com]". Existing markdown links do not match,
# because their bracket holds the title rather than the address.
BRACKETED_URLS = re.compile(r"\[([^\[\]]*?https?://[^\[\]]*?)\]")

# A URL loose in the prose. The lookbehind keeps this away from the target of
# a markdown link that is already formatted.
LOOSE_URL = re.compile(r"(?<!\]\()https?://[^\s\]\),]+")

_TRAILING = ".,;"


class Footnotes:
    """Collects URLs in order of first appearance and numbers them."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def _ref(self, url: str) -> str:
        url = url.rstrip(_TRAILING)
        if url not in self.urls:
            self.urls.append(url)
        return f"[{self.urls.index(url) + 1}]({url})"

    def rewrite(self, text: str) -> str:
        """Replace URLs in one block of prose with numbered references."""
        if not text:
            return text

        def group(match: re.Match) -> str:
            urls = URL_IN_TEXT.findall(match.group(1))
            if not urls:
                return match.group(0)
            return "[" + ", ".join(self._ref(u) for u in urls) + "]"

        text = BRACKETED_URLS.sub(group, text)
        return LOOSE_URL.sub(lambda m: self._ref(m.group(0)), text)

    def render(self) -> list[str]:
        """The reference list, as markdown lines. Empty if nothing was cited."""
        if not self.urls:
            return []
        lines = [
            "## Sources",
            "",
            "Numbered references from the report body. Every external claim above "
            "resolves to one of these.",
            "",
        ]
        for i, url in enumerate(self.urls, 1):
            lines.append(f"{i}. [{readable(url)}]({url})")
        lines.append("")
        return lines


def readable(url: str, width: int = 62) -> str:
    """A label for a URL that fits on a page.

    Some of these addresses are several hundred characters of base64 -- a
    Google News link is the usual offender -- and printing one as visible text
    in a reference list stretches the page and hides the one part a reader
    actually uses, which is the publisher. So the label shows the host and as
    much of the path as fits, and the full address stays as the link target.
    """
    trimmed = re.sub(r"^https?://(www\.)?", "", (url or "").strip())
    if len(trimmed) <= width:
        return trimmed
    host, _, rest = trimmed.partition("/")
    room = max(width - len(host) - 4, 8)
    return f"{host}/{rest[:room]}..." if rest else f"{host[:width]}..."


def short_tags(tags: list[str], limit: int = 5) -> str:
    """Record identifiers for prose, where a list of twenty is noise."""
    if not tags:
        return ""
    if len(tags) <= limit:
        return ", ".join(tags)
    return f"{', '.join(tags[:limit])} and {len(tags) - limit} more"


def shorten_identifier_runs(text: str, limit: int = 5) -> str:
    """Trim long bracketed identifier lists in prose to a readable head.

    Applied to the narrative sections only. The signal matrix and the evidence
    ledger are rendered from typed data and keep every identifier, so the full
    set is always one section away.
    """
    if not text:
        return text

    def one(match: re.Match) -> str:
        inner = match.group(1)
        parts = [p.strip() for p in inner.split(",") if p.strip()]
        if len(parts) <= limit:
            return match.group(0)
        if not all(re.fullmatch(r"[A-Z]{1,8}-\d{1,5}", p) for p in parts):
            return match.group(0)
        return f"[{short_tags(parts, limit)}]"

    return re.sub(r"\[([A-Z0-9,\-\s]+)\]", one, text)
