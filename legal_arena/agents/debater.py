from __future__ import annotations

from collections.abc import Awaitable, Callable

from legal_arena.llm import json_for_prompt, structured_completion
from legal_arena.schemas import CaseSchema, ConversationEntry, DebateArgument, Side, SourceFetchRequest, SynthesizedSources


FetchSources = Callable[[list[SourceFetchRequest], str], Awaitable[SynthesizedSources]]


class Debater:
    def __init__(self, side: Side, case: CaseSchema, fetch_sources: FetchSources):
        self.side = side
        self.case = case
        self.fetch_sources = fetch_sources

    async def run(self, *, conversation: list[ConversationEntry], round_number: int) -> DebateArgument:
        research = await self.fetch_sources(self._default_queries(round_number), self._question(round_number))
        system_prompt = self._system_prompt(conversation, round_number)
        user_prompt = f"Synthesized sources for this turn:\n{json_for_prompt(research)}"
        argument = await structured_completion(output_type=DebateArgument, system_prompt=system_prompt, user_prompt=user_prompt)
        if argument.side != self.side or argument.round_number != round_number:
            argument = argument.model_copy(update={"side": self.side, "round_number": round_number})
        return argument.model_copy(update={"sources_fetched": research})

    def _default_queries(self, round_number: int) -> list[SourceFetchRequest]:
        side_goals = self.case.prosecution_must_prove if self.side == "prosecution" else self.case.defense_must_prove
        jurisdiction = ", ".join(self.case.relevant_jurisdictions) or "controlling jurisdiction"
        base = f"{self.case.title}: {self.case.summary}"
        goals = "; ".join(side_goals[:4])
        return [
            SourceFetchRequest(
                source_type="case_law",
                query=f"{jurisdiction} case law {base} {goals}",
                context=f"Round {round_number} {self.side} argument needs controlling precedent.",
            ),
            SourceFetchRequest(
                source_type="uploaded_docs",
                query=f"{base} {goals}",
                context=f"Round {round_number} {self.side} argument needs facts from uploaded records.",
            ),
        ]

    def _question(self, round_number: int) -> str:
        return f"What authorities and record facts best support the round {round_number} {self.side} argument?"

    def _system_prompt(self, conversation: list[ConversationEntry], round_number: int) -> str:
        must_prove = self.case.prosecution_must_prove if self.side == "prosecution" else self.case.defense_must_prove
        penalties = ""
        if self.side == "defense" and self.case.penalties_at_stake:
            penalties = "\nPenalties at stake for defendant: " + "; ".join(self.case.penalties_at_stake)
        return f"""You are an experienced {self.side} attorney.
Case: {self.case.title}
You must prove: {'; '.join(must_prove)}{penalties}

Current debate transcript:
{json_for_prompt(conversation)}

Your turn is round {round_number}.
Construct the strongest possible argument for the {self.side}.
Acknowledge weak points because doing so will help address them.
Output JSON matching DebateArgument schema."""


def make_debater(side: Side, case: CaseSchema, fetch_sources: FetchSources) -> Debater:
    return Debater(side=side, case=case, fetch_sources=fetch_sources)