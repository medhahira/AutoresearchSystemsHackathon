from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from legal_arena.agents.case_builder import build_case
from legal_arena.agents.case_builder import make_case_builder_input, make_case_builder_prompt
from legal_arena.file_search import load_documents_from_paths
from legal_arena.modal_runtime import ModalRuntimeConfig, validate_modal_gpu
from legal_arena.schemas import Document
from legal_arena.pipeline import run_debate


def _print_toolbox(problem_statement: str, documents: list[Document], prompts: tuple[str, str], traces: list[str]) -> None:
    system_prompt, user_prompt = prompts
    print("\n=== Toolbox ===")
    print("Workflow: case_builder -> courtlistener/source_pool -> source_synthesizer -> debaters -> judge -> final_assessor")
    print("Prompt: case_builder")
    print(system_prompt)
    print(user_prompt)
    if traces:
        print("\nInput processing:")
        for trace in traces:
            print(f"- {trace}")
    if documents:
        print("\nLoaded documents:")
        for document in documents:
            print(f"- {document.title} ({document.source or 'inline'})")
    print("=== End Toolbox ===\n")


async def main(
    problem_statement: str,
    input_paths: list[Path],
    n_rounds: int = 2,
    use_modal: bool = False,
    modal_gpu: str | None = None,
    parallel_opening_round: bool = True,
    raindrop_enabled: bool | None = None,
    show_toolbox: bool = False,
    use_file_search: bool = False,
) -> None:
    documents, traces = load_documents_from_paths(input_paths, use_file_search=use_file_search, query=problem_statement)
    case_input = make_case_builder_input(problem_statement, documents)
    toolbox_prompts = make_case_builder_prompt(case_input)

    if show_toolbox:
        _print_toolbox(problem_statement, documents, toolbox_prompts, traces)

    case = await build_case(problem_statement, documents)

    print(f"\n=== Case: {case.title} ===")
    print(f"Optimising for: {case.optimise_for}")

    modal_config = ModalRuntimeConfig.from_env()
    modal_config.enabled = use_modal or modal_config.enabled
    if modal_gpu:
        modal_config.gpu = validate_modal_gpu(modal_gpu)

    assessment = await run_debate(
        case,
        n_rounds=n_rounds,
        documents=documents,
        modal_config=modal_config,
        parallel_opening_round=parallel_opening_round,
        raindrop_enabled=raindrop_enabled,
    )

    print("\n=== Final Assessment ===")
    print(f"Risk score: {assessment.risk_score}/10")
    print(f"Recommendation: {assessment.settle_recommendation}")
    print(f"Rationale: {assessment.settle_rationale}")
    if assessment.strongest_arguments:
        print("\nStrongest arguments:")
        for argument in assessment.strongest_arguments:
            print(f"- {argument}")
    if assessment.vulnerabilities:
        print("\nVulnerabilities:")
        for vulnerability in assessment.vulnerabilities:
            print(f"- {vulnerability}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Legal Arena debate workflow.")
    parser.add_argument("input_paths", nargs="*", type=Path, help="Optional PDF or text files to ingest.")
    parser.add_argument("--problem", help="Legal situation or problem statement. If omitted, prompts interactively.")
    parser.add_argument("--rounds", type=int, default=2, help="Number of prosecution/defense debate rounds.")
    parser.add_argument("--modal", action="store_true", help="Run source agents with Modal sandbox configuration when available.")
    parser.add_argument("--modal-gpu", help="Modal GPU spec for source-agent sandboxes. Legal Arena allows A10 only.")
    parser.add_argument("--sequential-opening", action="store_true", help="Make round 1 defense respond to prosecution instead of preparing independently.")
    parser.add_argument("--raindrop", action="store_true", help="Enable Raindrop Workshop tracing for this run.")
    parser.add_argument("--toolbox", action="store_true", help="Show the prompt, workflow, and file-ingestion toolbox before running.")
    parser.add_argument("--file-search", action="store_true", help="Use OpenAI file-search vector stores for uploaded files before case building.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    problem = args.problem or input("Describe your legal situation: ")
    asyncio.run(
        main(
            problem_statement=problem,
            input_paths=args.input_paths,
            n_rounds=args.rounds,
            use_modal=args.modal,
            modal_gpu=args.modal_gpu,
            parallel_opening_round=not args.sequential_opening,
            raindrop_enabled=args.raindrop,
            show_toolbox=args.toolbox,
            use_file_search=args.file_search,
        )
    )