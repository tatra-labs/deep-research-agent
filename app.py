"""Demonstration interface.

    uv run streamlit run app.py

The terminal output is fine for an engineer and wrong for the audience this is
built for. A VP watching a research run needs to see what the system did, why
each finding survived, and where the time and money went -- not follow a
scrolling log.

Two decisions shape this file.

**Everything is rendered from the run's own files.** The memo, the evidence
ledger, the manifest and the trace are read off disk rather than from live
objects, so a finished run and one from last week render through exactly the
same code. That means the interface cannot flatter a run: what is shown here is
what a reviewer would find in the repository. It also means the committed
sample opens immediately, with nothing to run and nothing to spend.

**The trace is a first-class view, not a debug panel.** It is the answer to the
first question anyone sensible asks -- "how do I know it actually did that" --
so it gets a timeline, per-phase attribution, and the verification events
called out rather than buried in a log.
"""

from __future__ import annotations

import html
import json
import threading
import time
from pathlib import Path

import streamlit as st


def esc(value) -> str:
    """Everything interpolated into markup goes through here.

    Tool arguments and model output end up on this page, and both can contain
    angle brackets. Escaping at the boundary rather than trusting the source
    is the only version of this that stays true as the sources change.
    """
    return html.escape(str(value), quote=True)


PROJECT_ROOT = Path(__file__).resolve().parent

st.set_page_config(
    page_title="Deep Research Agent",
    page_icon="\U0001f9ed",
    layout="wide",
    initial_sidebar_state="expanded",
)

# The only colours this file states outright. Page background and text colour
# belong to .streamlit/config.toml; setting them here as well is what broke
# the interface, because the two could then disagree and one of them wins
# silently. Everything below is a foreground colour chosen to stay legible on
# either a light or a dark page, and each is checked against both.
ACCENT = "#B4472A"   # the one accent, shared with the report and the deck
MUTED = "#5B6472"    # used only where a bar needs a colour, never for text
# Chosen to clear 4:1 against both a light and a dark page (4.07 / 4.44 and
# 4.28 / 4.23), because the viewer can switch theme in Streamlit's settings
# and that choice outranks the config file.
OK = "#2F8A63"       # verdict green
BAD = "#C9503D"      # verdict red

# The same two colours as channel triples, for the translucent fills below. A
# fill has to be translucent rather than a flat colour, because the page
# underneath it may be light or dark and this file no longer assumes which.
ACCENT_RGB = "180,71,42"
BAD_RGB = "201,80,61"

# Two ways of looking at the same run, plus both at once. They share a cursor,
# so switching never loses the reader's place -- which is the point of offering
# a choice rather than replacing one view with the other.
VIEW_MODES = ["Timeline & cards", "Crew graph", "Both"]

# One colour per kind of work. Muted slate for the two research legs, the
# accent reserved for the steps that make this system different from a market
# summary: the cross-check and the verification.
PHASE_COLOUR = {
    "intake": "#B9BDC4",
    "plan": "#8D95A0",
    "research_external": "#5C7A99",
    "assess_internal": "#4E8579",
    "fuse": ACCENT,
    "draft_and_audit": "#8A6A9E",
    "audit_gate": "#C08A5A",
    "revise": "#C08A5A",
    "after_revision": "#C08A5A",
    "publish": "#6E8B5A",
}

st.markdown(
    f"""
<style>
  /* Nothing here sets a page background or a text colour.
     .streamlit/config.toml owns those, and the two disagreeing is what made
     this interface unreadable: the stylesheet painted a light page while
     Streamlit, following the viewer's preference, kept colouring its own text
     for a dark one. Near-white on off-white is a contrast ratio of 1.00:1 --
     invisible until a hover style happened to override it.

     So these rules inherit the text colour and express every surface as a
     translucent overlay. A card is "slightly lighter or darker than whatever
     is behind it" rather than "white", which is true in either theme and
     stays true if someone switches theme in Streamlit's own settings menu,
     where the choice is stored in the browser and outranks the config file. */

  .block-container {{ padding-top: 2.1rem; max-width: 1240px; }}
  h1, h2, h3 {{ letter-spacing: -0.01em; }}

  .dr-sub {{ font-size: 1.02rem; margin: -.5rem 0 1.2rem; opacity: .72; }}
  .dr-card {{ background: rgba(127,127,127,.07);
             border: 1px solid rgba(127,127,127,.26);
             border-radius: 8px; padding: 13px 17px; margin-bottom: 12px; }}
  .dr-kpi {{ font-size: 1.5rem; font-weight: 650; color: {ACCENT}; line-height: 1.15; }}
  .dr-kpi-label {{ font-size: .72rem; text-transform: uppercase; letter-spacing: .05em;
                  opacity: .72; }}
  .dr-kpi-note {{ font-size: .74rem; opacity: .72; margin-top: 2px; }}

  /* timeline */
  .tl {{ background: rgba(127,127,127,.07); border: 1px solid rgba(127,127,127,.26);
        border-radius: 8px; padding: 16px 18px 8px; }}
  .tl-row {{ display:grid; grid-template-columns: 230px 1fr 96px; align-items:center;
            gap:12px; margin-bottom:7px; }}
  .tl-label {{ font-size:.82rem; text-align:right; line-height:1.25; }}
  .tl-track {{ position:relative; height:19px; background:rgba(127,127,127,.2);
              border-radius:4px; }}
  .tl-bar {{ position:absolute; top:0; height:19px; border-radius:4px; min-width:3px; }}
  .tl-dur {{ font-size:.78rem; opacity:.72;
            font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }}
  .tl-scale {{ display:grid; grid-template-columns: 230px 1fr 96px; gap:12px;
              font-size:.7rem; opacity:.72; margin-top:2px; }}
  .tl-ticks {{ display:flex; justify-content:space-between; }}
  .tl-legend {{ font-size:.74rem; opacity:.78; margin-top:10px; }}

  .ev {{ font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
        font-size:.78rem; line-height:1.6; opacity:.9; }}
  .ev-t {{ opacity:.7; }}

  /* Verdicts carry the word PASS or FAIL as well as a colour, so they stay
     readable to anyone who cannot separate these two hues. */
  .ok {{ color:{OK}; font-weight:700; }}
  .bad {{ color:{BAD}; font-weight:700; }}

  /* Step cards.
     Built on <details> rather than Streamlit expanders so that one renderer
     serves both a finished run and a run in flight. The live view redraws
     itself every second; a Streamlit expander loses its open state each time,
     where a <details> element is ordinary browser state and survives. */
  .dr-steps {{ display:flex; flex-direction:column; gap:7px; }}
  .dr-step {{ border:1px solid rgba(127,127,127,.26); border-radius:8px;
             background:rgba(127,127,127,.05); overflow:hidden; }}
  .dr-step > summary {{ list-style:none; cursor:pointer; padding:9px 14px;
                       display:grid; grid-template-columns:56px 11px 1fr auto;
                       gap:11px; align-items:center; }}
  .dr-step > summary::-webkit-details-marker {{ display:none; }}
  .dr-step > summary::marker {{ content:""; }}
  .dr-step > summary:hover {{ background:rgba(127,127,127,.09); }}
  .dr-step[open] > summary {{ border-bottom:1px solid rgba(127,127,127,.22); }}
  .dr-step.now {{ border-color:{ACCENT}; box-shadow:0 0 0 1px rgba({ACCENT_RGB},.55) inset; }}
  .dr-step.later {{ opacity:.4; }}
  .st-time {{ font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
             font-size:.72rem; opacity:.62; text-align:right; }}
  .st-dot {{ width:11px; height:11px; border-radius:3px; }}
  .st-name {{ font-weight:620; font-size:.92rem; }}
  .st-crew {{ font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
             font-size:.68rem; opacity:.58; margin-left:9px; }}
  .st-stats {{ font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
              font-size:.71rem; opacity:.66; white-space:nowrap; }}
  .st-body {{ padding:11px 16px 14px; font-size:.85rem; }}
  .st-sec {{ margin-top:11px; }}
  .st-h {{ font-size:.67rem; text-transform:uppercase; letter-spacing:.06em;
          opacity:.6; margin-bottom:4px; }}
  .st-pay {{ border-left:3px solid {ACCENT}; padding:7px 0 7px 11px;
            background:rgba({ACCENT_RGB},.07); border-radius:0 5px 5px 0; }}
  .st-pay code {{ font-size:.78rem; }}
  .st-agent {{ display:grid; grid-template-columns:210px 1fr; gap:12px;
              padding:2px 0; font-size:.82rem; align-items:baseline; }}
  .st-mono {{ font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
             font-size:.72rem; opacity:.72; }}
  .st-ev {{ font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
           font-size:.71rem; line-height:1.6; opacity:.85;
           max-height:290px; overflow:auto; }}

  /* The crew graph. Scrolls sideways below ~900px rather than shrinking the
     labels to the point of uselessness. */
  .dr-graph {{ border:1px solid rgba(127,127,127,.26); border-radius:8px;
              background:rgba(127,127,127,.05); padding:8px 8px 2px;
              overflow-x:auto; }}
  .dr-graph svg {{ width:100%; height:auto; min-width:940px; display:block; }}
  @keyframes dr-pulse {{ 0%,100% {{ opacity:.5; }} 50% {{ opacity:0; }} }}
  .g-halo {{ animation:dr-pulse 1.7s ease-in-out infinite; }}
  @keyframes dr-dash {{ to {{ stroke-dashoffset:-20; }} }}
  .g-flight {{ animation:dr-dash .85s linear infinite; }}
  @media (prefers-reduced-motion: reduce) {{
    .g-halo, .g-flight {{ animation:none; }}
  }}
  .g-legend {{ font-size:.74rem; opacity:.75; margin-top:8px; }}
  .g-swatch {{ display:inline-block; width:10px; height:10px; border-radius:3px;
              vertical-align:-1px; margin-right:4px; }}
</style>
""",
    unsafe_allow_html=True,
)


def load_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env")
    except Exception:
        pass


load_env()

from deep_research import llms  # noqa: E402
from deep_research.observability import graph as graphlib  # noqa: E402
from deep_research.observability import trace as tracelib  # noqa: E402

OUTPUT_DIR = PROJECT_ROOT / "output"
SAMPLE_DIR = PROJECT_ROOT / "sample_output"
DEFAULT_TOPIC = (
    "Agentic AI and autonomous operations in industrial manufacturing: where the "
    "market is moving, who the new entrants are, and whether Meridian should build, "
    "partner or buy"
)


# --------------------------------------------------------------------------
# Reading a run off disk
# --------------------------------------------------------------------------
def read_run(directory: Path) -> dict:
    """Everything the interface needs about one run, from its files."""
    run: dict = {"dir": directory, "exists": False}
    memo = directory / "market_investment_memo.md"
    ledger = directory / "evidence_ledger.json"
    manifest = directory / "run_manifest.json"
    trace = directory / "run_trace.jsonl"

    if memo.exists():
        run["memo_md"] = memo.read_text(encoding="utf-8")
        run["memo_path"] = memo
        run["exists"] = True
    for key, path in (("ledger", ledger), ("manifest", manifest)):
        if path.exists():
            try:
                run[key] = json.loads(path.read_text(encoding="utf-8"))
                run["exists"] = True
            except json.JSONDecodeError:
                run[key] = {}
    if trace.exists():
        run["records"] = tracelib.load(trace)
        run["trace_path"] = trace
        run["exists"] = True
    return run


def kpi(col, label: str, value, note: str = "") -> None:
    col.markdown(
        f'<div class="dr-card"><div class="dr-kpi-label">{label}</div>'
        f'<div class="dr-kpi">{value}</div>'
        + (f'<div class="dr-kpi-note">{note}</div>' if note else "")
        + "</div>",
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------
# The timeline
# --------------------------------------------------------------------------
def render_timeline(phases: list[dict], total: float, cursor: float | None = None) -> None:
    """A waterfall of the run's phases.

    Bars are positioned by real start and end times, so two bars that overlap
    really did overlap. That is worth seeing rather than asserting: the
    architecture claims the two research legs are independent, and this is the
    evidence for it.

    When a cursor is given, a line is drawn through every track at that moment
    and phases that have not started yet are faded. It is the same cursor the
    graph and the cards read, so the three views are looking at one instant
    rather than three.
    """
    if not phases or total <= 0:
        st.caption("No phase timing in this trace.")
        return

    rows = []
    for p in phases:
        left = 100.0 * p["start"] / total
        width = max(100.0 * p["duration"] / total, 0.6)
        colour = PHASE_COLOUR.get(p["phase"], MUTED)
        marker = " (running)" if p.get("running") else ""
        ahead = cursor is not None and p["start"] > cursor
        line = ""
        if cursor is not None:
            line = (
                f'<div style="position:absolute;top:-3px;height:25px;width:2px;'
                f'left:{100.0 * min(cursor, total) / total:.2f}%;background:{ACCENT};'
                'opacity:.75"></div>'
            )
        faded = ' style="opacity:.38"' if ahead else ""
        rows.append(
            f'<div class="tl-row"{faded}>'
            f'<div class="tl-label">{esc(p["label"])}{marker}</div>'
            f'<div class="tl-track">'
            f'<div class="tl-bar" style="left:{left:.2f}%;width:{width:.2f}%;'
            f'background:{colour}" title="{esc(p["label"])}: {p["duration"]:.1f}s"></div>'
            f"{line}</div>"
            f'<div class="tl-dur">{p["duration"]:.1f}s</div>'
            f"</div>"
        )

    ticks = "".join(f"<span>{int(total * f)}s</span>" for f in (0, 0.25, 0.5, 0.75, 1.0))
    concurrent = [p for p in phases if p.get("concurrent_with")]
    legend = ""
    if concurrent:
        names = " and ".join(sorted({p["label"] for p in concurrent}))
        legend = (
            f'<div class="tl-legend"><b>{names}</b> overlap on this timeline, '
            "which is what running concurrently looks like. They share no state, so "
            "neither waits for the other.</div>"
        )

    st.markdown(
        '<div class="tl">'
        + "".join(rows)
        + f'<div class="tl-scale"><div></div><div class="tl-ticks">{ticks}</div><div></div></div>'
        + legend
        + "</div>",
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------
# What each phase handed on
# --------------------------------------------------------------------------
def payload_of(phase_id: str, ledger: dict, manifest: dict) -> tuple[str, list[str]] | None:
    """The object a phase produced, summarised from the artifacts.

    Read out of the evidence ledger rather than described, so a card cannot
    claim a phase produced something the files do not contain. That is the
    same rule the rest of the interface follows, applied one level down.
    """
    brief = ledger.get("brief") or {}
    external = ledger.get("external") or {}
    internal = ledger.get("internal") or {}
    fusion = ledger.get("fusion") or {}
    audit = ledger.get("audit") or {}
    positions = ledger.get("adopted_position_test") or {}

    if phase_id == "intake":
        return "the question", [manifest.get("Topic") or brief.get("topic") or ""]
    if phase_id == "plan":
        segments = ", ".join(brief.get("segments") or [])
        return "ResearchBrief", [
            f"{len(brief.get('sub_questions') or [])} sub-questions to answer",
            f"{len(brief.get('search_queries') or [])} search queries, "
            f"{len(brief.get('filing_queries') or [])} regulatory-filing queries",
            f"Segments: {segments}" if segments else "",
        ]
    if phase_id == "research_external":
        counts = [
            f"{len(external.get(k) or [])} {name}"
            for k, name in (
                ("whats_changing", "shifts"),
                ("incumbent_moves", "incumbent moves"),
                ("new_entrants", "new entrants"),
                ("capital_flows", "capital-flow items"),
            )
        ]
        unavailable = external.get("sources_unavailable") or []
        return "ExternalFindings", [
            ", ".join(counts),
            f"{len(unavailable)} source(s) recorded as unavailable"
            if unavailable
            else "Every finding carries a retrievable source",
        ]
    if phase_id == "assess_internal":
        return "InternalAssessment", [
            f"{len(internal.get('findings') or [])} findings, each with the record "
            "identifiers behind it",
            f"{len(internal.get('adopted_positions') or [])} positions the company has "
            "formally adopted, extracted for testing",
            f"Weakest: {', '.join(internal.get('weakest_segments') or []) or '—'}",
        ]
    if phase_id == "fuse":
        return "FusionResult", [
            f"{len(fusion.get('signals') or [])} findings kept — each needs both the "
            "public record and an internal record",
            f"{len(fusion.get('rejected') or [])} rejected, "
            f"{len(fusion.get('gaps') or [])} gaps stated",
            f"Adopted positions: {len(positions.get('contradicted') or [])} contradicted, "
            f"{len(positions.get('upheld') or [])} upheld, "
            f"{len(positions.get('untestable') or [])} untestable",
        ]
    if phase_id in {"draft_and_audit", "revise"}:
        verdict = "passed" if audit.get("passed") else "failed"
        return "Memo + audit", [
            f"Draft written, then audited: {verdict}",
            f"{len(audit.get('unresolved_identifiers') or [])} identifiers could not be "
            "resolved against the source files",
            f"{manifest.get('Figures disagreeing with their records', 0)} figures disagreed "
            "with the records cited for them",
        ]
    if phase_id == "audit_gate":
        revisions = audit.get("revisions", manifest.get("Revisions required", 0))
        return None if revisions is None else (
            "the routing decision",
            [
                "Sent the draft back once" if revisions else "Released it to publish",
                "The gate is a router, not a warning: a failed audit changes what runs next.",
            ],
        )
    if phase_id == "publish":
        return "the artifacts", [
            "market_investment_memo.md and .html",
            "evidence_ledger.json — every typed object the run produced",
            "run_manifest.json and run_trace.jsonl",
        ]
    return None


# --------------------------------------------------------------------------
# Step cards
# --------------------------------------------------------------------------
def render_cards(
    phases: list[dict],
    snap: dict,
    cursor: float,
    ledger: dict,
    manifest: dict,
    *,
    event_limit: int = 90,
) -> None:
    """One card per phase, in the order they began, each opening on click.

    Built from `<details>` rather than Streamlit expanders deliberately. The
    live view repaints about once a second, and a Streamlit expander closes
    itself on every repaint; a `<details>` element is browser state and stays
    where the reader put it. One renderer therefore serves the run in flight
    and the run read back off disk, which is the same reason the trace reader
    is shared rather than duplicated.
    """
    if not phases:
        st.caption("No phases in this trace yet.")
        return

    cards: list[str] = []
    for p in phases:
        node = snap["nodes"].get(p["phase"], {})
        state = node.get("state", "done")
        colour = PHASE_COLOUR.get(p["phase"], MUTED)
        classes = ["dr-step"]
        if state in {"active", "failed"}:
            classes.append("now")
        elif state == "pending":
            classes.append("later")
        is_open = state in {"active", "failed"}

        stats = [f"{p['duration']:.1f}s"]
        if p["tool_calls"]:
            stats.append(f"{p['tool_calls']} tools")
        if p["llm_calls"]:
            stats.append(f"{p['llm_calls']} model calls")
        if p["prompt_tokens"] or p["completion_tokens"]:
            stats.append(f"{p['prompt_tokens'] + p['completion_tokens']:,} tok")
        if p.get("problems"):
            stats.append(f"{p['problems']} failed")

        crew = graphlib.NODE_BY_ID.get(p["phase"], {}).get("crew") or ""
        head = (
            "<summary>"
            f'<span class="st-time">{p["start"]:.1f}s</span>'
            f'<span class="st-dot" style="background:{colour}"></span>'
            f'<span><span class="st-name">{esc(p["label"])}</span>'
            + (f'<span class="st-crew">{esc(crew)}</span>' if crew else "")
            + (' <span class="st-crew">running</span>' if p.get("running") else "")
            + "</span>"
            f'<span class="st-stats">{esc(" · ".join(stats))}</span>'
            "</summary>"
        )

        body: list[str] = []
        if p["purpose"]:
            body.append(f'<div style="opacity:.8">{esc(p["purpose"])}</div>')

        payload = payload_of(p["phase"], ledger, manifest) if ledger else None
        if payload:
            name, facts = payload
            lines = "".join(
                f"<div>{esc(fact)}</div>" for fact in facts if fact
            )
            gloss = graphlib.PAYLOAD_PLAIN.get(name, "")
            body.append(
                '<div class="st-sec"><div class="st-h">Handed to the next phase</div>'
                f'<div class="st-pay"><code>{esc(name)}</code>'
                + (f' <span style="opacity:.72">— {esc(gloss)}</span>' if gloss else "")
                + f'<div style="margin-top:5px;opacity:.85">{lines}</div></div></div>'
            )

        agents = node.get("agents") or {}
        if agents:
            rows = []
            for name, info in agents.items():
                tools = ", ".join(
                    f"{tool} ×{n}"
                    for tool, n in list(_tools_for(p, name).items())[:5]
                )
                mark = {"active": "working", "done": "done", "pending": "not yet",
                        "skipped": "—", "idle": "no recorded calls"}.get(info["state"], "")
                detail = tools or ("no tool calls — this agent writes rather than searches"
                                   if info.get("inferred") else mark)
                colour_attr = f' style="color:{ACCENT}"' if info["state"] == "active" else ""
                rows.append(
                    f'<div class="st-agent"><div{colour_attr}>{esc(name)}'
                    + (f' <span style="opacity:.6">({info["tool_calls"]})</span>'
                       if info["tool_calls"] else "")
                    + f'</div><div class="st-mono">{esc(detail)}</div></div>'
                )
            body.append(
                '<div class="st-sec"><div class="st-h">Who did the work</div>'
                + "".join(rows)
                + "</div>"
            )

        verif = [r for r in p["events"] if r.get("kind") in {"audit", "fusion"}]
        if verif:
            body.append(
                '<div class="st-sec"><div class="st-h">Verification in this phase</div>'
                + "".join(
                    f'<div style="padding:1px 0">{esc(tracelib.clean_message(r))}</div>'
                    for r in verif
                )
                + "</div>"
            )

        shown = [r for r in p["events"] if r.get("kind") != "agent_start"]
        if shown:
            lines = []
            for r in shown[:event_limit]:
                kind = tracelib.KIND_LABELS.get(r.get("kind"), r.get("kind"))
                msg = esc(tracelib.clean_message(r)[:200])
                lines.append(
                    f'<span class="ev-t">{r.get("t", 0):>7.1f}s</span> &nbsp; '
                    f"{esc(kind)} &nbsp; {msg}"
                )
            more = (
                f'<div style="opacity:.6;margin-top:5px">…and {len(shown) - event_limit} '
                "more, in the raw run data tab</div>"
                if len(shown) > event_limit
                else ""
            )
            body.append(
                f'<div class="st-sec"><div class="st-h">Every event, in order '
                f"({len(shown)})</div>"
                f'<div class="st-ev">{"<br>".join(lines)}</div>{more}</div>'
            )

        cards.append(
            f'<details id="card-{esc(p["phase"])}" class="{" ".join(classes)}"'
            + (" open" if is_open else "")
            + ">"
            + head
            + f'<div class="st-body">{"".join(body)}</div></details>'
        )

    st.markdown('<div class="dr-steps">' + "".join(cards) + "</div>", unsafe_allow_html=True)


def _tools_for(phase: dict, agent: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in phase["events"]:
        if r.get("kind") != "tool" or tracelib.clean_agent(r.get("agent")) != agent:
            continue
        tool = r.get("tool")
        if tool:
            counts[tool] = counts.get(tool, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


# --------------------------------------------------------------------------
# The crew graph
# --------------------------------------------------------------------------
# Every colour here is a foreground or a translucent overlay, never a flat
# fill, so the diagram sits correctly on a light or a dark page.
G_STROKE = {
    "pending": "rgba(127,127,127,.34)",
    "active": ACCENT,
    "done": "rgba(127,127,127,.52)",
    "failed": BAD,
    "skipped": "rgba(127,127,127,.2)",
}
G_FILL = {
    "pending": "rgba(127,127,127,.04)",
    "active": f"rgba({ACCENT_RGB},.15)",
    "done": "rgba(127,127,127,.1)",
    "failed": f"rgba({BAD_RGB},.13)",
    "skipped": "rgba(127,127,127,.03)",
}
G_TEXT_OPACITY = {
    "pending": ".5", "active": "1", "done": ".92", "failed": "1", "skipped": ".35",
}
G_EDGE = {
    "idle": ("rgba(127,127,127,.3)", 1.3, "mk-idle", "4 5"),
    "in_flight": (ACCENT, 2.2, "mk-live", "7 5"),
    "delivered": ("rgba(127,127,127,.5)", 1.6, "mk-done", ""),
}


def render_graph(snap: dict, cursor: float, total: float) -> None:
    """The whole crew as one picture, at one moment of the run.

    The timeline shows how long each phase took; it cannot show the shape of
    the thing, and the shape is what carries the argument. The fork into two
    research legs, the join that waits for both, and the loop the audit can
    send a draft round -- those are the design, and a row of bars implies a
    straight line instead.

    Colour carries state and nothing else: the accent means running now, a
    neutral fill means finished, a dashed outline means not yet. Every node
    also carries its own counts, so the picture is never the only evidence.
    """
    width, height = graphlib.CANVAS
    out: list[str] = [
        f'<svg viewBox="0 0 {width} {height}" role="img" '
        'aria-label="The research flow, with the parts running now highlighted">',
        "<defs>",
    ]
    for marker, colour in (
        ("mk-idle", "rgba(127,127,127,.3)"),
        ("mk-live", ACCENT),
        ("mk-done", "rgba(127,127,127,.5)"),
    ):
        out.append(
            f'<marker id="{marker}" viewBox="0 0 10 10" refX="9" refY="5" '
            'markerWidth="6.5" markerHeight="6.5" orient="auto">'
            f'<path d="M0,1 L9,5 L0,9 z" fill="{colour}"/></marker>'
        )
    out.append("</defs>")

    # Edges first, so nodes sit on top of them.
    for edge in snap["edges"]:
        colour, stroke_w, marker, dash = G_EDGE[edge["state"]]
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        flight = ' class="g-flight"' if edge["state"] == "in_flight" else ""
        gloss = graphlib.PAYLOAD_PLAIN.get(edge["payload"], "")
        out.append(
            f'<path d="{edge["d"]}" fill="none" stroke="{colour}" '
            f'stroke-width="{stroke_w}"{dash_attr}{flight} '
            f'marker-end="url(#{marker})"><title>{esc(edge["payload"])}'
            + (f" — {esc(gloss)}" if gloss else "")
            + "</title></path>"
        )
        if edge["state"] == "in_flight":
            out.append(
                f'<circle r="4" fill="{ACCENT}"><animateMotion dur="1.7s" '
                f'repeatCount="indefinite" path="{edge["d"]}"/></circle>'
            )
        lx, ly = edge["label_xy"]
        label_colour = ACCENT if edge["state"] == "in_flight" else "currentColor"
        label_opacity = "1" if edge["state"] == "in_flight" else ".62"
        out.append(
            f'<text x="{lx}" y="{ly}" text-anchor="middle" font-size="10.5" '
            'font-family="ui-monospace,SFMono-Regular,Menlo,Consolas,monospace" '
            f'fill="{label_colour}" opacity="{label_opacity}">{esc(edge["payload"])}</text>'
        )

    for node in graphlib.NODES:
        info = snap["nodes"].get(node["id"], {})
        state = info.get("state", "pending")
        x, y, w, h = node["x"], node["y"], node["w"], node["h"]
        opacity = G_TEXT_OPACITY[state]

        if state in {"active", "failed"}:
            out.append(
                f'<rect class="g-halo" x="{x - 5}" y="{y - 5}" width="{w + 10}" '
                f'height="{h + 10}" rx="11" fill="none" stroke="{G_STROKE[state]}" '
                'stroke-width="2"/>'
            )
        out.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" '
            f'fill="{G_FILL[state]}" stroke="{G_STROKE[state]}" '
            f'stroke-width="{2 if state in {"active", "failed"} else 1.2}"'
            + (' stroke-dasharray="5 4"' if state in {"pending", "skipped"} else "")
            + "/>"
        )

        label = graphlib.SHORT_LABEL.get(node["id"], node["id"])
        if not node["crew"]:
            out.append(
                f'<text x="{x + w / 2}" y="{y + h / 2 + 4}" text-anchor="middle" '
                f'font-size="12" font-weight="640" fill="currentColor" '
                f'opacity="{opacity}">{esc(label)}</text>'
            )
        else:
            out.append(
                f'<text x="{x + 11}" y="{y + 17}" font-size="11.5" font-weight="650" '
                f'fill="currentColor" opacity="{opacity}">{esc(label)}</text>'
            )
            out.append(
                f'<text x="{x + 11}" y="{y + 29}" font-size="8.6" '
                'font-family="ui-monospace,SFMono-Regular,Menlo,Consolas,monospace" '
                f'fill="currentColor" opacity="{float(opacity) * 0.62:.2f}">'
                f'{esc(node["crew"])}</text>'
            )
            for index, name in enumerate(node["agents"]):
                agent = (info.get("agents") or {}).get(name, {})
                agent_state = agent.get("state", "pending")
                row_y = y + 44 + index * 15
                live = agent_state == "active"
                dot = ACCENT if live else "currentColor"
                dot_op = "1" if live else (".55" if agent_state == "done" else ".28")
                out.append(
                    f'<circle cx="{x + 15}" cy="{row_y - 3.5}" r="2.6" fill="{dot}" '
                    f'opacity="{dot_op}"/>'
                )
                calls = agent.get("tool_calls") or 0
                out.append(
                    f'<text x="{x + 23}" y="{row_y}" font-size="9.4" '
                    f'fill="{ACCENT if live else "currentColor"}" '
                    f'opacity="{"1" if live else (".8" if agent_state == "done" else ".4")}"'
                    f'>{esc(name)}</text>'
                )
                if calls:
                    out.append(
                        f'<text x="{x + w - 9}" y="{row_y}" text-anchor="end" font-size="8.8" '
                        'font-family="ui-monospace,SFMono-Regular,Menlo,Consolas,monospace" '
                        f'fill="currentColor" opacity=".55">{calls}</text>'
                    )

        badge = []
        if info.get("tool_calls"):
            badge.append(f'{info["tool_calls"]} tools')
        if info.get("duration"):
            badge.append(f'{info["duration"]:.0f}s')
        if state == "skipped" and info.get("note"):
            badge = [info["note"]]
        if badge:
            out.append(
                f'<text x="{x + w - 9}" y="{y + 17}" text-anchor="end" font-size="8.8" '
                'font-family="ui-monospace,SFMono-Regular,Menlo,Consolas,monospace" '
                f'fill="currentColor" opacity=".55">{esc(" · ".join(badge))}</text>'
            )

        # An invisible hit area on top, linking to that phase's card. It is a
        # separate transparent rectangle rather than a wrapper around the node
        # itself, so that if the markdown sanitiser ever declines to keep a
        # link inside an SVG, what disappears is an overlay nobody can see
        # rather than the box it was covering.
        tip = tracelib.PHASE_LABELS.get(node["id"], node["id"])
        detail = ", ".join(
            part
            for part in (
                f'{info["duration"]:.1f}s' if info.get("duration") else "",
                f'{info["tool_calls"]} tool calls' if info.get("tool_calls") else "",
            )
            if part
        )
        out.append(
            f'<a href="#card-{esc(node["id"])}">'
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="transparent" '
            'style="cursor:pointer"><title>'
            + esc(tip)
            + (f" — {esc(detail)}" if detail else "")
            + ". Opens this phase's card below.</title></rect></a>"
        )

    out.append("</svg>")
    st.markdown('<div class="dr-graph">' + "".join(out) + "</div>", unsafe_allow_html=True)

    running = graphlib.active_phases(snap)
    working = graphlib.active_agents(snap)
    if running:
        headline = (
            f'At <b>{cursor:.0f}s</b>: <b style="color:{ACCENT}">'
            + esc(" and ".join(running))
            + "</b>"
            + (f' — {esc(", ".join(working))}' if working else "")
        )
    elif cursor >= total:
        headline = f"At <b>{total:.0f}s</b>: finished. Every phase has run."
    else:
        headline = f"At <b>{cursor:.0f}s</b>: between phases."
    st.markdown(
        f'<div class="g-legend">{headline}<br>'
        f'<span class="g-swatch" style="background:{ACCENT}"></span>running now'
        '&nbsp;&nbsp;<span class="g-swatch" style="background:rgba(127,127,127,.45)">'
        "</span>finished"
        '&nbsp;&nbsp;<span class="g-swatch" style="border:1px dashed '
        'rgba(127,127,127,.55);background:none"></span>not yet reached'
        "&nbsp;&nbsp;· labels on the arrows are the typed objects the phases actually "
        "exchange, not a description of them. Hover one to read it in plain English."
        "</div>",
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------
# Running
# --------------------------------------------------------------------------
def run_flow(topic: str, offline: bool, box: dict) -> None:
    """Executed on a worker thread so the interface can poll progress."""
    import os

    if offline:
        os.environ["DR_OFFLINE"] = "1"
    else:
        os.environ.pop("DR_OFFLINE", None)
    try:
        from deep_research.flow import DeepResearchFlow
        from deep_research.knowledge_build import build as build_knowledge
        from deep_research.observability.listeners import install

        obs = install(trace_path=OUTPUT_DIR / "run_trace.jsonl", echo=False)
        box["observer"] = obs
        build_knowledge(verbose=False)
        flow = DeepResearchFlow()
        box["paths"] = flow.kickoff(inputs={"topic": topic})
        box["flow"] = flow
    except Exception as exc:  # surfaced in the interface rather than swallowed
        box["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        box["done"] = True


# --------------------------------------------------------------------------
# Header and controls
# --------------------------------------------------------------------------
st.title("Deep Research Agent")
st.markdown(
    '<div class="dr-sub">Your market, read against your own data. Every claim traceable '
    "to the record it came from.</div>",
    unsafe_allow_html=True,
)

key_ok = llms.api_key_present()

with st.sidebar:
    st.subheader("What to show")
    choices: dict[str, Path] = {}
    if (SAMPLE_DIR / "run_trace.jsonl").exists():
        choices["Committed sample run"] = SAMPLE_DIR
    local_complete = (OUTPUT_DIR / "market_investment_memo.md").exists()
    if (OUTPUT_DIR / "run_trace.jsonl").exists():
        label = "Most recent local run" if local_complete else "Most recent local run (incomplete)"
        choices[label] = OUTPUT_DIR
    if not choices:
        choices["Nothing yet — run one"] = OUTPUT_DIR

    # Prefer a local run only when it actually produced a memo. A run that
    # failed part-way still leaves a trace behind, and opening on it makes a
    # working system look broken.
    default = next(
        (k for k in choices if k == "Most recent local run"),
        next(iter(choices)),
    )
    picked = st.selectbox("Run", list(choices), index=list(choices).index(default))
    source_dir = choices[picked]
    st.caption(
        "Read from files in the repository, so this shows exactly what a reviewer "
        "would find — not a live object that no longer exists."
    )

    st.divider()
    st.subheader("Run a new one")
    topic = st.text_area("Research question", value=DEFAULT_TOPIC, height=130)
    offline = st.toggle(
        "Cached sources only",
        value=True,
        help="Runs against the search results and API responses committed to this "
        "repository. No search credentials needed, and nothing is spent on search.",
    )
    go = st.button(
        "Run research", type="primary", width="stretch", disabled=not key_ok
    )

    st.divider()
    st.caption("Configuration")
    st.write(f"{'OK' if key_ok else 'MISSING'} — model credential")
    st.caption(f"Reading: `{llms.worker_model()}`")
    st.caption(f"Thinking: `{llms.synth_model()}`")
    if not key_ok:
        st.warning("Set OPENAI_API_KEY in .env to run. Saved runs still display.")

    st.divider()
    st.caption("Internal records")
    try:
        from deep_research.tools import internal_analytics as ia

        seg = ia.segment_stress()["segments"][0]
        tot = ia.competitor_exposure()["totals"]
        st.caption(f"Largest exposure: `{seg['segment']}`")
        st.caption(
            f"${seg['arr_lost_usd']:,} lost, capability "
            f"{seg['capability_maturity_of_5']}/5"
        )
        st.caption(
            f"{tot['competitors_we_also_pay']} suppliers also compete "
            f"(${tot['annual_spend_to_competitors_usd']:,}/yr)"
        )
    except Exception:
        st.caption("unavailable")

if "box" not in st.session_state:
    st.session_state.box = None

if go:
    box: dict = {"done": False}
    st.session_state.box = box
    st.session_state.live_log = []
    threading.Thread(target=run_flow, args=(topic, offline, box), daemon=True).start()

box = st.session_state.box

# --------------------------------------------------------------------------
# Live progress, while a run is in flight
# --------------------------------------------------------------------------
if box is not None and not box.get("done"):
    st.subheader("Running")
    st.session_state.setdefault("view_mode", VIEW_MODES[0])
    live_view = st.segmented_control(
        "View",
        VIEW_MODES,
        key="view_mode",
        help="Switch at any point, during the run or after it. The same views render "
        "the finished run.",
    ) or VIEW_MODES[0]

    kpi_box = st.empty()
    main_box = st.empty()
    log_box = st.empty()
    if "live_log" not in st.session_state:
        st.session_state.live_log = []

    # The heavy views are redrawn only when an event actually arrives. Two
    # reasons: repainting a card every tick would close whatever the viewer had
    # just opened, and repainting the graph would restart its animations. The
    # counters and the tail get their own slots so the clock still ticks.
    drawn_at = -1
    while not box.get("done"):
        obs = box.get("observer")
        if obs is not None:
            st.session_state.live_log.extend(obs.drain())
            records = list(st.session_state.live_log)
            phases = tracelib.phases(records)
            totals = tracelib.totals(records)
            elapsed = obs.summary().get("elapsed_seconds", totals["elapsed_seconds"])
            span = max(float(elapsed), 0.1)

            cols = kpi_box.columns(4)
            current = next(
                (p["label"] for p in reversed(phases) if p.get("running")),
                phases[-1]["label"] if phases else "starting",
            )
            kpi(cols[0], "Now", current)
            kpi(cols[1], "Tool calls", totals["tool_calls"])
            kpi(cols[2], "Model calls", totals["llm_calls"])
            kpi(cols[3], "Elapsed", f"{span:.0f}s")

            if len(records) != drawn_at:
                drawn_at = len(records)
                snap = graphlib.snapshot(phases, records, span)
                with main_box.container():
                    if live_view in {"Crew graph", "Both"}:
                        render_graph(snap, span, span)
                    if live_view in {"Timeline & cards", "Both"}:
                        render_timeline(phases, span, cursor=span)
                        render_cards(phases, snap, span, {}, {}, event_limit=40)

            recent = [r for r in records if r.get("kind") != "agent_start"][-12:]
            log_box.markdown(
                '<div class="dr-card ev">'
                + "<br>".join(
                    f'<span class="ev-t">{r.get("t", 0):>7.1f}s</span> &nbsp; '
                    + esc(tracelib.clean_message(r)[:150])
                    for r in recent
                )
                + "</div>",
                unsafe_allow_html=True,
            )
        time.sleep(0.7)
    st.session_state.pop("cursor", None)  # the next view opens on the finished run
    st.rerun()

if box is not None and box.get("error"):
    st.error(f"The run failed: {box['error']}")
    st.caption("The trace below covers whatever completed before the failure.")

# After a completed run, show it rather than whatever the sidebar had selected.
if box is not None and box.get("done") and not box.get("error"):
    source_dir = OUTPUT_DIR

run = read_run(source_dir)

if not run.get("exists"):
    st.info(
        "No run to show yet. Press **Run research** in the sidebar, or check out the "
        "committed sample run."
    )
    with st.expander("What it reads on the internal side"):
        st.markdown(
            "- Deal outcomes, with the competitor and the recorded reason for each loss\n"
            "- Supplier spend, contract end dates and renewal risk\n"
            "- The company's own capability maturity ratings\n"
            "- The investment watchlist, including targets previously passed on\n"
            "- Buyer interviews, group strategy, and the last formally adopted market view"
        )
    st.stop()

manifest = run.get("manifest") or {}
ledger = run.get("ledger") or {}
fusion = ledger.get("fusion") or {}
audit = ledger.get("audit") or {}
records = run.get("records") or []
phases = tracelib.phases(records)
totals = tracelib.totals(records)

# --------------------------------------------------------------------------
# Headline
# --------------------------------------------------------------------------
signals = fusion.get("signals") or []
contradictions = fusion.get("contradictions") or []
audit_ok = audit.get("passed", manifest.get("Citation audit") == "passed")

def memo_field(markdown: str, label: str) -> str:
    """Pull a header field out of the memo.

    The recommendation lives in the memo itself rather than the manifest, so it
    is read from there instead of duplicated into a second file that could
    disagree with it.
    """
    for line in (markdown or "").splitlines()[:14]:
        if line.strip().startswith(f"**{label}:**"):
            return line.split("**", 2)[-1].replace(f"{label}:**", "").strip()
    return "—"


recommendation = memo_field(run.get("memo_md", ""), "Recommendation")
confidence = memo_field(run.get("memo_md", ""), "Confidence")

cols = st.columns(5)
kpi(cols[0], "The call", recommendation, f"{confidence} confidence" if confidence != "—" else "")
kpi(cols[1], "Findings needing both sides", len(signals))
kpi(cols[2], "Positions contradicted", len(contradictions))
kpi(
    cols[3],
    "Citation audit" if audit_ok else "Audit",
    "Passed" if audit_ok else "Exceptions",
    f"{audit.get('revisions', manifest.get('Revisions required', 0))} revision(s)",
)
kpi(
    cols[4],
    "Cost",
    str(manifest.get("Estimated cost", "—")).replace(" (indicative)", ""),
    f"{totals['elapsed_seconds']:.0f}s, {manifest.get('Mode', '')}".strip().rstrip(","),
)

tab_trace, tab_findings, tab_verify, tab_memo, tab_data = st.tabs(
    [
        "How it ran",
        "Findings that needed both sides",
        "Verification",
        "The memo",
        "Raw run data",
    ]
)

# --------------------------------------------------------------------------
# How it ran — the trace
# --------------------------------------------------------------------------
with tab_trace:
    run_total = max(totals["elapsed_seconds"], 0.1)

    # One cursor, read by all three views. Moving it moves the graph, the
    # timeline marker and which card is open together, which is the only way
    # the two views can be checked against each other rather than believed
    # separately.
    st.session_state.setdefault("cursor", run_total)
    st.session_state["cursor"] = min(float(st.session_state["cursor"]), run_total)

    def jump_to_phase() -> None:
        """Move the cursor into a chosen phase.

        Set from a callback rather than inline: Streamlit refuses to assign a
        widget's key after the widget exists, and callbacks run before the
        script re-executes, so the slider picks the new value up cleanly.
        """
        label = st.session_state.get("phase_jump")
        for phase in phases:
            if phase["label"] == label:
                st.session_state["cursor"] = min(
                    phase["start"] + max(phase["duration"] * 0.4, 0.5), run_total
                )
                return

    st.session_state.setdefault("view_mode", VIEW_MODES[0])
    pick, jump = st.columns([1.35, 1])
    with pick:
        view = st.segmented_control(
            "View",
            VIEW_MODES,
            key="view_mode",
            help="The graph and the cards read the same cursor, so switching between "
            "them keeps your place.",
        )
    with jump:
        st.selectbox(
            "Jump to a phase",
            [p["label"] for p in phases],
            index=None,
            placeholder="anywhere in the run",
            key="phase_jump",
            on_change=jump_to_phase,
        )

    cursor = st.slider(
        "Moment in the run",
        min_value=0.0,
        max_value=float(run_total),
        step=max(run_total / 400, 0.1),
        key="cursor",
        format="%.0fs",
    )
    st.caption(
        "Drag to replay the run. The graph, the timeline marker and the open card all "
        "follow it, so the three are always showing the same instant. It opens at the "
        "end, where everything has finished."
    )
    snap = graphlib.snapshot(phases, records, cursor)
    view = view or VIEW_MODES[0]

    if view in {"Crew graph", "Both"}:
        st.markdown("#### The crew, and what was running")
        render_graph(snap, cursor, run_total)

    if view in {"Timeline & cards", "Both"}:
        st.markdown("#### The run, phase by phase")
        st.caption(
            "Every bar is measured from the event stream that also writes the audit "
            "trail, so the picture and the record cannot disagree. The line is where "
            "the cursor above is."
        )
        render_timeline(phases, run_total, cursor=cursor)

    c1, c2, c3, c4 = st.columns(4)
    kpi(c1, "Phases", totals["phases"])
    kpi(c2, "Tool calls", totals["tool_calls"])
    kpi(
        c3,
        "Model calls",
        totals["llm_calls"] or "—",
        "" if totals["llm_calls"] else "not recorded in this trace",
    )
    kpi(
        c4,
        "Tokens",
        f"{(totals['prompt_tokens'] + totals['completion_tokens']):,}"
        if totals["prompt_tokens"]
        else str(manifest.get("Tokens", "—")),
        f"{totals['prompt_tokens']:,} in / {totals['completion_tokens']:,} out"
        if totals["prompt_tokens"]
        else "",
    )

    if view in {"Timeline & cards", "Both"}:
        st.markdown("#### What happened inside each phase")
        st.caption(
            "Open any card for what that phase produced, who did the work and every "
            "event it emitted. Work is attributed through the agent that did it, not by "
            "timestamp — the two research phases overlap, so a clock-based split would "
            "count the same tool call twice."
        )
        render_cards(phases, snap, cursor, ledger, manifest)

    st.markdown("#### Which tools did the work")
    usage = tracelib.tool_usage(records)
    if usage:
        st.bar_chart(
            {"tool": list(usage), "calls": list(usage.values())},
            x="tool",
            y="calls",
            horizontal=True,
            color=ACCENT,
            height=min(44 * len(usage) + 60, 520),
        )
        st.caption(
            "`look_up_an_internal_record` dominating is the citation audit doing its job: "
            "it resolves every identifier in the draft rather than trusting it."
        )
    else:
        st.caption("No tool calls in this trace.")

    problems = tracelib.problems(records)
    if problems:
        st.markdown("#### Failures")
        for r in problems:
            st.markdown(f'<span class="bad">•</span> {tracelib.clean_message(r)}', unsafe_allow_html=True)
    else:
        st.markdown(
            '<span class="ok">No tool or phase failures in this run.</span>',
            unsafe_allow_html=True,
        )

# --------------------------------------------------------------------------
# Findings
# --------------------------------------------------------------------------
with tab_findings:
    if signals:
        st.caption(
            "Each of these required the public record AND the internal files. Cover "
            "either column and ask whether the row still tells an executive to act — "
            "that is the test the pipeline applies to itself."
        )
        for i, sig in enumerate(signals, 1):
            st.markdown(f"#### {i}. {sig.get('headline', '')}")
            meta = [
                f"**Urgency:** {str(sig.get('urgency', '')).replace('_', ' ')}",
                f"**Confidence:** {sig.get('confidence', '')}",
            ]
            if sig.get("segment"):
                meta.append(f"**Segment:** `{sig['segment']}`")
            st.markdown(" &nbsp;·&nbsp; ".join(meta))
            left, right = st.columns(2)
            with left:
                st.markdown("**From the public record**")
                for ev in sig.get("external_evidence") or []:
                    url = ev.get("source_url", "")
                    title = ev.get("source_title") or ev.get("source_type") or "source"
                    st.markdown(f"- {ev.get('claim', '')}  \n  [{title}]({url})")
            with right:
                st.markdown("**From your own records**")
                for f in sig.get("internal_evidence") or []:
                    tags = " ".join(f"`[{t}]`" for t in (f.get("citation_tags") or []))
                    figure = f"  \n  Figure: **{f['metric']}**" if f.get("metric") else ""
                    st.markdown(f"- {f.get('claim', '')}{figure}  \n  {tags}")
            if sig.get("why_this_needs_both"):
                st.info(f"**Why this needs both:** {sig['why_this_needs_both']}")
            if sig.get("implication"):
                st.markdown(f"**Implication:** {sig['implication']}")
            st.divider()
    else:
        st.warning(
            "No finding in this run met the both-sources test. Candidates were produced, "
            "but none carried both a retrievable source and a resolvable record, so none "
            "is shown — rather than filling the section with findings that only look "
            "joined."
        )

    if contradictions:
        st.markdown("#### Positions the evidence has overtaken")
        st.caption(
            "Each of these is a position the company formally adopted and is still "
            "acting on. Surfacing them is the thing neither a market report nor the "
            "internal files can do alone."
        )
        for c in contradictions:
            st.markdown(f"- {c}")

    rejected = fusion.get("rejected") or []
    if rejected:
        with st.expander(f"Candidates that did not survive verification ({len(rejected)})"):
            st.caption(
                "Shown rather than hidden: knowing what was thrown out is part of "
                "judging what was kept."
            )
            for r in rejected:
                st.markdown(f"- {r}")

# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------
with tab_verify:
    st.markdown("#### Three checks before anything is published")
    unresolved = audit.get("unresolved_identifiers") or []
    arithmetic_bad = int(manifest.get("Figures disagreeing with their records", 0) or 0)
    verdict = audit.get("reviewer_verdict") or {}

    checks = [
        (
            "Every cited identifier exists",
            not unresolved,
            f"{len(unresolved)} unresolved",
            "Resolved in Python against the source files. Whether a record exists has a "
            "right answer, so it is not delegated to a model.",
        ),
        (
            "Every figure matches the records cited for it",
            arithmetic_bad == 0,
            f"{arithmetic_bad} disagreed",
            "Totalled in Python. This began as an instruction to the reviewing agent and "
            "had to move: given ten numbers to add from retrieved text it got sums wrong, "
            "once reporting a discrepancy between $6,869,000 and $6,869,000.",
        ),
        (
            "Records say what the sentences claim",
            bool(verdict.get("passed", audit_ok)),
            "reviewed by a separate agent",
            "The one check that genuinely needs judgement, so it belongs to an agent — a "
            "different one from the writer, because an agent checking its own work has an "
            "interest in finishing.",
        ),
    ]
    for label, ok, detail, why in checks:
        st.markdown(
            f'<span class="{"ok" if ok else "bad"}">{"PASS" if ok else "FAIL"}</span> '
            f"&nbsp; **{label}** — {detail}",
            unsafe_allow_html=True,
        )
        st.caption(why)

    revisions = audit.get("revisions", manifest.get("Revisions required", 0))
    if revisions:
        st.info(
            f"**{revisions} revision.** The first draft was failed, corrected and "
            "re-checked. That is the gate working, not a defect — and it is why the "
            "audit is a router rather than a warning."
        )

    if audit.get("notes"):
        with st.expander("What the audit said"):
            st.text(audit["notes"])

    positions = ledger.get("adopted_position_test") or {}
    if positions:
        st.markdown("#### Every adopted position was tested")
        p1, p2, p3 = st.columns(3)
        kpi(p1, "Contradicted", len(positions.get("contradicted") or []))
        kpi(p2, "Upheld", len(positions.get("upheld") or []))
        kpi(p3, "Could not test", len(positions.get("untestable") or []))
        st.caption(
            "An empty contradictions list means checked and found none, not did not look. "
            "That distinction is why this runs as its own task with its own output."
        )
        for group, title in (
            ("contradicted", "Contradicted by the evidence"),
            ("untestable", "No evidence either way this run"),
            ("upheld", "Still supported"),
        ):
            items = positions.get(group) or []
            if items:
                with st.expander(f"{title} ({len(items)})"):
                    for item in items:
                        st.markdown(f"- {item}")

    gaps = fusion.get("gaps") or []
    if gaps:
        st.markdown("#### What it could not establish")
        st.caption(
            "Required, not optional. An executive who finds an unstated gap themselves "
            "discounts the whole page."
        )
        for g in gaps:
            st.markdown(f"- {g}")

    events = tracelib.verification_events(records)
    if events:
        with st.expander(f"Verification events from the trace ({len(events)})"):
            for r in events:
                st.markdown(
                    f'<span class="ev"><span class="ev-t">{r.get("t", 0):>7.1f}s</span> '
                    f"&nbsp; {tracelib.clean_message(r)}</span>",
                    unsafe_allow_html=True,
                )

# --------------------------------------------------------------------------
# The memo
# --------------------------------------------------------------------------
with tab_memo:
    if run.get("memo_md"):
        c1, c2 = st.columns([1, 1])
        with c1:
            st.download_button(
                "Download the memo (Markdown)",
                run["memo_md"],
                file_name="market_investment_memo.md",
                width="stretch",
            )
        html_path = run["dir"] / "market_investment_memo.html"
        if html_path.exists():
            with c2:
                st.download_button(
                    "Download the print-ready page (HTML)",
                    html_path.read_text(encoding="utf-8"),
                    file_name="market_investment_memo.html",
                    mime="text/html",
                    width="stretch",
                )
        st.divider()
        st.markdown(run["memo_md"])
    else:
        st.caption("No memo in this run directory.")

# --------------------------------------------------------------------------
# Raw data
# --------------------------------------------------------------------------
with tab_data:
    st.markdown("#### The event stream")
    st.caption(
        "The same records the views above are built from. Filter it, or take the file — "
        "one JSON object per line, appended as the run happens."
    )
    kinds = sorted({r.get("kind", "") for r in records})
    picked_kinds = st.multiselect(
        "Event kinds",
        kinds,
        default=[k for k in kinds if k not in {"agent_start"}],
        format_func=lambda k: f"{k} — {tracelib.KIND_LABELS.get(k, k)}",
    )
    needle = st.text_input("Contains", placeholder="e.g. D-2201, or search_regulatory_filings")

    shown = [
        r
        for r in records
        if r.get("kind") in picked_kinds
        and (not needle or needle.lower() in json.dumps(r, default=str).lower())
    ]
    st.caption(f"{len(shown)} of {len(records)} records")
    st.dataframe(
        [
            {
                "t (s)": r.get("t"),
                "phase": tracelib.PHASE_LABELS.get(r.get("_phase") or "", r.get("_phase") or ""),
                "kind": r.get("kind"),
                "agent": tracelib.clean_agent(r.get("agent")),
                "tool": r.get("tool") or r.get("model") or "",
                "detail": tracelib.clean_message(r),
            }
            for r in shown
        ],
        width="stretch",
        hide_index=True,
        height=460,
    )

    if run.get("trace_path"):
        st.download_button(
            "Download the trace (JSONL)",
            run["trace_path"].read_text(encoding="utf-8"),
            file_name="run_trace.jsonl",
            width="stretch",
        )

    st.markdown("#### Run manifest")
    st.json({k: v for k, v in manifest.items() if k != "run_detail"})
    if manifest.get("run_detail"):
        with st.expander("Counters"):
            st.json(manifest["run_detail"])

    st.markdown("#### Evidence ledger")
    st.caption(
        "Every typed object the run produced: the brief, the external findings, the "
        "internal assessment, the position test, the fusion result and the audit."
    )
    for key in ("brief", "external", "internal", "adopted_position_test", "fusion", "audit"):
        if key in ledger:
            with st.expander(key.replace("_", " ")):
                st.json(ledger[key])
