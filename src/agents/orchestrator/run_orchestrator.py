import argparse
import json
from typing import Any, Dict

from orchestrator_agent import run_orchestrator_agent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run orchestrator agent")
    parser.add_argument("--input", required=True, help="Path to orchestrator input JSON")
    parser.add_argument(
        "--side",
        choices=["defense", "prosecution"],
        default=None,
        help="Optional side override",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with open(args.input, "r", encoding="utf-8") as f:
        payload: Dict[str, Any] = json.load(f)

    result = run_orchestrator_agent(payload, side_override=args.side)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
