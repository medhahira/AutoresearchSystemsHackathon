from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import pdfplumber

from legal_arena.agents.case_builder import build_case
from legal_arena.modal_runtime import ModalRuntimeConfig, validate_modal_gpu
from legal_arena.pipeline import run_debate


def extract_pdf_text(pdf_paths: list[Path]) -> list[str]:
    documents: list[str] = []
    for path in pdf_paths:
        with pdfplumber.open(path) as pdf:
            pages = [page.extract_text() for page in pdf.pages]
            text = "\n".join(page for page in pages if page)
            if text.strip():
                documents.append(text)
    return documents


async def main(
    problem_statement: str,
    pdf_paths: list[Path],
    n_rounds: int = 2,
    use_modal: bool = False,
    modal_gpu: str | None = None,
    parallel_opening_round: bool = True,
    raindrop_enabled: bool | None = None,
) -> None:
    documents = extract_pdf_text(pdf_paths)
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
    parser.add_argument("pdf_paths", nargs="*", type=Path, help="Optional PDF files to ingest.")
    parser.add_argument("--problem", help="Legal situation or problem statement. If omitted, prompts interactively.")
    parser.add_argument("--rounds", type=int, default=2, help="Number of prosecution/defense debate rounds.")
    parser.add_argument("--modal", action="store_true", help="Run source agents with Modal sandbox configuration when available.")
    parser.add_argument("--modal-gpu", help="Modal GPU spec for source-agent sandboxes. Legal Arena allows A10 only.")
    parser.add_argument("--sequential-opening", action="store_true", help="Make round 1 defense respond to prosecution instead of preparing independently.")
    parser.add_argument("--raindrop", action="store_true", help="Enable Raindrop Workshop tracing for this run.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    problem = args.problem or input("Describe your legal situation: ")
    asyncio.run(
        main(
            problem_statement=problem,
            pdf_paths=args.pdf_paths,
            n_rounds=args.rounds,
            use_modal=args.modal,
            modal_gpu=args.modal_gpu,
            parallel_opening_round=not args.sequential_opening,
            raindrop_enabled=args.raindrop,
        )
    )