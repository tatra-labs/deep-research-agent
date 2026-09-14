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

import json
import os
import threading
import time
from pathlib import Path

import streamlit as st


PROJECT_ROOT = Path(__file__).resolve().parent

st.set_page_config(
    page_title="Deep Research Agent",
    page_icon="\U0001f9ed",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Two ways of looking at the same run, plus both at once. They share a cursor,
# so switching never loses the reader's place -- which is the point of offering
# a choice rather than replacing one view with the other.
VIEW_MODES = ["Timeline & cards", "Crew graph", "Both"]


def load_env() -> None:
    """Make credentials visible to the code that reads the environment.

    Locally that is a .env file. On a hosted deployment there is no .env --
    the credential is entered in the app's settings and arrives as
    `st.secrets` -- but the flow, the tools and the model configuration all
    read `os.environ`, because they have to work from the command line too.
    Bridging the two here is a line of code; threading a secrets object
    through every call site would be a rewrite, and would leave the CLI
    reading one source and the interface another.
    """
    try:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env")
    except Exception:
        pass
    try:
        for key, value in st.secrets.items():
            if isinstance(value, str) and not os.environ.get(key):
                os.environ[key] = value
    except Exception:
        # No secrets configured, which is the normal case everywhere except
        # the hosted app. Reading st.secrets without a secrets file raises.
        pass


load_env()

# A public deployment exposes a button that spends real money on every press.
# Setting DR_DISABLE_RUN in the app's secrets turns the interface into a
# reader of runs already committed, without a redeploy or a code change.
RUNS_ENABLED = os.getenv("DR_DISABLE_RUN", "").strip().lower() not in {
    "1", "true", "yes", "on",
}

from deep_research import llms  # noqa: E402
from deep_research.observability import graph as graphlib  # noqa: E402
from deep_research.observability import trace as tracelib  # noqa: E402
from deep_research.observability import views  # noqa: E402
from deep_research.observability.views import ACCENT, esc  # noqa: E402

# The stylesheet is the same one the rendered walkthrough uses, for the same
# reason the markup is shared: one description of how a run looks.
st.markdown(f"<style>{views.STYLESHEET}</style>", unsafe_allow_html=True)

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
# The trace views
# --------------------------------------------------------------------------
# The markup for all three lives in observability/views.py, so the interface,
# a run in flight and the rendered walkthrough draw from one renderer. Three
# copies of this markup would eventually disagree about one set of events,
# which is the exact failure the observability layer exists to prevent.
def render_timeline(phases: list[dict], total: float, cursor: float | None = None) -> None:
    st.markdown(views.timeline_html(phases, total, cursor), unsafe_allow_html=True)


def render_cards(
    phases: list[dict],
    snap: dict,
    cursor: float,
    ledger: dict,
    manifest: dict,
    *,
    event_limit: int = 90,
) -> None:
    st.markdown(
        views.cards_html(phases, snap, ledger, manifest, event_limit=event_limit),
        unsafe_allow_html=True,
    )


def render_graph(snap: dict, cursor: float, total: float) -> None:
    st.markdown(views.graph_svg(snap), unsafe_allow_html=True)
    st.markdown(views.graph_legend_html(snap, cursor, total), unsafe_allow_html=True)


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
        "Run research",
        type="primary",
        width="stretch",
        disabled=not (key_ok and RUNS_ENABLED),
    )
    if not RUNS_ENABLED:
        st.caption(
            "Running is switched off on this deployment. Every saved run below "
            "still opens in full, including its trace."
        )

    st.divider()
    st.caption("Configuration")
    st.write(f"{'OK' if key_ok else 'MISSING'} — model credential")
    st.caption(f"Reading: `{llms.worker_model()}`")
    st.caption(f"Thinking: `{llms.synth_model()}`")
    if not key_ok:
        st.warning(
            "No model credential, so a new run cannot start. Saved runs still "
            "display in full. Set OPENAI_API_KEY in .env locally, or in the "
            "app's secrets when hosted."
        )

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
