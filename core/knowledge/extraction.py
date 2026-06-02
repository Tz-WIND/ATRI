"""Graph tuple extraction and normalization for knowledge ingestion."""

from __future__ import annotations

import asyncio
import json
import re
from hashlib import sha256
from typing import Any, Protocol


class ChatLLM(Protocol):
    def chat(self, messages: list[dict], stream: bool = False):
        """Return an LLM response with a content attribute."""


REQUIRED_TUPLE_FIELDS = ("subject", "subject_type", "predicate", "object", "object_type")
CHAT_METADATA_ENTITY_KEYS = {
    "chat",
    "chat log",
    "chat record",
    "conversation",
    "conversation log",
    "conversation record",
    "dialog",
    "dialogue",
    "message",
    "record",
    "transcript",
    "对话",
    "对话记录",
    "聊天",
    "聊天记录",
    "消息",
    "记录",
}
CHAT_ACTOR_ENTITY_KEYS = {
    "assistant",
    "bot",
    "human",
    "user",
    "用户",
    "助手",
}
CHAT_METADATA_PREDICATES = {
    "recorded_at",
    "recorded",
    "timestamp",
    "time",
    "created_at",
    "发生时间",
    "记录时间",
}
CHAT_ACTION_PREDICATES = {
    "ask",
    "asked",
    "asks",
    "mention",
    "mentioned",
    "mentions",
    "message",
    "reply",
    "replied",
    "replies",
    "request",
    "requested",
    "requests",
    "respond",
    "responded",
    "responds",
    "said",
    "say",
    "says",
    "sent",
    "tell",
    "told",
    "请求",
    "询问",
    "说",
    "回复",
}


class GraphTupleExtractor:
    """Extract subject/type/predicate/object/type facts from text with an LLM."""

    def __init__(self, llm_factory) -> None:
        self.llm_factory = llm_factory

    async def extract_facts(
        self,
        text: str,
        *,
        source_id: str,
        source_kind: str,
        metadata: dict[str, Any] | None = None,
    ) -> list[dict]:
        cleaned = str(text or "").strip()
        if not cleaned:
            return []
        llm = self.llm_factory()
        messages = [
            {
                "role": "system",
                "content": build_extraction_prompt(source_kind),
            },
            {"role": "user", "content": cleaned[:12000]},
        ]
        response = await asyncio.to_thread(lambda: llm.chat(messages, stream=False))
        content = getattr(response, "content", response)
        payload = parse_extraction_json(str(content or ""))
        return normalize_extracted_facts(
            payload,
            source_id=source_id,
            source_kind=source_kind,
            default_evidence=cleaned[:500],
            metadata=metadata,
        )


def parse_extraction_json(text: str) -> Any:
    """Parse raw model output, accepting fenced or surrounded JSON."""
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("empty graph extraction response")
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = min((idx for idx in (cleaned.find("{"), cleaned.find("[")) if idx >= 0), default=-1)
        end = max(cleaned.rfind("}"), cleaned.rfind("]"))
        if start < 0 or end <= start:
            raise
        return json.loads(cleaned[start : end + 1])


def normalize_extracted_facts(
    payload: Any,
    *,
    source_id: str,
    source_kind: str,
    default_evidence: str = "",
    metadata: dict[str, Any] | None = None,
) -> list[dict]:
    """Validate, normalize, and deduplicate extracted five-tuples."""
    raw_items = _tuple_items(payload)
    facts: list[dict] = []
    seen: set[str] = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        values = {field: _clean_text(item.get(field)) for field in REQUIRED_TUPLE_FIELDS}
        if any(not values[field] for field in REQUIRED_TUPLE_FIELDS):
            continue
        subject_key = normalize_entity_key(values["subject"])
        object_key = normalize_entity_key(values["object"])
        subject_type_key = normalize_entity_key(values["subject_type"])
        object_type_key = normalize_entity_key(values["object_type"])
        predicate_key = normalize_predicate(values["predicate"])
        if not subject_key or not object_key or not predicate_key:
            continue
        if _is_noise_tuple(
            subject_key=subject_key,
            object_key=object_key,
            predicate_key=predicate_key,
            source_kind=source_kind,
        ):
            continue
        fact_key = (
            f"{subject_type_key}:{subject_key}|{predicate_key}|{object_type_key}:{object_key}"
        )
        if fact_key in seen:
            continue
        seen.add(fact_key)
        facts.append(
            {
                "fact_key": fact_key,
                "subject": values["subject"],
                "subject_key": subject_key,
                "subject_type": values["subject_type"],
                "subject_type_key": subject_type_key,
                "predicate": predicate_key,
                "object": values["object"],
                "object_key": object_key,
                "object_type": values["object_type"],
                "object_type_key": object_type_key,
                "source_id": str(source_id or ""),
                "source_ids": _source_ids(source_id, metadata),
                "source_kind": str(source_kind or ""),
                "evidence": _clean_text(item.get("evidence")) or default_evidence,
                "confidence": _confidence(item.get("confidence")),
                "metadata": dict(metadata or {}),
            }
        )
    return facts


def build_extraction_prompt(source_kind: str) -> str:
    prompt = [
        "You are an information extraction engine for a long-term knowledge graph.",
        "",
        "Extract only durable, useful, explicitly supported facts from the text.",
        "Return ONLY valid JSON with this shape:",
        (
            '{"tuples":[{"subject":"canonical entity name",'
            '"subject_type":"Person|Project|Tool|System|Library|File|Concept|Preference|Error|Other",'
            '"predicate":"lower_snake_case_relation",'
            '"object":"canonical entity name or concise value",'
            '"object_type":"Person|Project|Tool|System|Library|File|Concept|Preference|Error|Other",'
            '"evidence":"short quote or close paraphrase from the text",'
            '"confidence":0.0}]}'
        ),
        "",
        "Rules:",
        "- Extract facts, not conversation mechanics.",
        (
            "- Do NOT extract tuples like user asked/requested/said unless the content is a "
            "stable preference, decision, constraint, or reusable project fact."
        ),
        "- Do NOT invent facts not directly supported by the text.",
        (
            "- Prefer specific technical facts: tools, errors, causes, configs, file paths, "
            "decisions, dependencies."
        ),
        (
            '- Use stable canonical entity names. Avoid vague subjects like "user", "this", '
            '"it", or "message" unless unavoidable.'
        ),
        (
            "- Use concise lower_snake_case predicates, e.g. uses, depends_on, "
            "configured_with, failed_because, prefers, located_at."
        ),
        "- Merge duplicates. Keep the most specific version.",
        (
            "- Skip timestamps, chat metadata, IDs, and numeric-only entities unless they are "
            "essential."
        ),
        '- If no useful durable facts exist, return {"tuples":[]}.',
        "- Limit output to the 12 most useful tuples.",
    ]
    if str(source_kind or "").lower() == "chat":
        prompt.extend(
            [
                "",
                "For chat text:",
                (
                    "- Keep stable user preferences, project decisions, recurring errors, "
                    "tool behavior, and environment facts."
                ),
                "- Skip one-off requests, greetings, task wording, and emotional filler.",
                "- Example skip: User -[requested]-> screenshot.",
                "- Example keep: ATRI screenshot tool -[failed_because]-> permission_denied.",
            ]
        )
    return "\n".join(prompt)


def normalize_entity_key(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def normalize_predicate(value: str) -> str:
    cleaned = "_".join(str(value or "").strip().lower().split())
    cleaned = re.sub(r"[^a-z0-9_\-\u4e00-\u9fff]+", "_", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned


def fallback_fact_key(*parts: str) -> str:
    return sha256("|".join(parts).encode("utf-8")).hexdigest()


def _tuple_items(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("tuples", "facts", "items", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _confidence(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 1.0
    return max(0.0, min(1.0, parsed))


def _source_ids(source_id: str, metadata: dict[str, Any] | None) -> list[str]:
    values: list[Any] = []
    if isinstance(metadata, dict):
        for key in ("source_ids", "chunk_ids"):
            raw = metadata.get(key)
            if isinstance(raw, list):
                values.extend(raw)
    values.append(source_id)
    result = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _is_noise_tuple(
    *,
    subject_key: str,
    object_key: str,
    predicate_key: str,
    source_kind: str,
) -> bool:
    if not _entity_key_has_letters(subject_key) or not _entity_key_has_letters(object_key):
        return True
    if str(source_kind or "").lower() != "chat":
        return False
    if predicate_key in CHAT_ACTION_PREDICATES and (
        subject_key in CHAT_ACTOR_ENTITY_KEYS or object_key in CHAT_ACTOR_ENTITY_KEYS
    ):
        return True
    if subject_key in CHAT_METADATA_ENTITY_KEYS or object_key in CHAT_METADATA_ENTITY_KEYS:
        return True
    return predicate_key in CHAT_METADATA_PREDICATES and (
        subject_key in CHAT_METADATA_ENTITY_KEYS or object_key in CHAT_METADATA_ENTITY_KEYS
    )


def _entity_key_has_letters(value: str) -> bool:
    return any(char.isalpha() for char in str(value or ""))
