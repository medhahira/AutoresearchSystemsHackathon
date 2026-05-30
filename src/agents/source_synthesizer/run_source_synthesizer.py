import argparse
import json
from typing import Any, Dict

from source_synthesizer_agent import run_source_synthesizer_agent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run source synthesizer agent")
    parser.add_argument("--input", required=True, help="Path to synthesizer input JSON")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with open(args.input, "r", encoding="utf-8") as f:
        payload: Dict[str, Any] = json.load(f)

    result = run_source_synthesizer_agent(payload)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
