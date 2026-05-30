from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from typing import Any, Sequence

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from legal_arena.llm import get_default_model, json_for_prompt, structured_completion
from legal_arena.schemas import Document, SourceFetchRequest, SourceResult


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

_QUERY_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is", "it",
    "of", "on", "or", "that", "the", "this", "to", "under", "was", "were", "with",
}


def _make_retrying_session() -> requests.Session:
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _normalize_case_law_query(query: str, *, max_terms: int = 18, max_chars: int = 220) -> str:
    tokens = re.findall(r"[A-Za-z0-9']+", query.lower())
    compact_tokens: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if len(token) <= 2 or token in _QUERY_STOPWORDS:
            continue
        if token in seen:
            continue
        seen.add(token)
        compact_tokens.append(token)
        if len(compact_tokens) >= max_terms:
            break

    normalized = " ".join(compact_tokens)
    return normalized[:max_chars].strip()


def _candidate_case_law_queries(request_query: str) -> list[str]:
    normalized = " ".join(request_query.replace("/", " ").split())
    simplified = _normalize_case_law_query(normalized)

    candidates: list[str] = []
    if len(normalized) <= 220:
        candidates.append(normalized)
    if simplified and simplified not in candidates:
        candidates.append(simplified)
    if normalized not in candidates:
        candidates.append(normalized[:350])
    return [candidate for candidate in candidates if candidate]


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
    if request.source_type == "case_law":
        courtlistener_result = _search_case_law_with_courtlistener(request, started)
        if courtlistener_result is not None:
            return courtlistener_result

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


def _search_uploaded_docs(request: SourceFetchRequest, documents: Sequence[str | Document], started: float) -> SourceResult:
    terms = [term.lower() for term in request.query.replace("/", " ").split() if len(term) > 3]
    excerpts: list[str] = []
    citations: list[str] = []

    for index, document in enumerate(documents):
        document_text = document.content if isinstance(document, Document) else document
        lower_document = document_text.lower()
        if not terms or any(term in lower_document for term in terms):
            excerpt = _best_excerpt(document_text, terms)
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


def _search_case_law_with_courtlistener(
    request: SourceFetchRequest, started: float
) -> SourceResult | None:
    """Use CourtListener API for case-law retrieval and return normalized findings.

    Returns None only when CourtListener is explicitly disabled, so callers can
    intentionally use the existing LLM/web-search path.
    """
    if os.getenv("LEGAL_ARENA_DISABLE_COURTLISTENER", "0").lower() in {"1", "true", "yes"}:
        return None

    headers = {"Accept": "application/json"}
    api_token = os.getenv("COURTLISTENER_API_TOKEN")
    if api_token:
        headers["Authorization"] = f"Token {api_token}"

    page_size = min(20, max(1, request.top_k))
    last_error: str | None = None
    results: list[dict[str, Any]] = []
    payload: dict[str, Any] = {}

    for candidate_query in _candidate_case_law_queries(request.query):
        session = _make_retrying_session()
        try:
            response = session.get(
                "https://www.courtlistener.com/api/rest/v4/search/",
                params={
                    "q": candidate_query,
                    "type": "o",
                    "page_size": page_size,
                },
                headers=headers,
                timeout=25,
            )
            response.raise_for_status()
            try:
                payload = response.json()
            except ValueError as exc:
                last_error = f"CourtListener returned non-JSON payload: {exc}"
                continue
            results = payload.get("results", [])[:page_size]
            if results:
                break
        except Exception as exc:
            last_error = f"CourtListener request failed after retries: {exc}"
        finally:
            session.close()

    if not results:
        return SourceResult(
            source_type=request.source_type,
            query=request.query,
            raw_findings="",
            citations=[],
            latency_ms=_elapsed_ms(started),
            error=last_error or "CourtListener returned no case-law matches.",
        )

    findings: list[str] = []
    citations: list[str] = []

    for index, item in enumerate(results, start=1):
        case_name = item.get("caseName") or item.get("caseNameShort") or "Unknown case"
        absolute_url = item.get("absolute_url") or ""
        if absolute_url.startswith("/"):
            absolute_url = f"https://www.courtlistener.com{absolute_url}"
        if absolute_url:
            citations.append(absolute_url)

        snippet = item.get("snippet") or item.get("text") or case_name
        court = item.get("court") or item.get("court_citation_string") or "Unknown court"
        date_filed = item.get("dateFiled") or "Unknown date"
        status = item.get("status") or "Unknown status"
        cite_count = item.get("citeCount")
        cite_count_label = str(cite_count) if cite_count is not None else "unknown"

        findings.append(
            "\n".join(
                [
                    f"[{index}] {case_name}",
                    f"Court: {court}",
                    f"Date filed: {date_filed}",
                    f"Status: {status}",
                    f"Citation count: {cite_count_label}",
                    f"URL: {absolute_url or 'N/A'}",
                    f"Excerpt: {snippet}",
                ]
            )
        )

    return SourceResult(
        source_type=request.source_type,
        query=request.query,
        raw_findings="\n\n".join(findings),
        citations=citations,
        latency_ms=_elapsed_ms(started),
        error=None,
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