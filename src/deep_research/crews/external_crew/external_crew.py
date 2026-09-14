"""Four parallel research streams over public sources, then a consolidation.

The four streams map one-to-one onto the four questions the brief has to
answer -- what is changing, how leaders are moving, who is new, where capital
is going -- and run concurrently because they share no state. The final task
consolidates rather than researches, which is where unsourced claims get
dropped.
"""

from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task

from ...llms import synth_llm, worker_llm
from ...state import ExternalFindings
from ...tools.read_url import ReadWebPageTool
from ...tools.search import (
    CommunitySignalTool,
    FilingSearchTool,
    ResearchSignalTool,
    WebSearchTool,
)


@CrewBase
class ExternalCrew:
    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    # Scouting and reading is high call volume, so it runs on the worker tier.
    def _scout(self, name: str, tools: list) -> Agent:
        return Agent(
            config=self.agents_config[name],
            tools=tools,
            llm=worker_llm(),
            verbose=True,
            max_iter=12,
            respect_context_window=True,
        )

    @agent
    def shift_analyst(self) -> Agent:
        return self._scout("shift_analyst", [WebSearchTool(), ReadWebPageTool(), ResearchSignalTool()])

    @agent
    def incumbent_analyst(self) -> Agent:
        return self._scout("incumbent_analyst", [FilingSearchTool(), WebSearchTool(), ReadWebPageTool()])

    @agent
    def entrant_scout(self) -> Agent:
        return self._scout(
            "entrant_scout",
            [FilingSearchTool(), CommunitySignalTool(), WebSearchTool(), ReadWebPageTool()],
        )

    @agent
    def capital_analyst(self) -> Agent:
        return self._scout("capital_analyst", [WebSearchTool(), FilingSearchTool(), ReadWebPageTool()])

    @agent
    def research_editor(self) -> Agent:
        """Consolidation is judgement, not extraction, so it runs a tier up.

        Deliberately given no tools. Handed a search tool, an editor re-runs
        the research instead of editing it, and the streams stop being
        independent. It also needs the larger completion budget: this is the
        one task that emits the whole typed evidence set at once, and a
        structured-output call that runs out of completion tokens does not
        return a partial object -- it raises.
        """
        return Agent(
            config=self.agents_config["research_editor"],
            tools=[],
            llm=synth_llm(),
            verbose=True,
            max_iter=4,
            respect_context_window=True,
        )

    @task
    def research_shifts(self) -> Task:
        return Task(config=self.tasks_config["research_shifts"])

    @task
    def research_incumbents(self) -> Task:
        return Task(config=self.tasks_config["research_incumbents"])

    @task
    def research_entrants(self) -> Task:
        return Task(config=self.tasks_config["research_entrants"])

    @task
    def research_capital(self) -> Task:
        return Task(config=self.tasks_config["research_capital"])

    @task
    def consolidate_external(self) -> Task:
        # Consolidation is judgement, not extraction, so it moves up a tier.
        return Task(
            config=self.tasks_config["consolidate_external"],
            output_pydantic=ExternalFindings,
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )
