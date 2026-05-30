from __future__ import annotations

from legal_arena.llm import json_for_prompt, structured_completion
from legal_arena.schemas import CaseSchema, ConversationEntry, DebateArgument, TurnJudgment


SYSTEM_PROMPT = """You are a neutral legal debate judge.
Score exactly one prosecution or defense turn using this 100 point rubric:
- Validity of argument: 0-20. Legal logic, element matching, and internal consistency.
- Groundedness in evidence: 0-20. Uses cited source packets and record facts accurately.
- Counter-attack or defense: 0-20. Responds to the opposing side and anticipates rebuttals.
- Legal specificity: 0-20. Jurisdiction, claims, standards, and remedies are specific.
- Strategic strength: 0-20. Advances the side's practical trial or settlement position.
Be strict. Penalize unsupported claims, generic legal statements, and failure to address weaknesses.
Output only valid JSON matching TurnJudgment. total_score must equal the sum of rubric scores."""


async def run_turn_judge(
    *,
    case: CaseSchema,
    conversation: list[ConversationEntry],
    argument: DebateArgument,
) -> TurnJudgment:
    user_prompt = "\n\n".join(
        [
            f"Case:\n{json_for_prompt(case)}",
            f"Conversation so far:\n{json_for_prompt(conversation)}",
            f"Argument to judge:\n{json_for_prompt(argument)}",
        ]
    )
    judgment = await structured_completion(output_type=TurnJudgment, system_prompt=SYSTEM_PROMPT, user_prompt=user_prompt)
    if judgment.side != argument.side or judgment.round_number != argument.round_number:
        judgment = judgment.model_copy(update={"side": argument.side, "round_number": argument.round_number})
    return judgment