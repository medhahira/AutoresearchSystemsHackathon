from __future__ import annotations

from legal_arena.llm import json_for_prompt, structured_completion
from legal_arena.schemas import SourceResult, SynthesizedSources


SYSTEM_PROMPT = """You are a legal research synthesizer. Given raw search results from multiple
sources and a specific legal question, extract only what is directly relevant.
Discard everything else. Cite every claim. Flag anything you could not verify.
Highlight strong evidence and strong arguments for the optimized side, and also
identify weak points in the opposing side's position. Do not hide weak points in
the optimized side's own case; label them clearly so counsel can repair them.
Output only valid JSON matching the SynthesizedSources schema."""

MAX_FINDINGS_CHARS = 2000
MAX_RESULTS_IN_PROMPT = 8


def _compact_source_result(source_result: SourceResult) -> dict:
    raw_findings = source_result.raw_findings or ""
    compact_findings = raw_findings[:MAX_FINDINGS_CHARS]
    if len(raw_findings) > MAX_FINDINGS_CHARS:
        compact_findings += "\n...[truncated]"
    return {
        "source_type": source_result.source_type,
        "query": source_result.query,
        "raw_findings": compact_findings,
        "citations": source_result.citations[:10],
        "error": source_result.error,
    }


async def synthesize_sources(source_results: list[SourceResult], question: str) -> SynthesizedSources:
    if not source_results:
        return SynthesizedSources(
            relevant_excerpts="No source results were returned.",
            gaps=["No source results were available for this turn."],
            confidence=0,
        )

    compact_results = [_compact_source_result(result) for result in source_results[:MAX_RESULTS_IN_PROMPT]]
    user_prompt = f"Question:\n{question}\n\nSource results:\n{json_for_prompt(compact_results)}"
    return await structured_completion(output_type=SynthesizedSources, system_prompt=SYSTEM_PROMPT, user_prompt=user_prompt)