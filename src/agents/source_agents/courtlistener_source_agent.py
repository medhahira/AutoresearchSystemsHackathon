from typing import Any, Dict

try:
    from .courtlistener_client import CourtListenerClient, to_evidence_snippets
except ImportError:  # Script execution fallback.
    from courtlistener_client import CourtListenerClient, to_evidence_snippets


COURTLISTENER_SOURCE = "courtlistener"


def run_courtlistener_source_agent(agent_input: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a source-agent turn using CourtListener as backing corpus."""
    request = agent_input.get("request", {})
    source_name = request.get("source_name", "")
    if source_name.lower() != COURTLISTENER_SOURCE:
        raise ValueError("source_name must be 'courtlistener'")

    query = request.get("query", "")
    top_k = int(request.get("top_k", 5))
    filters = request.get("filters") or {}

    client = CourtListenerClient()
    payload = client.search(query=query, top_k=top_k, filters=filters)
    evidence = to_evidence_snippets(payload, top_k=top_k)

    return {
        "source_name": COURTLISTENER_SOURCE,
        "query": query,
        "results": evidence,
        "latency_ms": 0,
        "error": None,
    }
