import json
from pathlib import Path
from typing import Any, Dict, List, Set


DEFAULT_CORPUS_PATH = Path("data/sources/statutes_corpus.json")
SOURCE_NAME = "statutes"


def _tokenize(text: str) -> Set[str]:
    return {token.lower() for token in text.replace("_", " ").split() if token.strip()}


def _load_corpus(corpus_path: Path) -> List[Dict[str, Any]]:
    with corpus_path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, list):
        raise ValueError("statutes corpus must be a JSON array")
    return payload


def run_statutes_source_agent(agent_input: Dict[str, Any]) -> Dict[str, Any]:
    request = agent_input.get("request", {})
    source_name = str(request.get("source_name", "")).lower()
    if source_name != SOURCE_NAME:
        raise ValueError("source_name must be 'statutes'")

    query = str(request.get("query", "")).strip()
    if not query:
        raise ValueError("query must not be empty")

    top_k = int(request.get("top_k", 5))
    filters = request.get("filters") or {}
    jurisdiction_filter = str(filters.get("jurisdiction", "")).lower().strip()

    corpus_path = Path(str(filters.get("corpus_path", DEFAULT_CORPUS_PATH)))
    corpus = _load_corpus(corpus_path)

    query_tokens = _tokenize(query)
    scored: List[Dict[str, Any]] = []

    for row in corpus:
        jurisdiction = str(row.get("jurisdiction", "")).lower()
        if jurisdiction_filter and jurisdiction_filter != jurisdiction:
            continue

        haystack = " ".join(
            [
                str(row.get("title", "")),
                str(row.get("text", "")),
                str(row.get("jurisdiction", "")),
            ]
        )
        row_tokens = _tokenize(haystack)
        overlap = len(query_tokens & row_tokens)
        if overlap == 0:
            continue

        base_score = overlap / max(1, len(query_tokens))
        scored.append(
            {
                "source_name": SOURCE_NAME,
                "citation": str(row.get("citation", "")),
                "snippet": str(row.get("text", ""))[:400],
                "relevance": round(min(1.0, 0.4 + base_score), 3),
                "source_type": str(row.get("source_type", "statute")),
                "jurisdiction": str(row.get("jurisdiction", "")),
                "decision_date": None,
                "court_level": "statutory",
                "precedential_status": "binding-if-applicable",
                "citation_count": None,
                "treatment_signal": "neutral",
            }
        )

    scored.sort(key=lambda x: x["relevance"], reverse=True)
    results = scored[: max(1, top_k)]

    return {
        "source_name": SOURCE_NAME,
        "query": query,
        "results": results,
        "latency_ms": 0,
        "error": None,
    }
