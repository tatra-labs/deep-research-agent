"""The join. This is the step the whole system exists to perform.

Two tasks rather than one, because generating candidate joins and destroying
the weak ones are different jobs with opposed incentives: an agent asked to
produce findings and then grade its own findings grades generously. The
reviewer's amputation test -- remove one side of the evidence and see whether
the finding still stands -- is what keeps ordinary market commentary out of the
signal matrix.

Both tasks run on the upper model tier. This is the least token-heavy phase and
the one where a weaker model shows most.
"""

from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task

from ...llms import synth_llm
from ...state import FusionResult, PositionTest
from ...tools.internal_analytics import CompetitorExposureTool, SegmentStressTool
from ...tools.record_lookup import RecordLookupTool


@CrewBase
class FusionCrew:
    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    @agent
    def cross_signal_analyst(self) -> Agent:
        return Agent(
            config=self.agents_config["cross_signal_analyst"],
            # Given read access to the exact internal figures so a candidate
            # join can be checked against the records, not against a paraphrase.
            tools=[SegmentStressTool(), CompetitorExposureTool(), RecordLookupTool()],
            llm=synth_llm(),
            verbose=True,
            max_iter=12,
        )

    @agent
    def contrarian_reviewer(self) -> Agent:
        return Agent(
            config=self.agents_config["contrarian_reviewer"],
            tools=[RecordLookupTool()],
            llm=synth_llm(),
            verbose=True,
            max_iter=10,
        )

    @agent
    def position_examiner(self) -> Agent:
        return Agent(
            config=self.agents_config["position_examiner"],
            tools=[RecordLookupTool()],
            llm=synth_llm(),
            verbose=True,
            max_iter=8,
        )

    @task
    def test_adopted_positions(self) -> Task:
        """Runs first, so a long findings list cannot crowd it out."""
        return Task(
            config=self.tasks_config["test_adopted_positions"],
            output_pydantic=PositionTest,
        )

    @task
    def fuse_signals(self) -> Task:
        return Task(config=self.tasks_config["fuse_signals"])

    @task
    def challenge_signals(self) -> Task:
        return Task(
            config=self.tasks_config["challenge_signals"],
            output_pydantic=FusionResult,
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )
