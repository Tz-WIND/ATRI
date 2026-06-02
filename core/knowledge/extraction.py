"""Graph tuple extraction and normalization for knowledge ingestion."""

from __future__ import annotations

import asyncio
import json
import re
from hashlib import sha256
from typing import Any, Protocol

from core.knowledge.graph_constants import CHAIN_ORDER_KEY_SEPARATOR, HYPER_ROLE_PREDICATE


class ChatLLM(Protocol):
    def chat(self, messages: list[dict], stream: bool = False):
        """Return an LLM response with a content attribute."""


REQUIRED_TUPLE_FIELDS = ("subject", "subject_type", "predicate", "object", "object_type")
HYPER_TUPLE_KEYS = ("hyper_tuples", "hyper_facts", "events")
MAX_HYPER_TUPLES = 2
MAX_HYPER_ROLES = 6
MAX_HYPER_CHAIN_EDGES = 5
ROLE_CHAIN_PREDICATES = {
    ("actor", "tool"): "uses",
    ("actor", "project"): "works_on",
    ("actor", "target"): "acts_on",
    ("tool", "project"): "used_in",
    ("tool", "purpose"): "supports",
    ("tool", "target"): "targets",
    ("project", "purpose"): "supports",
    ("project", "target"): "targets",
    ("error", "cause"): "failed_because",
    ("tool", "error"): "failed_with",
    ("system", "error"): "has_error",
    ("cause", "effect"): "causes",
    ("cause", "result"): "causes",
    ("file", "project"): "belongs_to",
}
AUTO_CHAIN_ROLE_ORDER = {
    "actor": 10,
    "cause": 20,
    "source": 30,
    "tool": 40,
    "system": 45,
    "file": 50,
    "library": 55,
    "project": 60,
    "target": 70,
    "purpose": 80,
    "effect": 90,
    "result": 100,
}
HYPER_FACT_FIELDS = (
    "chain_id",
    "chain_ids",
    "chain_order",
    "chain_order_keys",
    "chain_from_role",
    "chain_to_role",
    "hyper_event",
    "hyper_event_type",
    "hyper_role",
    "derived_from_hyper_tuple",
    "structural",
)
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
    raw_items = [
        *_tuple_items(payload),
        *_expand_hyper_tuples(payload, default_evidence=default_evidence),
    ]
    facts: list[dict] = []
    seen: set[str] = set()
    facts_by_key: dict[str, dict] = {}
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
            fact = facts_by_key[fact_key]
            _attach_hyper_fact_fields(fact, item)
            _merge_duplicate_fact_values(fact, item)
            continue
        seen.add(fact_key)
        fact = {
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
        _attach_hyper_fact_fields(fact, item)
        facts_by_key[fact_key] = fact
        facts.append(fact)
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
        "For multi-argument facts with three or more roles, also use hyper_tuples:",
        (
            '{"hyper_tuples":[{"event":"canonical event/fact name",'
            '"event_type":"Decision|Event|Process|Error|Other",'
            '"predicate":"main_relation",'
            '"roles":[{"role":"actor|tool|project|purpose|cause|target|other",'
            '"entity":"canonical entity name",'
            '"entity_type":"Person|Project|Tool|System|Library|File|Concept|Preference|Error|Other"}],'
            '"chain":[{"from_role":"actor","predicate":"uses","to_role":"tool"}],'
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
            "- Use hyper_tuples when one fact binds multiple roles in the same event; include "
            "a chain that preserves the useful retrieval path between roles."
        ),
        (
            "- If a hyper_tuple has no obvious chain, order roles from actor/cause/source "
            "toward tool/project/target/purpose/result."
        ),
        (
            f"- At most {MAX_HYPER_TUPLES} hyper_tuples; each hyper_tuple should have "
            f"at most {MAX_HYPER_ROLES} roles and at most "
            f"{MAX_HYPER_CHAIN_EDGES} chain edges."
        ),
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


def _expand_hyper_tuples(payload: Any, *, default_evidence: str) -> list[dict[str, Any]]:
    expanded: list[dict[str, Any]] = []
    for item in _hyper_tuple_items(payload)[:MAX_HYPER_TUPLES]:
        if not isinstance(item, dict):
            continue
        roles = _hyper_roles(item.get("roles"))[:MAX_HYPER_ROLES]
        if len(roles) < 2:
            continue
        event = _clean_text(item.get("event") or item.get("name"))
        event_type = _clean_text(item.get("event_type") or item.get("type")) or "Event"
        predicate = normalize_predicate(item.get("predicate")) or "related_to"
        evidence = _clean_text(item.get("evidence")) or default_evidence
        confidence = item.get("confidence")
        chain_id = _clean_text(item.get("chain_id") or item.get("hyper_id")) or _hyper_chain_id(
            event=event,
            event_type=event_type,
            predicate=predicate,
            roles=roles,
            evidence=evidence,
        )
        if not event:
            event = _event_name(roles, predicate)

        for role in roles:
            expanded.append(
                _hyper_fact_item(
                    subject=event,
                    subject_type=event_type,
                    predicate=HYPER_ROLE_PREDICATE,
                    obj=role["entity"],
                    object_type=role["entity_type"],
                    evidence=evidence,
                    confidence=confidence,
                    chain_id=chain_id,
                    hyper_event=event,
                    hyper_event_type=event_type,
                    hyper_role=role["role"],
                    structural=True,
                )
            )

        chain_edges = _explicit_chain_edges(item.get("chain"), roles)
        if not chain_edges:
            chain_edges = _auto_chain_edges(roles, predicate)
        chain_edges = chain_edges[:MAX_HYPER_CHAIN_EDGES]
        for edge in chain_edges:
            from_role, edge_predicate, to_role, order = edge
            expanded.append(
                _hyper_fact_item(
                    subject=from_role["entity"],
                    subject_type=from_role["entity_type"],
                    predicate=edge_predicate,
                    obj=to_role["entity"],
                    object_type=to_role["entity_type"],
                    evidence=evidence,
                    confidence=confidence,
                    chain_id=chain_id,
                    hyper_event=event,
                    hyper_event_type=event_type,
                    chain_order=order,
                    chain_from_role=from_role["role"],
                    chain_to_role=to_role["role"],
                )
            )
    return expanded


def _hyper_tuple_items(payload: Any) -> list[Any]:
    if not isinstance(payload, dict):
        return []
    items: list[Any] = []
    for key in HYPER_TUPLE_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            items.extend(value)
    return items


def _hyper_roles(raw_roles: Any) -> list[dict[str, str]]:
    if not isinstance(raw_roles, list):
        return []
    roles: list[dict[str, str]] = []
    for index, raw_role in enumerate(raw_roles, start=1):
        if not isinstance(raw_role, dict):
            continue
        role = normalize_predicate(raw_role.get("role")) or f"role_{index}"
        entity = _clean_text(
            raw_role.get("entity")
            or raw_role.get("name")
            or raw_role.get("value")
            or raw_role.get("object")
        )
        entity_type = _clean_text(raw_role.get("entity_type") or raw_role.get("type")) or "Other"
        if not role or not entity:
            continue
        roles.append({"role": role, "entity": entity, "entity_type": entity_type})
    return roles


def _explicit_chain_edges(
    raw_chain: Any,
    roles: list[dict[str, str]],
) -> list[tuple[dict[str, str], str, dict[str, str], int]]:
    if not isinstance(raw_chain, list):
        return []
    roles_by_key: dict[str, list[dict[str, str]]] = {}
    for role in roles:
        roles_by_key.setdefault(role["role"], []).append(role)
    edges: list[tuple[dict[str, str], str, dict[str, str], int]] = []
    for order, raw_edge in enumerate(raw_chain, start=1):
        if not isinstance(raw_edge, dict):
            continue
        from_role_key = normalize_predicate(raw_edge.get("from_role") or raw_edge.get("from"))
        to_role_key = normalize_predicate(raw_edge.get("to_role") or raw_edge.get("to"))
        predicate = normalize_predicate(raw_edge.get("predicate")) or _role_chain_predicate(
            from_role_key,
            to_role_key,
            "related_to",
        )
        if not predicate:
            continue
        from_roles = _matching_chain_roles(
            roles_by_key.get(from_role_key, []),
            raw_edge.get("from_entity") or raw_edge.get("from_name"),
        )
        to_roles = _matching_chain_roles(
            roles_by_key.get(to_role_key, []),
            raw_edge.get("to_entity") or raw_edge.get("to_name"),
        )
        for from_role in from_roles:
            for to_role in to_roles:
                if from_role is to_role:
                    continue
                edges.append((from_role, predicate, to_role, order))
    return edges


def _matching_chain_roles(
    roles: list[dict[str, str]],
    entity_filter: Any,
) -> list[dict[str, str]]:
    entity_key = normalize_entity_key(_clean_text(entity_filter))
    if not entity_key:
        return roles
    return [role for role in roles if normalize_entity_key(role["entity"]) == entity_key]


def _auto_chain_edges(
    roles: list[dict[str, str]],
    fallback_predicate: str,
) -> list[tuple[dict[str, str], str, dict[str, str], int]]:
    edges: list[tuple[dict[str, str], str, dict[str, str], int]] = []
    ordered_roles = _auto_chain_roles(roles)
    for index in range(len(ordered_roles) - 1):
        from_role = ordered_roles[index]
        to_role = ordered_roles[index + 1]
        predicate = _role_chain_predicate(
            from_role["role"],
            to_role["role"],
            fallback_predicate,
        )
        edges.append((from_role, predicate, to_role, index + 1))
    return edges


def _auto_chain_roles(roles: list[dict[str, str]]) -> list[dict[str, str]]:
    ordered = sorted(
        enumerate(roles),
        key=lambda indexed_role: (
            AUTO_CHAIN_ROLE_ORDER.get(indexed_role[1]["role"], 1000),
            indexed_role[0],
        ),
    )
    return [role for _, role in ordered]


def _role_chain_predicate(from_role: str, to_role: str, fallback_predicate: str) -> str:
    return (
        ROLE_CHAIN_PREDICATES.get((from_role, to_role))
        or normalize_predicate(fallback_predicate)
        or "related_to"
    )


def _hyper_fact_item(
    *,
    subject: str,
    subject_type: str,
    predicate: str,
    obj: str,
    object_type: str,
    evidence: str,
    confidence: Any,
    chain_id: str,
    hyper_event: str,
    hyper_event_type: str,
    chain_order: int | None = None,
    chain_from_role: str = "",
    chain_to_role: str = "",
    hyper_role: str = "",
    structural: bool = False,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "subject": subject,
        "subject_type": subject_type,
        "predicate": predicate,
        "object": obj,
        "object_type": object_type,
        "evidence": evidence,
        "confidence": confidence,
        "chain_id": chain_id,
        "hyper_event": hyper_event,
        "hyper_event_type": hyper_event_type,
        "derived_from_hyper_tuple": True,
    }
    if structural:
        item["structural"] = True
    if chain_order is not None:
        item["chain_order"] = chain_order
    if chain_from_role:
        item["chain_from_role"] = chain_from_role
    if chain_to_role:
        item["chain_to_role"] = chain_to_role
    if hyper_role:
        item["hyper_role"] = hyper_role
    return item


def _attach_hyper_fact_fields(fact: dict[str, Any], item: dict[str, Any]) -> None:
    chain_ids = _chain_ids(item)
    chain_order_keys = _chain_order_keys(item)
    for field in HYPER_FACT_FIELDS:
        if field in {"chain_ids", "chain_order", "chain_order_keys"}:
            continue
        value = item.get(field)
        if value is None or value == "":
            continue
        fact[field] = value
    if chain_ids:
        fact["chain_ids"] = _merge_unique([*fact.get("chain_ids", []), *chain_ids])
    if chain_order_keys:
        fact["chain_order_keys"] = _merge_unique(
            [*fact.get("chain_order_keys", []), *chain_order_keys]
        )
    _sync_scalar_chain_order(fact)


def _merge_unique(values: list[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = _clean_text(value)
        if text and text not in result:
            result.append(text)
    return result


def _chain_ids(item: dict[str, Any]) -> list[str]:
    values: list[Any] = []
    raw = item.get("chain_ids")
    if isinstance(raw, list):
        values.extend(raw)
    values.append(item.get("chain_id"))
    result: list[str] = []
    for value in values:
        text = _clean_text(value)
        if text and text not in result:
            result.append(text)
    return result


def _chain_order_keys(item: dict[str, Any]) -> list[str]:
    values: list[Any] = []
    raw = item.get("chain_order_keys")
    if isinstance(raw, list):
        values.extend(raw)
    chain_id = _clean_text(item.get("chain_id"))
    chain_order = _clean_text(item.get("chain_order"))
    if chain_id and chain_order:
        values.append(f"{chain_id}{CHAIN_ORDER_KEY_SEPARATOR}{chain_order}")
    return _merge_unique(values)


def _sync_scalar_chain_order(fact: dict[str, Any]) -> None:
    keys = fact.get("chain_order_keys")
    if not isinstance(keys, list) or len(keys) != 1:
        fact.pop("chain_order", None)
        return
    try:
        fact["chain_order"] = int(str(keys[0]).rsplit(CHAIN_ORDER_KEY_SEPARATOR, 1)[1])
    except (IndexError, ValueError):
        fact.pop("chain_order", None)


def _merge_duplicate_fact_values(fact: dict[str, Any], item: dict[str, Any]) -> None:
    evidence = _clean_text(item.get("evidence"))
    if item.get("derived_from_hyper_tuple") is True and evidence:
        fact["evidence"] = evidence
    elif not _clean_text(fact.get("evidence")) and evidence:
        fact["evidence"] = evidence
    if item.get("confidence") is not None and item.get("confidence") != "":
        fact["confidence"] = max(
            _confidence(fact.get("confidence")),
            _confidence(item.get("confidence")),
        )


def _hyper_chain_id(
    *,
    event: str,
    event_type: str,
    predicate: str,
    roles: list[dict[str, str]],
    evidence: str,
) -> str:
    payload = {
        "event": event,
        "event_type": event_type,
        "predicate": predicate,
        "roles": roles,
        "evidence": evidence,
    }
    digest = sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    return f"hyper:{digest.hexdigest()[:24]}"


def _event_name(roles: list[dict[str, str]], predicate: str) -> str:
    names = [role["entity"] for role in roles[:3]]
    return f"{' / '.join(names)} {predicate}".strip()


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
