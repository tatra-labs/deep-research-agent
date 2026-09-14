"""Reads the customer's own records: exact arithmetic plus semantic retrieval.

Two agents, deliberately different in instrument.

The position analyst gets deterministic analysis tools. Aggregating 48 deal
rows is arithmetic, and semantic retrieval returns only a handful of chunks --
so an agent asked to count from retrieved snippets produces confident wrong
numbers. Those tools compute over the complete files and return the record
identifiers behind every figure.

The archivist gets semantic retrieval over the same corpus, because "what did
buyers actually say" is a meaning question that no aggregation can answer.

Retrieval runs against a corpus rewritten so every chunk carries its own
citation tag, which is what lets the audit gate verify the result.

See docs/DECISIONS.md, D-07 and D-20.
"""

from crewai import Agent, Crew, Process, Task
from crewai.knowledge.knowledge_config import KnowledgeConfig
from crewai.knowledge.source.text_file_knowledge_source import TextFileKnowledgeSource
from crewai.project import CrewBase, agent, crew, task

from ...knowledge_build import DERIVED_RELATIVE
from ...llms import embedder, synth_llm, worker_llm
from ...state import InternalAssessment
from ...tools.internal_analytics import (
    CompetitorExposureTool,
    LossReasonTool,
    RenewalExposureTool,
    SegmentStressTool,
)
from ...tools.record_lookup import RecordLookupTool


def internal_knowledge() -> list:
    """Citation-addressable chunks of the internal corpus.

    Paths are relative to the knowledge/ directory, which is where the
    framework expects to find them.
    """
    return [TextFileKnowledgeSource(file_paths=list(DERIVED_RELATIVE))]


@CrewBase
class InternalCrew:
    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    @agent
    def position_analyst(self) -> Agent:
        return Agent(
            config=self.agents_config["position_analyst"],
            tools=[
                SegmentStressTool(),
                LossReasonTool(),
                CompetitorExposureTool(),
                RenewalExposureTool(),
            ],
            # Reading exact tool output and drawing the uncomfortable
            # conclusion from it is judgement, so this sits on the upper tier.
            llm=synth_llm(),
            verbose=True,
            max_iter=12,
        )

    @agent
    def evidence_archivist(self) -> Agent:
        return Agent(
            config=self.agents_config["evidence_archivist"],
            tools=[RecordLookupTool()],
            llm=worker_llm(),
            verbose=True,
            max_iter=10,
            # Chunks are single records, so the default of 3 results is far too
            # few to see a pattern across a corpus of this shape.
            knowledge_config=KnowledgeConfig(results_limit=10, score_threshold=0.3),
        )

    @task
    def analyse_position(self) -> Task:
        return Task(config=self.tasks_config["analyse_position"])

    @task
    def gather_internal_evidence(self) -> Task:
        return Task(
            config=self.tasks_config["gather_internal_evidence"],
            output_pydantic=InternalAssessment,
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            knowledge_sources=internal_knowledge(),
            # Set explicitly: knowledge and memory ship different embedding
            # defaults, and leaving both implicit creates two collections at
            # mismatched dimensionality.
            embedder=embedder(),
            verbose=True,
        )
