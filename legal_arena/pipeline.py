from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable
from typing import TypeVar

from legal_arena.agents.debater import build_case_law_bootstrap_query, make_debater
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
DEFAULT_STAGE_TIMEOUT_S = 180

_StageT = TypeVar("_StageT")


def _debug_enabled() -> bool:
    return os.getenv("LEGAL_ARENA_DEBUG", "0").lower() in {"1", "true", "yes"}


def _stage_timeout_s() -> int:
    raw = os.getenv("LEGAL_ARENA_STAGE_TIMEOUT_S", str(DEFAULT_STAGE_TIMEOUT_S))
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_STAGE_TIMEOUT_S


def _debug_log(message: str) -> None:
    if _debug_enabled():
        print(f"[legal_arena.debug] {message}")


async def _await_stage(label: str, awaitable: Awaitable[_StageT]) -> _StageT:
    timeout_s = _stage_timeout_s()
    started = time.perf_counter()
    _debug_log(f"start:{label} timeout_s={timeout_s}")
    try:
        if timeout_s > 0:
            result = await asyncio.wait_for(awaitable, timeout=timeout_s)
        else:
            result = await awaitable
        return result
    except asyncio.TimeoutError as exc:
        raise RuntimeError(f"Stage '{label}' timed out after {timeout_s}s") from exc
    finally:
        elapsed = time.perf_counter() - started
        _debug_log(f"end:{label} elapsed_s={elapsed:.2f}")


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
    trace_status: dict[str, object] | None = None,
    run_artifacts: dict[str, object] | None = None,
) -> FinalAssessment:
    _debug_log(
        f"run_debate start rounds={n_rounds} parallel_opening_round={parallel_opening_round} "
        f"early_convergence={enable_early_convergence}"
    )
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
    if trace_status is not None:
        trace_status.update(tracer.status())
    source_pool = SubAgentPool(documents, modal_config=modal_config)
    bootstrap_case_law_request = SourceFetchRequest(
        source_type="case_law",
        query=build_case_law_bootstrap_query(case),
        context="Global precedent retrieval for all debate rounds.",
    )
    bootstrap_case_law_result = (
        await _await_stage(
            "bootstrap_case_law",
            source_pool.run([bootstrap_case_law_request]),
        )
    )[0]
    conversation: list[ConversationEntry] = [
        ConversationEntry(role="case", round_number=0, content=case.model_dump_json())
    ]

    async def fetch_sources(queries: list[SourceFetchRequest], question: str) -> SynthesizedSources:
        started = time.perf_counter()
        try:
            non_case_law_queries = [query for query in queries if query.source_type != "case_law"]
            source_results = await _await_stage(
                "source_pool.run",
                source_pool.run(non_case_law_queries),
            )
            cached_case_law_results = [
                bootstrap_case_law_result.model_copy(update={"query": query.query})
                for query in queries
                if query.source_type == "case_law"
            ]
            source_results = cached_case_law_results + source_results
            synthesized = await _await_stage(
                "source_synthesizer",
                synthesize_sources(source_results, question),
            )
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
    if run_artifacts is not None:
        run_artifacts.update(
            {
                "case": case.model_dump(mode="json"),
                "turns": [],
                "completed_rounds": 0,
                "early_stopped": False,
            }
        )

    for round_num in range(1, n_rounds + 1):
        _debug_log(f"round={round_num} start")
        if round_num == 1 and parallel_opening_round:
            started = time.perf_counter()
            final_prosecution_argument, final_defense_argument = await _await_stage(
                f"round_{round_num}.parallel_opening_arguments",
                asyncio.gather(
                    prosecution.run(conversation=list(conversation), round_number=round_num),
                    defense.run(conversation=list(conversation), round_number=round_num),
                ),
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
            tracer.track_tool(
                name="prosecution_turn",
                started=started,
                input_payload={"round_number": round_num, "mode": "parallel_opening"},
                output_payload=final_prosecution_argument,
                properties={"side": "prosecution", "parallel_opening": True},
            )
            tracer.track_ai_event(
                event="prosecution_turn",
                input_payload={"round_number": round_num, "conversation": [entry.model_dump(mode="json") for entry in conversation]},
                output_payload=final_prosecution_argument.argument,
                properties={
                    "side": "prosecution",
                    "round_number": round_num,
                    "parallel_opening": True,
                    "key_points": final_prosecution_argument.key_points,
                    "weaknesses_acknowledged": final_prosecution_argument.weaknesses_acknowledged,
                },
            )
            tracer.track_tool(
                name="defense_turn",
                started=started,
                input_payload={"round_number": round_num, "mode": "parallel_opening"},
                output_payload=final_defense_argument,
                properties={"side": "defense", "parallel_opening": True},
            )
            tracer.track_ai_event(
                event="defense_turn",
                input_payload={"round_number": round_num, "conversation": [entry.model_dump(mode="json") for entry in conversation]},
                output_payload=final_defense_argument.argument,
                properties={
                    "side": "defense",
                    "round_number": round_num,
                    "parallel_opening": True,
                    "key_points": final_defense_argument.key_points,
                    "weaknesses_acknowledged": final_defense_argument.weaknesses_acknowledged,
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
            prosecution_judgment, defense_judgment = await _await_stage(
                f"round_{round_num}.parallel_opening_judgments",
                asyncio.gather(
                    run_turn_judge(case=case, conversation=conversation, argument=final_prosecution_argument),
                    run_turn_judge(case=case, conversation=conversation, argument=final_defense_argument),
                ),
            )
            _record_turn_artifact(
                run_artifacts,
                round_num=round_num,
                mode="parallel_opening",
                prosecution_argument=final_prosecution_argument,
                defense_argument=final_defense_argument,
                prosecution_judgment=prosecution_judgment,
                defense_judgment=defense_judgment,
            )
            _attach_turn_blocks(
                tracer,
                round_num=round_num,
                mode="parallel_opening",
                prosecution_argument=final_prosecution_argument,
                defense_argument=final_defense_argument,
                prosecution_judgment=prosecution_judgment,
                defense_judgment=defense_judgment,
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
            tracer.track_tool(
                name="judge_prosecution_turn",
                started=started,
                input_payload=final_prosecution_argument,
                output_payload=prosecution_judgment,
                properties={"side": "prosecution", "parallel_opening": True},
            )
            tracer.track_ai_event(
                event="judge_prosecution_turn",
                input_payload=final_prosecution_argument.argument,
                output_payload=prosecution_judgment.rationale,
                properties={
                    "side": "prosecution",
                    "round_number": round_num,
                    "score": prosecution_judgment.total_score,
                    "rubric": prosecution_judgment.scores.model_dump(mode="json"),
                },
            )
            tracer.track_tool(
                name="judge_defense_turn",
                started=started,
                input_payload=final_defense_argument,
                output_payload=defense_judgment,
                properties={"side": "defense", "parallel_opening": True},
            )
            tracer.track_ai_event(
                event="judge_defense_turn",
                input_payload=final_defense_argument.argument,
                output_payload=defense_judgment.rationale,
                properties={
                    "side": "defense",
                    "round_number": round_num,
                    "score": defense_judgment.total_score,
                    "rubric": defense_judgment.scores.model_dump(mode="json"),
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
            final_prosecution_argument = await _await_stage(
                f"round_{round_num}.prosecution_argument",
                prosecution.run(conversation=conversation, round_number=round_num),
            )
            tracer.track_tool(
                name="prosecution_turn",
                started=started,
                input_payload={"round_number": round_num},
                output_payload=final_prosecution_argument,
            )
            tracer.track_ai_event(
                event="prosecution_turn",
                input_payload={"round_number": round_num, "conversation": [entry.model_dump(mode="json") for entry in conversation]},
                output_payload=final_prosecution_argument.argument,
                properties={
                    "side": "prosecution",
                    "round_number": round_num,
                    "key_points": final_prosecution_argument.key_points,
                    "weaknesses_acknowledged": final_prosecution_argument.weaknesses_acknowledged,
                },
            )
            conversation.append(
                ConversationEntry(
                    role="prosecution",
                    round_number=round_num,
                    content=summarise_argument(final_prosecution_argument),
                )
            )
            started = time.perf_counter()
            prosecution_judgment = await _await_stage(
                f"round_{round_num}.prosecution_judgment",
                run_turn_judge(case=case, conversation=conversation, argument=final_prosecution_argument),
            )
            tracer.track_tool(
                name="judge_prosecution_turn",
                started=started,
                input_payload=final_prosecution_argument,
                output_payload=prosecution_judgment,
            )
            tracer.track_ai_event(
                event="judge_prosecution_turn",
                input_payload=final_prosecution_argument.argument,
                output_payload=prosecution_judgment.rationale,
                properties={
                    "side": "prosecution",
                    "round_number": round_num,
                    "score": prosecution_judgment.total_score,
                    "rubric": prosecution_judgment.scores.model_dump(mode="json"),
                },
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
            final_defense_argument = await _await_stage(
                f"round_{round_num}.defense_argument",
                defense.run(conversation=conversation, round_number=round_num),
            )
            tracer.track_tool(
                name="defense_turn",
                started=started,
                input_payload={"round_number": round_num},
                output_payload=final_defense_argument,
            )
            tracer.track_ai_event(
                event="defense_turn",
                input_payload={"round_number": round_num, "conversation": [entry.model_dump(mode="json") for entry in conversation]},
                output_payload=final_defense_argument.argument,
                properties={
                    "side": "defense",
                    "round_number": round_num,
                    "key_points": final_defense_argument.key_points,
                    "weaknesses_acknowledged": final_defense_argument.weaknesses_acknowledged,
                },
            )
            conversation.append(
                ConversationEntry(
                    role="defense",
                    round_number=round_num,
                    content=summarise_argument(final_defense_argument),
                )
            )
            started = time.perf_counter()
            defense_judgment = await _await_stage(
                f"round_{round_num}.defense_judgment",
                run_turn_judge(case=case, conversation=conversation, argument=final_defense_argument),
            )
            _record_turn_artifact(
                run_artifacts,
                round_num=round_num,
                mode="sequential_rebuttal",
                prosecution_argument=final_prosecution_argument,
                defense_argument=final_defense_argument,
                prosecution_judgment=prosecution_judgment,
                defense_judgment=defense_judgment,
            )
            _attach_turn_blocks(
                tracer,
                round_num=round_num,
                mode="sequential_rebuttal",
                prosecution_argument=final_prosecution_argument,
                defense_argument=final_defense_argument,
                prosecution_judgment=prosecution_judgment,
                defense_judgment=defense_judgment,
            )
            tracer.track_tool(
                name="judge_defense_turn",
                started=started,
                input_payload=final_defense_argument,
                output_payload=defense_judgment,
            )
            tracer.track_ai_event(
                event="judge_defense_turn",
                input_payload=final_defense_argument.argument,
                output_payload=defense_judgment.rationale,
                properties={
                    "side": "defense",
                    "round_number": round_num,
                    "score": defense_judgment.total_score,
                    "rubric": defense_judgment.scores.model_dump(mode="json"),
                },
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
            if run_artifacts is not None:
                run_artifacts["early_stopped"] = True
                run_artifacts["early_stop_round"] = round_num
            break
        if round_num < n_rounds:
            started = time.perf_counter()
            conversation = await _await_stage(
                f"round_{round_num}.condense_conversation",
                condense_conversation(conversation),
            )
            tracer.track_tool(
                name="condense_conversation",
                started=started,
                input_payload={"round_number": round_num},
                output_payload={"conversation_entries": len(conversation)},
            )

    if final_prosecution_argument is None or final_defense_argument is None:
        raise ValueError("At least one debate round is required.")
    if run_artifacts is not None:
        run_artifacts["completed_rounds"] = final_defense_argument.round_number
        run_artifacts["conversation"] = [entry.model_dump(mode="json") for entry in conversation]

    started = time.perf_counter()
    assessment = await _await_stage(
        "final_assessment",
        run_final_assessor(case, conversation, final_prosecution_argument, final_defense_argument),
    )
    tracer.track_tool(
        name="final_assessment",
        started=started,
        input_payload={"conversation_entries": len(conversation)},
        output_payload=assessment,
    )
    tracer.track_ai_event(
        event="final_assessment",
        input_payload=[entry.model_dump(mode="json") for entry in conversation],
        output_payload=assessment.risk_rationale,
        properties={
            "risk_score": assessment.risk_score,
            "recommendation": assessment.settle_recommendation,
            "optimised_for": assessment.optimised_for,
        },
    )
    tracer.add_attachment(
        name="final-assessment",
        value=_final_assessment_markdown(assessment),
    )
    tracer.finish(
        output_payload={
            "final_assessment": assessment.model_dump(mode="json"),
            "debate_transcript": run_artifacts.get("turns", []) if run_artifacts is not None else [],
        },
        properties={
            "completed_rounds": final_defense_argument.round_number,
            "early_stopped": bool(run_artifacts.get("early_stopped")) if run_artifacts is not None else False,
        },
    )
    if trace_status is not None:
        trace_status.update(tracer.status())
    tracer.shutdown()
    return assessment


def _record_turn_artifact(
    run_artifacts: dict[str, object] | None,
    *,
    round_num: int,
    mode: str,
    prosecution_argument: DebateArgument,
    defense_argument: DebateArgument,
    prosecution_judgment: TurnJudgment,
    defense_judgment: TurnJudgment,
) -> None:
    if run_artifacts is None:
        return
    turns = run_artifacts.get("turns")
    if not isinstance(turns, list):
        turns = []
        run_artifacts["turns"] = turns
    turns.append(
        {
            "round_number": round_num,
            "mode": mode,
            "prosecution": {
                "argument": prosecution_argument.model_dump(mode="json"),
                "judgment": prosecution_judgment.model_dump(mode="json"),
            },
            "defense": {
                "argument": defense_argument.model_dump(mode="json"),
                "judgment": defense_judgment.model_dump(mode="json"),
            },
            "score_margin_for_defense": defense_judgment.total_score - prosecution_judgment.total_score,
            "score_margin_for_prosecution": prosecution_judgment.total_score - defense_judgment.total_score,
        }
    )


def _turn_transcript_markdown(
    *,
    round_num: int,
    mode: str,
    prosecution_argument: DebateArgument,
    defense_argument: DebateArgument,
    prosecution_judgment: TurnJudgment,
    defense_judgment: TurnJudgment,
) -> str:
    return "\n\n".join(
        [
            f"# Round {round_num} ({mode})",
            "## Prosecution Argument",
            prosecution_argument.argument,
            "### Prosecution Key Points",
            "\n".join(f"- {point}" for point in prosecution_argument.key_points),
            "### Prosecution Judge Score",
            f"{prosecution_judgment.total_score}/100\n\n{prosecution_judgment.rationale}",
            "## Defense Argument",
            defense_argument.argument,
            "### Defense Key Points",
            "\n".join(f"- {point}" for point in defense_argument.key_points),
            "### Defense Judge Score",
            f"{defense_judgment.total_score}/100\n\n{defense_judgment.rationale}",
        ]
    )


def _attach_turn_blocks(
    tracer: RaindropTracer,
    *,
    round_num: int,
    mode: str,
    prosecution_argument: DebateArgument,
    defense_argument: DebateArgument,
    prosecution_judgment: TurnJudgment,
    defense_judgment: TurnJudgment,
) -> None:
    tracer.add_attachment(
        name=f"round-{round_num}-source-synthesis-prosecution",
        value=_sources_markdown(
            title=f"Round {round_num} Prosecution Source Synthesis ({mode})",
            argument=prosecution_argument,
        ),
    )
    tracer.add_attachment(
        name=f"round-{round_num}-prosecution-argument",
        value=_argument_markdown(
            title=f"Round {round_num} Prosecution Argument ({mode})",
            argument=prosecution_argument,
        ),
    )
    tracer.add_attachment(
        name=f"round-{round_num}-judge-prosecution",
        value=_judgment_markdown(
            title=f"Round {round_num} Judge: Prosecution ({mode})",
            judgment=prosecution_judgment,
        ),
    )
    tracer.add_attachment(
        name=f"round-{round_num}-source-synthesis-defense",
        value=_sources_markdown(
            title=f"Round {round_num} Defense Source Synthesis ({mode})",
            argument=defense_argument,
        ),
    )
    tracer.add_attachment(
        name=f"round-{round_num}-defense-argument",
        value=_argument_markdown(
            title=f"Round {round_num} Defense Argument ({mode})",
            argument=defense_argument,
        ),
    )
    tracer.add_attachment(
        name=f"round-{round_num}-judge-defense",
        value=_judgment_markdown(
            title=f"Round {round_num} Judge: Defense ({mode})",
            judgment=defense_judgment,
        ),
    )


def _sources_markdown(*, title: str, argument: DebateArgument) -> str:
    sources = argument.sources_fetched
    if sources is None:
        return f"# {title}\n\nNo sources fetched."
    return "\n\n".join(
        [
            f"# {title}",
            "## Relevant Excerpts",
            sources.relevant_excerpts,
            "## Strong Evidence For Optimized Side",
            _bullet_list(sources.strong_evidence_for_optimised_side),
            "## Strong Arguments For Optimized Side",
            _bullet_list(sources.strong_arguments_for_optimised_side),
            "## Weak Points For Opposing Side",
            _bullet_list(sources.weak_points_for_opposing_side),
            "## Weak Points For Optimized Side",
            _bullet_list(sources.weak_points_for_optimised_side),
            "## Citations",
            _bullet_list(sources.citations),
            "## Gaps",
            _bullet_list(sources.gaps),
        ]
    )


def _argument_markdown(*, title: str, argument: DebateArgument) -> str:
    return "\n\n".join(
        [
            f"# {title}",
            "## Argument",
            argument.argument,
            "## Key Points",
            _bullet_list(argument.key_points),
            "## Weaknesses Acknowledged",
            _bullet_list(argument.weaknesses_acknowledged),
        ]
    )


def _judgment_markdown(*, title: str, judgment: TurnJudgment) -> str:
    scores = judgment.scores
    return "\n\n".join(
        [
            f"# {title}",
            f"Total score: {judgment.total_score}/100",
            "## Rubric",
            "\n".join(
                [
                    f"- Validity of argument: {scores.validity_of_argument}/20",
                    f"- Groundedness in evidence: {scores.groundedness_in_evidence}/20",
                    f"- Counter-attack/defense: {scores.counter_attack_or_defense}/20",
                    f"- Legal specificity: {scores.legal_specificity}/20",
                    f"- Strategic strength: {scores.strategic_strength}/20",
                ]
            ),
            "## Strongest Points",
            _bullet_list(judgment.strongest_points),
            "## Weak Points",
            _bullet_list(judgment.weak_points),
            "## Counter Opportunities",
            _bullet_list(judgment.counter_opportunities),
            "## Rationale",
            judgment.rationale,
        ]
    )


def _final_assessment_markdown(assessment: FinalAssessment) -> str:
    return "\n\n".join(
        [
            "# Final Assessment",
            f"Optimised for: {assessment.optimised_for}",
            f"Risk score: {assessment.risk_score}/10",
            f"Recommendation: {assessment.settle_recommendation}",
            "## Strongest Arguments",
            _bullet_list(assessment.strongest_arguments),
            "## Vulnerabilities",
            _bullet_list(assessment.vulnerabilities),
            "## Precedents To Cite",
            _bullet_list(assessment.precedents_to_cite),
            "## Risk Rationale",
            assessment.risk_rationale,
            "## Settlement Rationale",
            assessment.settle_rationale,
            "## Suggested Settlement Terms",
            _bullet_list(assessment.suggested_settlement_terms or []),
            "## Evidence Gaps",
            _bullet_list(assessment.evidence_gaps),
        ]
    )


def _bullet_list(items: list[str]) -> str:
    if not items:
        return "- None"
    return "\n".join(f"- {item}" for item in items)


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