from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


Side = Literal["defense", "prosecution"]
ConversationRole = Literal["case", "prosecution", "defense", "judge", "summary"]
SourceType = Literal["case_law", "statutes", "uploaded_docs", "secondary"]
SettlementRecommendation = Literal["settle", "litigate", "negotiate"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Document(StrictModel):
    id: str
    title: str
    content: str
    source: str | None = None

    @classmethod
    def from_text(cls, content: str, index: int, title: str | None = None, source: str | None = None) -> "Document":
        return cls(id=f"doc-{index + 1}", title=title or f"Document {index + 1}", content=content, source=source)


class EvidenceSnippet(StrictModel):
    source_name: str
    snippet: str
    relevance: float = Field(ge=0, le=1)
    citation: str | None = None


class CaseBuilderInput(StrictModel):
    user_problem: str
    incident_summary: str
    documents: list[Document]
    jurisdiction: str | None = None
    constraints: list[str] = Field(default_factory=list)


class CaseBuilderOutput(StrictModel):
    case_title: str = Field(min_length=3)
    case_summary: str = Field(min_length=10)
    facts: list[str] = Field(min_length=1)
    prosecution_goals: list[str] = Field(min_length=1)
    defense_goals: list[str] = Field(min_length=1)
    defense_penalty_exposure: list[str] = Field(min_length=1)
    optimize_for: Side


class CaseSchema(StrictModel):
    title: str
    summary: str
    facts: list[str]
    prosecution_must_prove: list[str]
    defense_must_prove: list[str]
    charges_or_claims: list[str] = Field(default_factory=list)
    penalties_at_stake: list[str] = Field(default_factory=list)
    relevant_jurisdictions: list[str] = Field(default_factory=list)
    optimise_for: Side = "defense"

    @property
    def optimize_for(self) -> Side:
        return self.optimise_for

    @classmethod
    def from_case_builder_output(cls, output: CaseBuilderOutput) -> "CaseSchema":
        return cls(
            title=output.case_title,
            summary=output.case_summary,
            facts=output.facts,
            prosecution_must_prove=output.prosecution_goals,
            defense_must_prove=output.defense_goals,
            charges_or_claims=output.prosecution_goals,
            penalties_at_stake=output.defense_penalty_exposure,
            optimise_for=output.optimize_for,
        )

    def to_case_builder_output(self) -> CaseBuilderOutput:
        return CaseBuilderOutput(
            case_title=self.title,
            case_summary=self.summary,
            facts=self.facts,
            prosecution_goals=self.prosecution_must_prove,
            defense_goals=self.defense_must_prove,
            defense_penalty_exposure=self.penalties_at_stake or ["Unknown"],
            optimize_for=self.optimise_for,
        )


class SourceFetchRequest(StrictModel):
    source_type: SourceType
    query: str
    context: str
    top_k: int = Field(default=5, ge=1)

    @property
    def source_name(self) -> str:
        return self.source_type


class SourceAgentInput(StrictModel):
    request: SourceFetchRequest
    case_title: str
    round: int = Field(ge=1)
    side: Side


class SourceResult(StrictModel):
    source_type: str
    query: str
    raw_findings: str
    citations: list[str] = Field(default_factory=list)
    latency_ms: int | None = Field(default=None, ge=0)
    error: str | None = None

    @classmethod
    def from_snippets(
        cls,
        source_type: str,
        query: str,
        snippets: list[EvidenceSnippet],
        latency_ms: int | None = None,
        error: str | None = None,
    ) -> "SourceResult":
        return cls(
            source_type=source_type,
            query=query,
            raw_findings="\n\n".join(snippet.snippet for snippet in snippets),
            citations=[snippet.citation for snippet in snippets if snippet.citation],
            latency_ms=latency_ms,
            error=error,
        )

    def to_snippets(self) -> list[EvidenceSnippet]:
        if not self.raw_findings.strip():
            return []

        citation = self.citations[0] if self.citations else self.source_type
        return [
            EvidenceSnippet(
                source_name=self.source_type,
                citation=citation,
                snippet=self.raw_findings.strip(),
                relevance=0.75 if not self.error else 0.25,
            )
        ]


class SynthesizedSources(StrictModel):
    relevant_excerpts: str
    key_precedents: list[str] = Field(default_factory=list)
    supporting_statutes: list[str] = Field(default_factory=list)
    strong_evidence_for_optimised_side: list[str] = Field(default_factory=list)
    strong_arguments_for_optimised_side: list[str] = Field(default_factory=list)
    weak_points_for_opposing_side: list[str] = Field(default_factory=list)
    weak_points_for_optimised_side: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)

    def to_evidence_snippets(self) -> list[EvidenceSnippet]:
        citations = self.citations or self.key_precedents or self.supporting_statutes or ["synthesized_sources"]
        return [
            EvidenceSnippet(
                source_name="source_synthesizer",
                citation=", ".join(citations[:3]),
                snippet=self.relevant_excerpts,
                relevance=self.confidence,
            )
        ]


class SourceSynthesizerInput(StrictModel):
    source_results: list[SourceResult]
    question: str
    round_number: int = Field(ge=1)
    side: Side


class DebateArgument(StrictModel):
    side: Side
    round_number: int = Field(ge=1)
    sources_fetched: SynthesizedSources | None = None
    argument: str
    key_points: list[str]
    weaknesses_acknowledged: list[str] = Field(default_factory=list)

    @field_validator("key_points")
    @classmethod
    def require_key_points(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("key_points must contain at least one point")
        return value


class ConversationEntry(StrictModel):
    role: ConversationRole
    round_number: int = Field(ge=0)
    content: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_legacy(self) -> dict[str, Any]:
        speaker = "system" if self.role in {"case", "judge", "summary"} else self.role
        return {
            "round": max(1, self.round_number),
            "speaker": speaker,
            "argument": self.content,
            "timestamp": self.timestamp.isoformat(),
        }


class FinalAssessment(StrictModel):
    optimised_for: Side
    strongest_arguments: list[str]
    vulnerabilities: list[str]
    precedents_to_cite: list[str] = Field(default_factory=list)
    risk_score: int = Field(ge=1, le=10)
    risk_rationale: str
    settle_recommendation: SettlementRecommendation
    settle_rationale: str
    suggested_settlement_terms: list[str] | None = None
    next_actions: list[str] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)

    @property
    def optimize_for(self) -> Side:
        return self.optimised_for


class RubricScores(StrictModel):
    validity_of_argument: int = Field(ge=0, le=20)
    groundedness_in_evidence: int = Field(ge=0, le=20)
    counter_attack_or_defense: int = Field(ge=0, le=20)
    legal_specificity: int = Field(ge=0, le=20)
    strategic_strength: int = Field(ge=0, le=20)

    @property
    def total(self) -> int:
        return (
            self.validity_of_argument
            + self.groundedness_in_evidence
            + self.counter_attack_or_defense
            + self.legal_specificity
            + self.strategic_strength
        )


class TurnJudgment(StrictModel):
    side: Side
    round_number: int = Field(ge=1)
    scores: RubricScores
    total_score: int = Field(ge=0, le=100)
    strongest_points: list[str]
    weak_points: list[str]
    counter_opportunities: list[str]
    rationale: str

    @field_validator("total_score")
    @classmethod
    def total_score_matches_rubric(cls, value: int, info: Any) -> int:
        scores = info.data.get("scores")
        if isinstance(scores, RubricScores) and value != scores.total:
            raise ValueError("total_score must equal the sum of rubric scores")
        return value