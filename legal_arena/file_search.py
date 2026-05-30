from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pdfplumber

from legal_arena.schemas import Document


def load_documents_from_paths(
    input_paths: Iterable[Path],
    *,
    use_file_search: bool = False,
    query: str = "",
) -> tuple[list[Document], list[str]]:
    paths = [path for path in input_paths if path.exists() and path.is_file()]
    if not paths:
        return [], ["No input files supplied."]

    if use_file_search:
        try:
            return _load_documents_via_file_search(paths, query=query)
        except Exception as exc:
            return _load_documents_locally(paths), [f"File search unavailable; used local extraction instead ({exc})."]

    return _load_documents_locally(paths), ["Local file extraction used. Pass --file-search to use OpenAI vector-store retrieval."]


def _load_documents_locally(paths: list[Path]) -> list[Document]:
    documents: list[Document] = []
    for index, path in enumerate(paths):
        text = _extract_text(path)
        if text.strip():
            documents.append(Document.from_text(text, index, title=path.name, source=str(path)))
    return documents


def _extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        with pdfplumber.open(path) as pdf:
            pages = [page.extract_text() for page in pdf.pages]
            return "\n".join(page for page in pages if page)

    return path.read_text(encoding="utf-8", errors="ignore")


def _load_documents_via_file_search(paths: list[Path], *, query: str) -> tuple[list[Document], list[str]]:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("openai package is required for file-search ingestion") from exc

    client = OpenAI()
    vector_store = client.vector_stores.create(name="legal-arena-case-files")
    traces = [f"Created OpenAI vector store: {vector_store.id}"]

    handles = [path.open("rb") for path in paths]
    try:
        client.vector_stores.file_batches.upload_and_poll(vector_store_id=vector_store.id, files=handles)
        traces.append(f"Uploaded {len(handles)} file(s) for OpenAI file search.")

        search_results = client.vector_stores.search(
            vector_store_id=vector_store.id,
            query=query or "Summarize the most relevant facts, claims, dates, parties, and legal issues.",
            max_num_results=min(8, max(1, len(paths) * 2)),
            rewrite_query=True,
        )

        documents: list[Document] = []
        for index, item in enumerate(getattr(search_results, "data", [])):
            content = _format_search_result(item)
            if content.strip():
                documents.append(Document.from_text(content, index, title=getattr(item, "filename", f"search_result_{index + 1}"), source="openai_file_search"))

        if documents:
            traces.append(f"Retrieved {len(documents)} file-search snippet(s) from the vector store.")
            return documents, traces

        traces.append("File search returned no snippets; falling back to local file text extraction.")
        return _load_documents_locally(paths), traces
    finally:
        for handle in handles:
            handle.close()


def _format_search_result(item: object) -> str:
    filename = getattr(item, "filename", "unknown_file")
    score = getattr(item, "score", None)
    content_parts = []
    for chunk in getattr(item, "content", []) or []:
        text = getattr(chunk, "text", "")
        if text:
            content_parts.append(text)

    content = "\n".join(content_parts).strip()
    if score is not None:
        return f"File: {filename}\nScore: {score:.4f}\nExcerpt: {content}"
    return f"File: {filename}\nExcerpt: {content}"