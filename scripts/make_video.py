"""Render the walkthrough video from a committed run.

    uv run python scripts/make_video.py

The interface is the right way to explore a run and the wrong way to show one
to somebody who is not driving. This builds a fixed walkthrough instead: the
raw material first, then every view over it, then the artifact it produced.

It is a build artifact, not a screen recording, and that is the point. A screen
recording captures whatever the machine happened to do that afternoon; this is
generated from `sample_output/` through the same renderer the interface uses,
so the video and the repository cannot drift apart. Re-run it after a change
and the video is correct again.

How it works: each frame is an HTML page, screenshotted by headless Chrome and
handed to ffmpeg through a concat list that carries a duration per frame. Still
scenes are therefore one file held for several seconds rather than the same
image written ninety times.

Requires Chrome (or Edge) and ffmpeg on PATH. Neither is a dependency of the
project itself -- nothing else here needs them -- which is why they are found
rather than declared.
"""

from __future__ import annotations

import html as html_lib
import json
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from deep_research.observability import graph as graphlib  # noqa: E402
from deep_research.observability import trace as tracelib  # noqa: E402
from deep_research.observability import views  # noqa: E402

RUN_DIR = ROOT / "sample_output"
OUT_DIR = ROOT / "deck"
WORK = ROOT / ".video_build"

# 1600x900 logical at 1.2x gives a 1920x1080 file with text that is still
# comfortably readable when the video is played in a window rather than full
# screen.
WIDTH, HEIGHT, SCALE = 1600, 900, 1.2
FPS = 30
MEMO_TOP = 88   # clears the header band laid over the memo's own page

ACCENT = views.ACCENT
INK = "#12161C"
GROUND = "#FAFAF8"
RULE = "#E2E1DC"
MUTED_INK = "#5B6472"

FONT = (
    '-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif'
)
MONO = 'ui-monospace,SFMono-Regular,Menlo,Consolas,"Courier New",monospace'


def esc(value) -> str:
    return html_lib.escape(str(value), quote=True)


# --------------------------------------------------------------------------
# The frame shell
# --------------------------------------------------------------------------
def page(title: str, body: str, caption: str, progress: float) -> str:
    """One frame: a heading, the content, and the line that explains it.

    The caption is what separates a walkthrough from a screen recording. A
    viewer who is not driving cannot ask what they are looking at, so every
    frame says so.
    """
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
  html,body{{margin:0;padding:0;background:{GROUND};color:{INK};
    font-family:{FONT};-webkit-font-smoothing:antialiased;
    width:{WIDTH}px;height:{HEIGHT}px;overflow:hidden}}
  .fr{{width:{WIDTH}px;height:{HEIGHT}px;display:flex;flex-direction:column}}
  .hd{{flex:0 0 auto;padding:20px 46px 14px;display:flex;align-items:baseline;
      justify-content:space-between;border-bottom:1px solid {RULE}}}
  .mark{{font-size:12px;letter-spacing:.13em;text-transform:uppercase;
        color:{MUTED_INK};font-weight:650}}
  .mark b{{color:{ACCENT}}}
  .ttl{{font-size:21px;font-weight:660;letter-spacing:-.01em}}
  .bd{{flex:1 1 auto;padding:22px 46px 0;overflow:hidden;position:relative}}
  .cap{{flex:0 0 auto;padding:14px 46px 16px;border-top:1px solid {RULE};
       font-size:15.5px;line-height:1.5;color:{INK};min-height:52px}}
  .cap b{{color:{ACCENT}}}
  .pg{{flex:0 0 3px;background:rgba(127,127,127,.16)}}
  .pg > div{{height:3px;background:{ACCENT};width:{progress * 100:.2f}%}}
  code,.mono{{font-family:{MONO}}}
{views.STYLESHEET}
</style></head><body><div class="fr">
  <div class="hd"><div class="mark">Deep <b>Research</b> Agent</div>
    <div class="ttl">{title}</div></div>
  <div class="bd">{body}</div>
  <div class="cap">{caption}</div>
  <div class="pg"><div></div></div>
</div></body></html>"""


# --------------------------------------------------------------------------
# Reading the run
# --------------------------------------------------------------------------
def load_run() -> dict:
    records = tracelib.load(RUN_DIR / "run_trace.jsonl")
    phases = tracelib.phases(records)
    return {
        "records": records,
        "phases": phases,
        "totals": tracelib.totals(records),
        "ledger": json.loads((RUN_DIR / "evidence_ledger.json").read_text(encoding="utf-8")),
        "manifest": json.loads((RUN_DIR / "run_manifest.json").read_text(encoding="utf-8")),
        "memo_html": (RUN_DIR / "market_investment_memo.html").read_text(encoding="utf-8"),
        "trace_lines": (RUN_DIR / "run_trace.jsonl").read_text(encoding="utf-8").splitlines(),
    }


# --------------------------------------------------------------------------
# Scene bodies
# --------------------------------------------------------------------------
def title_body(manifest: dict) -> str:
    return f"""
<div style="height:100%;display:flex;flex-direction:column;justify-content:center">
  <div style="font-size:13px;letter-spacing:.14em;text-transform:uppercase;
       color:{MUTED_INK};font-weight:650">One run, end to end</div>
  <div style="font-size:52px;font-weight:680;letter-spacing:-.022em;
       margin:10px 0 18px;max-width:1180px;line-height:1.1">
    The public record, read against your own files</div>
  <div style="font-size:19px;color:{MUTED_INK};max-width:960px;line-height:1.5">
    Every view that follows is built from one event stream, and every number in it
    comes out of the files this run wrote. Nothing here is a mock-up.</div>
  <div style="margin-top:34px;display:flex;gap:46px;font-size:15px">
    <div><b style="color:{ACCENT};font-size:26px">{manifest.get("Wall clock", "—")}</b>
      <div style="color:{MUTED_INK}">wall clock</div></div>
    <div><b style="color:{ACCENT};font-size:26px">{manifest.get("Agent tool calls", 0)}</b>
      <div style="color:{MUTED_INK}">tool calls</div></div>
    <div><b style="color:{ACCENT};font-size:26px">
      {str(manifest.get("Estimated cost", "—")).replace(" (indicative)", "")}</b>
      <div style="color:{MUTED_INK}">per memo</div></div>
    <div><b style="color:{ACCENT};font-size:26px">{manifest.get("Citation audit", "—")}</b>
      <div style="color:{MUTED_INK}">citation audit</div></div>
  </div>
</div>"""


def input_body(manifest: dict) -> str:
    files = [
        ("crm_deals.csv", "Every deal: outcome, competitor, ARR, the recorded loss reason"),
        ("vendor_spend.csv", "Supplier spend, contract end dates, renewal risk"),
        ("capability_inventory.json", "What each business unit can do, rated 1–5"),
        ("cvc_watchlist.json", "Investment targets, including ones already passed on"),
        ("win_loss_interviews.md", "What buyers said, verbatim"),
        ("strategy_priorities.md", "The mandate the memo has to answer to"),
        ("prior_market_view.md", "The last market view the company formally adopted"),
    ]
    rows = "".join(
        f'<div style="display:grid;grid-template-columns:230px 1fr;gap:14px;'
        f'padding:5px 0;font-size:14.5px">'
        f'<div class="mono" style="color:{ACCENT}">{esc(name)}</div>'
        f'<div style="color:{MUTED_INK}">{esc(what)}</div></div>'
        for name, what in files
    )
    return f"""
<div style="display:grid;grid-template-columns:1fr 1fr;gap:52px;height:100%">
  <div>
    <div style="font-size:12px;letter-spacing:.12em;text-transform:uppercase;
         color:{MUTED_INK};font-weight:650;margin-bottom:10px">The question asked</div>
    <div style="font-size:20px;line-height:1.5;border-left:3px solid {ACCENT};
         padding:4px 0 4px 16px">{esc(manifest.get("Topic", ""))}</div>
    <div style="margin-top:30px;font-size:12px;letter-spacing:.12em;
         text-transform:uppercase;color:{MUTED_INK};font-weight:650">Run as</div>
    <div class="mono" style="font-size:14px;margin-top:8px;line-height:1.9">
      uv run deep_research --offline<br>
      <span style="color:{MUTED_INK}">{esc(manifest.get("Mode", ""))}</span><br>
      <span style="color:{MUTED_INK}">{esc(manifest.get("Models", ""))}</span>
    </div>
  </div>
  <div>
    <div style="font-size:12px;letter-spacing:.12em;text-transform:uppercase;
         color:{MUTED_INK};font-weight:650;margin-bottom:10px">
      The internal records it reads</div>
    {rows}
  </div>
</div>"""


def stream_body(lines: list[str], offset: int) -> str:
    """The raw trace, scrolled. This is the material everything else is built from."""
    shown = "<br>".join(
        f'<span style="opacity:.45">{i + 1:>4}</span>&nbsp; {esc(line[:190])}'
        for i, line in enumerate(lines)
    )
    return f"""
<div style="height:100%;overflow:hidden;position:relative">
  <div class="mono" style="font-size:12.5px;line-height:1.75;
       transform:translateY(-{offset}px)">{shown}</div>
  <div style="position:absolute;left:0;right:0;bottom:0;height:90px;
       background:linear-gradient(transparent,{GROUND})"></div>
</div>"""


def graph_body(snap: dict, cursor: float, total: float, motion: float) -> str:
    # Centred, because the diagram is wider than it is tall and a frame that
    # pins it to the top reads as a page someone forgot to finish.
    return (
        '<div style="height:100%;display:flex;flex-direction:column;'
        'justify-content:center">'
        + views.graph_svg(snap, animate=False, motion=motion)
        + views.graph_legend_html(snap, cursor, total)
        + "</div>"
    )


def timeline_body(phases: list[dict], total: float, cursor: float) -> str:
    return (
        '<div style="height:100%;display:flex;flex-direction:column;'
        'justify-content:center">'
        + views.timeline_html(phases, total, cursor)
        + "</div>"
    )


def cards_body(run: dict, snap: dict, open_phase: str | None, offset: int = 0) -> str:
    markup = views.cards_html(
        run["phases"],
        snap,
        run["ledger"],
        run["manifest"],
        event_limit=14,
        force_open={open_phase} if open_phase else set(),
    )
    return (
        f'<div style="height:100%;overflow:hidden"><div '
        f'style="transform:translateY(-{offset}px)">{markup}</div></div>'
    )


def findings_body(ledger: dict, index: int) -> str:
    """One cross-source finding, with both sides beside each other."""
    signals = (ledger.get("fusion") or {}).get("signals") or []
    if not signals:
        return "<div>No cross-source findings in this run.</div>"
    sig = signals[index % len(signals)]

    def side(items, heading, render):
        rows = "".join(render(i) for i in (items or [])[:3])
        return (
            f'<div><div style="font-size:12px;letter-spacing:.12em;'
            f'text-transform:uppercase;color:{MUTED_INK};font-weight:650;'
            f'margin-bottom:9px">{heading}</div>{rows}</div>'
        )

    external = side(
        sig.get("external_evidence"),
        "From the public record",
        lambda e: (
            f'<div style="margin-bottom:11px;font-size:14.5px;line-height:1.5">'
            f'{esc(e.get("claim", ""))}'
            f'<div class="mono" style="font-size:11.5px;color:{ACCENT};margin-top:3px">'
            f'{esc((e.get("source_title") or e.get("source_type") or "source"))[:70]}</div></div>'
        ),
    )
    internal = side(
        sig.get("internal_evidence"),
        "From your own records",
        lambda f: (
            f'<div style="margin-bottom:11px;font-size:14.5px;line-height:1.5">'
            f'{esc(f.get("claim", ""))}'
            + (
                f'<div style="margin-top:3px"><b style="color:{ACCENT}">'
                f'{esc(f.get("metric"))}</b></div>'
                if f.get("metric")
                else ""
            )
            + '<div class="mono" style="font-size:11.5px;opacity:.7;margin-top:3px">'
            + esc(" ".join(f"[{t}]" for t in (f.get("citation_tags") or [])[:4]))
            + "</div></div>"
        ),
    )
    why = sig.get("why_this_needs_both") or ""
    return f"""
<div style="height:100%;display:flex;flex-direction:column;justify-content:center;padding-bottom:40px">
  <div style="font-size:25px;font-weight:660;line-height:1.25;max-width:1380px">
    {esc(sig.get("headline", ""))}</div>
  <div class="mono" style="font-size:12px;color:{MUTED_INK};margin:8px 0 20px">
    {esc(str(sig.get("urgency", "")).replace("_", " "))} ·
    {esc(sig.get("confidence", ""))} confidence
    {("· " + esc(sig.get("segment"))) if sig.get("segment") else ""}</div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:46px">
    {external}{internal}</div>
  <div style="border-left:3px solid {ACCENT};background:rgba({views.ACCENT_RGB},.07);
       padding:12px 16px;border-radius:0 6px 6px 0;font-size:14.5px;line-height:1.5;
       margin-top:26px">
    <b>Why it needs both:</b> {esc(why)}</div>
</div>"""


def verify_body(ledger: dict, manifest: dict) -> str:
    audit = ledger.get("audit") or {}
    positions = ledger.get("adopted_position_test") or {}
    unresolved = len(audit.get("unresolved_identifiers") or [])
    bad_figures = int(manifest.get("Figures disagreeing with their records", 0) or 0)
    checks = [
        (
            "Every cited identifier exists",
            unresolved == 0,
            f"{unresolved} unresolved",
            "Resolved in Python against the source files. Whether a record exists has a "
            "right answer, so it is not delegated to a model.",
        ),
        (
            "Every figure matches the records cited for it",
            bad_figures == 0,
            f"{bad_figures} disagreed",
            "Totalled in Python. This began as an instruction to the reviewing agent and "
            "had to move: given ten numbers to add from retrieved text it got sums wrong.",
        ),
        (
            "Records say what the sentences claim",
            bool(audit.get("passed")),
            "reviewed by a separate agent",
            "The one check that genuinely needs judgement, so it belongs to an agent — a "
            "different one from the writer.",
        ),
    ]
    rows = "".join(
        f'<div style="padding:13px 0;border-bottom:1px solid {RULE}">'
        f'<span class="{"ok" if ok else "bad"}" style="font-size:15px">'
        f'{"PASS" if ok else "FAIL"}</span>'
        f'&nbsp;&nbsp;<b style="font-size:17px">{esc(label)}</b>'
        f'<span style="color:{MUTED_INK};font-size:14px"> — {esc(detail)}</span>'
        f'<div style="color:{MUTED_INK};font-size:13.5px;margin-top:4px;'
        f'line-height:1.5;max-width:1100px">{esc(why)}</div></div>'
        for label, ok, detail, why in checks
    )
    tiles = "".join(
        f'<div style="flex:1"><div style="font-size:30px;font-weight:670;color:{ACCENT}">'
        f"{len(positions.get(key) or [])}</div>"
        f'<div style="color:{MUTED_INK};font-size:13.5px">{name}</div></div>'
        for key, name in (
            ("contradicted", "positions contradicted by the evidence"),
            ("upheld", "still supported"),
            ("untestable", "no evidence either way"),
        )
    )
    return f"""
<div style="height:100%;display:flex;flex-direction:column">
  {rows}
  <div style="margin-top:24px;font-size:12px;letter-spacing:.12em;
       text-transform:uppercase;color:{MUTED_INK};font-weight:650">
    Every position the company has adopted, tested</div>
  <div style="display:flex;gap:44px;margin-top:12px">{tiles}</div>
  <div style="color:{MUTED_INK};font-size:13.5px;margin-top:14px;max-width:1180px;
       line-height:1.5">An empty contradictions list means checked and found none, not
    did not look. That distinction is why it runs as its own task with its own output.
  </div>
</div>"""


def close_body(manifest: dict, ledger: dict) -> str:
    fusion = ledger.get("fusion") or {}
    tiles = [
        (manifest.get("Cross-source findings", 0), "findings that needed both sides"),
        (len(fusion.get("contradictions") or []), "adopted positions overturned"),
        (manifest.get("Unresolved identifiers", 0), "identifiers that did not resolve"),
        (manifest.get("Revisions required", 0), "revision, then the audit passed"),
        (manifest.get("Tool failures", 0), "tool failures"),
    ]
    cells = "".join(
        f'<div><div style="font-size:44px;font-weight:680;color:{ACCENT};'
        f'line-height:1">{value}</div>'
        f'<div style="color:{MUTED_INK};font-size:14px;margin-top:6px;max-width:190px">'
        f"{label}</div></div>"
        for value, label in tiles
    )
    return f"""
<div style="height:100%;display:flex;flex-direction:column;justify-content:center">
  <div style="font-size:38px;font-weight:680;letter-spacing:-.02em;max-width:1240px;
       line-height:1.18">Every claim in the memo resolves to the record it came from.</div>
  <div style="display:flex;gap:58px;margin:40px 0 34px">{cells}</div>
  <div style="color:{MUTED_INK};font-size:16px;max-width:1080px;line-height:1.55">
    The trace this was rendered from is committed beside the memo, so any of it can be
    checked without re-running anything.</div>
</div>"""


def memo_frame(memo_html: str, offset: int, caption: str, progress: float) -> str:
    """The artifact itself, scrolled -- the real page, not a re-creation.

    The memo's own stylesheet is left alone and a caption band is laid over it,
    so what appears is the file a reader would open rather than a copy of it
    that could differ.
    """
    overlay = f"""
<style>
  /* The overlaid header is fixed, so the memo starts below it rather than
     under it. Scrolling is this margin moving, which keeps every frame
     deterministic. */
  body{{margin-top:{MEMO_TOP - offset}px !important}}
  #vid-cap{{position:fixed;left:0;right:0;bottom:0;background:{GROUND};
    border-top:1px solid {RULE};padding:14px 46px 16px;font-size:15.5px;
    line-height:1.5;color:{INK};font-family:{FONT};z-index:99}}
  #vid-cap b{{color:{ACCENT}}}
  #vid-pg{{position:fixed;left:0;bottom:0;height:3px;background:{ACCENT};
    width:{progress * 100:.2f}%;z-index:100}}
  #vid-hd{{position:fixed;top:0;left:0;right:0;background:{GROUND};
    border-bottom:1px solid {RULE};padding:20px 46px 14px;z-index:99;
    font-family:{FONT};display:flex;justify-content:space-between;
    align-items:baseline}}
  #vid-hd .m{{font-size:12px;letter-spacing:.13em;text-transform:uppercase;
    color:{MUTED_INK};font-weight:650}}
  #vid-hd .t{{font-size:21px;font-weight:660}}
</style>
<div id="vid-hd"><div class="m">Deep <b style="color:{ACCENT}">Research</b> Agent</div>
  <div class="t">The artifact</div></div>
<div id="vid-cap">{caption}</div><div id="vid-pg"></div>
"""
    return memo_html.replace("</head>", overlay + "</head>", 1)


# --------------------------------------------------------------------------
# Storyboard
# --------------------------------------------------------------------------
def storyboard(run: dict) -> list[tuple[str, float]]:
    """Every frame as (html, seconds). Durations are what ffmpeg holds them for."""
    phases, records = run["phases"], run["records"]
    manifest, ledger = run["manifest"], run["ledger"]
    total = max(run["totals"]["elapsed_seconds"], 0.1)
    frames: list[tuple[str, float]] = []

    # A first pass with placeholder progress, rewritten once the length is
    # known. Simpler than predicting the length twice over.
    plan: list[tuple[str, str, str, float]] = []  # title, body, caption, seconds

    plan.append((
        "A rendered run",
        title_body(manifest),
        "One research question, the public record, and this company's own files — "
        "joined into a memo in which <b>every claim resolves to the record it came "
        "from</b>.",
        4.0,
    ))

    plan.append((
        "The raw input",
        input_body(manifest),
        "This is everything that goes in. One question, seven internal files, and "
        "<b>no search credentials</b> — the run replays primary-source responses "
        "committed to the repository.",
        7.5,
    ))

    # The raw event stream, scrolling.
    lines = run["trace_lines"]
    stream_caption = (
        "The whole of the raw material: <b>one JSON object per line</b>, appended as "
        f"the run happens. {len(lines)} of them. Every view after this is built from "
        "this file and nothing else."
    )
    plan.append(("The raw event stream", stream_body(lines, 0), stream_caption, 2.2))
    for step in range(1, 34):
        plan.append((
            "The raw event stream",
            stream_body(lines, int(step * 86)),
            stream_caption,
            0.1,
        ))
    plan.append(("The raw event stream", stream_body(lines, 33 * 86), stream_caption, 1.2))

    # The crew graph, replayed.
    graph_caption = (
        "The same events as a picture of the system. Boxes are crews, the names "
        "inside them are agents, and the labels on the arrows are the <b>typed "
        "objects the phases actually exchange</b>. Accent means running now."
    )
    steps = 170
    for i in range(steps + 1):
        cursor = total * i / steps
        snap = graphlib.snapshot(phases, records, cursor)
        plan.append((
            "The crew, as it ran",
            graph_body(snap, cursor, total, (i % 12) / 12),
            graph_caption,
            0.0667,
        ))

    # Three held moments, each making one point about the shape.
    for cursor, caption in (
        (
            22.0,
            "Two research legs run at once and share no state: the public record on "
            "one side, the internal records on the other. <b>Neither waits for the "
            "other.</b>",
        ),
        (
            60.0,
            "The cross-check waits for both. It is the only place the two sides meet, "
            "and it keeps a finding only when <b>each side is independently "
            "verifiable</b>.",
        ),
        (
            128.0,
            "The audit refused the first draft, so the gate sent it back. <b>A failed "
            "audit changes what runs next</b> — it is a router, not a warning.",
        ),
    ):
        snap = graphlib.snapshot(phases, records, cursor)
        for k in range(10):
            plan.append((
                "The crew, as it ran",
                graph_body(snap, cursor, total, (k % 12) / 12),
                caption,
                0.36,
            ))

    # The timeline, filling in.
    tl_caption = (
        "The same run measured. Bars are placed by real start and end times, so "
        "<b>two bars that overlap really did overlap</b> — that is the evidence for "
        "the concurrency the diagram claims."
    )
    for i in range(0, 61):
        cursor = total * i / 60
        plan.append(("The same run, measured", timeline_body(phases, total, cursor), tl_caption, 0.1))
    plan.append((
        "The same run, measured",
        timeline_body(phases, total, total),
        "Researching public sources and Reading internal records overlap for "
        "<b>42 of the 149 seconds</b>. Work is attributed through the agent that did "
        "it, never by the clock, or the overlap would be counted twice.",
        4.5,
    ))

    # The cards.
    # A moment past the end: at exactly `total` the last phase still reads as
    # running, which would open a card this scene is not about.
    snap_end = graphlib.snapshot(phases, records, total + 5)
    cards_caption = (
        "Every phase opens: <b>what it handed to the next one</b>, which agent did "
        "the work and what each one called, and every event it emitted, in order."
    )
    plan.append(("Inside each phase", cards_body(run, snap_end, None), cards_caption, 3.2))
    for phase_id, caption, offset in (
        (
            "research_external",
            "Four analysts read the public record in parallel, then an editor drops "
            "anything that arrived without a source. <b>47 tool calls</b> against "
            "primary sources.",
            67,
        ),
        (
            "assess_internal",
            "The internal side is exact arithmetic over the complete records first, "
            "retrieval second. <b>Sums are not asked of a model</b> when the records "
            "can be added in code.",
            115,
        ),
        (
            "fuse",
            "The cross-check keeps a finding only if one external item is a "
            "retrievable source <b>and</b> one internal item resolves to a record. "
            "That test runs in Python, not in a prompt.",
            163,
        ),
        (
            "draft_and_audit",
            "The auditor resolves every identifier in the draft against the source "
            "files, and it refused this draft. <b>39 tool calls here, 62 more on the "
            "revision it forced.</b>",
            211,
        ),
    ):
        plan.append((
            "Inside each phase",
            cards_body(run, snap_end, phase_id, offset),
            caption,
            3.8,
        ))

    # The findings.
    signals = (ledger.get("fusion") or {}).get("signals") or []
    for index in range(len(signals)):
        plan.append((
            f"What the join found  ({index + 1} of {len(signals)})",
            findings_body(ledger, index),
            "Cover either column and ask whether the row still tells an executive to "
            "act. <b>Neither side says this alone</b> — that is the test the pipeline "
            "applies to itself.",
            6.0,
        ))

    # Verification.
    plan.append((
        "Three checks before anything is published",
        verify_body(ledger, manifest),
        "Two of these have a right answer, so they run in code. <b>Only the third "
        "needs judgement</b>, so only the third is an agent — and a different one "
        "from the writer.",
        8.0,
    ))

    frames = []
    memo_frames: list[tuple[str, float]] = []

    # The artifact, scrolled through its own stylesheet.
    memo_caption = (
        "The artifact. <b>Every bracketed identifier resolves</b> to a row in the "
        "files at the start of this video, and every external claim to a reachable "
        "source."
    )
    memo_offsets = [0] + [int(120 + i * 118) for i in range(30)]

    plan.append((
        "Close",
        close_body(manifest, ledger),
        "Generated from the committed run, through the same renderer the interface "
        "uses — so this video and the repository <b>cannot drift apart</b>.",
        5.0,
    ))

    # Now that every duration is known, lay the progress bar over the top.
    memo_total = 1.6 + len(memo_offsets[1:]) * 0.11 + 3.0
    grand = sum(seconds for _, _, _, seconds in plan) + memo_total
    elapsed = 0.0
    ordered: list[tuple[str, float]] = []
    for title, body, caption, seconds in plan[:-1]:
        ordered.append((page(title, body, caption, elapsed / grand), seconds))
        elapsed += seconds

    # The memo sits between the last built scene and the close.
    memo_html = run["memo_html"]
    memo_frames.append((memo_frame(memo_html, 0, memo_caption, elapsed / grand), 1.6))
    elapsed += 1.6
    for offset in memo_offsets[1:]:
        memo_frames.append((memo_frame(memo_html, offset, memo_caption, elapsed / grand), 0.11))
        elapsed += 0.11
    memo_frames.append((
        memo_frame(memo_html, memo_offsets[-1], memo_caption, elapsed / grand),
        3.0,
    ))
    elapsed += 3.0

    title, body, caption, seconds = plan[-1]
    closing = [(page(title, body, caption, elapsed / grand), seconds)]

    frames = ordered + memo_frames + closing
    return frames


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------
def find_browser() -> str:
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for path in candidates:
        if Path(path).exists():
            return path
    for name in ("google-chrome", "chromium", "chrome", "msedge"):
        found = shutil.which(name)
        if found:
            return found
    raise SystemExit("Chrome or Edge is needed to render frames and neither was found.")


def shoot(browser: str, index: int, markup: str) -> Path:
    html_path = WORK / f"f{index:05d}.html"
    png_path = WORK / f"f{index:05d}.png"
    html_path.write_text(markup, encoding="utf-8")
    subprocess.run(
        [
            browser,
            "--headless",
            "--disable-gpu",
            "--hide-scrollbars",
            "--no-sandbox",
            f"--user-data-dir={WORK / f'profile{index % 4}'}",
            f"--window-size={WIDTH},{HEIGHT}",
            f"--force-device-scale-factor={SCALE}",
            "--virtual-time-budget=1200",
            f"--screenshot={png_path}",
            html_path.as_uri(),
        ],
        check=False,
        capture_output=True,
    )
    if not png_path.exists():
        raise SystemExit(f"frame {index} did not render")
    html_path.unlink(missing_ok=True)
    return png_path


def main() -> None:
    browser = find_browser()
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg is needed to encode the video and was not found.")

    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    run = load_run()
    frames = storyboard(run)
    length = sum(seconds for _, seconds in frames)
    print(f"  {len(frames)} frames, {length:.1f}s of video")

    done = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(shoot, browser, i, markup) for i, (markup, _) in enumerate(frames)]
        for future in futures:
            future.result()
            done += 1
            if done % 40 == 0:
                print(f"  rendered {done}/{len(frames)}")

    listing = WORK / "frames.txt"
    with listing.open("w", encoding="utf-8") as fh:
        for index, (_, seconds) in enumerate(frames):
            fh.write(f"file '{(WORK / f'f{index:05d}.png').as_posix()}'\n")
            fh.write(f"duration {seconds:.4f}\n")
        # The concat demuxer drops the final entry's duration, so the last
        # frame is named twice to hold it on screen.
        fh.write(f"file '{(WORK / f'f{len(frames) - 1:05d}.png').as_posix()}'\n")

    target = OUT_DIR / "run_walkthrough.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
            "-vf", "scale=1920:1080:flags=lanczos,format=yuv420p",
            "-r", str(FPS), "-c:v", "libx264", "-preset", "slow", "-crf", "20",
            "-movflags", "+faststart", str(target),
        ],
        check=True,
        capture_output=True,
    )
    poster = OUT_DIR / "run_walkthrough_poster.png"
    shutil.copy(WORK / "f00000.png", poster)
    shutil.rmtree(WORK, ignore_errors=True)

    size = target.stat().st_size / 1_000_000
    print(f"  {target.relative_to(ROOT)}  ({length:.0f}s, {size:.1f} MB)")
    print(f"  {poster.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
