"""One event subscription, three consumers.

Three things need to know what a run is doing: the operator watching a
terminal, the demonstration interface showing progress, and an audit record of
what the agents actually did. Instrumenting those separately guarantees they
will eventually disagree about what happened, so there is a single subscription
to the framework's event bus that fans out to all three.

The trace file is therefore a side effect of the observability work rather than
a separate feature, which is the cheapest possible way to have an audit trail.

One footgun worth naming: a listener must be instantiated *and* keep a
module-level reference, or its handlers are garbage-collected and silently
never fire. `install()` exists to make that hard to get wrong.

See docs/DECISIONS.md, D-16.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path

from crewai.events import BaseEventListener
from crewai.events.types.agent_events import AgentExecutionStartedEvent
from crewai.events.types.crew_events import CrewKickoffCompletedEvent, CrewKickoffStartedEvent
from crewai.events.types.flow_events import (
    MethodExecutionFinishedEvent,
    MethodExecutionStartedEvent,
)
from crewai.events.types.llm_events import LLMCallCompletedEvent
from crewai.events.types.task_events import TaskCompletedEvent, TaskStartedEvent
from crewai.events.types.tool_usage_events import (
    ToolUsageErrorEvent,
    ToolUsageFinishedEvent,
    ToolUsageStartedEvent,
)

# Human-readable names for the flow's phases, used by the terminal and the UI.
PHASE_LABELS = {
    "intake": "Framing the question",
    "plan": "Scoping the research",
    "research_external": "Researching public sources",
    "assess_internal": "Reading internal records",
    "fuse": "Cross-checking the two sides",
    "draft_and_audit": "Writing and verifying the memo",
    "revise": "Revising after audit",
    "publish": "Publishing",
}


class RunObserver(BaseEventListener):
    """Fans framework events out to terminal, an in-process queue and disk."""

    def __init__(self, trace_path: Path | None = None, echo: bool = True):
        super().__init__()
        self.events: queue.Queue = queue.Queue()
        self.echo = echo
        self.trace_path = trace_path
        self._lock = threading.Lock()
        self._t0 = time.monotonic()
        self.counters = {
            "tool_calls": 0,
            "tool_cache_hits": 0,
            "tool_failures": 0,
            "llm_calls": 0,
            "tasks_completed": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }
        self.tools_used: dict[str, int] = {}
        if self.trace_path:
            self.trace_path.parent.mkdir(parents=True, exist_ok=True)
            self.trace_path.write_text("", encoding="utf-8")

    # -- fan-out ----------------------------------------------------------
    def _emit(self, kind: str, message: str, **extra) -> None:
        record = {
            "t": round(time.monotonic() - self._t0, 2),
            "kind": kind,
            "message": message,
            **extra,
        }
        with self._lock:
            self.events.put(record)
            if self.trace_path:
                with self.trace_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, default=str) + "\n")
            if self.echo:
                print(f"  [{record['t']:>7.2f}s] {message}", flush=True)

    def drain(self) -> list[dict]:
        """Pull everything queued. Used by the interface to poll progress."""
        out = []
        while True:
            try:
                out.append(self.events.get_nowait())
            except queue.Empty:
                return out

    def summary(self) -> dict:
        return {
            "elapsed_seconds": round(time.monotonic() - self._t0, 1),
            **self.counters,
            "tools_used": dict(sorted(self.tools_used.items(), key=lambda kv: -kv[1])),
        }

    # -- subscriptions ----------------------------------------------------
    def setup_listeners(self, bus):  # noqa: C901 - a flat registration table
        @bus.on(MethodExecutionStartedEvent)
        def _phase_start(source, event):
            label = PHASE_LABELS.get(event.method_name, event.method_name)
            self._emit("phase_start", f"PHASE  {label}", phase=event.method_name)

        @bus.on(MethodExecutionFinishedEvent)
        def _phase_end(source, event):
            label = PHASE_LABELS.get(event.method_name, event.method_name)
            self._emit("phase_end", f"done   {label}", phase=event.method_name)

        @bus.on(CrewKickoffStartedEvent)
        def _crew_start(source, event):
            self._emit("crew_start", f"crew   {event.crew_name or 'crew'} started",
                       crew=event.crew_name)

        @bus.on(CrewKickoffCompletedEvent)
        def _crew_end(source, event):
            self._emit("crew_end", f"crew   {event.crew_name or 'crew'} finished",
                       crew=event.crew_name)

        @bus.on(TaskStartedEvent)
        def _task_start(source, event):
            self._emit("task_start", f"task   {event.task_name or 'task'}",
                       task=event.task_name, agent=event.agent_role)

        @bus.on(TaskCompletedEvent)
        def _task_end(source, event):
            self.counters["tasks_completed"] += 1
            self._emit("task_end", f"task   {event.task_name or 'task'} complete",
                       task=event.task_name)

        @bus.on(AgentExecutionStartedEvent)
        def _agent_start(source, event):
            self._emit("agent_start", f"agent  {event.agent_role}", agent=event.agent_role)

        @bus.on(ToolUsageStartedEvent)
        def _tool_start(source, event):
            self.counters["tool_calls"] += 1
            self.tools_used[event.tool_name] = self.tools_used.get(event.tool_name, 0) + 1
            args = str(event.tool_args or "")[:120]
            self._emit("tool", f"tool   {event.tool_name} {args}",
                       tool=event.tool_name, agent=event.agent_role)

        @bus.on(ToolUsageFinishedEvent)
        def _tool_end(source, event):
            if getattr(event, "from_cache", False):
                self.counters["tool_cache_hits"] += 1
            if getattr(event, "failure", None):
                self.counters["tool_failures"] += 1
                self._emit("tool_failure", f"FAILED {event.tool_name}", tool=event.tool_name)

        @bus.on(ToolUsageErrorEvent)
        def _tool_error(source, event):
            self.counters["tool_failures"] += 1
            self._emit("tool_error", f"ERROR  {event.tool_name}: {str(event.error)[:160]}",
                       tool=event.tool_name)

        @bus.on(LLMCallCompletedEvent)
        def _llm_done(source, event):
            self.counters["llm_calls"] += 1
            got = {"prompt_tokens": 0, "completion_tokens": 0}
            usage = getattr(event, "usage", None)
            if usage is not None:
                for key in got:
                    val = getattr(usage, key, None)
                    if val is None and isinstance(usage, dict):
                        val = usage.get(key)
                    if isinstance(val, int):
                        self.counters[key] += val
                        got[key] = val
            # Recorded as an event, not just counted. Without this the trace
            # can show which phase made which tool call but not where the
            # tokens went -- so per-phase cost attribution, which is one of
            # the stated reasons for having an explicit flow, was not actually
            # answerable from the audit trail.
            self._emit(
                "llm",
                f"model  {event.model or 'model'} "
                f"({got['prompt_tokens']:,} in / {got['completion_tokens']:,} out)",
                model=event.model,
                agent=getattr(event, "agent_role", None),
                prompt_tokens=got["prompt_tokens"],
                completion_tokens=got["completion_tokens"],
            )


# A module-level reference is required: without one the handlers are collected
# and stop firing, with no error to explain it.
OBSERVER: RunObserver | None = None


def install(trace_path: Path | None = None, echo: bool = True) -> RunObserver:
    """Create and retain the observer. Safe to call more than once."""
    global OBSERVER
    if OBSERVER is None:
        OBSERVER = RunObserver(trace_path=trace_path, echo=echo)
    return OBSERVER


def observer() -> RunObserver | None:
    return OBSERVER
