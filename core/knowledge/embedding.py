"""Embedding model-pool validation and OpenAI-compatible embedding calls."""

from __future__ import annotations

import asyncio
import base64
import struct
from dataclasses import dataclass, field
from typing import Any, Protocol

from openai import OpenAI


@dataclass(frozen=True)
class ModelSelection:
    provider: str
    model: str
    config: dict[str, Any] = field(default_factory=dict)
    provider_config: dict[str, Any] = field(default_factory=dict)

    @property
    def dimensions(self) -> int:
        return int(self.config.get("dimensions") or 0)


class EmbeddingClient(Protocol):
    async def embed_texts(self, selection: ModelSelection, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text."""


class OpenAIEmbeddingClient:
    """OpenAI-compatible embedding client used by production knowledge imports."""

    async def embed_texts(self, selection: ModelSelection, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        batch_size = max(1, int(selection.config.get("batch_size") or 64))
        vectors: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            vectors.extend(await asyncio.to_thread(self._embed_batch, selection, batch))
        return vectors

    def _embed_batch(self, selection: ModelSelection, texts: list[str]) -> list[list[float]]:
        client = OpenAI(
            api_key=str(selection.provider_config.get("api_key") or ""),
            base_url=selection.provider_config.get("base_url") or None,
        )
        params: dict[str, Any] = {
            "model": selection.model,
            "input": texts,
        }
        if selection.dimensions:
            params["dimensions"] = selection.dimensions
        encoding_format = str(selection.config.get("encoding_format") or "float").lower()
        if encoding_format in {"float", "base64"}:
            params["encoding_format"] = encoding_format
        response = client.embeddings.create(**params)
        return [_embedding_to_float_list(item.embedding) for item in response.data]


def resolve_model_selection(
    *,
    config: dict[str, Any],
    pool_key: str,
    provider: str | None,
    model: str | None,
    required: bool,
    missing_message: str,
) -> ModelSelection | None:
    entries = config.get(pool_key, [])
    if not isinstance(entries, list):
        entries = []

    requested_provider = (provider or "").strip()
    requested_model = (model or "").strip()
    if not requested_model:
        if not required:
            return None
        requested_provider = str(config.get(_current_provider_key(pool_key)) or "").strip()
        requested_model = str(config.get(_current_model_key(pool_key)) or "").strip()
        if not requested_model and entries:
            first = next((item for item in entries if isinstance(item, dict)), None)
            if first:
                requested_provider = str(first.get("provider") or "").strip()
                requested_model = str(first.get("model") or "").strip()

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("model") or "") != requested_model:
            continue
        if str(entry.get("provider") or "") != requested_provider:
            continue
        providers = config.get("providers", {})
        provider_config = (
            providers.get(requested_provider, {}) if isinstance(providers, dict) else {}
        )
        entry_config = entry.get("config")
        if not isinstance(entry_config, dict):
            entry_config = {}
        return ModelSelection(
            provider=requested_provider,
            model=requested_model,
            config=dict(entry_config),
            provider_config=dict(provider_config if isinstance(provider_config, dict) else {}),
        )

    if required:
        raise ValueError(missing_message)
    return None


def _current_model_key(pool_key: str) -> str:
    return "embedding_model" if pool_key == "active_embedding_models" else "rerank_model"


def _current_provider_key(pool_key: str) -> str:
    return "embedding_provider" if pool_key == "active_embedding_models" else "rerank_provider"


def _embedding_to_float_list(value: Any) -> list[float]:
    if isinstance(value, str):
        raw = base64.b64decode(value)
        return list(struct.unpack("<" + "f" * (len(raw) // 4), raw))
    return [float(item) for item in value]
