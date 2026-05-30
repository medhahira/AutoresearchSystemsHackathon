"""Legal Arena multi-agent legal debate workflow."""

from legal_arena.pipeline import run_debate
from legal_arena.schemas import CaseSchema, FinalAssessment

__all__ = ["CaseSchema", "FinalAssessment", "run_debate"]