from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Sequence

from legal_arena.llm import get_default_model, json_for_prompt, structured_completion
from legal_arena.schemas import SourceFetchRequest, SourceResult


SOURCE_PROMPTS = {
    "case_law": """You are a case law source agent. Search for precedents, holdings,
procedural posture, and jurisdiction-specific case law relevant to the query. Cite sources.""",
    "statutes": """You are a statute source agent. Search for statutes, regulations,
elements, penalties, and jurisdiction-specific legal text relevant to the query. Cite sources.""",
    "uploaded_docs": """You are a document source agent. Use only the uploaded document excerpts
provided by the orchestrator. Return exact, relevant excerpts and identify the document title.""",
    "secondary": """You are a secondary source agent. Search for reliable explainers,
treatises, practice guides, and agency guidance relevant to the query. Cite sources.""",
}


@dataclass(slots=True)
class LocalSourceAgent:
    source_type: str
    documents: Sequence[str]

    @property
    def name(self) -> str:
        return f"source_agent_{self.source_type}"


def make_source_agent(source_type: str, *, documents: Sequence[str] = ()):
    try:
        from agents import Agent, WebSearchTool
    except ImportError:
        return LocalSourceAgent(source_type=source_type, documents=documents)

    tools = [] if source_type == "uploaded_docs" else [WebSearchTool()]
    return Agent(
        name=f"source_agent_{source_type}",
        model=get_default_model(),
        tools=tools,
        instructions=SOURCE_PROMPTS[source_type],
        output_type=SourceResult,
    )


async def run_source_agent(
    request: SourceFetchRequest,
    *,
    documents: Sequence[str] = (),
    run_config: Any | None = None,
) -> SourceResult:
    started = time.perf_counter()
    if request.source_type == "uploaded_docs":
        return _search_uploaded_docs(request, documents, started)

    agent = make_source_agent(request.source_type, documents=documents)
    if isinstance(agent, LocalSourceAgent):
        return await _run_llm_source_agent(request, started)

    try:
        from agents.run import Runner
    except ImportError:
        try:
            from agents import Runner
        except ImportError:
            return await _run_llm_source_agent(request, started)

    prompt = f"Source request:\n{json_for_prompt(request)}"
    try:
        result = await Runner.run(agent, prompt, run_config=run_config) if run_config else await Runner.run(agent, prompt)
    except TypeError:
        if run_config is None:
            raise
        return await _run_llm_source_agent(request, started)

    output = result.final_output
    if isinstance(output, SourceResult):
        return output.model_copy(update={"latency_ms": _elapsed_ms(started)})
    return SourceResult.model_validate(output).model_copy(update={"latency_ms": _elapsed_ms(started)})


async def _run_llm_source_agent(request: SourceFetchRequest, started: float) -> SourceResult:
    system_prompt = SOURCE_PROMPTS[request.source_type]
    user_prompt = f"Source request:\n{json_for_prompt(request)}"
    result = await structured_completion(output_type=SourceResult, system_prompt=system_prompt, user_prompt=user_prompt)
    return result.model_copy(update={"latency_ms": _elapsed_ms(started)})


def _search_uploaded_docs(request: SourceFetchRequest, documents: Sequence[str], started: float) -> SourceResult:
    terms = [term.lower() for term in request.query.replace("/", " ").split() if len(term) > 3]
    excerpts: list[str] = []
    citations: list[str] = []

    for index, document in enumerate(documents):
        lower_document = document.lower()
        if not terms or any(term in lower_document for term in terms):
            excerpt = _best_excerpt(document, terms)
            if excerpt:
                excerpts.append(excerpt)
                citations.append(f"uploaded_doc_{index + 1}")
        if len(excerpts) >= request.top_k:
            break

    if not excerpts:
        return SourceResult(
            source_type=request.source_type,
            query=request.query,
            raw_findings="",
            citations=[],
            latency_ms=_elapsed_ms(started),
            error="No matching uploaded document excerpts found.",
        )

    return SourceResult(
        source_type=request.source_type,
        query=request.query,
        raw_findings="\n\n".join(excerpts),
        citations=citations,
        latency_ms=_elapsed_ms(started),
    )


def _best_excerpt(document: str, terms: list[str], *, window: int = 1200) -> str:
    if not document.strip():
        return ""
    lower_document = document.lower()
    match_indexes = [lower_document.find(term) for term in terms if lower_document.find(term) >= 0]
    start = max(0, min(match_indexes) - window // 3) if match_indexes else 0
    excerpt = document[start : start + window].strip()
    return " ".join(excerpt.split())


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)