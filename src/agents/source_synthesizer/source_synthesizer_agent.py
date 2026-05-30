from typing import Any, Dict, List, Set, Tuple


def _tokenize(text: str) -> Set[str]:
    return {token.lower() for token in text.replace("_", " ").split() if token.strip()}


def _score_evidence(item: Dict[str, Any], target_terms: Set[str]) -> float:
    snippet = str(item.get("snippet", ""))
    relevance = float(item.get("relevance", 0.0))
    overlap = len(_tokenize(snippet) & target_terms)
    overlap_boost = min(0.4, overlap * 0.05)
    return min(1.0, relevance + overlap_boost)


def _dedupe(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: Set[Tuple[str, str]] = set()
    unique_items: List[Dict[str, Any]] = []
    for item in items:
        key = (
            str(item.get("citation", "")).strip().lower(),
            str(item.get("snippet", "")).strip().lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        unique_items.append(item)
    return unique_items


def run_source_synthesizer_agent(synth_input: Dict[str, Any]) -> Dict[str, Any]:
    case_obj = synth_input.get("case", {})
    conversation = synth_input.get("conversation", [])
    evidence = synth_input.get("retrieved_evidence", [])
    side = synth_input.get("side", "prosecution")
    round_no = int(synth_input.get("round", 1))

    if side not in {"prosecution", "defense"}:
        raise ValueError("side must be 'prosecution' or 'defense'")

    side_goals = case_obj.get(f"{side}_goals", [])
    facts = case_obj.get("facts", [])
    recent_args = [
        entry.get("argument", "")
        for entry in conversation[-3:]
        if isinstance(entry, dict)
    ]

    target_terms = _tokenize(" ".join([*facts, *side_goals, *recent_args]))

    unique_evidence = _dedupe(evidence)
    scored = []
    for item in unique_evidence:
        score = _score_evidence(item, target_terms)
        normalized = dict(item)
        normalized["relevance"] = round(score, 3)
        scored.append(normalized)

    scored.sort(key=lambda x: x.get("relevance", 0.0), reverse=True)
    top_items = scored[:5]

    if not top_items:
        summary = "No relevant external evidence found for this turn."
        confidence = 0.0
    else:
        bullets = []
        for item in top_items[:3]:
            snippet = str(item.get("snippet", "")).replace("\n", " ").strip()
            bullets.append(snippet[:140])
        summary = " | ".join(bullets)
        confidence = round(
            sum(float(item.get("relevance", 0.0)) for item in top_items)
            / len(top_items),
            3,
        )

    return {
        "round": round_no,
        "side": side,
        "synthesized_evidence": top_items,
        "summary": summary,
        "confidence": confidence,
    }
