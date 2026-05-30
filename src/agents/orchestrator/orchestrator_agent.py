from typing import Any, Dict, List, Optional, Set


SUPPORTED_SOURCES = {"courtlistener", "statutes", "dockets"}


def _tokenize(text: str) -> Set[str]:
    return {token.lower() for token in text.replace("_", " ").split() if token.strip()}


def _infer_side(current_round: int, conversation: List[Dict[str, Any]]) -> str:
    # Prosecution starts each round. If prosecution already spoke this round, defense goes next.
    round_entries = [
        entry
        for entry in conversation
        if int(entry.get("round", -1)) == current_round
        and entry.get("speaker") in {"prosecution", "defense"}
    ]
    speakers = {entry.get("speaker") for entry in round_entries}
    return "defense" if "prosecution" in speakers else "prosecution"


def _build_query(case_obj: Dict[str, Any], side: str) -> str:
    case_title = case_obj.get("case_title", "")
    facts = case_obj.get("facts", [])[:3]
    side_goals = case_obj.get(f"{side}_goals", [])[:2]

    chunks = [case_title] + facts + side_goals
    return " ".join(str(chunk) for chunk in chunks if chunk)


def _resolve_sources(orchestrator_input: Dict[str, Any]) -> List[str]:
    preferred = orchestrator_input.get("preferred_sources")
    if not isinstance(preferred, list) or not preferred:
        return ["courtlistener", "statutes", "dockets"]

    normalized = []
    for source in preferred:
        source_name = str(source).lower().strip()
        if source_name in SUPPORTED_SOURCES:
            normalized.append(source_name)

    return normalized or ["courtlistener", "statutes", "dockets"]


def run_orchestrator_agent(
    orchestrator_input: Dict[str, Any],
    side_override: Optional[str] = None,
) -> Dict[str, Any]:
    case_obj = orchestrator_input.get("case", {})
    conversation = orchestrator_input.get("conversation", [])
    current_round = int(orchestrator_input.get("current_round", 1))
    allow_new_retrieval = bool(orchestrator_input.get("allow_new_retrieval", True))

    side = side_override or _infer_side(current_round, conversation)
    if side not in {"defense", "prosecution"}:
        raise ValueError("side must be 'defense' or 'prosecution'")

    if not allow_new_retrieval:
        return {
            "round": current_round,
            "side": side,
            "fetch_plan": [],
            "instructions": "Reuse existing conversation evidence for this turn.",
            "should_fetch": False,
        }

    query = _build_query(case_obj, side)
    if not query.strip():
        raise ValueError("Unable to build retrieval query from case input")

    case_tokens = _tokenize(query)
    top_k = 5 if len(case_tokens) > 6 else 3
    sources = _resolve_sources(orchestrator_input)

    fetch_plan = []
    for source_name in sources:
        source_top_k = top_k
        filters: Dict[str, Any] = {}
        if source_name == "courtlistener":
            filters["type"] = "o"
        else:
            source_top_k = min(3, top_k)

        fetch_plan.append(
            {
                "source_name": source_name,
                "query": query,
                "top_k": source_top_k,
                "filters": filters,
            }
        )

    instructions = (
        f"Fetch multi-source evidence supporting {side} strategy for round {current_round}."
    )

    return {
        "round": current_round,
        "side": side,
        "fetch_plan": fetch_plan,
        "instructions": instructions,
        "should_fetch": True,
    }
