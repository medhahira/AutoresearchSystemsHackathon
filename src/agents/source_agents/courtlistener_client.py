import os
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class CourtListenerClient:
    """Small API client for CourtListener search endpoint."""

    def __init__(
        self,
        api_token: Optional[str] = None,
        base_url: str = "https://www.courtlistener.com/api/rest/v4",
        timeout: int = 20,
    ) -> None:
        self.api_token = api_token or os.getenv("COURTLISTENER_API_TOKEN")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_token:
            headers["Authorization"] = f"Token {self.api_token}"
        return headers

    def _session(self) -> requests.Session:
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

    def search(
        self,
        query: str,
        top_k: int = 5,
        result_type: str = "o",
        filters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not query.strip():
            raise ValueError("query must not be empty")

        params: Dict[str, Any] = {
            "q": query,
            "type": result_type,
            "page_size": min(20, max(1, top_k)),
        }

        if filters:
            params.update(filters)

        url = f"{self.base_url}/search/"
        session = self._session()
        try:
            response = session.get(
                url,
                params=params,
                headers=self._headers(),
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json()
        except ValueError as exc:
            raise RuntimeError(f"CourtListener returned non-JSON response: {exc}") from exc
        finally:
            session.close()


def to_evidence_snippets(payload: Dict[str, Any], top_k: int = 5) -> List[Dict[str, Any]]:
    """Convert CourtListener search payload to shared evidence snippet shape."""
    results = payload.get("results", [])[: max(1, top_k)]
    evidence: List[Dict[str, Any]] = []

    for rank, item in enumerate(results, start=1):
        case_name = item.get("caseName") or item.get("caseNameShort") or "Unknown case"
        absolute_url = item.get("absolute_url")
        if absolute_url and absolute_url.startswith("/"):
            absolute_url = f"https://www.courtlistener.com{absolute_url}"

        snippet = item.get("snippet") or item.get("text") or case_name
        citation_count = item.get("citeCount")
        if citation_count is not None:
            try:
                citation_count = int(citation_count)
            except (TypeError, ValueError):
                citation_count = None

        status = item.get("status")
        precedential_status = status if isinstance(status, str) and status.strip() else None

        court = item.get("court") or item.get("court_citation_string")
        court_level = court if isinstance(court, str) and court.strip() else None

        decision_date = item.get("dateFiled") or item.get("date_filed")
        if not isinstance(decision_date, str) or not decision_date.strip():
            decision_date = None

        jurisdiction = item.get("court_jurisdiction")
        if not isinstance(jurisdiction, str) or not jurisdiction.strip():
            jurisdiction = "unknown"

        evidence.append(
            {
                "source_name": "courtlistener",
                "citation": absolute_url or "",
                "snippet": snippet,
                "relevance": max(0.0, 1.0 - (rank - 1) * 0.1),
                "source_type": "case_law",
                "jurisdiction": jurisdiction,
                "court_level": court_level,
                "decision_date": decision_date,
                "precedential_status": precedential_status,
                "citation_count": citation_count,
                "treatment_signal": "neutral",
            }
        )

    return evidence
