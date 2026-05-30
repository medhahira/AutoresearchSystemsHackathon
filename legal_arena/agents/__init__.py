"""Agent factories for the Legal Arena workflow."""

from legal_arena.agents.case_builder import build_case
from legal_arena.agents.debater import make_debater
from legal_arena.agents.final_assessor import run_final_assessor
from legal_arena.agents.judge import run_turn_judge
from legal_arena.agents.source_agent import make_source_agent, run_source_agent
from legal_arena.agents.source_synthesizer import synthesize_sources

__all__ = [
    "build_case",
    "make_debater",
    "make_source_agent",
    "run_final_assessor",
    "run_turn_judge",
    "run_source_agent",
    "synthesize_sources",
]