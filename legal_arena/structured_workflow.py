from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from legal_arena.agents.case_builder import build_case
from legal_arena.agents.debater import build_case_law_bootstrap_query, make_debater
from legal_arena.agents.final_assessor import run_final_assessor
from legal_arena.agents.judge import run_turn_judge
from legal_arena.agents.source_synthesizer import synthesize_sources
from legal_arena.pipeline import SubAgentPool, should_stop_early
from legal_arena.schemas import (
    CaseSchema,
    ConversationEntry,
    DebateArgument,
    FinalAssessment,
    SourceFetchRequest,
    SourceResult,
    SynthesizedSources,
    TurnJudgment,
)
from legal_arena.modal_runtime import ModalRuntimeConfig


@dataclass(slots=True)
class SourcePacketTrace:
    side: str
    round_number: int
    question: str
    queries: list[dict[str, Any]]
    source_results: list[SourceResult]
    synthesized_sources: SynthesizedSources


@dataclass(slots=True)
class TurnTrace:
    side: str
    round_number: int
    source_packet: SourcePacketTrace
    argument: DebateArgument
    judgment: TurnJudgment


@dataclass(slots=True)
class StructuredWorkflowResult:
    case: CaseSchema
    source_packets: list[SourcePacketTrace] = field(default_factory=list)
    turn_traces: list[TurnTrace] = field(default_factory=list)
    final_assessment: FinalAssessment | None = None
    conversation: list[ConversationEntry] = field(default_factory=list)
    completed_rounds: int = 0
    stopped_early: bool = False


async def run_structured_workflow(
    *,
    problem_statement: str,
    documents: list[Any],
    n_rounds: int,
    modal_config: ModalRuntimeConfig | None = None,
    parallel_opening_round: bool = True,
    optimise_for: str = "defense",
) -> StructuredWorkflowResult:
    case = await build_case(problem_statement, documents, optimise_for=optimise_for)
    document_texts = [document.content if hasattr(document, "content") else str(document) for document in documents]
    source_pool = SubAgentPool(document_texts, modal_config=modal_config)
    bootstrap_case_law_request = SourceFetchRequest(
        source_type="case_law",
        query=build_case_law_bootstrap_query(case),
        context="Global precedent retrieval for all workflow rounds.",
    )
    bootstrap_case_law_result = (await source_pool.run([bootstrap_case_law_request]))[0]

    result = StructuredWorkflowResult(case=case)
    conversation: list[ConversationEntry] = [ConversationEntry(role="case", round_number=0, content=case.model_dump_json())]
    result.conversation = conversation

    async def build_argument(side: str, round_number: int) -> tuple[DebateArgument, SourcePacketTrace]:
        packet_box: dict[str, SourcePacketTrace] = {}

        async def fetch_sources(queries: list[SourceFetchRequest], question: str) -> SynthesizedSources:
            non_case_law_queries = [query for query in queries if query.source_type != "case_law"]
            source_results = await source_pool.run(non_case_law_queries)
            cached_case_law_results = [
                bootstrap_case_law_result.model_copy(update={"query": query.query})
                for query in queries
                if query.source_type == "case_law"
            ]
            source_results = cached_case_law_results + source_results
            synthesized = await synthesize_sources(source_results, question)
            packet = SourcePacketTrace(
                side=side,
                round_number=round_number,
                question=question,
                queries=[query.model_dump(mode="json") for query in queries],
                source_results=source_results,
                synthesized_sources=synthesized,
            )
            packet_box["packet"] = packet
            result.source_packets.append(packet)
            return synthesized

        debater = make_debater(side, case, fetch_sources)
        argument = await debater.run(conversation=list(conversation), round_number=round_number)
        packet = packet_box["packet"]
        return argument, packet

    final_prosecution_argument: DebateArgument | None = None
    final_defense_argument: DebateArgument | None = None
    latest_judgments: dict[str, TurnJudgment] = {}

    for round_number in range(1, n_rounds + 1):
        if round_number == 1 and parallel_opening_round:
            (prosecution_result, defense_result) = await asyncio.gather(
                build_argument("prosecution", round_number),
                build_argument("defense", round_number),
            )
            final_prosecution_argument, prosecution_packet = prosecution_result
            final_defense_argument, defense_packet = defense_result

            conversation.extend(
                [
                    ConversationEntry(role="prosecution", round_number=round_number, content=final_prosecution_argument.argument),
                    ConversationEntry(role="defense", round_number=round_number, content=final_defense_argument.argument),
                ]
            )

            prosecution_judgment, defense_judgment = await asyncio.gather(
                run_turn_judge(case=case, conversation=conversation, argument=final_prosecution_argument),
                run_turn_judge(case=case, conversation=conversation, argument=final_defense_argument),
            )
            result.turn_traces.extend(
                [
                    TurnTrace(
                        side="prosecution",
                        round_number=round_number,
                        source_packet=prosecution_packet,
                        argument=final_prosecution_argument,
                        judgment=prosecution_judgment,
                    ),
                    TurnTrace(
                        side="defense",
                        round_number=round_number,
                        source_packet=defense_packet,
                        argument=final_defense_argument,
                        judgment=defense_judgment,
                    ),
                ]
            )
            latest_judgments["prosecution"] = prosecution_judgment
            latest_judgments["defense"] = defense_judgment
            conversation.extend(
                [
                    ConversationEntry(role="judge", round_number=round_number, content=prosecution_judgment.model_dump_json()),
                    ConversationEntry(role="judge", round_number=round_number, content=defense_judgment.model_dump_json()),
                ]
            )
        else:
            final_prosecution_argument, prosecution_packet = await build_argument("prosecution", round_number)
            prosecution_judgment = await run_turn_judge(case=case, conversation=conversation, argument=final_prosecution_argument)
            result.turn_traces.append(
                TurnTrace(
                    side="prosecution",
                    round_number=round_number,
                    source_packet=prosecution_packet,
                    argument=final_prosecution_argument,
                    judgment=prosecution_judgment,
                )
            )
            latest_judgments["prosecution"] = prosecution_judgment
            conversation.extend(
                [
                    ConversationEntry(role="prosecution", round_number=round_number, content=final_prosecution_argument.argument),
                    ConversationEntry(role="judge", round_number=round_number, content=prosecution_judgment.model_dump_json()),
                ]
            )

            final_defense_argument, defense_packet = await build_argument("defense", round_number)
            defense_judgment = await run_turn_judge(case=case, conversation=conversation, argument=final_defense_argument)
            result.turn_traces.append(
                TurnTrace(
                    side="defense",
                    round_number=round_number,
                    source_packet=defense_packet,
                    argument=final_defense_argument,
                    judgment=defense_judgment,
                )
            )
            latest_judgments["defense"] = defense_judgment
            conversation.extend(
                [
                    ConversationEntry(role="defense", round_number=round_number, content=final_defense_argument.argument),
                    ConversationEntry(role="judge", round_number=round_number, content=defense_judgment.model_dump_json()),
                ]
            )

        result.completed_rounds = round_number
        if should_stop_early(
            round_num=round_number,
            optimise_for=case.optimise_for,
            latest_judgments=latest_judgments,
        ):
            result.stopped_early = True
            break

    if final_prosecution_argument is None or final_defense_argument is None:
        raise RuntimeError("Workflow did not produce both final prosecution and defense arguments.")

    result.final_assessment = await run_final_assessor(
        case=case,
        conversation=conversation,
        final_prosecution_argument=final_prosecution_argument,
        final_defense_argument=final_defense_argument,
    )
    result.conversation = conversation
    return result
