from __future__ import annotations

from legal_arena.llm import json_for_prompt, structured_completion
from legal_arena.schemas import CaseBuilderInput, CaseSchema, Document


SYSTEM_PROMPT = """You are a legal analyst. Given a problem statement and supporting documents,
extract a structured case profile. Be precise about what each side must prove.
Identify the most likely charges or claims and penalties at stake.
Ask yourself: what jurisdiction controls here? What area of law applies?
Output only valid JSON matching the CaseSchema."""


async def build_case(
    problem_statement: str,
    documents: list[str] | list[Document],
    *,
    jurisdiction: str | None = None,
    optimise_for: str = "defense",
) -> CaseSchema:
    normalized_documents = [
        document if isinstance(document, Document) else Document.from_text(document, index)
        for index, document in enumerate(documents)
    ]
    if not normalized_documents:
        normalized_documents = [Document.from_text("No uploaded documents provided.", 0, title="No Documents")]

    case_input = CaseBuilderInput(
        user_problem=problem_statement,
        incident_summary=problem_statement,
        documents=normalized_documents,
        jurisdiction=jurisdiction,
        constraints=[f"Optimize final strategy for {optimise_for}."] if optimise_for else [],
    )
    user_prompt = f"Case builder input:\n{json_for_prompt(case_input)}"
    return await structured_completion(output_type=CaseSchema, system_prompt=SYSTEM_PROMPT, user_prompt=user_prompt)