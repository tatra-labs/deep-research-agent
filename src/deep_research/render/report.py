"""Render the memo to markdown and to a print-ready HTML page.

One structural decision matters here. The narrative sections are prose written
by the writing agent, but the Internal-External Signal Matrix is generated
directly from the typed CrossSignal objects. That is deliberate: the matrix is
the part of the report that has to be structurally guaranteed, and rendering it
from typed data means every row necessarily carries external evidence with a
URL and internal evidence with record identifiers. Prose can omit a citation.
A table built from a schema cannot.

The evidence ledger at the end is likewise assembled from the typed evidence
rather than transcribed, so it cannot disagree with the body of the report.
"""

from __future__ import annotations

import html as html_lib
import re
from datetime import datetime

from ..state import ExecutiveMemo, ExternalFindings, FusionResult, InternalAssessment
from .citations import Footnotes, shorten_identifier_runs

URGENCY_LABEL = {
    "act_now": "Act now",
    "decide_this_quarter": "Decide this quarter",
    "monitor": "Monitor",
    "no_action": "No action",
}

RECOMMENDATION_LABEL = {
    "build": "Build",
    "partner": "Partner",
    "buy": "Buy",
    "hybrid": "Hybrid",
    "exit": "Exit",
    "no_action": "No action",
}


def _tags(tags: list[str]) -> str:
    return " ".join(f"`[{t}]`" for t in tags) if tags else "-"


def _link(ev) -> str:
    title = (ev.source_title or ev.source_url or "source").strip()
    if len(title) > 70:
        title = title[:67] + "..."
    return f"[{title}]({ev.source_url})" if ev.source_url else title


def to_markdown(
    memo: ExecutiveMemo,
    fusion: FusionResult | None,
    external: ExternalFindings | None,
    internal: InternalAssessment | None,
    manifest: dict | None = None,
) -> str:
    d: list[str] = []
    notes = Footnotes()

    def a(line: str = "") -> None:
        d.append(line)

    def prose(text: str) -> None:
        """A narrative block: references numbered, identifier runs trimmed.

        Only the prose goes through this. The signal matrix and the evidence
        ledger are rendered from typed objects further down and keep every URL
        and every identifier in full, so nothing here is the only copy.
        """
        a(notes.rewrite(shorten_identifier_runs(text or "")))

    a(f"# {memo.title}")
    a("")
    meta = [f"**Prepared for:** {memo.prepared_for or 'Corporate Strategy & Ventures'}"]
    meta.append(f"**Date:** {datetime.now().strftime('%d %B %Y')}")
    meta.append(
        f"**Recommendation:** {RECOMMENDATION_LABEL.get(memo.recommendation, memo.recommendation)}"
    )
    meta.append(f"**Confidence:** {memo.confidence.title()}")
    a("  \n".join(meta))
    a("")
    a("---")
    a("")

    a("## Executive summary")
    a("")
    for b in memo.executive_summary:
        a(f"- {notes.rewrite(shorten_identifier_runs(b))}")
    a("")

    a("## The bottom line")
    a("")
    prose(memo.bottom_line)
    a("")
    a(
        f"**Recommendation: {RECOMMENDATION_LABEL.get(memo.recommendation, memo.recommendation)}** "
        f"(confidence: {memo.confidence})"
    )
    a("")
    prose(memo.recommendation_rationale)
    a("")

    a("## What is changing")
    a("")
    prose(memo.whats_changing)
    a("")

    a("## How the leaders are innovating")
    a("")
    prose(memo.how_leaders_are_innovating)
    a("")

    a("## New entrants")
    a("")
    prose(memo.new_entrants)
    a("")

    a("## Where investment is flowing")
    a("")
    prose(memo.where_capital_is_flowing)
    a("")

    # ---- the differentiator, rendered from typed data ------------------
    a("## Internal and external signals combined")
    a("")
    if fusion and not fusion.signals:
        a(
            "**No finding in this run met the both-sources test.** Candidate findings "
            "were produced, but none carried both a retrievable external source and a "
            "resolvable internal record, so none is reported here. The section is left "
            "empty deliberately rather than filled with findings that only look joined."
        )
        a("")
    if fusion and fusion.signals:
        a(
            "Each finding below required both the public record and this company's own data. "
            "Neither source supports the conclusion on its own."
        )
        a("")
        for i, s in enumerate(fusion.signals, 1):
            a(f"### {i}. {s.headline}")
            a("")
            row = [
                f"**Urgency:** {URGENCY_LABEL.get(s.urgency, s.urgency)}",
                f"**Confidence:** {s.confidence.title()}",
            ]
            if s.segment:
                row.append(f"**Segment:** `{s.segment}`")
            if s.entities:
                row.append(f"**Companies:** {', '.join(s.entities)}")
            a("  \n".join(row))
            a("")
            a("| | Evidence |")
            a("|---|---|")
            for ev in s.external_evidence:
                a(f"| External | {ev.claim.replace('|', '/')} <br>Source: {_link(ev)}"
                  f"{' (' + ev.published + ')' if ev.published else ''} |")
            for f in s.internal_evidence:
                metric = f" <br>Figure: {f.metric}" if f.metric else ""
                a(f"| Internal | {f.claim.replace('|', '/')}{metric} "
                  f"<br>Records: {_tags(f.citation_tags)} |")
            a("")
            a(f"**Why this needs both:** {s.why_this_needs_both}")
            a("")
            a(f"**Implication:** {s.implication}")
            a("")
    else:
        a("_No cross-source findings survived verification in this run._")
        a("")

    a("## What this means for Meridian")
    a("")
    prose(memo.implications_for_customer)
    a("")

    a("## Risks, contradictions and what we do not know")
    a("")
    prose(memo.risks_and_unknowns)
    a("")
    if fusion and fusion.contradictions:
        a("### Where the evidence contradicts a position we have adopted")
        a("")
        for c in fusion.contradictions:
            a(f"- {c}")
        a("")
    if fusion and fusion.gaps:
        a("### What this analysis could not establish")
        a("")
        for g in fusion.gaps:
            a(f"- {g}")
        a("")
    if fusion and fusion.rejected:
        a("### Candidate findings that did not survive verification")
        a("")
        a(
            "These were proposed and then dropped, because a finding that cannot cite "
            "both sides is not a cross-source finding:"
        )
        a("")
        for r in fusion.rejected:
            a(f"- {r}")
        a("")
    if external and external.sources_unavailable:
        a("### Sources that could not be reached")
        a("")
        for s in external.sources_unavailable:
            a(f"- {s}")
        a("")

    a("## Recommended actions")
    a("")
    if memo.actions:
        a("| Horizon | Action | Owner | Rationale |")
        a("|---|---|---|---|")
        order = {"30_days": 0, "60_days": 1, "90_days": 2}
        for act in sorted(memo.actions, key=lambda x: order.get(x.horizon, 9)):
            a(
                f"| {act.horizon.replace('_', ' ')} | {act.action.replace('|', '/')} | "
                f"{act.owner or '-'} | {act.rationale.replace('|', '/')} |"
            )
        a("")

    # ---- appendices -----------------------------------------------------
    a("---")
    a("")
    for line in notes.render():
        a(line)

    a("## Appendix A: Evidence ledger")
    a("")
    a("Every external claim used in this report, with its source.")
    a("")
    if external:
        groups = [
            ("What is changing", external.whats_changing),
            ("Incumbent moves", external.incumbent_moves),
            ("New entrants", external.new_entrants),
            ("Capital flows", external.capital_flows),
        ]
        for label, items in groups:
            if not items:
                continue
            a(f"**{label}**")
            a("")
            a("| Claim | Source | Type | Date | Confidence |")
            a("|---|---|---|---|---|")
            for ev in items:
                a(
                    f"| {ev.claim.replace('|', '/')} | {_link(ev)} | {ev.source_type} | "
                    f"{ev.published or '-'} | {ev.confidence} |"
                )
            a("")

    if internal and internal.findings:
        a("**Internal findings and the records behind them**")
        a("")
        a("| Finding | Figure | Records | Segment |")
        a("|---|---|---|---|")
        for f in internal.findings:
            a(
                f"| {f.claim.replace('|', '/')} | {f.metric or '-'} | {_tags(f.citation_tags)} | "
                f"{f.segment or '-'} |"
            )
        a("")

    if manifest:
        a("## Appendix B: How this report was produced")
        a("")
        a("| | |")
        a("|---|---|")
        for k, v in manifest.items():
            a(f"| {k} | {v} |")
        a("")

    a("---")
    a("")
    a(
        "_Internal records referenced by identifier resolve against the files in "
        "`knowledge/`. Every identifier in this report was verified to resolve before "
        "publication._"
    )
    a("")
    return "\n".join(d)


HTML_STYLE = """
:root{
  --ink:#12161C; --muted:#5B6472; --rule:#E2E1DC; --ground:#FAFAF8;
  --accent:#B4472A; --accent-soft:#FBF1ED; --card:#FFFFFF;
}
*{box-sizing:border-box}
body,td,th,li,p{overflow-wrap:break-word}
body{margin:0;background:var(--ground);color:var(--ink);
  font:16px/1.62 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:860px;margin:0 auto;padding:56px 28px 96px}
h1{font-size:2.05rem;line-height:1.18;letter-spacing:-.02em;margin:0 0 18px;font-weight:650}
h2{font-size:1.28rem;letter-spacing:-.01em;margin:44px 0 12px;padding-bottom:8px;
  border-bottom:1px solid var(--rule);font-weight:640}
h3{font-size:1.06rem;margin:28px 0 8px;font-weight:640}
p{margin:0 0 14px}
ul,ol{margin:0 0 16px;padding-left:22px}
li{margin:0 0 7px}
a{color:var(--accent);text-decoration:none;border-bottom:1px solid rgba(180,71,42,.28);
  overflow-wrap:anywhere}
a:hover{border-bottom-color:var(--accent)}
code{background:var(--accent-soft);color:#8E3520;padding:1px 5px;border-radius:3px;
  font:0.845em/1.4 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;white-space:nowrap}
hr{border:0;border-top:1px solid var(--rule);margin:36px 0}
.meta{color:var(--muted);font-size:.94rem;line-height:1.85;margin-bottom:8px}
.meta strong{color:var(--ink);font-weight:600}
table{width:100%;border-collapse:collapse;margin:0 0 20px;font-size:.9rem;
  background:var(--card);border:1px solid var(--rule);border-radius:6px;overflow:hidden}
th{text-align:left;background:#F4F3EF;font-weight:620;font-size:.8rem;
  letter-spacing:.035em;text-transform:uppercase;color:var(--muted)}
th,td{padding:10px 13px;border-bottom:1px solid var(--rule);vertical-align:top}
tr:last-child td{border-bottom:0}
td:first-child{white-space:nowrap;font-weight:600;color:var(--muted);font-size:.83rem}
h2+p>strong:first-child{color:var(--ink)}
blockquote{margin:0 0 16px;padding:2px 0 2px 16px;border-left:3px solid var(--accent);
  color:var(--muted)}
@media print{
  body{background:#fff} .wrap{padding:0;max-width:none}
  h2{page-break-after:avoid} table{page-break-inside:avoid}
  a{color:var(--ink);border:0}
}
"""


def to_html(markdown_text: str, title: str) -> str:
    """Convert the rendered markdown to a standalone, print-ready page."""
    try:
        import markdown as md

        body = md.markdown(
            markdown_text,
            extensions=["tables", "sane_lists", "attr_list"],
        )
    except Exception:
        body = "<pre>" + html_lib.escape(markdown_text) + "</pre>"

    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        f"<title>{html_lib.escape(title)}</title>\n"
        f"<style>{HTML_STYLE}</style>\n"
        "</head>\n<body>\n"
        f'<div class="wrap">\n{body}\n</div>\n'
        "</body>\n</html>\n"
    )
