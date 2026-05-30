import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from orchestrator.orchestrator_agent import run_orchestrator_agent
from source_agents.source_router import run_source_agent
from source_synthesizer.source_synthesizer_agent import run_source_synthesizer_agent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one full multi-source round: orchestrator -> sources -> synthesizer"
    )
    parser.add_argument(
        "--input",
        default="data/conversation/orchestrator_input.sample.json",
        help="Path to orchestrator input JSON",
    )
    parser.add_argument(
        "--output",
        default="data/conversation/round_demo_trace.json",
        help="Path to write round trace JSON",
    )
    parser.add_argument(
        "--side",
        choices=["defense", "prosecution"],
        default=None,
        help="Optional side override",
    )
    return parser.parse_args()


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError("Input payload must be a JSON object")
    return payload


def _build_source_input(
    source_request: Dict[str, Any], orchestrator_input: Dict[str, Any], orchestrator_output: Dict[str, Any]
) -> Dict[str, Any]:
    case_obj = orchestrator_input.get("case", {})
    return {
        "request": source_request,
        "case_title": str(case_obj.get("case_title", "Unknown Case")),
        "round": int(orchestrator_output.get("round", 1)),
        "side": str(orchestrator_output.get("side", "prosecution")),
    }


def run_round(
    orchestrator_input: Dict[str, Any], side_override: Optional[str] = None
) -> Dict[str, Any]:
    orchestrator_output = run_orchestrator_agent(orchestrator_input, side_override=side_override)

    source_outputs: List[Dict[str, Any]] = []
    merged_evidence: List[Dict[str, Any]] = []

    for source_request in orchestrator_output.get("fetch_plan", []):
        source_input = _build_source_input(source_request, orchestrator_input, orchestrator_output)
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
        "case": orchestrator_input.get("case", {}),
        "conversation": orchestrator_input.get("conversation", []),
        "retrieved_evidence": merged_evidence,
        "round": orchestrator_output.get("round", 1),
        "side": orchestrator_output.get("side", "prosecution"),
    }
    synthesizer_output = run_source_synthesizer_agent(synth_input)

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "orchestrator_output": orchestrator_output,
        "source_outputs": source_outputs,
        "merged_evidence_count": len(merged_evidence),
        "synthesizer_output": synthesizer_output,
    }


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)

    orchestrator_input = _load_json(input_path)
    trace = run_round(orchestrator_input, side_override=args.side)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(trace, f, indent=2)

    print(json.dumps(trace, indent=2))
    print(f"\nSaved round trace to: {output_path}")


if __name__ == "__main__":
    main()
