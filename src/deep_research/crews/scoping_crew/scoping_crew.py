"""Turns a topic into a decision-shaped research brief."""

from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task

from ...llms import synth_llm
from ...state import ResearchBrief


@CrewBase
class ScopingCrew:
    """Single agent. Planning is cheap here and expensive to get wrong."""

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    @agent
    def research_director(self) -> Agent:
        return Agent(
            config=self.agents_config["research_director"],
            llm=synth_llm(),
            verbose=True,
            max_iter=8,
        )

    @task
    def scope_research(self) -> Task:
        return Task(
            config=self.tasks_config["scope_research"],
            output_pydantic=ResearchBrief,
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )
