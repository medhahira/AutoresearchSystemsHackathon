import argparse
import json
from typing import Any, Dict

try:
    from .source_router import run_source_agent
except ImportError:
    from source_router import run_source_agent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run generic source agent")
    parser.add_argument(
        "--source",
        required=True,
        choices=["courtlistener", "statutes", "dockets"],
        help="Source name",
    )
    parser.add_argument("--query", required=True, help="Search query")
    parser.add_argument("--case-title", default="Unknown Case", help="Case title")
    parser.add_argument("--round", type=int, default=1, help="Debate round")
    parser.add_argument(
        "--side",
        choices=["defense", "prosecution"],
        default="prosecution",
        help="Current side",
    )
    parser.add_argument("--top-k", type=int, default=5, help="Number of results")
    parser.add_argument("--jurisdiction", default="", help="Optional jurisdiction filter")
    return parser.parse_args()


def build_input(args: argparse.Namespace) -> Dict[str, Any]:
    filters: Dict[str, Any] = {}
    if args.jurisdiction:
        filters["jurisdiction"] = args.jurisdiction

    return {
        "request": {
            "source_name": args.source,
            "query": args.query,
            "top_k": args.top_k,
            "filters": filters,
        },
        "case_title": args.case_title,
        "round": args.round,
        "side": args.side,
    }


def main() -> None:
    args = parse_args()
    agent_input = build_input(args)
    output = run_source_agent(agent_input)
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
