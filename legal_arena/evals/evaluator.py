from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
from typing import Any

from legal_arena.evals.dummy_cases import DUMMY_CASES, DummyCase
from legal_arena.pipeline import DEFAULT_N_ROUNDS, EARLY_CONVERGENCE_MIN_ROUNDS, EARLY_STOP_SCORE_MARGIN, EARLY_STOP_SCORE_THRESHOLD, MAX_N_ROUNDS, should_stop_early
from legal_arena.schemas import FinalAssessment, RubricScores, TurnJudgment


def evaluate_dummy_cases(cases: list[DummyCase] | None = None) -> dict[str, Any]:
    cases = cases or DUMMY_CASES
    reports = [_evaluate_case(dummy_case) for dummy_case in cases]
    return {
        "config": {
            "default_n_rounds": DEFAULT_N_ROUNDS,
            "max_n_rounds": MAX_N_ROUNDS,
            "early_convergence_min_rounds": EARLY_CONVERGENCE_MIN_ROUNDS,
            "early_stop_score_threshold": EARLY_STOP_SCORE_THRESHOLD,
            "early_stop_score_margin": EARLY_STOP_SCORE_MARGIN,
        },
        "summary": {
            "case_count": len(reports),
            "avg_source_confidence": round(mean(report["avg_source_confidence"] for report in reports), 3),
            "avg_theme_coverage": round(mean(report["theme_coverage"] for report in reports), 3),
            "avg_citation_coverage": round(mean(report["citation_coverage"] for report in reports), 3),
            "cases_with_source_gaps": sum(1 for report in reports if report["source_gap_count"] > 0),
            "would_stop_early_count": sum(1 for report in reports if report["would_stop_early_after_round_2"]),
        },
        "cases": reports,
    }


def _evaluate_case(dummy_case: DummyCase) -> dict[str, Any]:
    source_packets = [turn.sources for turn in dummy_case.turn_sources]
    all_text = "\n".join(
        [
            dummy_case.case.model_dump_json(),
            *dummy_case.documents,
            *(source.relevant_excerpts for source in source_packets),
            *(" ".join(source.key_precedents + source.supporting_statutes) for source in source_packets),
        ]
    ).lower()
    covered_themes = [theme for theme in dummy_case.expected_themes if theme.lower() in all_text]
    citation_slots = len(source_packets)
    cited_slots = sum(1 for source in source_packets if source.citations)
    gap_count = sum(len(source.gaps) for source in source_packets)

    synthetic_assessment = _synthetic_assessment(dummy_case, covered_themes, gap_count)
    risk_in_band = dummy_case.expected_risk_band[0] <= synthetic_assessment.risk_score <= dummy_case.expected_risk_band[1]

    return {
        "case_id": dummy_case.case_id,
        "title": dummy_case.case.title,
        "optimise_for": dummy_case.case.optimise_for,
        "turn_source_packets": len(source_packets),
        "avg_source_confidence": round(mean(source.confidence for source in source_packets), 3),
        "theme_coverage": round(len(covered_themes) / len(dummy_case.expected_themes), 3),
        "covered_themes": covered_themes,
        "citation_coverage": round(cited_slots / citation_slots, 3) if citation_slots else 0,
        "source_gap_count": gap_count,
        "synthetic_risk_score": synthetic_assessment.risk_score,
        "risk_in_expected_band": risk_in_band,
        "would_stop_early_after_round_2": _would_stop_early(dummy_case),
    }


def _synthetic_assessment(dummy_case: DummyCase, covered_themes: list[str], gap_count: int) -> FinalAssessment:
    lower_bound, upper_bound = dummy_case.expected_risk_band
    uncovered_count = len(dummy_case.expected_themes) - len(covered_themes)
    risk_score = min(10, max(1, lower_bound + uncovered_count + min(2, gap_count // 2)))
    if risk_score > upper_bound:
        risk_score = upper_bound
    return FinalAssessment(
        optimised_for=dummy_case.case.optimise_for,
        strongest_arguments=covered_themes or ["Core legal theory needs more support"],
        vulnerabilities=["Unresolved source gaps"] if gap_count else ["Opposing side still has factual rebuttals"],
        precedents_to_cite=[precedent for turn in dummy_case.turn_sources for precedent in turn.sources.key_precedents],
        risk_score=risk_score,
        risk_rationale="Synthetic offline score based on expected theme coverage and unresolved source gaps.",
        settle_recommendation="negotiate" if 4 <= risk_score <= 7 else "litigate",
        settle_rationale="Offline benchmark placeholder, not legal advice.",
        evidence_gaps=[gap for turn in dummy_case.turn_sources for gap in turn.sources.gaps],
    )


def _would_stop_early(dummy_case: DummyCase) -> bool:
    judgments = {
        "prosecution": TurnJudgment(
            side="prosecution",
            round_number=2,
            scores=RubricScores(
                validity_of_argument=15,
                groundedness_in_evidence=14,
                counter_attack_or_defense=13,
                legal_specificity=14,
                strategic_strength=14,
            ),
            total_score=70,
            strongest_points=["Synthetic prosecution strength"],
            weak_points=["Synthetic prosecution weakness"],
            counter_opportunities=["Synthetic prosecution opportunity"],
            rationale="Synthetic judgment for offline early-stop check.",
        ),
        "defense": TurnJudgment(
            side="defense",
            round_number=2,
            scores=RubricScores(
                validity_of_argument=17,
                groundedness_in_evidence=17,
                counter_attack_or_defense=17,
                legal_specificity=17,
                strategic_strength=17,
            ),
            total_score=85,
            strongest_points=["Synthetic defense strength"],
            weak_points=["Synthetic defense weakness"],
            counter_opportunities=["Synthetic defense opportunity"],
            rationale="Synthetic judgment for offline early-stop check.",
        ),
    }
    return should_stop_early(
        round_num=2,
        optimise_for=dummy_case.case.optimise_for,
        latest_judgments=judgments,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate Legal Arena dummy cases without external API calls.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    parser.add_argument("--output", type=Path, help="Optional path to write the JSON report.")
    args = parser.parse_args()
    report = evaluate_dummy_cases()
    output = json.dumps(report, indent=2 if args.pretty else None)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()