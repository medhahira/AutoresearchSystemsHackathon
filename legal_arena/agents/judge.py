from __future__ import annotations

from pydantic import BaseModel, Field

from legal_arena.llm import json_for_prompt, structured_completion
from legal_arena.schemas import CaseSchema, ConversationEntry, DebateArgument, RubricScores, TurnJudgment


SYSTEM_PROMPT = """You are a neutral legal debate judge.
Score exactly one prosecution or defense turn using this 100 point rubric:
- Validity of argument: 0-20. Legal logic, element matching, and internal consistency.
- Groundedness in evidence: 0-20. Uses cited source packets and record facts accurately.
- Counter-attack or defense: 0-20. Responds to the opposing side and anticipates rebuttals.
- Legal specificity: 0-20. Jurisdiction, claims, standards, and remedies are specific.
- Strategic strength: 0-20. Advances the side's practical trial or settlement position.
Be strict. Penalize unsupported claims, generic legal statements, and failure to address weaknesses.
Output only valid JSON matching TurnJudgment. total_score must equal the sum of rubric scores."""

MAX_CONVERSATION_ENTRIES = 8
MAX_ENTRY_CONTENT_CHARS = 1200
MAX_RELEVANT_EXCERPTS_CHARS = 1500


class _TurnJudgmentDraft(BaseModel):
    side: str
    round_number: int = Field(ge=1)
    scores: RubricScores
    total_score: int = Field(ge=0, le=100)
    strongest_points: list[str]
    weak_points: list[str]
    counter_opportunities: list[str]
    rationale: str


def _compact_conversation(conversation: list[ConversationEntry]) -> list[dict]:
    compact_entries = []
    for entry in conversation[-MAX_CONVERSATION_ENTRIES:]:
        content = entry.content
        if len(content) > MAX_ENTRY_CONTENT_CHARS:
            content = content[:MAX_ENTRY_CONTENT_CHARS] + "\n...[truncated]"
        compact_entries.append(
            {
                "role": entry.role,
                "round_number": entry.round_number,
                "content": content,
                "timestamp": entry.timestamp.isoformat(),
            }
        )
    return compact_entries


def _compact_argument(argument: DebateArgument) -> dict:
    payload = argument.model_dump(mode="json")
    sources = payload.get("sources_fetched")
    if isinstance(sources, dict):
        excerpts = sources.get("relevant_excerpts") or ""
        if isinstance(excerpts, str) and len(excerpts) > MAX_RELEVANT_EXCERPTS_CHARS:
            sources["relevant_excerpts"] = excerpts[:MAX_RELEVANT_EXCERPTS_CHARS] + "\n...[truncated]"
        for key in (
            "key_precedents",
            "supporting_statutes",
            "strong_evidence_for_optimised_side",
            "strong_arguments_for_optimised_side",
            "weak_points_for_opposing_side",
            "weak_points_for_optimised_side",
            "gaps",
            "citations",
        ):
            value = sources.get(key)
            if isinstance(value, list):
                sources[key] = value[:8]
    return payload


async def run_turn_judge(
    *,
    case: CaseSchema,
    conversation: list[ConversationEntry],
    argument: DebateArgument,
) -> TurnJudgment:
    compact_conversation = _compact_conversation(conversation)
    compact_argument = _compact_argument(argument)
    user_prompt = "\n\n".join(
        [
            f"Case:\n{json_for_prompt(case)}",
            f"Conversation so far:\n{json_for_prompt(compact_conversation)}",
            f"Argument to judge:\n{json_for_prompt(compact_argument)}",
        ]
    )
    try:
        judgment = await structured_completion(output_type=TurnJudgment, system_prompt=SYSTEM_PROMPT, user_prompt=user_prompt)
    except Exception:
        draft = await structured_completion(
            output_type=_TurnJudgmentDraft,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )
        draft_payload = draft.model_dump(mode="json")
        draft_payload["total_score"] = draft.scores.total
        judgment = TurnJudgment.model_validate(draft_payload)

    if judgment.total_score != judgment.scores.total:
        judgment = judgment.model_copy(update={"total_score": judgment.scores.total})
    if judgment.side != argument.side or judgment.round_number != argument.round_number:
        judgment = judgment.model_copy(update={"side": argument.side, "round_number": argument.round_number})
    return judgment