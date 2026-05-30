import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from orchestrator.orchestrator_agent import run_orchestrator_agent
from source_agents.source_router import run_source_agent
from source_synthesizer.source_synthesizer_agent import run_source_synthesizer_agent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run N rounds with prosecution and defense turns and save one conversation log"
    )
    parser.add_argument(
        "--input",
        default="data/conversation/orchestrator_input.sample.json",
        help="Path to base orchestrator input JSON",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=2,
        help="Number of rounds to run",
    )
    parser.add_argument(
        "--output",
        default="data/conversation/multi_round_demo_trace.json",
        help="Path to write full multi-round trace JSON",
    )
    parser.add_argument(
        "--conversation-output",
        default="data/conversation/multi_round_conversation.json",
        help="Path to write final conversation JSON",
    )
    return parser.parse_args()


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError("Input payload must be a JSON object")
    return payload


def _build_source_input(
    source_request: Dict[str, Any], case_obj: Dict[str, Any], round_no: int, side: str
) -> Dict[str, Any]:
    return {
        "request": source_request,
        "case_title": str(case_obj.get("case_title", "Unknown Case")),
        "round": round_no,
        "side": side,
    }


def _generate_mock_argument(
    side: str, round_no: int, synth_output: Dict[str, Any]
) -> str:
    summary = str(synth_output.get("summary", "")).strip()
    evidence = synth_output.get("synthesized_evidence", [])
    top_citation = ""
    if evidence:
        top_citation = str(evidence[0].get("citation", "")).strip()

    opening = "We argue" if side == "prosecution" else "We rebut"
    citation_clause = f" Key support: {top_citation}." if top_citation else ""
    if not summary:
        summary = "No external evidence was available this turn."

    return f"Round {round_no} {side} turn: {opening} based on synthesized evidence. {summary}.{citation_clause}".strip()


def _run_turn(
    case_obj: Dict[str, Any],
    conversation: List[Dict[str, Any]],
    round_no: int,
    side: str,
    preferred_sources: List[str],
) -> Dict[str, Any]:
    orchestrator_input = {
        "case": case_obj,
        "conversation": conversation,
        "total_rounds": round_no,
        "current_round": round_no,
        "allow_new_retrieval": True,
        "preferred_sources": preferred_sources,
    }
    orchestrator_output = run_orchestrator_agent(orchestrator_input, side_override=side)

    source_outputs: List[Dict[str, Any]] = []
    merged_evidence: List[Dict[str, Any]] = []

    for source_request in orchestrator_output.get("fetch_plan", []):
        source_input = _build_source_input(source_request, case_obj, round_no, side)
        try:
            source_output = run_source_agent(source_input)
        except Exception as exc:
            source_output = {
                "source_name": source_request.get("source_name", "unknown"),
                "query": source_request.get("query", ""),
                "results": [],
                "latency_ms": 0,
                "error": str(exc),
            }
        source_outputs.append(source_output)
        merged_evidence.extend(source_output.get("results", []))

    synth_input = {
        "case": case_obj,
        "conversation": conversation,
        "retrieved_evidence": merged_evidence,
        "round": round_no,
        "side": side,
    }
    synthesizer_output = run_source_synthesizer_agent(synth_input)

    argument = _generate_mock_argument(side, round_no, synthesizer_output)
    conversation_entry = {
        "round": round_no,
        "speaker": side,
        "synthesized_evidence": synthesizer_output.get("synthesized_evidence", []),
        "argument": argument,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    return {
        "side": side,
        "orchestrator_output": orchestrator_output,
        "source_outputs": source_outputs,
        "merged_evidence_count": len(merged_evidence),
        "synthesizer_output": synthesizer_output,
        "conversation_entry": conversation_entry,
    }


def run_multi_round(base_input: Dict[str, Any], rounds: int) -> Dict[str, Any]:
    if rounds < 1:
        raise ValueError("rounds must be >= 1")

    case_obj = base_input.get("case", {})
    conversation = list(base_input.get("conversation", []))
    preferred_sources = base_input.get("preferred_sources") or [
        "courtlistener",
        "statutes",
        "dockets",
    ]

    round_traces: List[Dict[str, Any]] = []
    for round_no in range(1, rounds + 1):
        prosecution_trace = _run_turn(
            case_obj, conversation, round_no, "prosecution", preferred_sources
        )
        conversation.append(prosecution_trace["conversation_entry"])

        defense_trace = _run_turn(
            case_obj, conversation, round_no, "defense", preferred_sources
        )
        conversation.append(defense_trace["conversation_entry"])

        round_traces.append(
            {
                "round": round_no,
                "prosecution_turn": prosecution_trace,
                "defense_turn": defense_trace,
            }
        )

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "rounds": rounds,
        "total_turns": rounds * 2,
        "round_traces": round_traces,
        "final_conversation": conversation,
    }


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)
    conversation_output_path = Path(args.conversation_output)

    base_input = _load_json(input_path)
    trace = run_multi_round(base_input, args.rounds)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(trace, f, indent=2)

    with conversation_output_path.open("w", encoding="utf-8") as f:
        json.dump(trace.get("final_conversation", []), f, indent=2)

    print(json.dumps(trace, indent=2))
    print(f"\nSaved multi-round trace to: {output_path}")
    print(f"Saved final conversation to: {conversation_output_path}")


if __name__ == "__main__":
    main()
