from __future__ import annotations

import asyncio
import time

from legal_arena.agents.debater import make_debater
from legal_arena.agents.final_assessor import run_final_assessor
from legal_arena.agents.judge import run_turn_judge
from legal_arena.agents.source_agent import run_source_agent
from legal_arena.agents.source_synthesizer import synthesize_sources
from legal_arena.llm import json_for_prompt, structured_completion
from legal_arena.modal_runtime import ModalRuntimeConfig, create_modal_sandbox_client, create_modal_sandbox_options, create_sandbox_run_config, modal_extension_available
from legal_arena.schemas import CaseSchema, ConversationEntry, DebateArgument, FinalAssessment, Side, SourceFetchRequest, SourceResult, SynthesizedSources, TurnJudgment
from legal_arena.tracing import RaindropTracer


DEFAULT_N_ROUNDS = 2
MAX_N_ROUNDS = 10
EARLY_CONVERGENCE_MIN_ROUNDS = 2
EARLY_STOP_SCORE_THRESHOLD = 85
EARLY_STOP_SCORE_MARGIN = 15


class SubAgentPool:
    """Parallel source-agent runner.

    The class keeps the source fan-out behind one interface. For the hackathon MVP it
    uses asyncio locally; the interface is intentionally small so Modal sandbox
    dispatch can replace the implementation without touching debater code.
    """

    def __init__(self, documents: list[str], modal_config: ModalRuntimeConfig | None = None):
        self.documents = documents
        self.modal_config = modal_config or ModalRuntimeConfig.from_env()
        self._modal_client = None
        self._gpu_counts: dict[str, int] = {}

    async def run(self, requests: list[SourceFetchRequest]) -> list[SourceResult]:
        tasks = [self._run_one(request) for request in requests]
        if not tasks:
            return []
        return list(await asyncio.gather(*tasks))

    async def _run_one(self, request: SourceFetchRequest) -> SourceResult:
        if not self.modal_config.enabled or request.source_type == "uploaded_docs":
            return await run_source_agent(request, documents=self.documents)
        if not modal_extension_available():
            return await run_source_agent(request, documents=self.documents)

        gpu = self.modal_config.gpu
        if gpu is not None:
            limit = self.modal_config.gpu_limits.get(gpu)
            in_use = self._gpu_counts.get(gpu, 0)
            if limit is not None and in_use >= limit:
                return SourceResult(
                    source_type=request.source_type,
                    query=request.query,
                    raw_findings="",
                    citations=[],
                    error=f"Modal GPU quota reached for {gpu}.",
                )
            self._gpu_counts[gpu] = in_use + 1

        client = self._modal_client or create_modal_sandbox_client()
        self._modal_client = client
        options = create_modal_sandbox_options(self.modal_config, gpu=gpu)
        session = await client.create(options=options)
        try:
            run_config = create_sandbox_run_config(client=client, session=session)
            return await run_source_agent(request, documents=self.documents, run_config=run_config)
        finally:
            if gpu is not None:
                self._gpu_counts[gpu] = max(0, self._gpu_counts.get(gpu, 1) - 1)
            await client.delete(session)


async def run_debate(
    case: CaseSchema,
    n_rounds: int = DEFAULT_N_ROUNDS,
    documents: list[str] | None = None,
    modal_config: ModalRuntimeConfig | None = None,
    enable_early_convergence: bool = True,
    parallel_opening_round: bool = True,
    raindrop_enabled: bool | None = None,
) -> FinalAssessment:
    if n_rounds < 1:
        raise ValueError("n_rounds must be at least 1.")
    if n_rounds > MAX_N_ROUNDS:
        raise ValueError(f"n_rounds cannot exceed {MAX_N_ROUNDS}.")

    documents = documents or []
    tracer = RaindropTracer.start(
        enabled=raindrop_enabled,
        event="legal_arena_debate",
        input_payload=case,
        properties={
            "n_rounds": n_rounds,
            "optimise_for": case.optimise_for,
            "parallel_opening_round": parallel_opening_round,
            "modal_enabled": bool(modal_config.enabled) if modal_config else False,
        },
    )
    source_pool = SubAgentPool(documents, modal_config=modal_config)
    conversation: list[ConversationEntry] = [
        ConversationEntry(role="case", round_number=0, content=case.model_dump_json())
    ]

    async def fetch_sources(queries: list[SourceFetchRequest], question: str) -> SynthesizedSources:
        started = time.perf_counter()
        try:
            source_results = await source_pool.run(queries)
            synthesized = await synthesize_sources(source_results, question)
            tracer.track_tool(
                name="fetch_sources",
                started=started,
                input_payload={"question": question, "queries": [query.model_dump(mode="json") for query in queries]},
                output_payload=synthesized,
                properties={"source_result_count": len(source_results)},
            )
            return synthesized
        except Exception as exc:
            tracer.track_tool(
                name="fetch_sources",
                started=started,
                input_payload={"question": question, "queries": [query.model_dump(mode="json") for query in queries]},
                error=exc,
            )
            raise

    prosecution = make_debater("prosecution", case, fetch_sources)
    defense = make_debater("defense", case, fetch_sources)
    final_prosecution_argument: DebateArgument | None = None
    final_defense_argument: DebateArgument | None = None
    latest_judgments: dict[Side, TurnJudgment] = {}

    for round_num in range(1, n_rounds + 1):
        if round_num == 1 and parallel_opening_round:
            started = time.perf_counter()
            final_prosecution_argument, final_defense_argument = await asyncio.gather(
                prosecution.run(conversation=list(conversation), round_number=round_num),
                defense.run(conversation=list(conversation), round_number=round_num),
            )
            tracer.track_tool(
                name="parallel_opening_arguments",
                started=started,
                input_payload={"round_number": round_num},
                output_payload={
                    "prosecution": final_prosecution_argument.model_dump(mode="json"),
                    "defense": final_defense_argument.model_dump(mode="json"),
                },
            )
            conversation.extend(
                [
                    ConversationEntry(
                        role="prosecution",
                        round_number=round_num,
                        content=summarise_argument(final_prosecution_argument),
                    ),
                    ConversationEntry(
                        role="defense",
                        round_number=round_num,
                        content=summarise_argument(final_defense_argument),
                    ),
                ]
            )
            prosecution_judgment, defense_judgment = await asyncio.gather(
                run_turn_judge(case=case, conversation=conversation, argument=final_prosecution_argument),
                run_turn_judge(case=case, conversation=conversation, argument=final_defense_argument),
            )
            tracer.track_tool(
                name="parallel_opening_judgments",
                started=started,
                input_payload={"round_number": round_num},
                output_payload={
                    "prosecution_score": prosecution_judgment.total_score,
                    "defense_score": defense_judgment.total_score,
                },
            )
            latest_judgments["prosecution"] = prosecution_judgment
            latest_judgments["defense"] = defense_judgment
            conversation.extend(
                [
                    ConversationEntry(
                        role="judge",
                        round_number=round_num,
                        content=summarise_judgment(prosecution_judgment),
                    ),
                    ConversationEntry(
                        role="judge",
                        round_number=round_num,
                        content=summarise_judgment(defense_judgment),
                    ),
                ]
            )
        else:
            started = time.perf_counter()
            final_prosecution_argument = await prosecution.run(conversation=conversation, round_number=round_num)
            tracer.track_tool(
                name="prosecution_turn",
                started=started,
                input_payload={"round_number": round_num},
                output_payload=final_prosecution_argument,
            )
            conversation.append(
                ConversationEntry(
                    role="prosecution",
                    round_number=round_num,
                    content=summarise_argument(final_prosecution_argument),
                )
            )
            started = time.perf_counter()
            prosecution_judgment = await run_turn_judge(case=case, conversation=conversation, argument=final_prosecution_argument)
            tracer.track_tool(
                name="judge_prosecution_turn",
                started=started,
                input_payload=final_prosecution_argument,
                output_payload=prosecution_judgment,
            )
            latest_judgments["prosecution"] = prosecution_judgment
            conversation.append(
                ConversationEntry(
                    role="judge",
                    round_number=round_num,
                    content=summarise_judgment(prosecution_judgment),
                )
            )

            started = time.perf_counter()
            final_defense_argument = await defense.run(conversation=conversation, round_number=round_num)
            tracer.track_tool(
                name="defense_turn",
                started=started,
                input_payload={"round_number": round_num},
                output_payload=final_defense_argument,
            )
            conversation.append(
                ConversationEntry(
                    role="defense",
                    round_number=round_num,
                    content=summarise_argument(final_defense_argument),
                )
            )
            started = time.perf_counter()
            defense_judgment = await run_turn_judge(case=case, conversation=conversation, argument=final_defense_argument)
            tracer.track_tool(
                name="judge_defense_turn",
                started=started,
                input_payload=final_defense_argument,
                output_payload=defense_judgment,
            )
            latest_judgments["defense"] = defense_judgment
            conversation.append(
                ConversationEntry(
                    role="judge",
                    round_number=round_num,
                    content=summarise_judgment(defense_judgment),
                )
            )

        if enable_early_convergence and should_stop_early(
            round_num=round_num,
            optimise_for=case.optimise_for,
            latest_judgments=latest_judgments,
        ):
            break
        if round_num < n_rounds:
            started = time.perf_counter()
            conversation = await condense_conversation(conversation)
            tracer.track_tool(
                name="condense_conversation",
                started=started,
                input_payload={"round_number": round_num},
                output_payload={"conversation_entries": len(conversation)},
            )

    if final_prosecution_argument is None or final_defense_argument is None:
        raise ValueError("At least one debate round is required.")

    started = time.perf_counter()
    assessment = await run_final_assessor(case, conversation, final_prosecution_argument, final_defense_argument)
    tracer.track_tool(
        name="final_assessment",
        started=started,
        input_payload={"conversation_entries": len(conversation)},
        output_payload=assessment,
    )
    tracer.finish(
        output_payload=assessment,
        properties={"completed_rounds": final_defense_argument.round_number},
    )
    tracer.shutdown()
    return assessment


def summarise_argument(argument: DebateArgument) -> str:
    source_summary = ""
    if argument.sources_fetched:
        citations = argument.sources_fetched.citations or argument.sources_fetched.key_precedents or argument.sources_fetched.supporting_statutes
        if citations:
            source_summary = "\nSources: " + "; ".join(citations[:5])
    weaknesses = ""
    if argument.weaknesses_acknowledged:
        weaknesses = "\nWeaknesses: " + "; ".join(argument.weaknesses_acknowledged[:3])
    return "\n".join(argument.key_points) + source_summary + weaknesses


def summarise_judgment(judgment: TurnJudgment) -> str:
    return "\n".join(
        [
            f"{judgment.side} score: {judgment.total_score}/100",
            "Strongest points: " + "; ".join(judgment.strongest_points[:3]),
            "Weak points: " + "; ".join(judgment.weak_points[:3]),
            "Counter opportunities: " + "; ".join(judgment.counter_opportunities[:3]),
        ]
    )


def should_stop_early(
    *,
    round_num: int,
    optimise_for: Side,
    latest_judgments: dict[Side, TurnJudgment],
) -> bool:
    if round_num < EARLY_CONVERGENCE_MIN_ROUNDS:
        return False
    optimised_judgment = latest_judgments.get(optimise_for)
    opposing_side: Side = "defense" if optimise_for == "prosecution" else "prosecution"
    opposing_judgment = latest_judgments.get(opposing_side)
    if optimised_judgment is None or opposing_judgment is None:
        return False
    score_margin = optimised_judgment.total_score - opposing_judgment.total_score
    return optimised_judgment.total_score >= EARLY_STOP_SCORE_THRESHOLD and score_margin >= EARLY_STOP_SCORE_MARGIN


async def condense_conversation(conversation: list[ConversationEntry]) -> list[ConversationEntry]:
    if len(conversation) <= 4:
        return conversation

    latest_entries = conversation[-2:]
    entries_to_summarize = conversation[:-2]
    system_prompt = """Condense a legal debate transcript for context management.
Keep material facts, legal issues, authorities cited, concessions, and unresolved weaknesses.
Output JSON matching ConversationEntry with role='summary'."""
    user_prompt = f"Entries to summarize:\n{json_for_prompt(entries_to_summarize)}"
    summary_entry = await structured_completion(output_type=ConversationEntry, system_prompt=system_prompt, user_prompt=user_prompt)
    summary_entry = summary_entry.model_copy(update={"role": "summary", "round_number": latest_entries[0].round_number})
    return [summary_entry, *latest_entries]