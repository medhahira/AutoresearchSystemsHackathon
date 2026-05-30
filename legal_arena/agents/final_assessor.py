from __future__ import annotations

from legal_arena.llm import json_for_prompt, structured_completion
from legal_arena.schemas import CaseSchema, ConversationEntry, DebateArgument, FinalAssessment


async def run_final_assessor(
    case: CaseSchema,
    conversation: list[ConversationEntry],
    final_prosecution_argument: DebateArgument,
    final_defense_argument: DebateArgument,
) -> FinalAssessment:
    system_prompt = f"""You are a senior {case.optimise_for} attorney reviewing a legal debate transcript.
Your job is NOT to be balanced. Optimise entirely for the {case.optimise_for} side.
Identify the 3 strongest arguments for {case.optimise_for}.
Identify the 3 biggest vulnerabilities.
Score the litigation risk (1=very likely to win, 10=very likely to lose).
Give a clear recommendation: settle, negotiate, or litigate, with concrete reasoning.
Output only valid JSON matching FinalAssessment."""
    user_prompt = "\n\n".join(
        [
            f"Case:\n{json_for_prompt(case)}",
            f"Conversation:\n{json_for_prompt(conversation)}",
            f"Final prosecution argument:\n{json_for_prompt(final_prosecution_argument)}",
            f"Final defense argument:\n{json_for_prompt(final_defense_argument)}",
        ]
    )
    assessment = await structured_completion(output_type=FinalAssessment, system_prompt=system_prompt, user_prompt=user_prompt)
    if assessment.optimised_for != case.optimise_for:
        assessment = assessment.model_copy(update={"optimised_for": case.optimise_for})
    return assessment