"""The markup for the trace views.

Three things draw a run: the interface, a run in flight inside that same
interface, and the rendered walkthrough video. Letting each build its own
markup is how they end up telling different stories about one set of events --
the precise failure this project's observability layer was built to avoid --
so the markup is built here and the callers only decide where to put it.

Every function returns a string. Nothing here imports Streamlit, which is what
lets a video be rendered without a browser session and lets the markup be
checked without one either.

On colour: these are foreground colours and translucent overlays, never flat
fills. The interface may sit on a light or a dark page depending on a setting
stored in the viewer's browser, so a surface is expressed as "slightly lighter
or darker than whatever is behind it" and stays correct either way.
"""

from __future__ import annotations

import html

from . import graph as graphlib
from . import trace as tracelib

ACCENT = "#B4472A"   # the one accent, shared with the report and the deck
MUTED = "#5B6472"    # used only where a bar needs a colour, never for text
# Chosen to clear 4:1 against both a light and a dark page (4.07 / 4.44 and
# 4.28 / 4.23), because the viewer can switch theme in Streamlit's settings
# and that choice outranks the config file.
OK = "#2F8A63"       # verdict green
BAD = "#C9503D"      # verdict red

# The same two colours as channel triples, for the translucent fills below.
ACCENT_RGB = "180,71,42"
BAD_RGB = "201,80,61"

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


def esc(value) -> str:
    """Everything interpolated into markup goes through here.

    Tool arguments and model output end up on these pages, and both can contain
    angle brackets. Escaping at the boundary rather than trusting the source is
    the only version of this that stays true as the sources change.
    """
    return html.escape(str(value), quote=True)


# --------------------------------------------------------------------------
# Stylesheet
# --------------------------------------------------------------------------
STYLESHEET = f"""
  /* Nothing here sets a page background or a text colour.
     .streamlit/config.toml owns those, and the two disagreeing is what made
     this interface unreadable once: the stylesheet painted a light page while
     Streamlit, following the viewer's preference, kept colouring its own text
     for a dark one. Near-white on off-white is a contrast ratio of 1.00:1 --
     invisible until a hover style happened to override it.

     So these rules inherit the text colour and express every surface as a
     translucent overlay, which is true in either theme and stays true if
     someone switches theme in Streamlit's own settings menu, where the choice
     is stored in the browser and outranks the config file. */

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
     Built on <details> rather than framework expanders so that one renderer
     serves both a finished run and a run in flight. The live view redraws
     itself every second; an expander loses its open state each time, where a
     <details> element is ordinary browser state and survives. */
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
"""


# --------------------------------------------------------------------------
# The timeline
# --------------------------------------------------------------------------
def timeline_html(phases: list[dict], total: float, cursor: float | None = None) -> str:
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
        return '<div class="tl-legend">No phase timing in this trace.</div>'

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
            f'<div class="tl-legend"><b>{esc(names)}</b> overlap on this timeline, '
            "which is what running concurrently looks like. They share no state, so "
            "neither waits for the other.</div>"
        )

    return (
        '<div class="tl">'
        + "".join(rows)
        + f'<div class="tl-scale"><div></div><div class="tl-ticks">{ticks}</div><div></div></div>'
        + legend
        + "</div>"
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
            (
                f"{len(brief.get('search_queries') or [])} search queries, "
                f"{len(brief.get('filing_queries') or [])} regulatory-filing queries"
            ),
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
            (
                f"{len(unavailable)} source(s) recorded as unavailable"
                if unavailable
                else "Every finding carries a retrievable source"
            ),
        ]
    if phase_id == "assess_internal":
        return "InternalAssessment", [
            (
                f"{len(internal.get('findings') or [])} findings, each with the record "
                "identifiers behind it"
            ),
            (
                f"{len(internal.get('adopted_positions') or [])} positions the company has "
                "formally adopted, extracted for testing"
            ),
            f"Weakest: {', '.join(internal.get('weakest_segments') or []) or '—'}",
        ]
    if phase_id == "fuse":
        return "FusionResult", [
            (
                f"{len(fusion.get('signals') or [])} findings kept — each needs both the "
                "public record and an internal record"
            ),
            (
                f"{len(fusion.get('rejected') or [])} rejected, "
                f"{len(fusion.get('gaps') or [])} gaps stated"
            ),
            (
                f"Adopted positions: {len(positions.get('contradicted') or [])} contradicted, "
                f"{len(positions.get('upheld') or [])} upheld, "
                f"{len(positions.get('untestable') or [])} untestable"
            ),
        ]
    if phase_id in {"draft_and_audit", "revise"}:
        revisions = int(audit.get("revisions", manifest.get("Revisions required", 0)) or 0)
        passed = bool(audit.get("passed"))
        if phase_id == "draft_and_audit" and revisions:
            # The ledger holds the final verdict. On a run that went round the
            # loop, that verdict belongs to the revision, not to this draft --
            # and this phase's own trace says so a few lines further down.
            first_line = "Draft written; the audit refused it and the gate sent it back"
        elif phase_id == "revise":
            first_line = f"Rewritten and re-audited: {'passed' if passed else 'failed'}"
        else:
            first_line = f"Draft written, then audited: {'passed' if passed else 'failed'}"
        return "Memo + audit", [
            first_line,
            (
                f"{len(audit.get('unresolved_identifiers') or [])} identifiers could not be "
                "resolved against the source files"
            ),
            (
                f"{manifest.get('Figures disagreeing with their records', 0)} figures disagreed "
                "with the records cited for them"
            ),
        ]
    if phase_id == "audit_gate":
        revisions = audit.get("revisions", manifest.get("Revisions required", 0))
        return "the routing decision", [
            "Sent the draft back once" if revisions else "Released it to publish",
            "The gate is a router, not a warning: a failed audit changes what runs next.",
        ]
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
def cards_html(
    phases: list[dict],
    snap: dict,
    ledger: dict,
    manifest: dict,
    *,
    event_limit: int = 90,
    force_open: set[str] | None = None,
) -> str:
    """One card per phase, in the order they began, each opening on click.

    Built from `<details>` rather than framework expanders deliberately. The
    live view repaints about once a second, and an expander closes itself on
    every repaint; a `<details>` element is browser state and stays where the
    reader put it. One renderer therefore serves the run in flight, the run
    read back off disk, and the rendered video.

    `force_open` exists for the video, which has no one to click.
    """
    if not phases:
        return '<div class="tl-legend">No phases in this trace yet.</div>'

    opened = force_open or set()
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
        is_open = state in {"active", "failed"} or p["phase"] in opened

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
            lines = "".join(f"<div>{esc(fact)}</div>" for fact in facts if fact)
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
                    f"{tool} ×{n}" for tool, n in list(tools_for(p, name).items())[:5]
                )
                mark = {
                    "active": "working", "done": "done", "pending": "not yet",
                    "skipped": "—", "idle": "no recorded calls",
                }.get(info["state"], "")
                detail = tools or (
                    "no tool calls — this agent writes rather than searches"
                    if info.get("inferred")
                    else mark
                )
                colour_attr = f' style="color:{ACCENT}"' if info["state"] == "active" else ""
                rows.append(
                    f'<div class="st-agent"><div{colour_attr}>{esc(name)}'
                    + (
                        f' <span style="opacity:.6">({info["tool_calls"]})</span>'
                        if info["tool_calls"]
                        else ""
                    )
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
            lines_out = []
            for r in shown[:event_limit]:
                kind = tracelib.KIND_LABELS.get(r.get("kind"), r.get("kind"))
                msg = esc(tracelib.clean_message(r)[:200])
                lines_out.append(
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
                f'<div class="st-ev">{"<br>".join(lines_out)}</div>{more}</div>'
            )

        cards.append(
            f'<details id="card-{esc(p["phase"])}" class="{" ".join(classes)}"'
            + (" open" if is_open else "")
            + ">"
            + head
            + f'<div class="st-body">{"".join(body)}</div></details>'
        )

    return '<div class="dr-steps">' + "".join(cards) + "</div>"


def tools_for(phase: dict, agent: str) -> dict[str, int]:
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


def graph_svg(snap: dict, *, animate: bool = True, motion: float = 0.0) -> str:
    """The whole crew as one picture, at one moment of the run.

    The timeline shows how long each phase took; it cannot show the shape of
    the thing, and the shape is what carries the argument. The fork into two
    research legs, the join that waits for both, and the loop the audit can
    send a draft round -- those are the design, and a row of bars implies a
    straight line instead.

    Colour carries state and nothing else: the accent means running now, a
    neutral fill means finished, a dashed outline means not yet. Every node
    also carries its own counts, so the picture is never the only evidence.

    `animate=False` swaps the browser's own animation clock for `motion`, a
    position from 0 to 1 that the caller advances itself. A video needs each
    frame placed deliberately; catching the browser's animation wherever it
    happened to be would flicker.
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
        offset = ""
        flight = ""
        if edge["state"] == "in_flight":
            if animate:
                flight = ' class="g-flight"'
            else:
                offset = f' stroke-dashoffset="{-20 * motion:.2f}"'
        gloss = graphlib.PAYLOAD_PLAIN.get(edge["payload"], "")
        out.append(
            f'<path d="{edge["d"]}" fill="none" stroke="{colour}" '
            f'stroke-width="{stroke_w}"{dash_attr}{offset}{flight} '
            f'marker-end="url(#{marker})"><title>{esc(edge["payload"])}'
            + (f" — {esc(gloss)}" if gloss else "")
            + "</title></path>"
        )
        if edge["state"] == "in_flight":
            if animate:
                out.append(
                    f'<circle r="4" fill="{ACCENT}"><animateMotion dur="1.7s" '
                    f'repeatCount="indefinite" path="{edge["d"]}"/></circle>'
                )
            else:
                cx, cy = graphlib.point_at(edge["d"], motion)
                out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="4" fill="{ACCENT}"/>')
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
            halo_class = ' class="g-halo"' if animate else ""
            # The stylesheet's pulse runs 0.5 -> 0 -> 0.5; matched here so a
            # frozen frame looks like a frame of the same animation.
            halo_opacity = "" if animate else f' opacity="{abs(0.5 - motion):.3f}"'
            out.append(
                f'<rect{halo_class} x="{x - 5}" y="{y - 5}" width="{w + 10}" '
                f'height="{h + 10}" rx="11" fill="none" stroke="{G_STROKE[state]}" '
                f'stroke-width="2"{halo_opacity}/>'
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
                    f">{esc(name)}</text>"
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
            badge_y = y + 29 if node["crew"] else y + 17
            out.append(
                f'<text x="{x + w - 9}" y="{badge_y}" text-anchor="end" font-size="8.8" '
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
    return '<div class="dr-graph">' + "".join(out) + "</div>"


def graph_legend_html(snap: dict, cursor: float, total: float) -> str:
    """One line saying what the picture above is currently showing."""
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
    return (
        f'<div class="g-legend">{headline}<br>'
        f'<span class="g-swatch" style="background:{ACCENT}"></span>running now'
        '&nbsp;&nbsp;<span class="g-swatch" style="background:rgba(127,127,127,.45)">'
        "</span>finished"
        '&nbsp;&nbsp;<span class="g-swatch" style="border:1px dashed '
        'rgba(127,127,127,.55);background:none"></span>not yet reached'
        "&nbsp;&nbsp;· labels on the arrows are the typed objects the phases actually "
        "exchange, not a description of them. Hover one to read it in plain English."
        "</div>"
    )
