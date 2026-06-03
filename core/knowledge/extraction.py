"""Graph tuple extraction and normalization for knowledge ingestion."""

from __future__ import annotations

import asyncio
import json
import re
from hashlib import sha256
from typing import Any, Protocol

from core.knowledge.graph_constants import (
    ASSISTANT_CANONICAL_NAME,
    ASSISTANT_CANONICAL_TYPE,
    ASSISTANT_ENTITY_ALIAS_KEYS,
    CHAIN_ORDER_KEY_SEPARATOR,
    HYPER_ROLE_PREDICATE,
)


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
    ("actor", "model"): "uses",
    ("actor", "config"): "configures",
    ("actor", "environment"): "uses",
    ("actor", "project"): "works_on",
    ("actor", "target"): "acts_on",
    ("provider", "model"): "provides",
    ("model", "version"): "has_version",
    ("model", "config"): "configured_with",
    ("model", "project"): "used_in",
    ("model", "purpose"): "supports",
    ("model", "target"): "targets",
    ("tool", "project"): "used_in",
    ("tool", "purpose"): "supports",
    ("tool", "target"): "targets",
    ("tool", "config"): "configured_with",
    ("environment", "tool"): "runs",
    ("environment", "system"): "hosts",
    ("system", "config"): "configured_with",
    ("config", "project"): "configured_for",
    ("config", "purpose"): "supports",
    ("config", "target"): "targets",
    ("config", "result"): "produces",
    ("project", "purpose"): "supports",
    ("project", "target"): "targets",
    ("project", "result"): "produces",
    ("error", "cause"): "failed_because",
    ("cause", "error"): "causes",
    ("tool", "error"): "failed_with",
    ("system", "error"): "has_error",
    ("file", "config"): "defines",
    ("cause", "effect"): "causes",
    ("cause", "result"): "causes",
    ("effect", "result"): "produces",
    ("file", "project"): "belongs_to",
    ("library", "project"): "used_in",
    ("library", "version"): "has_version",
}
AUTO_CHAIN_ROLE_ORDER = {
    "actor": 10,
    "cause": 20,
    "source": 30,
    "provider": 35,
    "tool": 40,
    "model": 42,
    "version": 43,
    "environment": 44,
    "system": 45,
    "config": 47,
    "file": 50,
    "library": 55,
    "project": 60,
    "target": 70,
    "purpose": 80,
    "effect": 90,
    "result": 100,
}
PREDICATE_ALIASES = {
    "prefer": "prefers",
    "prefers": "prefers",
    "like": "prefers",
    "likes": "prefers",
    "喜欢": "prefers",
    "偏好": "prefers",
    "喜好": "prefers",
    "倾向": "prefers",
    "avoid": "avoids",
    "avoids": "avoids",
    "dislike": "avoids",
    "dislikes": "avoids",
    "不喜欢": "avoids",
    "避免": "avoids",
    "不要": "avoids",
    "讨厌": "avoids",
    "require": "requires",
    "requires": "requires",
    "need": "requires",
    "needs": "requires",
    "需要": "requires",
    "必须": "requires",
    "要求": "requires",
    "constrained_by": "constrained_by",
    "limited_by": "constrained_by",
    "限制": "constrained_by",
    "约束": "constrained_by",
    "边界": "constrained_by",
    "禁忌": "constrained_by",
    "受限于": "constrained_by",
    "has_trait": "has_trait",
    "trait": "has_trait",
    "特点": "has_trait",
    "特征": "has_trait",
    "性格": "has_trait",
    "气质": "has_trait",
    "口头禅": "has_trait",
    "has_identity": "has_identity",
    "identity": "has_identity",
    "身份": "has_identity",
    "has_style": "has_style",
    "style": "has_style",
    "风格": "has_style",
    "语气": "has_style",
    "表达方式": "has_style",
    "use": "uses",
    "uses": "uses",
    "using": "uses",
    "使用": "uses",
    "采用": "uses",
    "用": "uses",
    "depends": "depends_on",
    "depends_on": "depends_on",
    "dependent_on": "depends_on",
    "依赖": "depends_on",
    "依赖于": "depends_on",
    "configured_with": "configured_with",
    "configured": "configured_with",
    "set_to": "configured_with",
    "配置": "configured_with",
    "配置为": "configured_with",
    "设置": "configured_with",
    "设置为": "configured_with",
    "参数为": "configured_with",
    "located_at": "located_at",
    "path": "located_at",
    "路径": "located_at",
    "位于": "located_at",
    "所在位置": "located_at",
    "failed_because": "failed_because",
    "failure_reason": "failed_because",
    "error_reason": "failed_because",
    "失败原因": "failed_because",
    "报错原因": "failed_because",
    "错误原因": "failed_because",
    "caused_by": "caused_by",
    "原因是": "caused_by",
    "由导致": "caused_by",
    "causes": "causes",
    "cause": "causes",
    "导致": "causes",
    "造成": "causes",
    "fixed_by": "fixed_by",
    "fix": "fixed_by",
    "fixes": "fixed_by",
    "解决方案": "fixed_by",
    "修复方式": "fixed_by",
    "修复": "fixed_by",
    "supports": "supports",
    "support": "supports",
    "支持": "supports",
    "用于": "supports",
    "works_on": "works_on",
    "负责": "works_on",
    "工作于": "works_on",
    "belongs_to": "belongs_to",
    "属于": "belongs_to",
    "produces": "produces",
    "produce": "produces",
    "输出": "produces",
    "产生": "produces",
    "结果是": "produces",
    "has_version": "has_version",
    "version": "has_version",
    "版本": "has_version",
    "版本为": "has_version",
    "provided_by": "provided_by",
    "由提供": "provided_by",
    "服务商": "provided_by",
    "runs_on": "runs_on",
    "run_on": "runs_on",
    "运行于": "runs_on",
    "运行环境": "runs_on",
}
ROLE_ALIASES = {
    "actor": "actor",
    "执行者": "actor",
    "操作者": "actor",
    "用户": "actor",
    "使用者": "actor",
    "主体": "actor",
    "source": "source",
    "来源": "source",
    "provider": "provider",
    "供应商": "provider",
    "服务商": "provider",
    "提供方": "provider",
    "tool": "tool",
    "工具": "tool",
    "model": "model",
    "模型": "model",
    "version": "version",
    "版本": "version",
    "environment": "environment",
    "环境": "environment",
    "运行环境": "environment",
    "system": "system",
    "系统": "system",
    "config": "config",
    "配置": "config",
    "参数": "config",
    "设置": "config",
    "file": "file",
    "文件": "file",
    "路径": "file",
    "library": "library",
    "库": "library",
    "依赖": "library",
    "依赖库": "library",
    "project": "project",
    "项目": "project",
    "target": "target",
    "目标": "target",
    "对象": "target",
    "purpose": "purpose",
    "目的": "purpose",
    "用途": "purpose",
    "cause": "cause",
    "原因": "cause",
    "error": "error",
    "错误": "error",
    "报错": "error",
    "异常": "error",
    "effect": "effect",
    "影响": "effect",
    "效果": "effect",
    "result": "result",
    "结果": "result",
    "输出": "result",
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
        content = _extraction_response_text(response)
        payload = parse_extraction_json(content)
        return normalize_extracted_facts(
            payload,
            source_id=source_id,
            source_kind=source_kind,
            default_evidence=cleaned[:500],
            metadata=metadata,
        )


def _extraction_response_text(response: Any) -> str:
    content = str(getattr(response, "content", response) or "").strip()
    if content:
        return content
    reasoning_content = str(getattr(response, "reasoning_content", "") or "").strip()
    if reasoning_content:
        return reasoning_content
    return ""


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
    except json.JSONDecodeError as e:
        embedded = _parse_embedded_json(cleaned)
        if embedded is not None:
            return embedded
        preview = cleaned[:240].replace("\n", "\\n")
        raise ValueError(
            f"invalid graph extraction JSON response: {e.msg}; preview={preview!r}"
        ) from e


def _parse_embedded_json(text: str) -> Any:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"[\{\[]", text):
        try:
            payload, _ = decoder.raw_decode(text, match.start())
        except json.JSONDecodeError:
            continue
        if isinstance(payload, (dict, list)):
            return payload
    return None


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
        raw_values = {field: _clean_text(item.get(field)) for field in REQUIRED_TUPLE_FIELDS}
        if any(not raw_values[field] for field in REQUIRED_TUPLE_FIELDS):
            continue
        raw_subject_key = normalize_entity_key(raw_values["subject"])
        raw_object_key = normalize_entity_key(raw_values["object"])
        predicate_key = normalize_predicate(raw_values["predicate"])
        if not raw_subject_key or not raw_object_key or not predicate_key:
            continue
        if _is_noise_tuple(
            subject_key=raw_subject_key,
            object_key=raw_object_key,
            predicate_key=predicate_key,
            source_kind=source_kind,
        ):
            continue
        subject, subject_type = _canonical_entity(
            raw_values["subject"],
            raw_values["subject_type"],
        )
        obj, object_type = _canonical_entity(
            raw_values["object"],
            raw_values["object_type"],
        )
        values = {
            **raw_values,
            "subject": subject,
            "subject_type": subject_type,
            "object": obj,
            "object_type": object_type,
        }
        subject_key = normalize_entity_key(values["subject"])
        object_key = normalize_entity_key(values["object"])
        subject_type_key = normalize_entity_key(values["subject_type"])
        object_type_key = normalize_entity_key(values["object_type"])
        if not subject_key or not object_key or not predicate_key:
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
        "你是长期知识图谱（Neo4j Graph RAG）的信息抽取引擎。",
        "",
        "只抽取文本中明确支持、可长期复用的有用事实。",
        "只返回合法 JSON，结构如下（字段名保持英文）：",
        "不要输出 JSON 以外的说明或分析。",
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
        "若同一事实包含三个及以上角色，另用 hyper_tuples：",
        (
            '{"hyper_tuples":[{"event":"canonical event/fact name",'
            '"event_type":"Decision|Event|Process|Error|Other",'
            '"predicate":"main_relation",'
            '"roles":[{"role":"actor|source|provider|tool|model|version|environment|'
            "system|config|file|library|project|target|purpose|cause|error|effect|"
            'result|other",'
            '"entity":"canonical entity name",'
            '"entity_type":"Person|Project|Tool|System|Library|File|Concept|Preference|Error|Other"}],'
            '"chain":[{"from_role":"actor","predicate":"uses","to_role":"tool"}],'
            '"evidence":"short quote or close paraphrase from the text",'
            '"confidence":0.0}]}'
        ),
        "",
        "规则：",
        "- 抽取事实，不要抽取对话动作本身。",
        (
            "- 不要抽取 user asked/requested/said 类 tuples，除非是稳定的偏好、"
            "决策、约束或可复用的 Project 事实。"
        ),
        "- 不要编造文本未直接支持的事实。",
        (
            "- 优先具体技术事实：Tool、Model、Provider、版本、Error、原因、"
            "config、文件路径、决策、依赖。"
        ),
        ("- 使用稳定的 canonical 实体名；避免模糊主语 user/this/it/message，除非无法避免。"),
        ("- 助手统一用实体名 ATRI；不要另建 Assistant、Bot、助手 等实体。"),
        (
            "- predicate 尽量用 lower_snake_case 英文：uses, depends_on, "
            "configured_with, failed_because, caused_by, causes, fixed_by, prefers, "
            "avoids, requires, constrained_by, has_trait, has_identity, has_style, "
            "located_at, belongs_to, supports, works_on, produces, has_version, runs_on。"
        ),
        "- 合并重复项，保留最具体版本。",
        (
            "- 面向检索的连边：文本把 Person 与 Tool/Project 绑在一起时，"
            "必须输出 Person-[uses/works_on]->Tool/Project，不能只有 Project->Tool。"
        ),
        ("- 把原文中的活动词写入 evidence（如 配乐、剪辑、podcast editing），便于后续查询匹配。"),
        (
            "- 有 Person/Project/Tool 时，避免孤立 Concept（如 Music scoring、配乐工作）；"
            "若文本说明了谁在用 Tool，不要把 Tool 只挂在 Project 上。"
        ),
        ("- 说话人自报姓名（如 我叫林晚）时，用该 Person 名；不要用 User、用户 等泛称。"),
        ("- 同一事件绑定多角色时用 hyper_tuples，并写 chain 保留可检索路径。"),
        (
            "- Person、Project、Tool 同句出现时，优先 hyper_tuple 链 "
            "actor->project->tool（以及 actor->tool），避免断开的 tuples。"
        ),
        (
            "- hyper_tuple 无明显 chain 时，按 actor/cause/source → "
            "provider/tool/model/config/file/library/project/target/purpose/result 排序。"
        ),
        (
            f"- hyper_tuples 最多 {MAX_HYPER_TUPLES} 条；每条最多 {MAX_HYPER_ROLES} 个 role、"
            f"最多 {MAX_HYPER_CHAIN_EDGES} 条 chain 边。"
        ),
        "- 跳过时间戳、聊天元数据、ID、纯数字实体（除非必不可少）。",
        '- 若无有用事实，返回 {"tuples":[]}。',
        "- 最多输出 12 条最有用的 tuples。",
        "- Tool/Model/Library/File 等专有名词保持英文原文（如 Ableton Live、Neo4j、config.yaml）。",
    ]
    if str(source_kind or "").lower() == "chat":
        prompt.extend(
            [
                "",
                "针对 chat 文本：",
                ("- 保留稳定用户偏好、Project 决策、反复出现的 Error、Tool 行为与环境事实。"),
                (
                    "- 若聊天写明某人用某软件做某活动，必须抽 Person-[uses]->Tool，"
                    "evidence 写明活动；即使同一 Tool 也挂在 Project 上。"
                ),
                ("- 示例保留：林晚 -[uses]-> Ableton Live (evidence: 林晚用 Ableton 做配乐)。"),
                "- 跳过一次性请求、问候、任务措辞与情绪填充。",
                "- 示例跳过：User -[requested]-> screenshot。",
                "- 示例保留：ATRI screenshot tool -[failed_because]-> permission_denied。",
            ]
        )
    return "\n".join(prompt)


def normalize_entity_key(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def normalize_predicate(value: object | None) -> str:
    cleaned = _normalize_relation_token(value)
    return PREDICATE_ALIASES.get(cleaned, cleaned)


def normalize_role(value: object | None) -> str:
    cleaned = _normalize_relation_token(value)
    return ROLE_ALIASES.get(cleaned, cleaned)


def _normalize_relation_token(value: object | None) -> str:
    cleaned = "_".join(str(value or "").strip().lower().split())
    cleaned = re.sub(r"[^a-z0-9_\-\u4e00-\u9fff]+", "_", cleaned)
    return re.sub(r"_+", "_", cleaned).strip("_")


def _canonical_entity(name: str, entity_type: str) -> tuple[str, str]:
    if normalize_entity_key(name) in ASSISTANT_ENTITY_ALIAS_KEYS:
        return ASSISTANT_CANONICAL_NAME, ASSISTANT_CANONICAL_TYPE
    return name, entity_type


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
        role = normalize_role(raw_role.get("role")) or f"role_{index}"
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
        from_role_key = normalize_role(raw_edge.get("from_role") or raw_edge.get("from"))
        to_role_key = normalize_role(raw_edge.get("to_role") or raw_edge.get("to"))
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
