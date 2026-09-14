"""Reading a run trace back.

The trace is written as one JSON object per line while the run happens. That
shape is right for writing -- append-only, no seeking, survives a crash
mid-run -- and wrong for looking at, because what a reader wants is the
structure: which phase took the time, what each phase actually did, where the
tokens went.

This module turns the flat stream into that structure. It is deliberately
separate from the interface that displays it, for two reasons: the same shaping
serves a live run and a saved trace read off disk, so the demonstration view
and the audit view cannot tell different stories; and the arithmetic here can
be checked against a committed trace without starting a browser.

Every function takes the raw records and returns plain data. Nothing here
imports the framework, so a trace can be read without it.
"""

from __future__ import annotations

import json
from pathlib import Path

PHASE_LABELS = {
    "intake": "Framing the question",
    "plan": "Scoping the research",
    "research_external": "Researching public sources",
    "assess_internal": "Reading internal records",
    "fuse": "Cross-checking the two sides",
    "draft_and_audit": "Writing and verifying the memo",
    "audit_gate": "Deciding whether to publish",
    "revise": "Revising after audit",
    "after_revision": "Re-checking the revision",
    "publish": "Publishing",
}

# What each phase is for, in the language of the person the report is for.
PHASE_PURPOSE = {
    "intake": "Normalise the question and stamp the run.",
    "plan": "Turn the question into sub-questions, segments and search queries.",
    "research_external": "Four analysts read the public record in parallel, then an editor drops anything unsourced.",
    "assess_internal": "Exact arithmetic over the internal records, plus retrieval for what the numbers cannot say.",
    "fuse": "Test each adopted position against the evidence, then keep only findings that need both sources.",
    "draft_and_audit": "Write the memo, then check every claim against the record it cites.",
    "audit_gate": "Publish, or send the draft back once.",
    "revise": "Rewrite the claims the audit rejected.",
    "after_revision": "Confirm the revision before publishing.",
    "publish": "Write the memo, the evidence ledger and the run manifest.",
}

# Which phase each task belongs to. This is a static map on purpose.
#
# Attributing work to a phase by timestamp alone is wrong here, and wrong in a
# way that flatters the numbers: the external and internal research phases
# genuinely run concurrently, so their windows overlap, and any event inside
# the overlap lands in both. That double-counts every tool call made while both
# legs were running. Task names are fixed in the crew YAML, so mapping them
# directly gives one honest home for each piece of work.
TASK_PHASE = {
    "scope_research": "plan",
    "research_shifts": "research_external",
    "research_incumbents": "research_external",
    "research_entrants": "research_external",
    "research_capital": "research_external",
    "consolidate_external": "research_external",
    "analyse_position": "assess_internal",
    "gather_internal_evidence": "assess_internal",
    "test_adopted_positions": "fuse",
    "fuse_signals": "fuse",
    "challenge_signals": "fuse",
    "draft_memo": "draft_and_audit",
    "audit_memo": "draft_and_audit",
}

# Which crew each agent belongs to, and which crew each phase runs.
#
# Together these attribute work correctly while two phases are running at
# once. The agent identifies the crew; the phase window identifies which run
# of that crew, which matters because the writer crew runs twice when the
# audit sends a draft back.
#
# The obvious alternative -- read the agent off the task-start event -- does
# not work: `TaskStartedEvent.agent_role` is None in this version, so matching
# on it silently failed and every tool call made during the concurrent research
# phases was attributed to whichever window happened to be narrower. The
# internal leg showed zero tool calls while plainly making dozens.
AGENT_CREW = {
    "Research Director": "ScopingCrew",
    "Market Shift Analyst": "ExternalCrew",
    "Incumbent Strategy Analyst": "ExternalCrew",
    "New Entrant Scout": "ExternalCrew",
    "Capital Flows Analyst": "ExternalCrew",
    "Research Editor": "ExternalCrew",
    "Internal Position Analyst": "InternalCrew",
    "Internal Evidence Archivist": "InternalCrew",
    "Adopted Position Examiner": "FusionCrew",
    "Cross-Signal Analyst": "FusionCrew",
    "Contrarian Reviewer": "FusionCrew",
    "Memo Writer": "WriterCrew",
    "Citation Auditor": "WriterCrew",
}

PHASE_CREW = {
    "plan": "ScopingCrew",
    "research_external": "ExternalCrew",
    "assess_internal": "InternalCrew",
    "fuse": "FusionCrew",
    "draft_and_audit": "WriterCrew",
    "revise": "WriterCrew",
}

KIND_LABELS = {
    "phase_start": "phase begins",
    "phase_end": "phase ends",
    "crew_start": "crew starts",
    "crew_end": "crew finishes",
    "task_start": "task starts",
    "task_end": "task finishes",
    "agent_start": "agent works",
    "tool": "tool call",
    "llm": "model call",
    "audit": "verification",
    "fusion": "verification",
    "tool_failure": "tool failed",
    "tool_error": "tool error",
    "error": "phase failed",
}

PROBLEM_KINDS = {"tool_failure", "tool_error", "error"}


def load(path: str | Path) -> list[dict]:
    """Read a trace file. Tolerates a partial last line from a live run."""
    rows: list[dict] = []
    p = Path(path)
    if not p.exists():
        return rows
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            # The run is still writing. A half-written final line is expected,
            # not a corrupt file.
            continue
    return rows


def phases(records: list[dict]) -> list[dict]:
    """Phase windows in the order they began, with what happened inside each.

    A phase that has begun but not finished is reported as still running with
    its end set to the last event seen, so a live run renders the same way a
    finished one does.
    """
    last_t = max((r.get("t", 0) for r in records), default=0.0)
    out: list[dict] = []
    open_by_name: dict[str, dict] = {}

    for rec in records:
        kind = rec.get("kind")
        name = rec.get("phase")
        if kind == "phase_start" and name:
            entry = {
                "phase": name,
                "label": PHASE_LABELS.get(name, name),
                "purpose": PHASE_PURPOSE.get(name, ""),
                "start": float(rec.get("t", 0.0)),
                "end": None,
                "running": True,
            }
            out.append(entry)
            open_by_name[name] = entry
        elif kind == "phase_end" and name in open_by_name:
            entry = open_by_name.pop(name)
            entry["end"] = float(rec.get("t", 0.0))
            entry["running"] = False

    for entry in out:
        if entry["end"] is None:
            entry["end"] = last_t
        entry["duration"] = round(max(entry["end"] - entry["start"], 0.0), 2)

    # Concurrent phases mean overlapping windows, so work is attributed
    # through the task that did it rather than by timestamp. Anything with no
    # task -- the verification events the flow emits itself -- falls back to
    # the time window, which is correct for those because they are emitted
    # from the phase method directly.
    assign(records)
    by_phase: dict[str, list[dict]] = {}
    for rec in records:
        name = rec.get("_phase")
        if name:
            by_phase.setdefault(name, []).append(rec)

    for entry in out:
        inside = by_phase.get(entry["phase"], [])
        entry["events"] = inside
        entry["tool_calls"] = sum(1 for r in inside if r.get("kind") == "tool")
        entry["llm_calls"] = sum(1 for r in inside if r.get("kind") == "llm")
        entry["prompt_tokens"] = sum(int(r.get("prompt_tokens") or 0) for r in inside)
        entry["completion_tokens"] = sum(int(r.get("completion_tokens") or 0) for r in inside)
        entry["problems"] = sum(1 for r in inside if r.get("kind") in PROBLEM_KINDS)
        entry["agents"] = sorted({clean_agent(r["agent"]) for r in inside if r.get("agent")})
        entry["tools"] = _counted(r.get("tool") for r in inside if r.get("kind") == "tool")
        entry["by_agent"] = _agent_rollup(inside)
        entry["task_names"] = [
            r["task"] for r in inside if r.get("kind") == "task_start" and r.get("task")
        ]

    # Flag genuine concurrency, because it is a claim the architecture makes
    # and this is the evidence for it.
    for a in out:
        a["concurrent_with"] = sorted(
            b["label"]
            for b in out
            if b is not a and a["start"] < b["end"] and b["start"] < a["end"]
        )
    return out


def _agent_rollup(events: list[dict]) -> list[dict]:
    """Who did the work in a phase, and how much of it.

    Grouped by agent rather than by task on purpose. The four external
    research tasks run concurrently, so their windows overlap and a tool call
    inside the overlap cannot be assigned to one of them without guessing.
    The agent that made the call is always known, and it is the more
    meaningful unit anyway: "the New Entrant Scout made fourteen tool calls"
    says something, where a task name mostly repeats the phase.
    """
    rows: dict[str, dict] = {}
    for r in events:
        name = clean_agent(r.get("agent"))
        if not name:
            continue
        row = rows.setdefault(
            name, {"agent": name, "tool_calls": 0, "llm_calls": 0, "tools": {}}
        )
        if r.get("kind") == "tool":
            row["tool_calls"] += 1
            tool = r.get("tool")
            if tool:
                row["tools"][tool] = row["tools"].get(tool, 0) + 1
        elif r.get("kind") == "llm":
            row["llm_calls"] += 1
    return sorted(rows.values(), key=lambda row: (-row["tool_calls"], row["agent"]))


def assign(records: list[dict]) -> list[dict]:
    """Tag each record in place with the phase that owns it, as `_phase`.

    Resolution order, most reliable first: a record naming a task is placed by
    that task; a record naming an agent is placed in the phase window whose
    crew that agent belongs to; anything left is placed in the narrowest phase
    window containing it, which is right for the verification events because
    the flow emits those from the phase method itself.
    """
    last_t = max((r.get("t", 0.0) for r in records), default=0.0)

    phase_windows: list[dict] = []
    open_phases: dict[str, dict] = {}
    for rec in records:
        kind, name = rec.get("kind"), rec.get("phase")
        if kind == "phase_start" and name:
            w = {"phase": name, "start": float(rec.get("t", 0.0)), "end": None}
            phase_windows.append(w)
            open_phases[name] = w
        elif kind == "phase_end" and name in open_phases:
            open_phases.pop(name)["end"] = float(rec.get("t", 0.0))
    for w in phase_windows:
        if w["end"] is None:
            w["end"] = last_t

    for rec in records:
        rec["_phase"] = None
        t = float(rec.get("t", 0.0))
        containing = [w for w in phase_windows if w["start"] <= t <= w["end"]]

        task_name = rec.get("task")
        if task_name and TASK_PHASE.get(task_name):
            rec["_phase"] = TASK_PHASE[task_name]
            # The writer crew runs twice; the second run belongs to `revise`.
            if rec["_phase"] == "draft_and_audit" and any(
                w["phase"] == "revise" for w in containing
            ):
                rec["_phase"] = "revise"
            continue

        crew = AGENT_CREW.get(clean_agent(rec.get("agent")))
        if crew:
            matched = [w for w in containing if PHASE_CREW.get(w["phase"]) == crew]
            if matched:
                rec["_phase"] = min(matched, key=lambda w: w["end"] - w["start"])["phase"]
                continue

        if containing:
            rec["_phase"] = min(containing, key=lambda w: w["end"] - w["start"])["phase"]
    return records


def clean_agent(name: str | None) -> str:
    """Agent roles as written in YAML carry newlines and interpolated context."""
    text = str(name or "").strip()
    if " for " in text:
        text = text.split(" for ", 1)[0]
    return text


def _counted(values) -> dict[str, int]:
    counts: dict[str, int] = {}
    for v in values:
        if v:
            counts[v] = counts.get(v, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def tool_usage(records: list[dict]) -> dict[str, int]:
    return _counted(r.get("tool") for r in records if r.get("kind") == "tool")


def agent_activity(records: list[dict]) -> list[dict]:
    """Per agent: how many tool calls and model calls it made."""
    agents: dict[str, dict] = {}
    for r in records:
        name = r.get("agent")
        if not name:
            continue
        row = agents.setdefault(name, {"agent": name, "tool_calls": 0, "llm_calls": 0, "tasks": 0})
        if r.get("kind") == "tool":
            row["tool_calls"] += 1
        elif r.get("kind") == "llm":
            row["llm_calls"] += 1
        elif r.get("kind") == "task_start":
            row["tasks"] += 1
    return sorted(agents.values(), key=lambda row: -row["tool_calls"])


def tasks(records: list[dict]) -> list[dict]:
    """Task windows, so the work inside a phase can be seen in order."""
    started: dict[str, dict] = {}
    out: list[dict] = []
    last_t = max((r.get("t", 0) for r in records), default=0.0)
    for r in records:
        name = r.get("task")
        if not name:
            continue
        if r.get("kind") == "task_start":
            entry = {
                "task": name,
                "agent": r.get("agent"),
                "start": float(r.get("t", 0.0)),
                "end": None,
            }
            out.append(entry)
            started[name] = entry
        elif r.get("kind") == "task_end" and name in started:
            started.pop(name)["end"] = float(r.get("t", 0.0))
    for entry in out:
        if entry["end"] is None:
            entry["end"] = last_t
        entry["duration"] = round(max(entry["end"] - entry["start"], 0.0), 2)
    return out


def totals(records: list[dict]) -> dict:
    """Run-level counts, derived from the trace rather than passed alongside it."""
    return {
        "records": len(records),
        "elapsed_seconds": round(max((r.get("t", 0.0) for r in records), default=0.0), 1),
        "phases": sum(1 for r in records if r.get("kind") == "phase_start"),
        "crews": sum(1 for r in records if r.get("kind") == "crew_start"),
        "tasks_completed": sum(1 for r in records if r.get("kind") == "task_end"),
        "tool_calls": sum(1 for r in records if r.get("kind") == "tool"),
        "llm_calls": sum(1 for r in records if r.get("kind") == "llm"),
        "prompt_tokens": sum(int(r.get("prompt_tokens") or 0) for r in records),
        "completion_tokens": sum(int(r.get("completion_tokens") or 0) for r in records),
        "problems": sum(1 for r in records if r.get("kind") in PROBLEM_KINDS),
    }


def verification_events(records: list[dict]) -> list[dict]:
    """The audit and cross-check events, which are the point of the trace."""
    return [r for r in records if r.get("kind") in {"audit", "fusion"}]


def problems(records: list[dict]) -> list[dict]:
    return [r for r in records if r.get("kind") in PROBLEM_KINDS]


def tool_argument(message: str) -> str:
    """The argument part of a tool-call message, for display.

    The message is written as "tool   <name> <args>". Splitting it here keeps
    the display code from having to know that format.
    """
    text = (message or "").replace("tool   ", "", 1).strip()
    for _ in range(1):
        # Drop the tool name, which is shown in its own column.
        parts = text.split(" ", 1)
        if len(parts) == 2 and parts[1].startswith("{"):
            return parts[1]
    start = text.find("{")
    return text[start:] if start >= 0 else ""


def clean_message(record: dict) -> str:
    """A trace line with its fixed-width prefix removed."""
    msg = str(record.get("message", ""))
    for prefix in ("PHASE  ", "done   ", "crew   ", "task   ", "agent  ", "tool   ",
                   "model  ", "audit  ", "fusion ", "error  ", "FAILED ", "ERROR  "):
        if msg.startswith(prefix):
            return msg[len(prefix):].strip()
    return msg.strip()
