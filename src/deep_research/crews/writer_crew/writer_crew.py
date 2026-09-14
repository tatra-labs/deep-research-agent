"""Draft the memo, then have a different agent try to fail it.

The writer and the auditor are separate agents on purpose. An agent asked to
write a memo and then verify its own citations has an interest in finishing,
and that interest quietly lowers the bar. Separating them means the check does
not share the writer's incentive.

The auditor gets the record lookup tool and is instructed to resolve every
identifier rather than recognise it, because the failure that matters most is
a real identifier attached to a claim it does not support -- that one looks
verified.

This crew returns two outputs: `tasks_output[0].pydantic` is the memo,
`tasks_output[1].pydantic` is the verdict. The flow reads both.

See docs/DECISIONS.md, D-12.
"""

from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task

from ...llms import synth_llm
from ...state import AuditVerdict, ExecutiveMemo
from ...tools.internal_analytics import SegmentStressTool
from ...tools.record_lookup import RecordLookupTool


@CrewBase
class WriterCrew:
    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    @agent
    def memo_writer(self) -> Agent:
        return Agent(
            config=self.agents_config["memo_writer"],
            tools=[RecordLookupTool(), SegmentStressTool()],
            llm=synth_llm(),
            verbose=True,
            max_iter=10,
        )

    @agent
    def citation_auditor(self) -> Agent:
        return Agent(
            config=self.agents_config["citation_auditor"],
            # Lookup only. The totalling tool was given to this agent when it
            # was still responsible for checking figures; that duty moved into
            # code, so the tool went with it. An agent holding a tool it has
            # been told not to use is an invitation to use it.
            tools=[RecordLookupTool()],
            llm=synth_llm(),
            verbose=True,
            max_iter=14,
        )

    @task
    def draft_memo(self) -> Task:
        return Task(
            config=self.tasks_config["draft_memo"],
            output_pydantic=ExecutiveMemo,
        )

    @task
    def audit_memo(self) -> Task:
        return Task(
            config=self.tasks_config["audit_memo"],
            output_pydantic=AuditVerdict,
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )
