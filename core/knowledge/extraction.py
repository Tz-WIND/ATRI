"""Graph tuple extraction and normalization for knowledge ingestion."""

from __future__ import annotations

import asyncio
import json
import re
from hashlib import sha256
from typing import Any, Protocol

from core import logger
from core.agent.context import estimate_tokens
from core.knowledge.graph_constants import (
    ASSISTANT_CANONICAL_NAME,
    ASSISTANT_CANONICAL_TYPE,
    ASSISTANT_ENTITY_ALIAS_KEYS,
    CHAIN_ORDER_KEY_SEPARATOR,
    GRAPH_EXTRACTION_INPUT_MAX_CHARS,
    HYPER_ROLE_PREDICATE,
)


class ChatLLM(Protocol):
    def chat(self, messages: list[dict], stream: bool = False):
        """Return an LLM response with a content attribute."""


REQUIRED_TUPLE_FIELDS = ("subject", "subject_type", "predicate", "object", "object_type")
HYPER_TUPLE_KEYS = ("hyper_tuples", "hyper_facts", "events")
MAX_HYPER_TUPLES = 4
MAX_HYPER_ROLES = 6
MAX_HYPER_CHAIN_EDGES = 5
MAX_EXTRACTION_TUPLES = 32
EXISTING_GRAPH_CONTEXT_METADATA_KEY = "existing_graph_context"
REFERENCE_DATE_METADATA_KEY = "reference_date"
_TYPED_GRAPH_FACT_RE = re.compile(
    r"^(?P<subject>.+?) \((?P<subject_type>[A-Za-z][A-Za-z0-9_ ]*)\) "
    r"-\[[^\]]+\]-> "
    r"(?P<object>.+?) \((?P<object_type>[A-Za-z][A-Za-z0-9_ ]*)\)"
    r"(?:\s+\(.*\))?$"
)
_GENERIC_ENTITY_TYPE_KEYS = {"", "concept", "entity", "item", "object", "other", "thing", "unknown"}
_COMPATIBLE_ENTITY_TYPE_GROUPS = (
    frozenset({"system", "component", "service", "platform", "application", "app"}),
    frozenset({"organization", "company", "team", "department"}),
    frozenset({"tool", "library", "framework"}),
    frozenset({"event", "incident", "alert", "error"}),
)
_TIME_OF_DAY_ONLY_RE = re.compile(
    r"^(?:"
    r"今天上午|今天下午|今天晚上|今天早上|今早|今晚|昨夜|昨天|明天|今天|"
    r"上午|下午|晚上|早上|中午|凌晨|傍晚|早晨|夜间|"
    r"this\s+morning|this\s+afternoon|this\s+evening|tonight|yesterday|tomorrow|today|"
    r"morning|afternoon|evening|noon|midnight|night"
    r")$",
    re.IGNORECASE,
)
_CONCRETE_DATE_RE = re.compile(
    r"(?:"
    r"\b(?:19|20)\d{2}\b|"
    r"\d{4}年|"
    r"\d{4}-\d{2}-\d{2}|"
    r"\d{4}/\d{1,2}/\d{1,2}|"
    r"\d{1,2}月\d{1,2}日|"
    r"\b(?:january|february|march|april|may|june|july|august|september|october|"
    r"november|december)\s+\d{1,2}(?:,\s*\d{4})?\b|"
    r"\b(?:jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\s+\d{1,2}(?:,\s*\d{4})?\b|"
    r"q[1-4]\s*(?:19|20)\d{2}|"
    r"(?:19|20)\d{2}\s*q[1-4]"
    r")",
    re.IGNORECASE,
)
EXTRACTION_ANCHOR_CONTEXT_HEADER = "[已有图谱上下文]"
EXTRACTION_ANCHOR_CONTEXT_GUIDANCE = (
    "以下是当前图数据库中按本段文本检索到的相关子图。"
    "你的任务是把本段待抽取文本中的新事实挂接到这些已有节点与关系上，"
    "而不是另起一套近义实体名。\n"
    "操作顺序：\n"
    "1. 先阅读已有事实，记下其中的 Subject/Object 实体名与常用 predicate。\n"
    "2. 再阅读待抽取文本，识别其中实体；能与已有行匹配的，输出时必须复用已有实体名的原文拼写"
    "（语言、大小写、空格一致）。\n"
    "3. 只输出本段文本明确支持的新边；已有边不要重复；完成实体对齐后再写入 tuples/hyper_tuples。\n"
    "注意：已有事实仅用于对齐与挂接，不得当作本段文本的证据来源；"
    "evidence 必须引用下方待抽取文本。"
)
ROLE_CHAIN_PREDICATES = {
    ("actor", "tool"): "uses",
    ("actor", "model"): "uses",
    ("actor", "config"): "configures",
    ("actor", "environment"): "uses",
    ("actor", "project"): "works_on",
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
    ("actor", "target"): "transferred_to",
    ("source", "target"): "transferred_to",
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
    "occurred_at": "occurred_at",
    "happened_at": "occurred_at",
    "happened_on": "occurred_at",
    "occurs_at": "occurred_at",
    "发生于": "occurred_at",
    "发生在": "occurred_at",
    "发生时间": "occurred_at",
    "事发时间": "occurred_at",
    "事发于": "occurred_at",
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
    "holds": "holds",
    "held": "holds",
    "持有": "holds",
    "拥有": "holds",
    "在手里": "holds",
    "purchased": "purchased",
    "bought": "purchased",
    "购买": "purchased",
    "买过": "purchased",
    "transferred_to": "transferred_to",
    "transferred": "transferred_to",
    "handed_to": "transferred_to",
    "转交": "transferred_to",
    "交给": "transferred_to",
    "pawned_at": "pawned_at",
    "pawned": "pawned_at",
    "sold_at": "pawned_at",
    "典当": "pawned_at",
    "变卖": "pawned_at",
    "can_access": "can_access",
    "accesses": "can_access",
    "进出": "can_access",
    "可进出": "can_access",
    "involves_item": "involves_item",
    "involves_object": "involves_item",
    "涉及物品": "involves_item",
    "涉及对象": "involves_item",
    "involves_stolen_item": "involves_stolen_item",
    "stolen_item": "involves_stolen_item",
    "失窃物品": "involves_stolen_item",
    "entry_method": "entry_method",
    "入室方式": "entry_method",
    "作案方式": "entry_method",
    "can_cut": "can_cut",
    "cuts": "can_cut",
    "切开": "can_cut",
    "可切开": "can_cut",
    "可切": "can_cut",
    "involves_person": "involves_person",
    "involves_people": "involves_person",
    "mentions_person": "involves_person",
    "涉及人物": "involves_person",
    "涉案人员": "involves_person",
    "相关人员": "involves_person",
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
        existing_graph_context: str = "",
    ) -> list[dict]:
        cleaned = str(text or "").strip()
        if not cleaned:
            return []
        llm = self.llm_factory()
        prompt_metadata = dict(metadata or {})
        context_text = str(
            existing_graph_context or prompt_metadata.get(EXISTING_GRAPH_CONTEXT_METADATA_KEY) or ""
        ).strip()
        if context_text:
            prompt_metadata[EXISTING_GRAPH_CONTEXT_METADATA_KEY] = context_text
        user_content = _build_segmented_user_content(cleaned, prompt_metadata)
        messages = [
            {
                "role": "system",
                "content": build_extraction_prompt(source_kind),
            },
            {"role": "user", "content": user_content},
        ]
        response = await asyncio.to_thread(lambda: llm.chat(messages, stream=False))
        prompt_tokens = _response_int_attr(response, "prompt_tokens")
        completion_tokens = _response_int_attr(response, "completion_tokens")
        logger.info(
            "Graph extraction token usage: source_kind=%s source_id=%s input_chars=%s "
            "estimated_prompt_tokens=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s",
            source_kind,
            source_id,
            len(user_content),
            estimate_tokens(messages),
            prompt_tokens,
            completion_tokens,
            prompt_tokens + completion_tokens,
        )
        content = _extraction_response_text(response)
        payload = parse_extraction_json(content)
        entity_type_hints = _entity_type_hints_from_graph_context(context_text)
        return normalize_extracted_facts(
            payload,
            source_id=source_id,
            source_kind=source_kind,
            default_evidence=cleaned[:500],
            metadata=_fact_metadata(metadata),
            entity_type_hints=entity_type_hints,
        )


def _response_int_attr(response: Any, name: str) -> int:
    try:
        return int(getattr(response, name, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _extraction_response_text(response: Any) -> str:
    content = str(getattr(response, "content", response) or "").strip()
    if content:
        return content
    reasoning_content = str(getattr(response, "reasoning_content", "") or "").strip()
    if reasoning_content:
        return reasoning_content
    return ""


def _fact_metadata(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(metadata, dict):
        return metadata
    cleaned = dict(metadata)
    cleaned.pop(EXISTING_GRAPH_CONTEXT_METADATA_KEY, None)
    return cleaned


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
    entity_type_hints: dict[str, str] | None = None,
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
        subject_type = _entity_type_from_hint(subject, subject_type, entity_type_hints)
        obj, object_type = _canonical_entity(
            raw_values["object"],
            raw_values["object_type"],
        )
        object_type = _entity_type_from_hint(obj, object_type, entity_type_hints)
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
        if predicate_key == "occurred_at" and not _has_concrete_datetime(values["object"]):
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


def _build_segmented_user_content(text: str, metadata: dict[str, Any] | None) -> str:
    prefixes: list[str] = []
    if isinstance(metadata, dict):
        try:
            part_count = int(metadata.get("text_part_count") or 0)
            part_index = int(metadata.get("text_part_index") or 0)
        except (TypeError, ValueError):
            part_count = 0
            part_index = 0
        if part_count > 1 and part_index > 0:
            prefixes.append(
                f"[文本分段 {part_index}/{part_count}] "
                "原文因过长被分成多段；仅抽取本段文本中明确支持的事实。"
                "段首段尾可能与相邻段重叠，仅用于保留上下文。"
            )
        reference_date = str(metadata.get(REFERENCE_DATE_METADATA_KEY) or "").strip()
        if reference_date:
            prefixes.append(
                f"[参考日期] {reference_date}\n"
                "仅用于本段 chat 对话：把用户口中的「今天/今天下午/今晚/早上」换算为具体日期。"
                "不要用于 document/叙述性文本；那些文本的日期须从文内语境自行查找。"
            )
        existing_context = str(metadata.get(EXISTING_GRAPH_CONTEXT_METADATA_KEY) or "").strip()
        if existing_context:
            fact_lines = _fact_lines_from_graph_context(existing_context)
            if fact_lines:
                prefixes.append(
                    f"{EXTRACTION_ANCHOR_CONTEXT_HEADER}\n"
                    f"{EXTRACTION_ANCHOR_CONTEXT_GUIDANCE}\n" + "\n".join(fact_lines)
                )
    body = text[:GRAPH_EXTRACTION_INPUT_MAX_CHARS]
    return "\n\n".join([*prefixes, body])


def build_extraction_prompt(source_kind: str) -> str:
    prompt = [
        "你是长期知识图谱（Neo4j Graph RAG）的信息抽取引擎。",
        "",
        "只抽取文本中明确支持、可长期复用的有用事实；不要推断、不要补全未写明的因果。",
        "只返回合法 JSON，结构如下（字段名保持英文）：",
        "不要输出 JSON 以外的说明或分析。",
        (
            '{"tuples":[{"subject":"原文实体名（不翻译）",'
            '"subject_type":"Person|Organization|Project|Tool|System|Component|Library|File|Event|Process|Concept|Preference|Error|Other",'
            '"predicate":"specific_lower_snake_case_relation",'
            '"object":"原文实体名或原文中的简洁取值（不翻译）",'
            '"object_type":"Person|Organization|Project|Tool|System|Component|Library|File|Event|Process|Concept|Preference|Error|Other",'
            '"evidence":"short quote or close paraphrase from the text",'
            '"confidence":0.0}]}'
        ),
        "",
        "若同一事实包含三个及以上角色，另用 hyper_tuples：",
        (
            '{"hyper_tuples":[{"event":"原文事件名（不翻译）",'
            '"event_type":"Decision|Event|Process|Error|Other",'
            '"predicate":"most_specific_main_relation",'
            '"roles":[{"role":"actor|source|provider|tool|model|version|environment|'
            "system|config|file|library|project|target|purpose|cause|error|effect|"
            'result|other",'
            '"entity":"原文实体名（不翻译）",'
            '"entity_type":"Person|Organization|Project|Tool|System|Component|Library|File|Event|Process|Concept|Preference|Error|Other"}],'
            '"chain":[{"from_role":"actor","predicate":"uses","to_role":"tool"},'
            '{"from_role":"tool","predicate":"configured_with","to_role":"config"}],'
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
            "- 若用户消息以 [文本分段 i/n] 开头，说明这是长文的一段；"
            "只根据本段内容抽取事实，不要假设未出现的上下文。"
        ),
        (
            "- 保留细节：一句里有多个人名、物品、地点、动作时，"
            "尽量拆成多条可检索的边，不要只压成一条事件摘要。"
        ),
        (
            "- 事件类内容（Event/Process：事故、会议、发布、部署、交易、调查等）："
            "事件本身与主要参与人员（Person/组织）应同为图谱中心——既要写事件属性，"
            "也要写参与者的动作边，并用事件↔参与者的连边把两侧连起来。"
        ),
        (
            "- 事件何时发生用 occurred_at 记录，object_type 用 Concept；"
            "occurred_at 的 object 必须能定位到具体日期，禁止只写「上午/下午/晚上/今早/今晚」"
            "这类无法单独断定日期的时段词。"
        ),
        (
            "- 文件、报告、日记、新闻、小说等叙述性文字里出现「今天/明天/昨天/早上/下午/晚上」"
            "是正常现象——它们是文内相对说法，不是让你原样写入知识库的 occurred_at。"
            "必须从同篇语境中定位该事件实际对应的日期：文首日期、段落时间线、"
            "前后文已写明的具体日期、章节标题、会议纪要时间栏等。"
        ),
        (
            "- 遇到「今天下午/今天晚上/今早/今晚/今天/早上」等相对时间时，"
            "不要直接把这几个词当作 occurred_at；先在全文或本段语境里找该事件锚定的具体日期，"
            "再换算为「具体日期+可选时段」。"
        ),
        (
            "- 仅在与 AI 的 chat 文本且提供 [参考日期] 时，才把该日期当作对话当天的「今天」锚点；"
            "例如 [参考日期] 2026-06-08 且用户说「今天下午开会」"
            "→ occurred_at 写「2026年6月8日下午」。"
            "document/叙述性文本不要使用 [参考日期]，即使出现也不要用它替代文内语境。"
        ),
        (
            "- 若只能确定年月或季度（如 2024年3月、Q3 2024）可写该粒度；"
            "若从语境中实在找不到任何可锚定的日期信息，则不要写 occurred_at 边。"
        ),
        (
            "- occurred_at 仅用于叙事中事件本身的发生时间，不用于对话记录时间、"
            "消息时间戳或系统元数据。"
        ),
        (
            "- 同一事件只用 ONE 个 canonical 事件名，不要用近义重复实体"
            "（如「X」与「X案」「X事件」）；该名称为本篇的事件 hub。"
        ),
        (
            "- 禁止用子步骤/子动作短语另建 Event 实体；分步流程用 hyper_tuples 的 chain 表达，"
            "且所有相关 hyper 的 event 字段必须填同一 canonical 事件名，不要用子动作当 event 名。"
        ),
        (
            "- 主要参与人员：除以其为 subject 的动作边外，"
            "为每位写 {事件} -[involves_person]-> {Person}（evidence 引用原文；"
            "「主要」指文中明确承担动作/职责/出现频率高的人，不推断未写明的关系）。"
        ),
        (
            "- 技术事实与事件事实同等重要：Tool/Model/Error/config 要写；"
            "事件、Person、Tool、地点、组织等也要写。"
        ),
        (
            "- 故障/异常/告警/运维台账类记录（标题或正文含 异常、故障、告警、失效、宕机、"
            "中断、报错 等）必须同时建立两层结构："
            "① 每条具体记录一个 canonical 事件 hub（用原文标题或最贴近原文的事件名）；"
            "② 主动挂到稳定的分类中心 Concept（从文本语义归纳，如 故障事件、异常事件、"
            "服务中断；更细可用 数据库异常、网络故障 等；"
            "不要因为正文没写出分类名就省略这层）。"
        ),
        (
            "- 分类中心引申规则：允许从事件名称、告警词、错误码、症状语义归纳分类中心；"
            "例如「硬盘掉线」「I/O error」「SMART 告警」可归到「硬件故障」或「存储故障」，"
            "「接口 5xx 告警」可归到「服务异常」或「异常事件」。"
            "若已有图谱里已有匹配的分类中心，优先复用其原文 canonical 写法；"
            "不得补写原文未支持的根因、责任人、修复结果、影响范围或发生时间。"
        ),
        (
            "- 标题或文件名以 YYYYMMDD 开头时：事件 hub 用完整标题；occurred_at 从日期前缀"
            "换算为具体日期；并抽取标题/正文中的涉事 System 或组件实体。"
        ),
        (
            "- 具体异常事件除 occurred_at 外，至少写："
            "{事件} -[belongs_to]-> {分类中心}；"
            "{事件} -[involves_system]-> {涉事系统/组件}。"
            "若名称体现从属关系，可再写 {子组件} -[part_of]-> {上级系统/平台}。"
        ),
        (
            "- 事件信息尽量抽全：在 tuple 上限内保留症状、错误码、告警级别、原因/根因、"
            "影响对象、处理动作、恢复状态、修复方式、责任组织、上下游组件等。"
            "这些事实必须有原文证据；不确定时只写分类中心，不写具体因果或结论。"
        ),
        (
            "- 多条同类记录不要只留孤立事件节点；应通过共同的分类中心与共同上级系统/平台"
            "形成可串联子图，便于按类别和时间检索。"
        ),
        "",
        "实体命名（语言与 canonical）：",
        (
            "- subject/object/event/roles.entity 必须使用原文中的实体表面形式，"
            "严格保持原文语言与写法；禁止自行翻译、音译、罗马化或添加英文括号注释。"
        ),
        (
            "- 中文人名/项目/地点/组织写中文（如 林晚、星尘计划、新加坡）；"
            "英文专有名词写英文（如 Neo4j、Ableton Live）；"
            "日文/韩文等亦保持原文脚本，不要改成英文等价词。"
        ),
        (
            "- canonical 指同一文档/会话内拼写一致，不是把实体统一译成英文；"
            "同一实体只用一种写法，但不得改变语言。"
        ),
        ("- entity_type 仍用英文 schema 值；predicate 仍用 lower_snake_case 英文关系名。"),
        ("- 使用稳定的 canonical 实体名；避免模糊主语 user/this/it/message，除非无法避免。"),
        ("- 助手统一用实体名 ATRI；不要另建 Assistant、Bot、助手 等实体。"),
        "",
        "已有图谱挂接（当用户提供 [已有图谱上下文] 时）：",
        (
            "- 先把待抽取文本中的实体与已有图谱行做对齐：同一人/项目/工具/事件/地点，"
            "必须使用已有行里完全相同的实体名，不要因简称、别名、翻译或近义说法另起新节点。"
        ),
        (
            "- 若新文本用简称、代词、英文缩写或近义说法指代已有实体，"
            "输出时改回已有图谱中的 canonical 名；"
            "例如已有「林晚」，新文本写「晚姐」仍输出「林晚」。"
        ),
        (
            "- 若已有上下文标出实体类型（如 订单系统 (System)），"
            "同一实体必须同时复用已有 name 与 type，"
            "不要只复用 name 后改成 Component/Concept/Other。"
            "但同名异物可以保留不同 type：只有当上下文明确是同一个实体时才复用类型。"
        ),
        (
            "- 对已有关系补充新信息：保留相同 subject 与 object，可新增不同 predicate 的边；"
            "若语义与已有边等价则复用已有 predicate，不要为同一关系发明近义谓词。"
        ),
        (
            "- 若新文本更新/修正已有关系（如换工作、更换工具、迁移项目），"
            "仍挂到同一 subject 实体写新边，evidence 引用本段文本；"
            "不要创建「张三」与「张先生」这类近义并存节点，除非原文明确是不同人。"
        ),
        (
            "- 新事实应尽量从已有节点向外延伸：若已有 A-[r1]->B，新文本说 B 与 C 有关，"
            "优先写 B-[r2]->C，而不是生成与已有子图断开的孤立节点。"
        ),
        (
            "- 事件续写：若已有图谱中已有 canonical 事件名，同一事件的新步骤、参与者、"
            "时间/地点属性必须继续挂到该事件名；不要另建「X」「X案」「X事件」等近义 Event。"
        ),
        ("- 仅当待抽取文本明确引入全新实体，或已有图谱中确实无对应节点时，才创建新实体名。"),
        ("- 不要把已有图谱中的事实重复输出；只输出本段文本新支持、且完成挂接后的边。"),
        "",
        "关系细化（predicate 选择）：",
        (
            "- 优先最具体、可检索、方向明确的关系；"
            "禁止滥用 related_to、associated_with、has、connected_to、involves 等泛化谓词，"
            "除非原文确实无法区分更细关系。"
        ),
        (
            "- 按原文动词/因果/从属选 predicate："
            "使用→uses；依赖→depends_on；配置→configured_with；"
            "失败原因→failed_because；根因→caused_by；导致→causes；修复→fixed_by；"
            "偏好→prefers；回避→avoids；要求→requires；约束→constrained_by；"
            "位于→located_at；发生于/发生时间→occurred_at（事件何时发生）；"
            "属于/归类→belongs_to（具体事件挂到故障/异常等分类中心）；"
            "涉事系统→involves_system；组件归属→part_of；"
            "症状/表现→has_symptom；错误码→has_error_code；告警级别→has_severity；"
            "影响对象/范围→affects；处理动作→handled_by；恢复状态→has_recovery_status；"
            "负责/参与项目→works_on；"
            "支持用途→supports；产出→produces；版本→has_version；运行于→runs_on；"
            "转交→transferred_to；涉及人员→involves_person；涉及物品→involves_item。"
        ),
        (
            "- 一句含多个动作/对象时，拆成多条不同 predicate 的边，"
            "不要合并成一条 related_to 或单个 Event 摘要。"
        ),
        (
            "- hyper_tuple 的 chain 中每条边必须写独立、具体的 predicate，"
            "不得让所有 chain 边复用同一个 main_relation 或 related_to。"
        ),
        (
            "- 能区分因果/配置/用途/归属时，不要降级为 uses 或 related_to；"
            "例：「因权限不足失败」用 failed_because，不用 causes；"
            "「把 temperature 设为 0.7」用 configured_with，不用 uses。"
        ),
        (
            "- 也可用其它贴合原文语义的 lower_snake_case predicate；"
            "新 predicate 仍须比 related_to 更具体。"
        ),
        (
            "- 合并重复项，保留最具体版本；evidence 用原文短引或贴近原文的 "
            "paraphrase，带上关键动词与时间/阶段状语。"
        ),
        (
            "- 面向检索的连边：文本把 Person 与 Tool/Project/地点/另一 Person 绑在一起时，"
            "必须输出 Person 为 subject 的边，不能只有 Event/Concept->Object。"
        ),
        ("- 把原文中的活动词、人名、物名写入 evidence，便于后续关键词检索。"),
        (
            "- 有 Person/Project/Tool 时，避免孤立 Concept；"
            "若文本说明了谁对某物执行动作，不要只写 Event/Concept 属性而缺少 Person 边。"
        ),
        ("- 说话人自报姓名（如 我叫林晚）时，用该 Person 名；不要用 User、用户 等泛称。"),
        (
            "- 多步骤流程：写多条 hyper_tuples，每条 event 均为同一 canonical 事件名，"
            "用不同 chain 区分步骤；每条写全 chain（如 actor->tool、actor->target），"
            "便于从事件节点与参与人节点双向检索。"
        ),
        (
            "- Person、Project、Tool 同句出现时，优先 hyper_tuple 链 "
            "actor->tool->target（以及 actor->tool、actor->project），避免断开的 tuples。"
        ),
        (
            "- hyper_tuple 无明显 chain 时，按 actor/cause/source → "
            "provider/tool/model/config/file/library/project/target/purpose/result 排序。"
        ),
        (
            f"- hyper_tuples 最多 {MAX_HYPER_TUPLES} 条；每条最多 {MAX_HYPER_ROLES} 个 role、"
            f"最多 {MAX_HYPER_CHAIN_EDGES} 条 chain 边。"
        ),
        (
            "- 跳过聊天元数据时间戳、消息 ID、纯数字实体（除非必不可少）；"
            "不要跳过原文叙述中的事件发生时间——后者用 occurred_at 记在事件节点上。"
        ),
        '- 若无有用事实，返回 {"tuples":[]}。',
        f"- 最多输出 {MAX_EXTRACTION_TUPLES} 条 tuples（含 hyper 展开前的条目）；"
        "事件类文本优先完整覆盖事件属性、分类中心、涉事系统/组件、症状、原因、影响、处理动作、"
        "恢复状态、involves_person 与参与者动作，避免只留少量摘要边。",
        "",
        "故障/异常类结构示例（仅示连边模式，实体名必须来自原文，勿照抄占位符）：",
        "- {具体事件标题} -[occurred_at]-> {具体日期} (evidence: …)",
        "- {具体事件标题} -[belongs_to]-> {分类中心} (evidence: …)",
        "- {具体事件标题} -[involves_system]-> {涉事系统或组件} (evidence: …)",
        "- {子组件} -[part_of]-> {上级系统或平台} (evidence: …，仅当原文/命名体现从属时)",
        "- {具体事件标题} -[has_symptom]-> {原文症状/告警表现} (evidence: …，原文有症状时)",
        "- {具体事件标题} -[handled_by]-> {原文处理动作} (evidence: …，原文有处理动作时)",
        "- {具体事件标题} -[has_recovery_status]-> {原文恢复状态} (evidence: …，原文有恢复状态时)",
        "",
        "事件类双中心示例（占位名，勿照抄未出现的实体；实体名保持原文语言）：",
        "- 星尘计划发布 -[occurred_at]-> 2024年3月15日下午 (evidence: …)",
        "- 星尘计划发布 -[located_at]-> 新加坡 (evidence: …)",
        "- 星尘计划发布 -[involves_person]-> 林晚 (evidence: 林晚主持 …)",
        "- 林晚 -[works_on]-> 星尘计划发布 (evidence: …)",
        "- Q3 Product Launch -[occurred_at]-> March 2024 (evidence: …)",
        "- Q3 Product Launch -[located_at]-> Singapore (evidence: …)",
        "- Q3 Product Launch -[involves_person]-> Alice (evidence: Alice led …)",
        "- Alice -[works_on]-> Q3 Product Launch (evidence: …)",
        (
            "- hyper_tuple：event 均为 星尘计划发布；roles=林晚(actor)、GitHub Actions(tool)、"
            "main 分支(config)；"
            "chain: actor-[uses]->tool, tool-[configured_with]->config；"
            "另可写 测试环境(environment) 等分步 chain，每步 predicate 各自具体。"
            "勿另建「林晚 配置 CI」类 Event 节点。"
        ),
    ]
    source = str(source_kind or "").lower()
    if source == "chat":
        prompt.extend(
            [
                "",
                "针对 chat 文本：",
                (
                    "- 聊天里用户说「今天下午/今晚/早上」时，以 [参考日期] 作为对话当天锚点，"
                    "换算成含具体日期的 occurred_at；"
                    "禁止把「上午/下午/晚上」等模糊时段单独写入知识库。"
                ),
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
    elif source == "document":
        prompt.extend(
            [
                "",
                "针对 document 文本：",
                (
                    "- 运维台账、告警记录、故障通报、日期前缀标题（YYYYMMDD …）的文件："
                    "按故障/异常类规则建具体事件 hub，并主动挂到分类中心 Concept，"
                    "不要只写 System-Error 边或只留一条孤立摘要。"
                ),
                (
                    "- 文档/叙述性文本里「今天/早上/下午」只是文内说法，"
                    "常见于新闻、日记、纪要、小说；"
                    "不要照抄进 occurred_at，也不要用抽取时的系统日期替代。"
                ),
                (
                    "- 须从文档语境找日期：文件标题/页眉日期、同段或相邻段写明的年月日、"
                    "时间线叙述（如「3月15日……当天早上……」）、会议纪要时间栏、章节时间标记等；"
                    "找到后再写 occurred_at；语境中找不到就不要写时间。"
                ),
                (
                    "- 长段落按句拆事实；同一文档内实体名保持同一 canonical 拼写，"
                    "且始终保留原文语言，不得跨语言统一。"
                ),
                "- 事件叙述与工程技术事实同等优先；事件与主要参与人应形成可互通的子图。",
                (
                    "- 不要把整段只压缩成一个 Event 节点加少量属性边；"
                    "子步骤 hyper 的 event 必须与该段 canonical 事件名相同。"
                ),
            ]
        )
    return "\n".join(prompt)


def _has_concrete_datetime(value: str) -> bool:
    cleaned = _clean_text(value)
    if not cleaned:
        return False
    if _TIME_OF_DAY_ONLY_RE.fullmatch(cleaned):
        return False
    return _CONCRETE_DATE_RE.search(cleaned) is not None


def _fact_lines_from_graph_context(context: str) -> list[str]:
    lines: list[str] = []
    for raw_line in str(context or "").splitlines():
        line = raw_line.strip()
        if line.startswith("- "):
            lines.append(line)
    return lines


def _entity_type_hints_from_graph_context(context: str) -> dict[str, str]:
    candidates: dict[str, set[str]] = {}
    display_types: dict[tuple[str, str], str] = {}
    for line in _fact_lines_from_graph_context(context):
        for segment in _typed_fact_segments(line):
            match = _TYPED_GRAPH_FACT_RE.match(segment)
            if not match:
                continue
            for name_key, type_key, display_type in (
                (
                    normalize_entity_key(match.group("subject")),
                    normalize_entity_key(match.group("subject_type")),
                    _clean_text(match.group("subject_type")),
                ),
                (
                    normalize_entity_key(match.group("object")),
                    normalize_entity_key(match.group("object_type")),
                    _clean_text(match.group("object_type")),
                ),
            ):
                if not name_key or not type_key:
                    continue
                candidates.setdefault(name_key, set()).add(type_key)
                display_types.setdefault((name_key, type_key), display_type)
    hints: dict[str, str] = {}
    for name_key, type_keys in candidates.items():
        if len(type_keys) == 1:
            type_key = next(iter(type_keys))
            hints[name_key] = display_types[(name_key, type_key)]
    return hints


def _typed_fact_segments(line: str) -> list[str]:
    cleaned = str(line or "").strip()
    if cleaned.startswith("- "):
        cleaned = cleaned[2:].strip()
    parts = [part.strip() for part in cleaned.split(" | linked: ")]
    segments: list[str] = []
    for part in parts:
        segments.append(re.sub(r"^\[\d+-hop\]\s+", "", part).strip())
    return segments


def _entity_type_from_hint(
    entity_name: str,
    current_type: str,
    entity_type_hints: dict[str, str] | None,
) -> str:
    if not entity_type_hints:
        return current_type
    hinted_type = entity_type_hints.get(normalize_entity_key(entity_name))
    if not hinted_type:
        return current_type
    current_type_key = normalize_entity_key(current_type)
    hinted_type_key = normalize_entity_key(hinted_type)
    if (
        current_type_key in _GENERIC_ENTITY_TYPE_KEYS
        or current_type_key == hinted_type_key
        or _entity_type_keys_compatible(current_type_key, hinted_type_key)
    ):
        return hinted_type
    return current_type


def _entity_type_keys_compatible(current_type_key: str, hinted_type_key: str) -> bool:
    if not current_type_key or not hinted_type_key:
        return False
    return any(
        current_type_key in group and hinted_type_key in group
        for group in _COMPATIBLE_ENTITY_TYPE_GROUPS
    )


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
