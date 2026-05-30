from typing import Any, Dict

try:
    from .courtlistener_source_agent import run_courtlistener_source_agent
    from .dockets_source_agent import run_dockets_source_agent
    from .statutes_source_agent import run_statutes_source_agent
except ImportError:
    from courtlistener_source_agent import run_courtlistener_source_agent
    from dockets_source_agent import run_dockets_source_agent
    from statutes_source_agent import run_statutes_source_agent


def run_source_agent(agent_input: Dict[str, Any]) -> Dict[str, Any]:
    request = agent_input.get("request", {})
    source_name = str(request.get("source_name", "")).lower()

    if source_name == "courtlistener":
        return run_courtlistener_source_agent(agent_input)
    if source_name == "statutes":
        return run_statutes_source_agent(agent_input)
    if source_name == "dockets":
        return run_dockets_source_agent(agent_input)

    raise ValueError(
        "Unsupported source_name. Expected one of: courtlistener, statutes, dockets"
    )
