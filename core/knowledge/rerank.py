"""Optional rerank support for knowledge retrieval."""

from __future__ import annotations

from typing import Protocol

import httpx

from core.knowledge.embedding import ModelSelection


class RerankClient(Protocol):
    async def rerank(
        self,
        selection: ModelSelection,
        query: str,
        documents: list[str],
    ) -> list[dict]:
        """Return rerank items with `index` and `score` keys."""


class OpenAIRerankClient:
    """Best-effort OpenAI-compatible rerank client for providers exposing `/rerank`."""

    async def rerank(
        self,
        selection: ModelSelection,
        query: str,
        documents: list[str],
    ) -> list[dict]:
        if not documents:
            return []
        base_url = str(selection.provider_config.get("base_url") or "").rstrip("/")
        if not base_url:
            raise ValueError("rerank provider base_url is required")
        url = _rerank_url(selection, base_url)
        headers = {}
        api_key = str(selection.provider_config.get("api_key") or "")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload = {
            "model": selection.model,
            "query": query,
            "documents": documents,
            "top_n": int(selection.config.get("top_n") or len(documents)),
        }
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        return _parse_rerank_response(data)


def _parse_rerank_response(data: object) -> list[dict]:
    if not isinstance(data, dict):
        return []
    raw_results = data.get("results") or data.get("data") or []
    if not isinstance(raw_results, list):
        return []
    parsed = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        if index is None and isinstance(item.get("document"), dict):
            index = item["document"].get("index")
        if index is None:
            continue
        score = item.get("relevance_score", item.get("score", 0.0))
        if score is None:
            score = 0.0
        try:
            parsed.append({"index": int(index), "score": float(score)})
        except (TypeError, ValueError):
            continue
    return parsed


def _rerank_url(selection: ModelSelection, base_url: str) -> str:
    endpoint = (
        selection.config.get("rerank_endpoint")
        or selection.config.get("rerank_url")
        or selection.provider_config.get("rerank_endpoint")
        or selection.provider_config.get("rerank_url")
    )
    if endpoint:
        return str(endpoint).rstrip("/")
    return f"{base_url}/rerank"
