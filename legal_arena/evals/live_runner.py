from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any


DEFAULT_LIVE_EVAL_MODEL = "gpt-5.4-mini"


async def run_live_dummy_cases(
    *,
    model: str = DEFAULT_LIVE_EVAL_MODEL,
    n_rounds: int = 2,
    use_modal: bool = True,
    modal_gpu: str | None = None,
    limit: int | None = None,
    parallel_opening_round: bool = True,
) -> dict[str, Any]:
    os.environ["LEGAL_ARENA_MODEL"] = model
    if not os.getenv("OPENAI_API_KEY") and not os.getenv("OPENAI_ADMIN_KEY"):
        return {
            "config": {
                "model": model,
                "n_rounds": n_rounds,
                "use_modal": use_modal,
                "modal_gpu": modal_gpu,
                "case_count_requested": limit or "all",
                "parallel_opening_round": parallel_opening_round,
            },
            "seconds": 0,
            "cases": [],
            "error": "OPENAI_API_KEY is required for live dummy evaluation.",
        }

    from legal_arena.evals.dummy_cases import DUMMY_CASES
    from legal_arena.modal_runtime import ModalRuntimeConfig, validate_modal_gpu
    from legal_arena.pipeline import run_debate

    modal_config = ModalRuntimeConfig.from_env()
    modal_config.enabled = use_modal or modal_config.enabled
    if modal_gpu:
        modal_config.gpu = validate_modal_gpu(modal_gpu)

    selected_cases = DUMMY_CASES[:limit] if limit else DUMMY_CASES
    reports: list[dict[str, Any]] = []
    started_all = time.perf_counter()

    for dummy_case in selected_cases:
        started_case = time.perf_counter()
        try:
            assessment = await run_debate(
                dummy_case.case,
                n_rounds=n_rounds,
                documents=dummy_case.documents,
                modal_config=modal_config,
                parallel_opening_round=parallel_opening_round,
            )
            reports.append(
                {
                    "case_id": dummy_case.case_id,
                    "title": dummy_case.case.title,
                    "status": "ok",
                    "seconds": round(time.perf_counter() - started_case, 2),
                    "risk_score": assessment.risk_score,
                    "recommendation": assessment.settle_recommendation,
                    "strongest_arguments": assessment.strongest_arguments,
                    "vulnerabilities": assessment.vulnerabilities,
                    "evidence_gaps": assessment.evidence_gaps,
                }
            )
        except Exception as exc:
            reports.append(
                {
                    "case_id": dummy_case.case_id,
                    "title": dummy_case.case.title,
                    "status": "error",
                    "seconds": round(time.perf_counter() - started_case, 2),
                    "error": str(exc),
                }
            )
            break

    return {
        "config": {
            "model": model,
            "n_rounds": n_rounds,
            "use_modal": modal_config.enabled,
            "modal_gpu": modal_config.gpu,
            "case_count_requested": len(selected_cases),
            "parallel_opening_round": parallel_opening_round,
        },
        "seconds": round(time.perf_counter() - started_all, 2),
        "cases": reports,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run dummy cases through the live Legal Arena pipeline.")
    parser.add_argument("--model", default=DEFAULT_LIVE_EVAL_MODEL, help="OpenAI model to use for live eval calls.")
    parser.add_argument("--rounds", type=int, default=2, help="Debate rounds per dummy case.")
    parser.add_argument("--no-modal", action="store_true", help="Disable Modal sandbox integration.")
    parser.add_argument("--modal-gpu", help="Optional Modal GPU spec. Legal Arena allows A10 only.")
    parser.add_argument("--limit", type=int, help="Limit number of dummy cases for a smoke run.")
    parser.add_argument("--sequential-opening", action="store_true", help="Make round 1 defense respond to prosecution instead of preparing independently.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    parser.add_argument("--output", type=Path, help="Optional path to write the JSON report.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = asyncio.run(
        run_live_dummy_cases(
            model=args.model,
            n_rounds=args.rounds,
            use_modal=not args.no_modal,
            modal_gpu=args.modal_gpu,
            limit=args.limit,
            parallel_opening_round=not args.sequential_opening,
        )
    )
    output = json.dumps(report, indent=2 if args.pretty else None)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()