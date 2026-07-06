import asyncio
import json
import logging
import re
import threading
from pathlib import Path
from typing import Any, cast

import pytest

from core.knowledge.extraction import (
    EXISTING_GRAPH_CONTEXT_METADATA_KEY,
    MAX_EXTRACTION_TUPLES,
    MAX_HYPER_CHAIN_EDGES,
    MAX_HYPER_ROLES,
    MAX_HYPER_TUPLES,
    REFERENCE_DATE_METADATA_KEY,
    GraphTupleExtractor,
    _build_segmented_user_content,
    _fact_lines_from_graph_context,
    _has_concrete_datetime,
    build_extraction_prompt,
    normalize_extracted_facts,
    parse_extraction_json,
)
from core.knowledge.graph import Neo4jGraphClient, _query_terms
from core.knowledge.graph_constants import (
    CHAIN_ORDER_KEY_SEPARATOR,
    GRAPH_EXTRACTION_BATCH_CHARS,
    format_graph_context,
)
from core.knowledge.graph_worker import (
    GraphKnowledgeManager,
    _chat_turn_text,
    _document_batch_text,
    _document_extraction_batches,
    _extraction_text_segments,
    _graph_config_from_app_config,
    _plain_text_extraction_batches,
)
from core.runtime.tasks import TaskStore


def test_graph_module_split_keeps_compatibility_imports():
    from core.knowledge.graph import Neo4jGraphClient as CompatNeo4jGraphClient
    from core.knowledge.graph import _query_terms as compat_query_terms
    from core.knowledge.graph_cache import GraphRetrievalCache
    from core.knowledge.graph_format import _format_retrieved_fact_lines
    from core.knowledge.graph_query import _query_terms as split_query_terms
    from core.knowledge.graph_values import _retrieval_depth

    assert CompatNeo4jGraphClient is Neo4jGraphClient
    assert compat_query_terms is split_query_terms
    assert GraphRetrievalCache(ttl_seconds=0).get("x", "y") is None
    assert _retrieval_depth(99) == 7
    assert _format_retrieved_fact_lines([], depth=1, limit=3) == []


def test_default_multihop_expansion_cache_preload_seed_limit_is_64():
    from core.knowledge.graph_values import (
        _multi_hop_expansion_cache_path_limit,
        _multi_hop_expansion_cache_preload_path_limit,
        _multi_hop_expansion_cache_preload_seed_limit,
    )

    assert _multi_hop_expansion_cache_path_limit(None) == 1000
    assert _multi_hop_expansion_cache_path_limit(2000) == 2000
    assert _multi_hop_expansion_cache_path_limit(50000) == 10000
    assert _multi_hop_expansion_cache_preload_seed_limit(None) == 64
    assert _multi_hop_expansion_cache_preload_seed_limit(999) == 999
    assert _multi_hop_expansion_cache_preload_seed_limit(4096) == 2048
    assert _multi_hop_expansion_cache_preload_path_limit(None) == 200
    assert _multi_hop_expansion_cache_preload_path_limit(800) == 800
    assert _multi_hop_expansion_cache_preload_path_limit(100000) == 50000


@pytest.mark.parametrize("value", [False, "false", "0", "no", "off", ""])
def test_legacy_persistent_multihop_cache_false_values_map_to_memory(value):
    worker_config = _graph_config_from_app_config(
        {"knowledge": {"graph": {"persistent_multi_hop_expansion_cache_enabled": value}}}
    )
    client = Neo4jGraphClient({"persistent_multi_hop_expansion_cache_enabled": value})

    assert worker_config["multi_hop_expansion_cache_mode"] == "memory"
    assert client._multi_hop_expansion_cache_mode() == "memory"


def test_graph_worker_config_includes_multihop_cache_preload_seed_limit():
    worker_config = _graph_config_from_app_config(
        {
            "knowledge": {
                "graph": {
                    "multi_hop_expansion_cache_preload_seed_limit": "512",
                    "multi_hop_expansion_cache_path_limit": "2000",
                    "multi_hop_expansion_cache_preload_path_limit": "800",
                }
            }
        }
    )
    clamped_config = _graph_config_from_app_config(
        {
            "knowledge": {
                "graph": {
                    "multi_hop_expansion_cache_preload_seed_limit": "4096",
                    "multi_hop_expansion_cache_path_limit": "50000",
                    "multi_hop_expansion_cache_preload_path_limit": "4096",
                }
            }
        }
    )

    assert worker_config["multi_hop_expansion_cache_preload_seed_limit"] == 512
    assert worker_config["multi_hop_expansion_cache_path_limit"] == 2000
    assert worker_config["multi_hop_expansion_cache_preload_path_limit"] == 800
    assert clamped_config["multi_hop_expansion_cache_preload_seed_limit"] == 2048
    assert clamped_config["multi_hop_expansion_cache_path_limit"] == 10000
    assert clamped_config["multi_hop_expansion_cache_preload_path_limit"] == 4096


def test_build_extraction_prompt_documents_segmented_input():
    prompt = build_extraction_prompt("document")

    assert "[文本分段 i/n]" in prompt


def test_build_extraction_prompt_handles_noisy_input_text():
    prompt = build_extraction_prompt("document")

    assert "错别字" in prompt
    assert "语音识别/转写错误" in prompt
    assert "手误打错" in prompt
    assert "evidence 保留原文表述" in prompt
    assert "不要强行猜测" in prompt


def test_build_extraction_prompt_prefers_person_tool_links_for_chat():
    prompt = build_extraction_prompt("chat")

    assert "Person 为 subject 的边" in prompt
    assert "不要用 User、用户" in prompt
    assert "林晚 -[uses]-> Ableton Live" in prompt
    assert "孤立 Concept" in prompt
    assert "严格保持原文语言" in prompt
    assert "禁止自行翻译" in prompt
    assert "禁止滥用 related_to" in prompt
    assert "事件类内容" in prompt
    assert "双中心" in prompt or "同为图谱中心" in prompt


def test_build_extraction_prompt_event_dual_hub_for_documents():
    prompt = build_extraction_prompt("document")

    assert "针对 document 文本" in prompt
    assert "不要把整段只压缩成一个 Event" in prompt
    assert "事件本身与主要参与人员" in prompt
    assert "同一事件只用 ONE 个 canonical 事件名" in prompt
    assert "事件 hub" in prompt
    assert "禁止用子步骤" in prompt or "禁止用子动作" in prompt
    assert "involves_person" in prompt
    assert "hyper 的 event 字段" in prompt
    assert "Q3 Product Launch" in prompt
    assert "勿另建" in prompt
    assert f"最多输出 {MAX_EXTRACTION_TUPLES} 条 tuples" in prompt


def test_build_extraction_prompt_records_event_occurrence_time():
    prompt = build_extraction_prompt("document")

    assert "occurred_at" in prompt
    assert "2024年3月15日下午" in prompt
    assert "禁止只写「上午/下午/晚上" in prompt
    assert "叙述性文字里出现「今天" in prompt
    assert "不要照抄进 occurred_at" in prompt
    assert "document/叙述性文本不要使用 [参考日期]" in prompt
    assert "不要写 occurred_at 边" in prompt
    assert "不要跳过原文叙述中的事件发生时间" in prompt
    assert "不用于对话记录时间" in prompt


def test_build_extraction_prompt_chat_resolves_relative_time_with_reference_date():
    prompt = build_extraction_prompt("chat")

    assert "[参考日期]" in prompt
    assert "今天下午" in prompt
    assert "禁止把「上午/下午/晚上」" in prompt


def test_build_extraction_prompt_document_resolves_relative_time_from_narrative_context():
    prompt = build_extraction_prompt("document")

    assert "针对 document 文本" in prompt
    assert "文内说法" in prompt
    assert "从文档语境找日期" in prompt
    assert "会议纪要时间栏" in prompt


def test_has_concrete_datetime_rejects_vague_time_only_values():
    assert _has_concrete_datetime("下午") is False
    assert _has_concrete_datetime("今天上午") is False
    assert _has_concrete_datetime("今天晚上") is False
    assert _has_concrete_datetime("2026年6月8日下午") is True
    assert _has_concrete_datetime("2026-06-08") is True
    assert _has_concrete_datetime("3月15日") is True


def test_normalize_extracted_facts_drops_occurred_at_without_concrete_datetime():
    facts = normalize_extracted_facts(
        {
            "tuples": [
                {
                    "subject": "项目评审",
                    "subject_type": "Event",
                    "predicate": "occurred_at",
                    "object": "今天下午",
                    "object_type": "Concept",
                    "evidence": "项目评审安排在今天下午。",
                },
                {
                    "subject": "项目评审",
                    "subject_type": "Event",
                    "predicate": "occurred_at",
                    "object": "2026年6月8日下午",
                    "object_type": "Concept",
                    "evidence": "项目评审安排在2026年6月8日下午。",
                },
            ]
        },
        source_id="chunk-time",
        source_kind="chat",
    )

    assert len(facts) == 1
    assert facts[0]["object"] == "2026年6月8日下午"


def test_build_extraction_prompt_links_incidents_to_category_hub():
    prompt = build_extraction_prompt("document")

    assert "故障/异常/告警" in prompt
    assert "分类中心" in prompt
    assert "{具体事件标题} -[belongs_to]-> {分类中心}" in prompt
    assert "involves_system" in prompt
    assert "YYYYMMDD" in prompt
    assert "预警平台" not in prompt


def test_build_extraction_prompt_infers_incident_category_without_inventing_facts():
    prompt = build_extraction_prompt("document")

    assert MAX_EXTRACTION_TUPLES >= 28
    assert "允许从事件名称、告警词、错误码、症状语义归纳分类中心" in prompt
    assert "硬盘掉线" in prompt
    assert "硬件故障" in prompt
    assert "已有图谱里已有匹配的分类中心" in prompt
    assert "优先复用其原文 canonical 写法" in prompt
    assert "不得补写原文未支持的根因、责任人、修复结果、影响范围或发生时间" in prompt
    assert "症状" in prompt
    assert "处理动作" in prompt
    assert "恢复状态" in prompt


def test_build_extraction_prompt_anchors_new_facts_to_existing_graph_nodes():
    prompt = build_extraction_prompt("document")

    assert "已有图谱挂接" in prompt
    assert "[已有图谱上下文]" in prompt
    assert "另起新节点" in prompt
    assert "从已有节点向外延伸" in prompt
    assert "不要把已有图谱中的事实重复输出" in prompt
    assert "若已有上下文标出实体类型" in prompt
    assert "同时复用已有 name 与 type" in prompt
    assert "同名异物" in prompt


def test_fact_lines_from_graph_context_keeps_only_fact_rows():
    wrapped = format_graph_context(
        [
            "- 林晚 -[works_on]-> 星尘计划",
            "- 星尘计划 -[uses]-> Neo4j",
        ]
    )

    assert _fact_lines_from_graph_context(wrapped) == [
        "- 林晚 -[works_on]-> 星尘计划",
        "- 星尘计划 -[uses]-> Neo4j",
    ]


def test_format_graph_context_includes_usage_guidance():
    context = format_graph_context(["- Alice -[works_at]-> Acme"])

    assert context.startswith("[Graph context]\n")
    assert "跨跳串联" in context
    assert "实体名保持原文语言" in context
    assert context.endswith("- Alice -[works_at]-> Acme")


def test_chain_order_separator_is_shared_and_parameterized_in_cypher():
    from core.knowledge.graph_constants import GRAPH_CYPHER_QUERY_TIMEOUT_SECONDS

    root = Path(__file__).resolve().parents[1]
    constants_path = root / "core" / "knowledge" / "graph_constants.py"
    extraction_source = (root / "core" / "knowledge" / "extraction.py").read_text(encoding="utf-8")
    graph_source = (root / "core" / "knowledge" / "graph.py").read_text(encoding="utf-8")

    assert constants_path.exists()
    constants_source = constants_path.read_text(encoding="utf-8")
    assert constants_source.count('CHAIN_ORDER_KEY_SEPARATOR = "::order::"') == 1
    assert 'CHAIN_ORDER_KEY_SEPARATOR = "::order::"' not in extraction_source
    assert '_CHAIN_ORDER_KEY_SEPARATOR = "::order::"' not in graph_source
    assert "'::order::'" not in graph_source
    assert "split(chain_order_keys[0], $chain_order_separator)" in graph_source
    assert "split(right_key, $chain_order_separator)" in graph_source
    assert GRAPH_CYPHER_QUERY_TIMEOUT_SECONDS >= 30
    assert "timeout=GRAPH_CYPHER_QUERY_TIMEOUT_SECONDS" in graph_source


def test_graph_enumeration_terms_are_shared_between_process_and_query_expansion():
    import core.knowledge.graph as graph_module
    import core.pipeline.stages.process as process_stage_module
    from core.knowledge.graph_constants import GRAPH_QUERY_ENUMERATION_TERMS

    terms = tuple(GRAPH_QUERY_ENUMERATION_TERMS)

    assert len(terms) >= 10
    assert process_stage_module.GRAPH_QUERY_ENUMERATION_TERMS == terms
    assert graph_module.GRAPH_QUERY_ENUMERATION_TERMS == terms
    assert all(process_stage_module._is_enumeration_graph_query(f"请{term}项目") for term in terms)
    assert all("count" in _query_terms(f"请{term}项目") for term in terms)


def test_normalize_extracted_facts_filters_and_deduplicates_graph_tuples():
    facts = normalize_extracted_facts(
        {
            "tuples": [
                {
                    "subject": " Alice ",
                    "subject_type": "Person",
                    "predicate": " works_at ",
                    "object": " Acme ",
                    "object_type": "Company",
                    "confidence": "0.9",
                    "evidence": "Alice works at Acme.",
                },
                {
                    "subject": "alice",
                    "subject_type": "Person",
                    "predicate": "works_at",
                    "object": "acme",
                    "object_type": "Company",
                },
                {
                    "subject": "",
                    "subject_type": "Person",
                    "predicate": "knows",
                    "object": "Bob",
                    "object_type": "Person",
                },
            ]
        },
        source_id="chunk-1",
        source_kind="document",
        default_evidence="fallback evidence",
    )

    assert len(facts) == 1
    assert facts[0]["subject"] == "Alice"
    assert facts[0]["subject_key"] == "alice"
    assert facts[0]["subject_type"] == "Person"
    assert facts[0]["subject_type_key"] == "person"
    assert facts[0]["predicate"] == "works_at"
    assert facts[0]["object"] == "Acme"
    assert facts[0]["object_key"] == "acme"
    assert facts[0]["object_type"] == "Company"
    assert facts[0]["object_type_key"] == "company"
    assert facts[0]["source_id"] == "chunk-1"
    assert facts[0]["source_kind"] == "document"
    assert facts[0]["confidence"] == 0.9
    assert facts[0]["evidence"] == "Alice works at Acme."
    assert facts[0]["fact_key"] == "person:alice|works_at|company:acme"


def test_normalize_extracted_facts_filters_chat_metadata_and_numeric_entities():
    facts = normalize_extracted_facts(
        {
            "tuples": [
                {
                    "subject": "对话记录",
                    "subject_type": "Record",
                    "predicate": "recorded_at",
                    "object": "178027 8275.00",
                    "object_type": "Timestamp",
                },
                {
                    "subject": "Alice",
                    "subject_type": "Person",
                    "predicate": "works_at",
                    "object": "Acme",
                    "object_type": "Company",
                },
            ]
        },
        source_id="chat-task-1",
        source_kind="chat",
        default_evidence="User: Alice works at Acme.",
    )

    assert len(facts) == 1
    assert facts[0]["subject"] == "Alice"
    assert facts[0]["object"] == "Acme"


def test_normalize_extracted_facts_filters_chat_request_actions():
    facts = normalize_extracted_facts(
        {
            "tuples": [
                {
                    "subject": "User",
                    "subject_type": "Person",
                    "predicate": "requested",
                    "object": "screenshot",
                    "object_type": "Task",
                },
                {
                    "subject": "ATRI screenshot tool",
                    "subject_type": "Tool",
                    "predicate": "failed_because",
                    "object": "permission denied",
                    "object_type": "Error",
                    "evidence": "screenshot failed because permission denied",
                    "confidence": 0.8,
                },
            ]
        },
        source_id="chat-task-1",
        source_kind="chat",
        default_evidence="User asked why screenshot failed.",
    )

    assert len(facts) == 1
    assert facts[0]["subject"] == "ATRI screenshot tool"
    assert facts[0]["predicate"] == "failed_because"


def test_normalize_extracted_facts_canonicalizes_predicate_aliases():
    facts = normalize_extracted_facts(
        {
            "tuples": [
                {
                    "subject": "ATRI",
                    "subject_type": "Project",
                    "predicate": "依赖于",
                    "object": "Neo4j",
                    "object_type": "Tool",
                },
                {
                    "subject": "User",
                    "subject_type": "Person",
                    "predicate": "喜欢",
                    "object": "concise answers",
                    "object_type": "Preference",
                },
                {
                    "subject": "screenshot tool",
                    "subject_type": "Tool",
                    "predicate": "报错原因",
                    "object": "permission denied",
                    "object_type": "Error",
                },
                {
                    "subject": "config.yaml",
                    "subject_type": "File",
                    "predicate": "位于",
                    "object": "workspace root",
                    "object_type": "Concept",
                },
                {
                    "subject": "星尘计划发布",
                    "subject_type": "Event",
                    "predicate": "发生于",
                    "object": "2024年3月15日",
                    "object_type": "Concept",
                },
            ]
        },
        source_id="chunk-predicate-aliases",
        source_kind="document",
    )

    predicates_by_subject = {fact["subject"]: fact["predicate"] for fact in facts}

    assert predicates_by_subject["ATRI"] == "depends_on"
    assert predicates_by_subject["User"] == "prefers"
    assert predicates_by_subject["screenshot tool"] == "failed_because"
    assert predicates_by_subject["config.yaml"] == "located_at"
    assert predicates_by_subject["星尘计划发布"] == "occurred_at"


def test_normalize_extracted_facts_canonicalizes_assistant_aliases_to_atri():
    facts = normalize_extracted_facts(
        {
            "tuples": [
                {
                    "subject": "助手",
                    "subject_type": "Person",
                    "predicate": "can_help_with",
                    "object": "写代码",
                    "object_type": "Concept",
                },
                {
                    "subject": "ATRI",
                    "subject_type": "System",
                    "predicate": "can_help_with",
                    "object": "写代码",
                    "object_type": "Concept",
                },
                {
                    "subject": "User",
                    "subject_type": "Person",
                    "predicate": "uses",
                    "object": "Assistant",
                    "object_type": "Person",
                },
            ]
        },
        source_id="chat-aliases",
        source_kind="chat",
    )

    by_edge = {(fact["subject"], fact["predicate"], fact["object"]): fact for fact in facts}

    assert len(facts) == 2
    assert ("ATRI", "can_help_with", "写代码") in by_edge
    assert ("User", "uses", "ATRI") in by_edge
    assert by_edge[("ATRI", "can_help_with", "写代码")]["subject_type"] == "System"
    assert by_edge[("User", "uses", "ATRI")]["object_type"] == "System"
    assert all(fact["subject"] != "助手" for fact in facts)
    assert all(fact["object"] != "Assistant" for fact in facts)


def test_normalize_extracted_facts_filters_assistant_action_before_aliasing():
    facts = normalize_extracted_facts(
        [
            {
                "subject": "Assistant",
                "subject_type": "Person",
                "predicate": "replied",
                "object": "User",
                "object_type": "Person",
            }
        ],
        source_id="chat-noise-alias",
        source_kind="chat",
    )

    assert facts == []


def test_parse_extraction_json_skips_bracketed_non_json_preamble():
    payload = parse_extraction_json(
        "[analysis]\n"
        '{"tuples":[{"subject":"User","subject_type":"Person",'
        '"predicate":"commutes_by","object":"bike","object_type":"Preference"}]}'
    )

    assert payload["tuples"][0]["predicate"] == "commutes_by"


def test_normalize_extracted_facts_expands_hyper_tuples_into_event_and_chain_facts():
    facts = normalize_extracted_facts(
        {
            "hyper_tuples": [
                {
                    "event": "ATRI graph RAG Neo4j adoption",
                    "event_type": "Decision",
                    "predicate": "adopted_for",
                    "roles": [
                        {"role": "actor", "entity": "Alice", "entity_type": "Person"},
                        {"role": "tool", "entity": "Neo4j", "entity_type": "Tool"},
                        {"role": "project", "entity": "ATRI", "entity_type": "Project"},
                        {"role": "purpose", "entity": "Graph RAG", "entity_type": "Concept"},
                    ],
                    "chain": [
                        {"from_role": "actor", "predicate": "uses", "to_role": "tool"},
                        {"from_role": "tool", "predicate": "used_in", "to_role": "project"},
                        {
                            "from_role": "project",
                            "predicate": "supports",
                            "to_role": "purpose",
                        },
                    ],
                    "evidence": "Alice uses Neo4j in ATRI for Graph RAG.",
                    "confidence": 0.9,
                }
            ]
        },
        source_id="chunk-hyper-1",
        source_kind="document",
    )

    by_edge = {(fact["subject"], fact["predicate"], fact["object"]): fact for fact in facts}
    chain_ids = {fact.get("chain_id") for fact in facts}

    assert len(chain_ids) == 1
    assert None not in chain_ids
    assert by_edge[("ATRI graph RAG Neo4j adoption", "has_role", "Alice")]["hyper_role"] == "actor"
    assert by_edge[("ATRI graph RAG Neo4j adoption", "has_role", "Alice")]["structural"] is True
    assert by_edge[("ATRI graph RAG Neo4j adoption", "has_role", "Neo4j")]["hyper_role"] == "tool"
    assert by_edge[("ATRI graph RAG Neo4j adoption", "has_role", "ATRI")]["hyper_role"] == "project"
    assert (
        by_edge[("ATRI graph RAG Neo4j adoption", "has_role", "Graph RAG")]["hyper_role"]
        == "purpose"
    )
    assert by_edge[("Alice", "uses", "Neo4j")]["chain_order"] == 1
    assert by_edge[("Alice", "uses", "Neo4j")].get("structural") is not True
    assert by_edge[("Neo4j", "used_in", "ATRI")]["chain_order"] == 2
    assert by_edge[("ATRI", "supports", "Graph RAG")]["chain_order"] == 3
    assert all(fact["hyper_event"] == "ATRI graph RAG Neo4j adoption" for fact in facts)
    assert all(fact["derived_from_hyper_tuple"] is True for fact in facts)


def test_normalize_extracted_facts_auto_chains_hyper_roles_when_chain_is_missing():
    facts = normalize_extracted_facts(
        {
            "hyper_tuples": [
                {
                    "event": "ATRI graph RAG Neo4j adoption",
                    "event_type": "Decision",
                    "predicate": "adopted_for",
                    "roles": [
                        {"role": "actor", "entity": "Alice", "entity_type": "Person"},
                        {"role": "tool", "entity": "Neo4j", "entity_type": "Tool"},
                        {"role": "project", "entity": "ATRI", "entity_type": "Project"},
                    ],
                }
            ]
        },
        source_id="chunk-hyper-2",
        source_kind="document",
        default_evidence="Alice adopted Neo4j for ATRI.",
    )

    by_edge = {(fact["subject"], fact["predicate"], fact["object"]): fact for fact in facts}

    assert by_edge[("Alice", "uses", "Neo4j")]["chain_order"] == 1
    assert by_edge[("Neo4j", "used_in", "ATRI")]["chain_order"] == 2
    assert by_edge[("Alice", "uses", "Neo4j")]["evidence"] == "Alice adopted Neo4j for ATRI."


def test_normalize_extracted_facts_auto_chains_roles_by_semantic_order():
    facts = normalize_extracted_facts(
        {
            "hyper_tuples": [
                {
                    "event": "ATRI graph RAG Neo4j adoption",
                    "event_type": "Decision",
                    "roles": [
                        {"role": "purpose", "entity": "Graph RAG", "entity_type": "Concept"},
                        {"role": "project", "entity": "ATRI", "entity_type": "Project"},
                        {"role": "tool", "entity": "Neo4j", "entity_type": "Tool"},
                        {"role": "actor", "entity": "Alice", "entity_type": "Person"},
                    ],
                }
            ]
        },
        source_id="chunk-hyper-sorted",
        source_kind="document",
    )

    by_edge = {(fact["subject"], fact["predicate"], fact["object"]): fact for fact in facts}

    assert ("Graph RAG", "related_to", "ATRI") not in by_edge
    assert by_edge[("Alice", "uses", "Neo4j")]["chain_order"] == 1
    assert by_edge[("Neo4j", "used_in", "ATRI")]["chain_order"] == 2
    assert by_edge[("ATRI", "supports", "Graph RAG")]["chain_order"] == 3


def test_normalize_extracted_facts_canonicalizes_hyper_role_aliases_for_auto_chain():
    facts = normalize_extracted_facts(
        {
            "hyper_tuples": [
                {
                    "event": "graph extraction model budget",
                    "event_type": "Decision",
                    "roles": [
                        {
                            "role": "结果",
                            "entity": "larger extraction budget",
                            "entity_type": "Concept",
                        },
                        {"role": "配置", "entity": "max_tokens=4096", "entity_type": "Concept"},
                        {"role": "模型", "entity": "deepseek-v4-pro", "entity_type": "System"},
                        {"role": "执行者", "entity": "ATRI", "entity_type": "Project"},
                    ],
                }
            ]
        },
        source_id="chunk-role-aliases",
        source_kind="document",
    )

    by_edge = {(fact["subject"], fact["predicate"], fact["object"]): fact for fact in facts}
    role_edges = {
        (fact["object"], fact.get("hyper_role"))
        for fact in facts
        if fact["predicate"] == "has_role"
    }

    assert ("ATRI", "actor") in role_edges
    assert ("deepseek-v4-pro", "model") in role_edges
    assert ("max_tokens=4096", "config") in role_edges
    assert ("larger extraction budget", "result") in role_edges
    assert by_edge[("ATRI", "uses", "deepseek-v4-pro")]["chain_order"] == 1
    assert by_edge[("deepseek-v4-pro", "configured_with", "max_tokens=4096")]["chain_order"] == 2
    assert by_edge[("max_tokens=4096", "produces", "larger extraction budget")]["chain_order"] == 3


def test_normalize_extracted_facts_caps_hyper_tuple_count():
    facts = normalize_extracted_facts(
        {
            "hyper_tuples": [
                {
                    "event": f"event {index}",
                    "event_type": "Event",
                    "roles": [
                        {
                            "role": "actor",
                            "entity": f"Person {index}",
                            "entity_type": "Person",
                        },
                        {
                            "role": "tool",
                            "entity": f"Tool {index}",
                            "entity_type": "Tool",
                        },
                    ],
                    "chain": [
                        {"from_role": "actor", "predicate": "uses", "to_role": "tool"},
                    ],
                }
                for index in range(MAX_HYPER_TUPLES + 2)
            ]
        },
        source_id="chunk-hyper-capped",
        source_kind="document",
    )

    assert {fact["hyper_event"] for fact in facts} == {
        f"event {index}" for index in range(MAX_HYPER_TUPLES)
    }


def test_normalize_extracted_facts_caps_hyper_roles_and_chain_edges():
    facts = normalize_extracted_facts(
        {
            "hyper_tuples": [
                {
                    "event": "oversized event",
                    "event_type": "Event",
                    "roles": [
                        {
                            "role": f"role_{index}",
                            "entity": f"Entity {index}",
                            "entity_type": "Other",
                        }
                        for index in range(MAX_HYPER_ROLES + 3)
                    ],
                    "chain": [
                        {
                            "from_role": f"role_{index}",
                            "predicate": "related_to",
                            "to_role": f"role_{index + 1}",
                        }
                        for index in range(MAX_HYPER_CHAIN_EDGES + 3)
                    ],
                }
            ]
        },
        source_id="chunk-hyper-capped-roles",
        source_kind="document",
    )

    role_facts = [fact for fact in facts if fact["predicate"] == "has_role"]
    chain_facts = [fact for fact in facts if fact["predicate"] == "related_to"]

    assert len(role_facts) == MAX_HYPER_ROLES
    assert len(chain_facts) == MAX_HYPER_CHAIN_EDGES
    assert "Entity 0" in {fact["object"] for fact in role_facts}
    assert f"Entity {MAX_HYPER_ROLES}" not in {fact["object"] for fact in role_facts}


def test_normalize_extracted_facts_preserves_hyper_metadata_on_duplicate_chain_fact():
    facts = normalize_extracted_facts(
        {
            "tuples": [
                {
                    "subject": "Alice",
                    "subject_type": "Person",
                    "predicate": "uses",
                    "object": "Neo4j",
                    "object_type": "Tool",
                    "evidence": "plain evidence",
                    "confidence": 0.4,
                }
            ],
            "hyper_tuples": [
                {
                    "event": "ATRI graph RAG Neo4j adoption",
                    "event_type": "Decision",
                    "predicate": "adopted_for",
                    "roles": [
                        {"role": "actor", "entity": "Alice", "entity_type": "Person"},
                        {"role": "tool", "entity": "Neo4j", "entity_type": "Tool"},
                    ],
                    "chain": [
                        {"from_role": "actor", "predicate": "uses", "to_role": "tool"},
                    ],
                    "evidence": "hyper evidence",
                    "confidence": 0.8,
                }
            ],
        },
        source_id="chunk-hyper-3",
        source_kind="document",
    )

    chain_fact = next(
        fact
        for fact in facts
        if fact["subject"] == "Alice" and fact["predicate"] == "uses" and fact["object"] == "Neo4j"
    )

    assert chain_fact["chain_id"]
    assert chain_fact["chain_ids"] == [chain_fact["chain_id"]]
    assert chain_fact["chain_order"] == 1
    assert chain_fact["derived_from_hyper_tuple"] is True
    assert chain_fact["evidence"] == "hyper evidence"
    assert chain_fact["confidence"] == 0.8


def test_normalize_extracted_facts_preserves_multiple_chain_order_memberships():
    facts = normalize_extracted_facts(
        {
            "hyper_tuples": [
                {
                    "event": "first adoption",
                    "event_type": "Decision",
                    "roles": [
                        {"role": "actor", "entity": "Alice", "entity_type": "Person"},
                        {"role": "tool", "entity": "Neo4j", "entity_type": "Tool"},
                    ],
                    "chain": [
                        {"from_role": "actor", "predicate": "uses", "to_role": "tool"},
                    ],
                },
                {
                    "event": "second adoption",
                    "event_type": "Decision",
                    "roles": [
                        {"role": "project", "entity": "ATRI", "entity_type": "Project"},
                        {"role": "actor", "entity": "Alice", "entity_type": "Person"},
                        {"role": "tool", "entity": "Neo4j", "entity_type": "Tool"},
                    ],
                    "chain": [
                        {"from_role": "project", "predicate": "has_owner", "to_role": "actor"},
                        {"from_role": "actor", "predicate": "uses", "to_role": "tool"},
                    ],
                },
            ]
        },
        source_id="chunk-hyper-4",
        source_kind="document",
    )

    chain_fact = next(
        fact
        for fact in facts
        if fact["subject"] == "Alice" and fact["predicate"] == "uses" and fact["object"] == "Neo4j"
    )

    assert len(chain_fact["chain_ids"]) == 2
    assert len(chain_fact["chain_order_keys"]) == 2
    assert any(
        key.endswith(f"{CHAIN_ORDER_KEY_SEPARATOR}1") for key in chain_fact["chain_order_keys"]
    )
    assert any(
        key.endswith(f"{CHAIN_ORDER_KEY_SEPARATOR}2") for key in chain_fact["chain_order_keys"]
    )
    assert "chain_order" not in chain_fact


def test_normalize_extracted_facts_expands_explicit_chain_for_duplicate_roles():
    facts = normalize_extracted_facts(
        {
            "hyper_tuples": [
                {
                    "event": "pair programming with Neo4j",
                    "event_type": "Event",
                    "roles": [
                        {"role": "actor", "entity": "Alice", "entity_type": "Person"},
                        {"role": "actor", "entity": "Bob", "entity_type": "Person"},
                        {"role": "tool", "entity": "Neo4j", "entity_type": "Tool"},
                    ],
                    "chain": [
                        {"from_role": "actor", "predicate": "uses", "to_role": "tool"},
                    ],
                }
            ]
        },
        source_id="chunk-hyper-5",
        source_kind="document",
    )

    by_edge = {(fact["subject"], fact["predicate"], fact["object"]): fact for fact in facts}

    assert ("Alice", "uses", "Neo4j") in by_edge
    assert ("Bob", "uses", "Neo4j") in by_edge
    assert by_edge[("Alice", "uses", "Neo4j")]["chain_order"] == 1
    assert by_edge[("Bob", "uses", "Neo4j")]["chain_order"] == 1


def test_normalize_extracted_facts_can_disambiguate_duplicate_roles_by_entity():
    facts = normalize_extracted_facts(
        {
            "hyper_tuples": [
                {
                    "event": "Bob configured Neo4j",
                    "event_type": "Event",
                    "roles": [
                        {"role": "actor", "entity": "Alice", "entity_type": "Person"},
                        {"role": "actor", "entity": "Bob", "entity_type": "Person"},
                        {"role": "tool", "entity": "Neo4j", "entity_type": "Tool"},
                    ],
                    "chain": [
                        {
                            "from_role": "actor",
                            "from_entity": "Bob",
                            "predicate": "configured",
                            "to_role": "tool",
                        },
                    ],
                }
            ]
        },
        source_id="chunk-hyper-6",
        source_kind="document",
    )

    by_edge = {(fact["subject"], fact["predicate"], fact["object"]): fact for fact in facts}

    assert ("Bob", "configured_with", "Neo4j") in by_edge
    assert ("Alice", "configured_with", "Neo4j") not in by_edge


@pytest.mark.asyncio
async def test_graph_tuple_extractor_uses_chat_specific_durable_fact_prompt():
    captured = {}

    class FakeLLM:
        def chat(self, messages, stream=False):
            captured["messages"] = messages
            captured["stream"] = stream
            return type("Response", (), {"content": '{"tuples":[]}'})()

    extractor = GraphTupleExtractor(lambda: FakeLLM())

    facts = await extractor.extract_facts(
        "User: why did screenshot fail?",
        source_id="chat-task-1",
        source_kind="chat",
    )

    system_prompt = captured["messages"][0]["content"]
    assert facts == []
    assert captured["stream"] is False
    assert "明确支持、可长期复用的有用事实" in system_prompt
    assert "不要抽取 user asked/requested/said 类 tuples" in system_prompt
    assert "助手统一用实体名 ATRI" in system_prompt
    assert "不要输出 JSON 以外的说明或分析" in system_prompt
    assert "针对 chat 文本" in system_prompt
    assert "示例跳过" in system_prompt
    assert "User -[requested]-> screenshot" in system_prompt
    assert "hyper_tuples" in system_prompt
    assert "chain" in system_prompt
    assert f"hyper_tuples 最多 {MAX_HYPER_TUPLES} 条" in system_prompt
    assert f"每条最多 {MAX_HYPER_ROLES} 个 role" in system_prompt
    assert f"最多 {MAX_HYPER_CHAIN_EDGES} 条 chain 边" in system_prompt
    assert f"最多输出 {MAX_EXTRACTION_TUPLES} 条 tuples" in system_prompt


@pytest.mark.asyncio
async def test_graph_tuple_extractor_includes_existing_graph_context_in_user_prompt():
    captured = {}
    existing_context = format_graph_context(["- Alice -[works_at]-> Acme"])

    class FakeLLM:
        def chat(self, messages, stream=False):
            captured["messages"] = messages
            return type("Response", (), {"content": '{"tuples":[]}'})()

    extractor = GraphTupleExtractor(lambda: FakeLLM())

    facts = await extractor.extract_facts(
        "Alice now works on Neo4j graph extraction.",
        source_id="chunk-existing-context",
        source_kind="document",
        metadata={EXISTING_GRAPH_CONTEXT_METADATA_KEY: existing_context},
    )

    user_content = captured["messages"][1]["content"]
    assert facts == []
    assert "[已有图谱上下文]" in user_content
    assert "挂接到这些已有节点与关系上" in user_content
    assert "- Alice -[works_at]-> Acme" in user_content
    assert "若这些事实能回答问题" not in user_content
    assert "evidence 必须引用下方待抽取文本" in user_content
    assert user_content.rstrip().endswith("Alice now works on Neo4j graph extraction.")


@pytest.mark.asyncio
async def test_graph_tuple_extractor_reuses_unambiguous_existing_entity_type():
    existing_context = format_graph_context(
        ["- 订单系统 (System) -[belongs_to]-> 交易平台 (System)"]
    )

    class FakeLLM:
        def chat(self, messages, stream=False):
            return type(
                "Response",
                (),
                {
                    "content": json.dumps(
                        {
                            "tuples": [
                                {
                                    "subject": "订单系统",
                                    "subject_type": "Component",
                                    "predicate": "has_error",
                                    "object": "5xx 告警",
                                    "object_type": "Concept",
                                    "evidence": "订单系统出现 5xx 告警。",
                                }
                            ]
                        },
                        ensure_ascii=False,
                    )
                },
            )()

    extractor = GraphTupleExtractor(lambda: FakeLLM())

    facts = await extractor.extract_facts(
        "订单系统出现 5xx 告警。",
        source_id="chunk-type-reuse",
        source_kind="document",
        existing_graph_context=existing_context,
    )

    assert facts[0]["subject"] == "订单系统"
    assert facts[0]["subject_type"] == "System"
    assert facts[0]["subject_type_key"] == "system"
    assert facts[0]["fact_key"] == "system:订单系统|has_error|concept:5xx 告警"


@pytest.mark.asyncio
async def test_graph_tuple_extractor_does_not_force_ambiguous_same_name_type_reuse():
    existing_context = format_graph_context(
        [
            "- Apple (Company) -[makes]-> iPhone (Product)",
            "- Apple (Product) -[has_color]-> Red (Color)",
        ]
    )

    class FakeLLM:
        def chat(self, messages, stream=False):
            return type(
                "Response",
                (),
                {
                    "content": json.dumps(
                        {
                            "tuples": [
                                {
                                    "subject": "Apple",
                                    "subject_type": "Product",
                                    "predicate": "has_color",
                                    "object": "Green",
                                    "object_type": "Color",
                                    "evidence": "Apple is green.",
                                }
                            ]
                        }
                    )
                },
            )()

    extractor = GraphTupleExtractor(lambda: FakeLLM())

    facts = await extractor.extract_facts(
        "Apple is green.",
        source_id="chunk-ambiguous-type",
        source_kind="document",
        existing_graph_context=existing_context,
    )

    assert facts[0]["subject_type"] == "Product"
    assert facts[0]["subject_type_key"] == "product"
    assert facts[0]["fact_key"] == "product:apple|has_color|color:green"


@pytest.mark.asyncio
async def test_graph_tuple_extractor_keeps_incompatible_same_name_entity_type():
    existing_context = format_graph_context(["- Apple (Company) -[makes]-> iPhone (Product)"])

    class FakeLLM:
        def chat(self, messages, stream=False):
            return type(
                "Response",
                (),
                {
                    "content": json.dumps(
                        {
                            "tuples": [
                                {
                                    "subject": "Apple",
                                    "subject_type": "Product",
                                    "predicate": "has_color",
                                    "object": "Green",
                                    "object_type": "Color",
                                    "evidence": "The Apple is green.",
                                }
                            ]
                        }
                    )
                },
            )()

    extractor = GraphTupleExtractor(lambda: FakeLLM())

    facts = await extractor.extract_facts(
        "The Apple is green.",
        source_id="chunk-incompatible-same-name",
        source_kind="document",
        existing_graph_context=existing_context,
    )

    assert facts[0]["subject_type"] == "Product"
    assert facts[0]["fact_key"] == "product:apple|has_color|color:green"


@pytest.mark.asyncio
async def test_graph_tuple_extractor_parses_reasoning_json_when_content_is_empty():
    class FakeLLM:
        def chat(self, messages, stream=False):
            return type(
                "Response",
                (),
                {
                    "content": "",
                    "reasoning_content": (
                        '{"tuples":[{"subject":"User","subject_type":"Person",'
                        '"predicate":"commutes_by","object":"bike",'
                        '"object_type":"Preference","evidence":"用户改骑车去公司",'
                        '"confidence":0.8}]}'
                    ),
                },
            )()

    extractor = GraphTupleExtractor(lambda: FakeLLM())

    facts = await extractor.extract_facts(
        "User: 我这段时间上班都改骑车去公司了。",
        source_id="chat-task-commute",
        source_kind="chat",
    )

    assert len(facts) == 1
    assert facts[0]["subject"] == "User"
    assert facts[0]["predicate"] == "commutes_by"
    assert facts[0]["object"] == "bike"


@pytest.mark.asyncio
async def test_graph_tuple_extractor_logs_token_usage_to_console(caplog):
    class FakeLLM:
        def chat(self, messages, stream=False):
            return type(
                "Response",
                (),
                {
                    "content": (
                        '{"tuples":[{"subject":"Alice","subject_type":"Person",'
                        '"predicate":"works_at","object":"Acme",'
                        '"object_type":"Company","evidence":"Alice works at Acme.",'
                        '"confidence":0.9}]}'
                    ),
                    "prompt_tokens": 123,
                    "completion_tokens": 45,
                },
            )()

    caplog.set_level(logging.INFO, logger="atri")
    extractor = GraphTupleExtractor(lambda: FakeLLM())

    facts = await extractor.extract_facts(
        "Alice works at Acme.",
        source_id="chunk-token-log",
        source_kind="document",
    )

    assert len(facts) == 1
    assert "Graph extraction token usage" in caplog.text
    assert "source_kind=document" in caplog.text
    assert "source_id=chunk-token-log" in caplog.text
    assert "prompt_tokens=123" in caplog.text
    assert "completion_tokens=45" in caplog.text
    assert "total_tokens=168" in caplog.text


def test_chat_turn_text_does_not_include_runtime_timestamp():
    text = _chat_turn_text("Alice works at Acme.", "Noted.")

    assert "Alice works at Acme." in text
    assert "Noted." in text
    assert "Recorded at" not in text


class FakeNeo4jSession:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def run(self, query, **params):
        self.calls.append({"query": query, "params": params})
        if "FACT*1..2" in query:
            return [
                {
                    "subject": "Alice",
                    "subject_type": "Person",
                    "predicate": "works_at",
                    "object": "Acme",
                    "object_type": "Company",
                    "evidence": "Alice works at Acme.",
                    "confidence": 0.9,
                    "hop": 1,
                },
                {
                    "subject": "Acme",
                    "subject_type": "Company",
                    "predicate": "uses",
                    "object": "Neo4j",
                    "object_type": "Tool",
                    "evidence": "Acme uses Neo4j.",
                    "confidence": 0.8,
                    "hop": 2,
                },
            ]
        if "RETURN s.name AS subject" in query:
            return [
                {
                    "subject": "Alice",
                    "subject_type": "Person",
                    "predicate": "works_at",
                    "object": "Acme",
                    "object_type": "Company",
                    "evidence": "Alice works at Acme.",
                    "confidence": 0.9,
                }
            ]
        return []


class FakeNeo4jDriver:
    def __init__(self):
        self.session_obj = FakeNeo4jSession()
        self.closed = False
        self.verified = False

    def verify_connectivity(self):
        self.verified = True

    def session(self, database=None):
        self.database = database
        return self.session_obj

    def close(self):
        self.closed = True


class ParallelRetrievalState:
    def __init__(self):
        self.single_started = threading.Event()
        self.multi_started = threading.Event()
        self.single_saw_multi_started = False
        self.calls = []
        self.lock = threading.Lock()


class ParallelRetrievalSession:
    def __init__(self, state: ParallelRetrievalState):
        self.state = state

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def run(self, query, **params):
        with self.state.lock:
            self.state.calls.append({"query": query, "params": params})
        if "RETURN s.name AS subject" in query:
            self.state.single_started.set()
            self.state.single_saw_multi_started = self.state.multi_started.wait(timeout=0.5)
            return [
                {
                    "subject": "Alice",
                    "subject_type": "Person",
                    "predicate": "works_at",
                    "object": "Acme",
                    "object_type": "Company",
                    "evidence": "Alice works at Acme.",
                    "confidence": 0.9,
                }
            ]
        if "RETURN startNode(r).name AS subject" in query:
            self.state.multi_started.set()
            return [
                {
                    "subject": "Bob",
                    "subject_type": "Person",
                    "predicate": "uses",
                    "object": "Neo4j",
                    "object_type": "Tool",
                    "evidence": "Bob uses Neo4j.",
                    "confidence": 0.8,
                    "hop": 2,
                }
            ]
        return []


class ParallelRetrievalDriver:
    def __init__(self, state: ParallelRetrievalState):
        self.state = state
        self.closed = False
        self.verified = False

    def verify_connectivity(self):
        self.verified = True

    def session(self, database=None):
        self.database = database
        return ParallelRetrievalSession(self.state)

    def close(self):
        self.closed = True


def _retrieve_calls(calls):
    return [
        call
        for call in calls
        if "RETURN s.name AS subject" in call["query"]
        or "RETURN startNode(r).name AS subject" in call["query"]
    ]


def _single_hop_retrieve_call(calls):
    return next(
        call for call in _retrieve_calls(calls) if "RETURN s.name AS subject" in call["query"]
    )


def _multi_hop_retrieve_call(calls):
    return next(
        call
        for call in _retrieve_calls(calls)
        if "RETURN startNode(r).name AS subject" in call["query"]
    )


def _status_filter_property_reads(query: str) -> list[str]:
    return re.findall(r"\b(?:r|rel|source_r)\.status\b", query)


class FailingOnceRetrieveSession(FakeNeo4jSession):
    def __init__(self, error: Exception):
        super().__init__()
        self.error = error
        self.failed = False

    def run(self, query, **params):
        self.calls.append({"query": query, "params": params})
        is_retrieve_query = (
            "RETURN s.name AS subject" in query or "RETURN startNode(r).name AS subject" in query
        )
        if not self.failed and is_retrieve_query:
            self.failed = True
            raise self.error
        return super().run(query, **params)


def test_neo4j_graph_client_initializes_upserts_and_retrieves_context():
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
            "max_facts": 8,
        },
        driver_factory=lambda uri, auth: driver,
    )
    fact = normalize_extracted_facts(
        [
            {
                "subject": "Alice",
                "subject_type": "Person",
                "predicate": "works_at",
                "object": "Acme",
                "object_type": "Company",
            }
        ],
        source_id="chunk-1",
        source_kind="document",
        metadata={"chunk_ids": ["chunk-1"], "label": "测试"},
    )[0]

    client.initialize()
    count = client.upsert_facts([fact])
    context = client.retrieve_context(query="Alice Acme", source_ids=["chunk-1"], max_facts=3)
    client.close()

    queries = "\n".join(call["query"] for call in driver.session_obj.calls)
    assert driver.verified is True
    assert driver.database == "atri"
    assert "CREATE CONSTRAINT" in queries
    assert "DROP CONSTRAINT entity_name_key IF EXISTS" in queries
    assert "REQUIRE (e.name_key, e.type_key) IS UNIQUE" in queries
    assert "CREATE INDEX fact_source_id IF NOT EXISTS" in queries
    assert "CREATE INDEX fact_updated_at IF NOT EXISTS" in queries
    assert "CREATE FULLTEXT INDEX entity_text IF NOT EXISTS" in queries
    assert "CREATE FULLTEXT INDEX fact_text IF NOT EXISTS" in queries
    assert "GraphExpansionCache" in queries
    assert "GraphMetadata" in queries
    assert (
        "MERGE (s:Entity {name_key: fact.subject_key, type_key: fact.subject_type_key})" in queries
    )
    assert "MERGE (o:Entity {name_key: fact.object_key, type_key: fact.object_type_key})" in queries
    assert "apoc." not in queries
    assert "r.metadata_json = fact.metadata_json" in queries
    assert "source_count = coalesce" not in queries
    assert "r.source_count = size(source_ids)" in queries
    upsert_call = next(call for call in driver.session_obj.calls if "facts" in call["params"])
    upsert_row = upsert_call["params"]["facts"][0]
    assert json.loads(upsert_row["metadata_json"]) == {
        "chunk_ids": ["chunk-1"],
        "label": "测试",
    }
    assert count == 1
    assert context == format_graph_context(
        ["- [1-hop] Alice -[works_at]-> Acme (Alice works at Acme.)"]
    )
    assert driver.closed is True


def test_neo4j_graph_client_marks_current_single_facts_with_slot_metadata():
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )
    fact = normalize_extracted_facts(
        [
            {
                "subject": "Alice",
                "subject_type": "Person",
                "predicate": "works_at",
                "object": "Acme",
                "object_type": "Company",
            }
        ],
        source_id="chunk-current-single",
        source_kind="document",
    )[0]

    client.upsert_facts([fact])

    upsert_call = next(call for call in driver.session_obj.calls if "facts" in call["params"])
    upsert_row = upsert_call["params"]["facts"][0]
    assert upsert_row["status"] == "active"
    assert upsert_row["conflict_policy"] == "current_single"
    assert upsert_row["slot_key"] == "person:alice|works_at"
    assert upsert_row["valid_from"] is None
    assert upsert_row["valid_to"] is None
    assert upsert_row["superseded_by"] is None


def test_neo4j_graph_client_keeps_connection_facts_append_only():
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )
    fact = normalize_extracted_facts(
        [
            {
                "subject": "Alice",
                "subject_type": "Person",
                "predicate": "uses",
                "object": "Neo4j",
                "object_type": "Tool",
            }
        ],
        source_id="chunk-append-only",
        source_kind="document",
    )[0]

    client.upsert_facts([fact])

    upsert_call = next(call for call in driver.session_obj.calls if "facts" in call["params"])
    upsert_row = upsert_call["params"]["facts"][0]
    assert upsert_row["status"] == "active"
    assert upsert_row["conflict_policy"] == "append_only"
    assert upsert_row["slot_key"] is None
    assert upsert_row["valid_from"] is None
    assert upsert_row["valid_to"] is None
    assert upsert_row["superseded_by"] is None


def test_neo4j_graph_client_keeps_ambiguous_location_facts_append_only():
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )
    fact = normalize_extracted_facts(
        [
            {
                "subject": "Alice",
                "subject_type": "Person",
                "predicate": "located_at",
                "object": "Shanghai",
                "object_type": "Location",
            }
        ],
        source_id="chunk-location",
        source_kind="document",
    )[0]

    client.upsert_facts([fact])

    upsert_call = next(call for call in driver.session_obj.calls if "facts" in call["params"])
    upsert_row = upsert_call["params"]["facts"][0]
    assert upsert_row["conflict_policy"] == "append_only"
    assert upsert_row["slot_key"] is None


def test_neo4j_graph_client_upsert_supersedes_only_current_single_slot():
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )
    fact = normalize_extracted_facts(
        [
            {
                "subject": "Alice",
                "subject_type": "Person",
                "predicate": "works_at",
                "object": "Contoso",
                "object_type": "Company",
            }
        ],
        source_id="chunk-current-single-2",
        source_kind="document",
    )[0]

    client.upsert_facts([fact])

    upsert_query = next(
        call["query"] for call in driver.session_obj.calls if "MERGE (s)-[r:FACT" in call["query"]
    )
    assert "fact.conflict_policy = 'current_single'" in upsert_query
    assert "old.slot_key = fact.slot_key" in upsert_query
    assert "old.fact_key <> fact.fact_key" in upsert_query
    assert "coalesce(old[$status_property], 'active') = 'active'" in upsert_query
    upsert_call = next(
        call for call in driver.session_obj.calls if "MERGE (s)-[r:FACT" in call["query"]
    )
    assert upsert_call["params"]["status_property"] == "status"
    assert "old.status = 'superseded'" in upsert_query
    assert "old.superseded_by = fact.fact_key" in upsert_query
    assert "uses" not in upsert_query


def test_neo4j_graph_client_supersedes_legacy_current_single_without_slot_key():
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )
    fact = normalize_extracted_facts(
        [
            {
                "subject": "Alice",
                "subject_type": "Person",
                "predicate": "works_at",
                "object": "Contoso",
                "object_type": "Company",
            }
        ],
        source_id="chunk-current-single-legacy",
        source_kind="document",
    )[0]

    client.upsert_facts([fact])

    upsert_query = next(
        call["query"] for call in driver.session_obj.calls if "MERGE (s)-[r:FACT" in call["query"]
    )
    assert "old.slot_key IS NULL AND old.predicate = fact.predicate" in upsert_query
    assert "old.slot_key = coalesce(old.slot_key, fact.slot_key)" in upsert_query


def test_neo4j_graph_client_folds_same_batch_current_single_slots_deterministically():
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )
    facts = normalize_extracted_facts(
        [
            {
                "subject": "Alice",
                "subject_type": "Person",
                "predicate": "works_at",
                "object": "Acme",
                "object_type": "Company",
            },
            {
                "subject": "Alice",
                "subject_type": "Person",
                "predicate": "works_at",
                "object": "Contoso",
                "object_type": "Company",
            },
            {
                "subject": "Alice",
                "subject_type": "Person",
                "predicate": "uses",
                "object": "Neo4j",
                "object_type": "Tool",
            },
        ],
        source_id="chunk-current-single-batch",
        source_kind="document",
    )

    count = client.upsert_facts(facts)

    upsert_call = next(call for call in driver.session_obj.calls if "facts" in call["params"])
    rows = upsert_call["params"]["facts"]
    assert count == 2
    assert [row["predicate"] for row in rows] == ["works_at", "uses"]
    assert rows[0]["object"] == "Contoso"
    assert rows[0]["slot_key"] == "person:alice|works_at"
    assert rows[1]["object"] == "Neo4j"
    assert rows[1]["conflict_policy"] == "append_only"


def test_neo4j_graph_client_keeps_non_active_current_single_from_superseding():
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )
    facts = normalize_extracted_facts(
        [
            {
                "subject": "Alice",
                "subject_type": "Person",
                "predicate": "works_at",
                "object": "Contoso",
                "object_type": "Company",
            },
            {
                "subject": "Alice",
                "subject_type": "Person",
                "predicate": "works_at",
                "object": "OldCo",
                "object_type": "Company",
            },
        ],
        source_id="chunk-current-single-history",
        source_kind="document",
    )
    facts[1]["status"] = "superseded"

    count = client.upsert_facts(facts)

    upsert_call = next(call for call in driver.session_obj.calls if "facts" in call["params"])
    rows = upsert_call["params"]["facts"]
    upsert_query = upsert_call["query"]
    assert count == 2
    assert [row["object"] for row in rows] == ["Contoso", "OldCo"]
    assert rows[0]["status"] == "active"
    assert rows[1]["status"] == "superseded"
    assert "coalesce(fact.status, 'active') = 'active'" in upsert_query


def test_neo4j_graph_client_retrieval_filters_superseded_facts_by_default():
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    client.retrieve_context(
        query="Alice Acme",
        source_ids=["chunk-1"],
        max_facts=2,
        retrieval_depth=2,
    )

    single_hop_query = _single_hop_retrieve_call(driver.session_obj.calls)["query"]
    multi_hop_query = _multi_hop_retrieve_call(driver.session_obj.calls)["query"]
    assert "coalesce(r[$status_property], 'active') = 'active'" in single_hop_query
    assert "all(rel IN rels WHERE coalesce(rel[$status_property], 'active') = 'active')" in (
        multi_hop_query
    )


def test_neo4j_graph_client_retrieval_uses_dynamic_status_property_reads():
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    client.retrieve_context(
        query="Alice Acme",
        source_ids=["chunk-1"],
        max_facts=2,
        retrieval_depth=2,
    )

    graph_calls = [
        call
        for call in driver.session_obj.calls
        if "FACT" in call["query"]
        and (
            "RETURN elementId(r) AS element_id" in call["query"]
            or "RETURN elementId(seed) AS element_id" in call["query"]
            or "RETURN seed_element_id, paths" in call["query"]
            or "RETURN s.name AS subject" in call["query"]
            or "RETURN startNode(r).name AS subject" in call["query"]
        )
    ]

    assert graph_calls
    assert all(call["params"]["status_property"] == "status" for call in graph_calls)
    assert all(not _status_filter_property_reads(call["query"]) for call in graph_calls)
    assert any(
        "coalesce(r[$status_property], 'active') = 'active'" in call["query"]
        for call in graph_calls
    )
    assert any(
        "coalesce(source_r[$status_property], 'active') = 'active'" in call["query"]
        for call in graph_calls
    )
    assert any(
        "all(rel IN rels WHERE coalesce(rel[$status_property], 'active') = 'active')"
        in call["query"]
        for call in graph_calls
    )


def test_neo4j_graph_client_builds_source_index_nodes_for_fact_source_ids():
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )
    fact = normalize_extracted_facts(
        [
            {
                "subject": "Alice",
                "subject_type": "Person",
                "predicate": "works_at",
                "object": "Acme",
                "object_type": "Company",
            }
        ],
        source_id="chunk-1",
        source_kind="document",
        metadata={"chunk_ids": ["chunk-1", "chunk-2"]},
    )[0]

    client.upsert_facts([fact])

    queries = "\n".join(call["query"] for call in driver.session_obj.calls)
    assert re.search(r"(?m)^\s*CALL\s+\{", queries) is None
    assert "CALL (fact_node, source_ids) {" in queries
    assert (
        "CREATE CONSTRAINT graph_source_id IF NOT EXISTS "
        "FOR (source:GraphSource) REQUIRE source.source_id IS UNIQUE"
    ) in queries
    assert (
        "CREATE CONSTRAINT graph_fact_key IF NOT EXISTS "
        "FOR (fact:GraphFact) REQUIRE fact.fact_key IS UNIQUE"
    ) in queries
    assert "MERGE (fact_node:GraphFact {fact_key: fact.fact_key})" in queries
    assert "MERGE (fact_node)-[:FACT_SUBJECT]->(s)" in queries
    assert "MERGE (fact_node)-[:FACT_OBJECT]->(o)" in queries
    assert "MERGE (source_node:GraphSource {source_id: source_id})" in queries
    assert "MERGE (source_node)-[:SUPPORTS_FACT]->(fact_node)" in queries


def test_neo4j_graph_client_backfills_source_index_nodes_for_existing_facts():
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    client.initialize()

    queries = "\n".join(call["query"] for call in driver.session_obj.calls)
    assert "MATCH (s:Entity)-[r:FACT]->(o:Entity)" in queries
    assert "MERGE (fact_node:GraphFact {fact_key: r.fact_key})" in queries
    assert "MERGE (fact_node)-[:FACT_SUBJECT]->(s)" in queries
    assert "MERGE (fact_node)-[:FACT_OBJECT]->(o)" in queries
    assert "UNWIND source_ids AS source_id" in queries
    assert "MERGE (source_node:GraphSource {source_id: source_id})" in queries
    assert "MERGE (source_node)-[:SUPPORTS_FACT]->(fact_node)" in queries


def test_neo4j_graph_client_uses_source_index_nodes_for_single_hop_source_seed():
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    client.retrieve_context(
        query="Alice Acme",
        source_ids=["chunk-1"],
        max_facts=2,
        retrieval_depth=1,
    )

    retrieve_query = next(
        call["query"]
        for call in driver.session_obj.calls
        if "RETURN s.name AS subject" in call["query"]
    )
    assert re.search(r"(?m)^\s*CALL\s+\{", retrieve_query) is None
    assert "CALL () {" in retrieve_query
    assert "MATCH (source_node:GraphSource)-[:SUPPORTS_FACT]->(fact_node:GraphFact)" in (
        retrieve_query
    )
    assert "fact_node.fact_key = r.fact_key" in retrieve_query
    assert "any(source_id IN coalesce(r.source_ids, []) WHERE source_id IN $source_ids)" not in (
        retrieve_query
    )


def test_neo4j_graph_client_uses_source_index_nodes_for_multihop_source_seeds():
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    client.retrieve_context(
        query="Alice Acme",
        source_ids=["chunk-1"],
        max_facts=2,
        retrieval_depth=2,
    )

    multihop_query = next(
        call["query"] for call in driver.session_obj.calls if "FACT*1..2" in call["query"]
    )
    assert re.search(r"(?m)^\s*CALL\s+\{", multihop_query) is None
    assert "CALL () {" in multihop_query
    assert "MATCH (source_node:GraphSource)-[:SUPPORTS_FACT]->(fact_node:GraphFact)" in (
        multihop_query
    )
    assert "MATCH (s:Entity)-[source_r:FACT]->(o:Entity)" in multihop_query
    assert "source_r.fact_key = fact_node.fact_key" in multihop_query
    assert "coalesce(source_r[$status_property], 'active') = 'active'" in multihop_query
    assert "any(source_id IN coalesce(source_r.source_ids, [])" not in multihop_query


def test_neo4j_graph_client_multihop_seed_probe_requires_active_source_fact():
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    client.retrieve_context(
        query="",
        source_ids=["chunk-1"],
        max_facts=2,
        retrieval_depth=2,
    )

    seed_probe_query = next(
        call["query"]
        for call in driver.session_obj.calls
        if "RETURN elementId(seed) AS element_id" in call["query"]
    )
    assert "MATCH (s:Entity)-[source_r:FACT]->(o:Entity)" in seed_probe_query
    assert "source_r.fact_key = fact_node.fact_key" in seed_probe_query
    assert "coalesce(source_r[$status_property], 'active') = 'active'" in seed_probe_query


def test_neo4j_graph_client_live_multihop_source_seeds_require_active_source_fact():
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    client.retrieve_context(
        query="Alice Acme",
        source_ids=["chunk-1"],
        max_facts=2,
        retrieval_depth=2,
    )

    multihop_query = _multi_hop_retrieve_call(driver.session_obj.calls)["query"]
    assert "MATCH (s:Entity)-[source_r:FACT]->(o:Entity)" in multihop_query
    assert "source_r.fact_key = fact_node.fact_key" in multihop_query
    assert "coalesce(source_r[$status_property], 'active') = 'active'" in multihop_query


def test_neo4j_graph_client_can_include_entity_types_in_retrieved_context():
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    context = client.retrieve_context(
        query="Alice Acme",
        source_ids=[],
        max_facts=1,
        include_entity_types=True,
    )

    query = next(
        call["query"]
        for call in driver.session_obj.calls
        if "RETURN s.name AS subject" in call["query"]
    )
    assert "s.type AS subject_type" in query
    assert "o.type AS object_type" in query
    assert context == format_graph_context(
        ["- [1-hop] Alice (Person) -[works_at]-> Acme (Company) (Alice works at Acme.)"]
    )


def test_neo4j_graph_client_uses_fulltext_seeded_single_hop_retrieval():
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    client.retrieve_context(query="Alice Acme", source_ids=[], max_facts=2)

    call = next(
        call for call in driver.session_obj.calls if "RETURN s.name AS subject" in call["query"]
    )
    query = call["query"]
    seed_queries = [
        seed_call["query"]
        for seed_call in driver.session_obj.calls
        if "db.index.fulltext.queryNodes" in seed_call["query"]
        or "db.index.fulltext.queryRelationships" in seed_call["query"]
    ]
    assert len(seed_queries) == 2
    assert "UNWIND $entity_seed_rows AS entity_seed" in query
    assert "UNWIND $fact_seed_rows AS fact_seed" in query
    assert "db.index.fulltext.queryNodes" not in query
    assert "db.index.fulltext.queryRelationships" not in query
    assert "MATCH (s:Entity)-[r:FACT]->(o:Entity)" in query
    assert call["params"]["fulltext_query"] == '"alice" OR "acme"'
    assert call["params"]["seed_limit"] >= call["params"]["limit"]


def test_neo4j_graph_client_fulltext_fact_seeds_filter_active_before_final_limit():
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    client.retrieve_context(query="Alice Acme", source_ids=[], max_facts=2)

    fact_seed_call = next(
        seed_call
        for seed_call in driver.session_obj.calls
        if "db.index.fulltext.queryRelationships" in seed_call["query"]
    )
    query = fact_seed_call["query"]
    assert "{limit: $fact_seed_candidate_limit}" in query
    assert "WHERE coalesce(r[$status_property], 'active') = 'active'" in query
    assert fact_seed_call["params"]["status_property"] == "status"
    assert "ORDER BY score DESC" in query
    assert "LIMIT $seed_limit" in query
    assert (
        fact_seed_call["params"]["fact_seed_candidate_limit"]
        > fact_seed_call["params"]["seed_limit"]
    )


def test_neo4j_graph_client_falls_back_when_fulltext_index_creation_fails(caplog):
    class FulltextUnsupportedSession(FakeNeo4jSession):
        def run(self, query, **params):
            self.calls.append({"query": query, "params": params})
            if "CREATE FULLTEXT INDEX" in query:
                raise RuntimeError("fulltext indexes are not allowed")
            if "db.index.fulltext" in query:
                raise AssertionError("fulltext retrieval should be disabled")
            if "RETURN s.name AS subject" in query:
                return [
                    {
                        "subject": "Alice",
                        "subject_type": "Person",
                        "predicate": "works_at",
                        "object": "Acme",
                        "object_type": "Company",
                        "evidence": "Alice works at Acme.",
                        "confidence": 0.9,
                    }
                ]
            return []

    caplog.set_level(logging.WARNING, logger="atri")
    driver = FakeNeo4jDriver()
    driver.session_obj = FulltextUnsupportedSession()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    context = client.retrieve_context(query="Alice Acme", source_ids=[], max_facts=2)

    query = next(
        call["query"]
        for call in driver.session_obj.calls
        if "RETURN s.name AS subject" in call["query"]
    )
    assert "Neo4j graph fulltext index entity_text skipped" in caplog.text
    assert "Neo4j graph fulltext index fact_text skipped" in caplog.text
    assert "Neo4j graph fulltext index unavailable; using scan fallback" in caplog.text
    assert "entity_text: fulltext indexes are not allowed" in caplog.text
    assert "fact_text: fulltext indexes are not allowed" in caplog.text
    assert "db.index.fulltext" not in query
    assert "toLower(s.name) CONTAINS term" in query
    assert context == format_graph_context(
        ["- [1-hop] Alice -[works_at]-> Acme (Alice works at Acme.)"]
    )


def test_neo4j_graph_client_skips_unbounded_multihop_when_fulltext_unavailable():
    class FulltextUnsupportedSession(FakeNeo4jSession):
        def run(self, query, **params):
            self.calls.append({"query": query, "params": params})
            if "CREATE FULLTEXT INDEX" in query:
                raise RuntimeError("fulltext indexes are not allowed")
            if "db.index.fulltext" in query:
                raise AssertionError("fulltext retrieval should be disabled")
            if "RETURN startNode(r).name AS subject" in query:
                raise AssertionError("unbounded multi-hop scan should not run")
            if "RETURN s.name AS subject" in query:
                return [
                    {
                        "subject": "Alice",
                        "subject_type": "Person",
                        "predicate": "works_at",
                        "object": "Acme",
                        "object_type": "Company",
                        "evidence": "Alice works at Acme.",
                        "confidence": 0.9,
                    }
                ]
            return []

    driver = FakeNeo4jDriver()
    driver.session_obj = FulltextUnsupportedSession()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )
    timings: dict[str, Any] = {}

    context = client.retrieve_context(
        query="Alice Acme",
        source_ids=[],
        max_facts=2,
        retrieval_depth=2,
        timings=timings,
    )

    assert not any(
        "RETURN startNode(r).name AS subject" in call["query"] for call in driver.session_obj.calls
    )
    assert timings["graph_multihop_degraded"] is True
    assert timings["graph_multi_hop_ms"] == 0.0
    assert context == format_graph_context(
        ["- [1-hop] Alice -[works_at]-> Acme (Alice works at Acme.)"]
    )


def test_neo4j_graph_client_uses_source_seeded_multihop_when_fulltext_unavailable():
    class FulltextUnsupportedSession(FakeNeo4jSession):
        def run(self, query, **params):
            self.calls.append({"query": query, "params": params})
            if "CREATE FULLTEXT INDEX" in query:
                raise RuntimeError("fulltext indexes are not allowed")
            if "db.index.fulltext" in query:
                raise AssertionError("fulltext retrieval should be disabled")
            if "RETURN s.name AS subject" in query:
                return [
                    {
                        "subject": "Alice",
                        "subject_type": "Person",
                        "predicate": "works_at",
                        "object": "Acme",
                        "object_type": "Company",
                        "evidence": "Alice works at Acme.",
                        "confidence": 0.9,
                    }
                ]
            if "RETURN startNode(r).name AS subject" in query:
                return []
            return []

    driver = FakeNeo4jDriver()
    driver.session_obj = FulltextUnsupportedSession()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )
    timings: dict[str, Any] = {}

    client.retrieve_context(
        query="Alice Acme",
        source_ids=["chunk-1"],
        max_facts=2,
        retrieval_depth=2,
        timings=timings,
    )

    multi_hop_query = _multi_hop_retrieve_call(driver.session_obj.calls)["query"]
    assert "MATCH path = (seed)-[:FACT*1..2]-(o:Entity)" in multi_hop_query
    assert "MATCH path = (s:Entity)-[:FACT*1..2]->(o:Entity)" not in multi_hop_query
    assert timings["graph_multihop_degraded"] is False


@pytest.mark.parametrize("failing_index", ["entity_text", "fact_text"])
def test_neo4j_graph_client_logs_specific_fulltext_index_creation_failure(caplog, failing_index):
    class PartiallyUnsupportedFulltextSession(FakeNeo4jSession):
        def run(self, query, **params):
            self.calls.append({"query": query, "params": params})
            if f"CREATE FULLTEXT INDEX {failing_index}" in query:
                raise RuntimeError(f"{failing_index} index is unavailable")
            if "db.index.fulltext" in query:
                raise AssertionError("fulltext retrieval should be disabled")
            if "RETURN s.name AS subject" in query:
                return [
                    {
                        "subject": "Alice",
                        "subject_type": "Person",
                        "predicate": "works_at",
                        "object": "Acme",
                        "object_type": "Company",
                        "evidence": "Alice works at Acme.",
                        "confidence": 0.9,
                    }
                ]
            return []

    caplog.set_level(logging.WARNING, logger="atri")
    driver = FakeNeo4jDriver()
    driver.session_obj = PartiallyUnsupportedFulltextSession()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    client.retrieve_context(query="Alice Acme", source_ids=[], max_facts=2)

    assert f"Neo4j graph fulltext index {failing_index} skipped" in caplog.text
    assert (
        "Neo4j graph fulltext index unavailable; using scan fallback "
        f"for graph retrieval: {failing_index}: {failing_index} index is unavailable"
    ) in caplog.text


def test_neo4j_graph_client_keeps_same_name_different_types_separate():
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )
    facts = normalize_extracted_facts(
        [
            {
                "subject": "Apple",
                "subject_type": "Company",
                "predicate": "makes",
                "object": "iPhone",
                "object_type": "Product",
            },
            {
                "subject": "Apple",
                "subject_type": "Product",
                "predicate": "has_color",
                "object": "Red",
                "object_type": "Color",
            },
        ],
        source_id="chunk-1",
        source_kind="document",
    )

    client.upsert_facts(facts)

    upsert_call = next(call for call in driver.session_obj.calls if "facts" in call["params"])
    rows = upsert_call["params"]["facts"]
    assert rows[0]["subject_key"] == rows[1]["subject_key"] == "apple"
    assert rows[0]["subject_type_key"] == "company"
    assert rows[1]["subject_type_key"] == "product"
    assert rows[0]["fact_key"] != rows[1]["fact_key"]


def test_neo4j_graph_client_persists_hyper_chain_metadata_on_facts():
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )
    facts = normalize_extracted_facts(
        {
            "hyper_tuples": [
                {
                    "event": "ATRI graph RAG Neo4j adoption",
                    "event_type": "Decision",
                    "predicate": "adopted_for",
                    "roles": [
                        {"role": "actor", "entity": "Alice", "entity_type": "Person"},
                        {"role": "tool", "entity": "Neo4j", "entity_type": "Tool"},
                    ],
                    "chain": [
                        {"from_role": "actor", "predicate": "uses", "to_role": "tool"},
                    ],
                }
            ]
        },
        source_id="chunk-hyper-1",
        source_kind="document",
    )

    client.upsert_facts(facts)

    upsert_call = next(call for call in driver.session_obj.calls if "facts" in call["params"])
    queries = "\n".join(call["query"] for call in driver.session_obj.calls)
    chain_row = next(row for row in upsert_call["params"]["facts"] if row["predicate"] == "uses")
    role_row = next(row for row in upsert_call["params"]["facts"] if row["predicate"] == "has_role")
    assert chain_row["chain_id"]
    assert chain_row["chain_ids"] == [chain_row["chain_id"]]
    assert chain_row["chain_order"] == 1
    assert chain_row["chain_order_keys"] == [f"{chain_row['chain_id']}{CHAIN_ORDER_KEY_SEPARATOR}1"]
    assert chain_row["hyper_event"] == "ATRI graph RAG Neo4j adoption"
    assert chain_row["derived_from_hyper_tuple"] is True
    assert role_row["hyper_role"] == "actor"
    assert "r.chain_id = coalesce(fact.chain_id, r.chain_id)" in queries
    assert "r.chain_ids = chain_ids" in queries
    assert "r.chain_order_keys = chain_order_keys" in queries
    assert "WHEN size(chain_order_keys) = 1" in queries
    assert "WHEN size(chain_order_keys) > 1 THEN NULL" in queries
    assert upsert_call["params"]["chain_order_separator"] == CHAIN_ORDER_KEY_SEPARATOR
    assert "r.hyper_event = coalesce(fact.hyper_event, r.hyper_event)" in queries
    assert "r.hyper_role = coalesce(fact.hyper_role, r.hyper_role)" in queries


def test_neo4j_graph_client_indexes_chain_ids_for_fulltext_seed_retrieval():
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )
    facts = normalize_extracted_facts(
        {
            "hyper_tuples": [
                {
                    "event": "ATRI graph RAG Neo4j adoption",
                    "event_type": "Decision",
                    "predicate": "adopted_for",
                    "roles": [
                        {"role": "actor", "entity": "Alice", "entity_type": "Person"},
                        {"role": "tool", "entity": "Neo4j", "entity_type": "Tool"},
                    ],
                    "chain": [
                        {"from_role": "actor", "predicate": "uses", "to_role": "tool"},
                    ],
                }
            ]
        },
        source_id="chunk-hyper-chain-index",
        source_kind="document",
    )

    client.upsert_facts(facts)

    queries = "\n".join(call["query"] for call in driver.session_obj.calls)
    assert "r.chain_ids_text" in queries
    assert "CREATE FULLTEXT INDEX fact_text IF NOT EXISTS" in queries
    assert "r.chain_ids_text" in next(
        call["query"]
        for call in driver.session_obj.calls
        if "CREATE FULLTEXT INDEX fact_text IF NOT EXISTS" in call["query"]
    )
    assert "r.chain_ids_text = reduce" in queries


def test_neo4j_graph_client_retrieves_limited_multihop_context():
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    context = client.retrieve_context(
        query="Alice",
        source_ids=[],
        max_facts=4,
        retrieval_depth=2,
    )

    single_hop_call = _single_hop_retrieve_call(driver.session_obj.calls)
    multi_hop_call = _multi_hop_retrieve_call(driver.session_obj.calls)
    assert single_hop_call["params"]["limit"] == 4
    assert "FACT*1..2" in multi_hop_call["query"]
    assert multi_hop_call["params"]["limit"] == 40
    assert context == format_graph_context(
        [
            (
                "- [1-hop] Alice -[works_at]-> Acme (Alice works at Acme.) "
                "| linked: [2-hop] Acme -[uses]-> Neo4j (Acme uses Neo4j.)"
            ),
        ]
    )


def test_neo4j_graph_client_global_reranks_merged_single_and_multihop_rows():
    class MergedRankingSession(FakeNeo4jSession):
        def run(self, query, **params):
            self.calls.append({"query": query, "params": params})
            if "RETURN s.name AS subject" in query:
                return [
                    {
                        "subject": "Alice",
                        "subject_type": "Person",
                        "predicate": "works_at",
                        "object": "Acme",
                        "object_type": "Company",
                        "evidence": "Low-confidence single-hop evidence.",
                        "confidence": 0.2,
                        "graph_score": 0.1,
                    }
                ]
            if "RETURN startNode(r).name AS subject" in query:
                return [
                    {
                        "subject": "Alice",
                        "subject_type": "Person",
                        "predicate": "works_at",
                        "object": "Acme",
                        "object_type": "Company",
                        "evidence": "High-confidence path evidence.",
                        "confidence": 0.95,
                        "graph_score": 9.0,
                        "hop": 1,
                    },
                    {
                        "subject": "Acme",
                        "subject_type": "Company",
                        "predicate": "uses",
                        "object": "Neo4j",
                        "object_type": "Tool",
                        "evidence": "Acme uses Neo4j.",
                        "confidence": 0.8,
                        "graph_score": 8.0,
                        "hop": 2,
                    },
                ]
            return []

    driver = FakeNeo4jDriver()
    driver.session_obj = MergedRankingSession()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    context = client.retrieve_context(
        query="Alice",
        source_ids=[],
        max_facts=2,
        retrieval_depth=2,
    )

    assert context == format_graph_context(
        [
            (
                "- [1-hop] Alice -[works_at]-> Acme (High-confidence path evidence.) "
                "| linked: [2-hop] Acme -[uses]-> Neo4j (Acme uses Neo4j.)"
            )
        ]
    )


def test_neo4j_graph_client_multihop_expands_from_fulltext_seeds():
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    client.retrieve_context(
        query="Alice Acme Neo4j",
        source_ids=[],
        max_facts=4,
        retrieval_depth=3,
    )

    call = next(call for call in driver.session_obj.calls if "FACT*1..3" in call["query"])
    query = call["query"]
    assert re.search(r"(?m)^\s*CALL\s+\{", query) is None
    assert "CALL () {" in query
    seed_queries = [
        seed_call["query"]
        for seed_call in driver.session_obj.calls
        if "db.index.fulltext.queryNodes" in seed_call["query"]
        or "db.index.fulltext.queryRelationships" in seed_call["query"]
    ]
    assert len(seed_queries) == 2
    assert "UNWIND $entity_seed_rows AS entity_seed" in query
    assert "UNWIND $fact_seed_rows AS fact_seed" in query
    assert "db.index.fulltext.queryNodes" not in query
    assert "db.index.fulltext.queryRelationships" not in query
    assert (
        "UNWIND fact_seeds AS seed\n          WITH seed, fact_seed\n          WHERE seed:Entity"
        in query
    )
    assert "UNWIND fact_seeds AS seed\n          WHERE seed:Entity" not in query
    assert "WITH seed, max(seed_score) AS seed_score" in query
    assert "MATCH path = (seed)-[:FACT*1..3]" in query
    assert "MATCH path = (s:Entity)-[:FACT*1..3]->(o:Entity)" not in query
    assert call["params"]["fulltext_query"] == '"alice" OR "acme" OR "neo4j"'
    assert call["params"]["seed_limit"] >= call["params"]["limit"]


def test_neo4j_graph_client_skips_fulltext_scan_fallback_when_seeded_results_are_enough():
    class NoScanFallbackSession(FakeNeo4jSession):
        def run(self, query, **params):
            self.calls.append({"query": query, "params": params})
            if "MATCH path = (s:Entity)-[:FACT*1..2]->(o:Entity)" in query:
                raise AssertionError("scan fallback should not run when seeded results are enough")
            if "FACT*1..2" in query:
                return [
                    {
                        "subject": "Alice",
                        "subject_type": "Person",
                        "predicate": "works_at",
                        "object": "Acme",
                        "object_type": "Company",
                        "evidence": "Alice works at Acme.",
                        "confidence": 0.9,
                        "hop": 1,
                    },
                    {
                        "subject": "Acme",
                        "subject_type": "Company",
                        "predicate": "uses",
                        "object": "Neo4j",
                        "object_type": "Tool",
                        "evidence": "Acme uses Neo4j.",
                        "confidence": 0.8,
                        "hop": 2,
                    },
                ]
            if "RETURN s.name AS subject" in query:
                return []
            return []

    driver = FakeNeo4jDriver()
    driver.session_obj = NoScanFallbackSession()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    context = client.retrieve_context(
        query="Alice Acme Neo4j",
        source_ids=[],
        max_facts=2,
        retrieval_depth=2,
    )

    retrieve_queries = [
        call["query"]
        for call in driver.session_obj.calls
        if "RETURN s.name AS subject" in call["query"]
        or "RETURN startNode(r).name AS subject" in call["query"]
    ]
    assert len(retrieve_queries) == 2
    assert "MATCH path = (s:Entity)-[:FACT*1..2]->(o:Entity)" not in "\n".join(retrieve_queries)
    assert context == format_graph_context(
        [
            (
                "- [1-hop] Alice -[works_at]-> Acme (Alice works at Acme.) "
                "| linked: [2-hop] Acme -[uses]-> Neo4j (Acme uses Neo4j.)"
            )
        ]
    )


def test_neo4j_graph_client_multihop_rescues_non_seeded_path_term_matches():
    class NonSeededPathMatchSession(FakeNeo4jSession):
        def run(self, query, **params):
            self.calls.append({"query": query, "params": params})
            if "RETURN s.name AS subject" in query:
                return []
            if "MATCH path = (seed)-[:FACT*1..3]" in query:
                return []
            if "MATCH path = (s:Entity)-[:FACT*1..3]->(o:Entity)" in query:
                return [
                    {
                        "subject": "Alpha",
                        "predicate": "routes_through",
                        "object": "TargetNode",
                        "evidence": "Alpha routes through TargetNode.",
                        "hop": 1,
                    },
                    {
                        "subject": "TargetNode",
                        "predicate": "causes",
                        "object": "Incident",
                        "evidence": "TargetNode causes Incident.",
                        "hop": 2,
                    },
                ]
            return []

    driver = FakeNeo4jDriver()
    driver.session_obj = NonSeededPathMatchSession()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    context = client.retrieve_context(
        query="TargetNode",
        source_ids=[],
        max_facts=4,
        retrieval_depth=3,
    )

    scan_call = next(
        call
        for call in driver.session_obj.calls
        if "MATCH path = (s:Entity)-[:FACT*1..3]->(o:Entity)" in call["query"]
    )
    assert "db.index.fulltext" not in scan_call["query"]
    assert "WHERE hop > 1" in scan_call["query"]
    assert scan_call["params"]["limit"] == 40
    assert context == format_graph_context(
        [
            (
                "- [1-hop] Alpha -[routes_through]-> TargetNode "
                "(Alpha routes through TargetNode.) | linked: [2-hop] "
                "TargetNode -[causes]-> Incident (TargetNode causes Incident.)"
            )
        ]
    )


def test_neo4j_graph_client_multihop_scan_filters_use_weighted_query_terms():
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    client.retrieve_context(
        query="Alice Acme Neo4j",
        source_ids=[],
        max_facts=2,
        retrieval_depth=3,
    )

    scan_call = next(
        call
        for call in driver.session_obj.calls
        if "MATCH path = (s:Entity)-[:FACT*1..3]->(o:Entity)" in call["query"]
    )
    scan_query = scan_call["query"]
    assert "any(term_row IN $term_rows WHERE" in scan_query
    assert "toLower(node.name) CONTAINS term_row.term" in scan_query
    assert "toLower(rel.predicate) CONTAINS term_row.term" in scan_query
    assert "any(term IN $terms WHERE" not in scan_query
    assert scan_call["params"]["term_rows"]


def test_neo4j_graph_client_logs_retrieval_metrics(caplog):
    caplog.set_level(logging.INFO, logger="atri")
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    client.retrieve_context(
        query="Alice Acme",
        source_ids=["chunk-1"],
        max_facts=2,
        retrieval_depth=2,
    )

    assert "Neo4j graph retrieval done" in caplog.text
    assert "depth=2" in caplog.text
    assert "source_ids_count=1" in caplog.text
    assert "row_count=" in caplog.text
    assert "returned_facts=" in caplog.text
    assert "used_fulltext=True" in caplog.text
    assert "graph_multihop_seed_count=" in caplog.text
    assert "graph_multihop_cache_hit=" in caplog.text


def test_neo4j_graph_client_records_retrieval_timing_segments():
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    timings: dict[str, Any] = {}
    client.retrieve_context(
        query="Alice Acme",
        source_ids=["chunk-1"],
        max_facts=2,
        retrieval_depth=2,
        timings=timings,
    )

    for key in (
        "graph_total_ms",
        "graph_single_hop_ms",
        "graph_multi_hop_ms",
        "graph_scan_fallback_ms",
        "graph_format_ms",
    ):
        assert key in timings
        assert timings[key] >= 0
    assert timings["graph_rows"] >= 1
    assert timings["graph_returned_facts"] >= 1
    assert timings["graph_used_fulltext"] is True
    assert timings["graph_multihop_seed_count"] == 0
    assert timings["graph_multihop_cache_hit"] is False
    assert timings["graph_multihop_cached_seed_count"] == 0
    assert timings["graph_multihop_live_seed_limit"] == 40
    assert timings["graph_multihop_partial_cache_hit"] is False
    assert timings["graph_multihop_persistent_cache_hit_count"] == 0


def test_neo4j_graph_client_reuses_final_context_cache_until_graph_revision_changes():
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    context = client.retrieve_context(
        query="Alice Acme",
        source_ids=[],
        max_facts=3,
        retrieval_depth=2,
    )
    retrieve_count = len(_retrieve_calls(driver.session_obj.calls))

    cached_context = client.retrieve_context(
        query="Alice Acme",
        source_ids=[],
        max_facts=3,
        retrieval_depth=2,
    )

    assert cached_context == context
    assert len(_retrieve_calls(driver.session_obj.calls)) == retrieve_count

    fact = normalize_extracted_facts(
        [
            {
                "subject": "Carol",
                "subject_type": "Person",
                "predicate": "uses",
                "object": "Redis",
                "object_type": "Tool",
            }
        ],
        source_id="chunk-cache-bust",
        source_kind="document",
    )[0]
    client.upsert_facts([fact])
    client.retrieve_context(
        query="Alice Acme",
        source_ids=[],
        max_facts=3,
        retrieval_depth=2,
    )

    assert len(_retrieve_calls(driver.session_obj.calls)) > retrieve_count


def test_neo4j_graph_client_cache_mode_change_busts_final_context_cache():
    driver = FakeNeo4jDriver()
    base_config = {
        "enabled": True,
        "uri": "bolt://localhost:7687",
        "username": "neo4j",
        "password": "secret",
        "database": "atri",
        "multi_hop_expansion_cache_mode": "persistent",
    }
    client = Neo4jGraphClient(
        base_config,
        driver_factory=lambda uri, auth: driver,
    )

    context = client.retrieve_context(
        query="Alice Acme",
        source_ids=[],
        max_facts=3,
        retrieval_depth=2,
    )
    retrieve_count = len(_retrieve_calls(driver.session_obj.calls))

    client.update_config({**base_config, "multi_hop_expansion_cache_mode": "off"})
    context_after_mode_change = client.retrieve_context(
        query="Alice Acme",
        source_ids=[],
        max_facts=3,
        retrieval_depth=2,
    )

    assert context_after_mode_change == context
    assert len(_retrieve_calls(driver.session_obj.calls)) > retrieve_count


@pytest.mark.parametrize(
    ("field", "updated_value"),
    [
        ("multi_hop_expansion_cache_path_limit", 500),
        ("multi_hop_expansion_cache_preload_path_limit", 500),
    ],
)
def test_neo4j_graph_client_multihop_cache_limit_change_busts_final_context_cache(
    field, updated_value
):
    driver = FakeNeo4jDriver()
    base_config = {
        "enabled": True,
        "uri": "bolt://localhost:7687",
        "username": "neo4j",
        "password": "secret",
        "database": "atri",
        "multi_hop_expansion_cache_mode": "persistent",
        "multi_hop_expansion_cache_path_limit": 1000,
        "multi_hop_expansion_cache_preload_path_limit": 200,
    }
    client = Neo4jGraphClient(
        base_config,
        driver_factory=lambda uri, auth: driver,
    )

    context = client.retrieve_context(
        query="Alice Acme",
        source_ids=[],
        max_facts=3,
        retrieval_depth=2,
    )
    retrieve_count = len(_retrieve_calls(driver.session_obj.calls))

    client.update_config({**base_config, field: updated_value})
    context_after_limit_change = client.retrieve_context(
        query="Alice Acme",
        source_ids=[],
        max_facts=3,
        retrieval_depth=2,
    )

    assert context_after_limit_change == context
    assert len(_retrieve_calls(driver.session_obj.calls)) > retrieve_count


def test_neo4j_graph_client_refreshes_external_revision_before_final_context_cache_hit():
    class ExternalRevisionSession(FakeNeo4jSession):
        def __init__(self):
            super().__init__()
            self.graph_revision = 0

        def run(self, query, **params):
            self.calls.append({"query": query, "params": params})
            if "GraphMetadata" in query and "RETURN meta.value AS value" in query:
                return [{"value": self.graph_revision}]
            if "RETURN s.name AS subject" in query:
                if self.graph_revision == 0:
                    return [
                        {
                            "subject": "Alice",
                            "subject_type": "Person",
                            "predicate": "works_at",
                            "object": "Acme",
                            "object_type": "Company",
                            "evidence": "Alice works at Acme.",
                            "confidence": 0.9,
                        }
                    ]
                return [
                    {
                        "subject": "Bob",
                        "subject_type": "Person",
                        "predicate": "uses",
                        "object": "Redis",
                        "object_type": "Tool",
                        "evidence": "Bob uses Redis.",
                        "confidence": 0.8,
                    }
                ]
            return []

    driver = FakeNeo4jDriver()
    driver.session_obj = ExternalRevisionSession()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    first_context = client.retrieve_context(
        query="Alice Acme",
        source_ids=[],
        max_facts=1,
        retrieval_depth=1,
    )
    retrieve_count = len(_retrieve_calls(driver.session_obj.calls))

    driver.session_obj.graph_revision = 1
    second_context = client.retrieve_context(
        query="Alice Acme",
        source_ids=[],
        max_facts=1,
        retrieval_depth=1,
    )

    assert first_context == format_graph_context(
        ["- Alice -[works_at]-> Acme (Alice works at Acme.)"]
    )
    assert second_context == format_graph_context(["- Bob -[uses]-> Redis (Bob uses Redis.)"])
    assert len(_retrieve_calls(driver.session_obj.calls)) > retrieve_count
    assert client._graph_revision == 1


def test_neo4j_graph_client_atomically_increments_graph_revision_across_clients():
    class SharedRevisionSession(FakeNeo4jSession):
        def __init__(self):
            super().__init__()
            self.graph_revision = 0

        def run(self, query, **params):
            self.calls.append({"query": query, "params": params})
            if (
                "MERGE (meta:GraphMetadata {key: $key})" in query
                and "RETURN meta.value AS value" in query
            ):
                if "coalesce(meta.value, 0) + 1" in query:
                    self.graph_revision += 1
                return [{"value": self.graph_revision}]
            if "MERGE (meta:GraphMetadata {key: $key})" in query and "value" in params:
                self.graph_revision = int(params["value"])
                return []
            if "facts" in params:
                return [{"count": 1, "seed_element_ids": ["seed-alice"]}]
            return []

    driver = FakeNeo4jDriver()
    driver.session_obj = SharedRevisionSession()
    config = {
        "enabled": True,
        "uri": "bolt://localhost:7687",
        "username": "neo4j",
        "password": "secret",
        "database": "atri",
        "multi_hop_expansion_cache_prewarm_seed_limit": 0,
    }
    first_client = Neo4jGraphClient(config, driver_factory=lambda uri, auth: driver)
    second_client = Neo4jGraphClient(config, driver_factory=lambda uri, auth: driver)
    first_fact = normalize_extracted_facts(
        [
            {
                "subject": "Alice",
                "subject_type": "Person",
                "predicate": "works_at",
                "object": "Acme",
                "object_type": "Company",
            }
        ],
        source_id="chunk-revision-1",
        source_kind="document",
    )[0]
    second_fact = normalize_extracted_facts(
        [
            {
                "subject": "Bob",
                "subject_type": "Person",
                "predicate": "uses",
                "object": "Neo4j",
                "object_type": "Tool",
            }
        ],
        source_id="chunk-revision-2",
        source_kind="document",
    )[0]

    first_client.initialize()
    second_client.initialize()
    assert first_client._graph_revision == 0
    assert second_client._graph_revision == 0

    first_client.upsert_facts([first_fact])
    second_client.upsert_facts([second_fact])

    assert driver.session_obj.graph_revision == 2
    assert first_client._graph_revision == 1
    assert second_client._graph_revision == 2


def test_neo4j_graph_client_prunes_stale_persistent_expansion_cache_after_revision_bump():
    class StaleExpansionCacheSession(FakeNeo4jSession):
        def __init__(self):
            super().__init__()
            self.graph_revision = 1
            self.persistent_cache: dict[str, dict[str, Any]] = {
                "revision-0": {"key": "revision-0", "graph_revision": 0},
                "revision-1": {"key": "revision-1", "graph_revision": 1},
                "revision-2": {"key": "revision-2", "graph_revision": 2},
            }

        def run(self, query, **params):
            self.calls.append({"query": query, "params": params})
            if (
                "MERGE (meta:GraphMetadata {key: $key})" in query
                and "RETURN meta.value AS value" in query
            ):
                if "coalesce(meta.value, 0) + 1" in query:
                    self.graph_revision += 1
                return [{"value": self.graph_revision}]
            if "MATCH (cache:GraphExpansionCache)" in query and "DELETE cache" in query:
                current_revision = int(params["current_revision"])
                self.persistent_cache = {
                    key: value
                    for key, value in self.persistent_cache.items()
                    if int(value.get("graph_revision", -1)) >= current_revision
                }
                return []
            if "facts" in params:
                return [{"count": 1, "seed_element_ids": ["seed-alice"]}]
            return []

    driver = FakeNeo4jDriver()
    driver.session_obj = StaleExpansionCacheSession()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
            "multi_hop_expansion_cache_prewarm_seed_limit": 0,
        },
        driver_factory=lambda uri, auth: driver,
    )
    fact = normalize_extracted_facts(
        [
            {
                "subject": "Alice",
                "subject_type": "Person",
                "predicate": "works_at",
                "object": "Acme",
                "object_type": "Company",
            }
        ],
        source_id="chunk-prune-cache",
        source_kind="document",
    )[0]

    client.initialize()
    client.upsert_facts([fact])

    prune_calls = [
        call
        for call in driver.session_obj.calls
        if "MATCH (cache:GraphExpansionCache)" in call["query"] and "DELETE cache" in call["query"]
    ]
    assert client._graph_revision == 2
    assert list(driver.session_obj.persistent_cache) == ["revision-2"]
    assert prune_calls
    assert prune_calls[0]["params"]["current_revision"] == 2
    assert prune_calls[0]["params"]["graph_revision_property"] == "graph_revision"
    assert "properties(cache)" in prune_calls[0]["query"]
    assert ".graph_revision" not in prune_calls[0]["query"]


def test_neo4j_graph_client_clears_and_skips_persistent_prewarm_when_revision_bump_fails():
    class FailedRevisionBumpSession(FakeNeo4jSession):
        def __init__(self):
            super().__init__()
            self.graph_revision = 0
            self.persistent_cache: dict[str, dict[str, Any]] = {
                "stale": {"key": "stale", "graph_revision": 0}
            }
            self.persistent_writes: list[dict[str, Any]] = []

        def run(self, query, **params):
            self.calls.append({"query": query, "params": params})
            if (
                "MERGE (meta:GraphMetadata {key: $key})" in query
                and "RETURN meta.value AS value" in query
            ):
                if "coalesce(meta.value, 0) + 1" in query:
                    raise RuntimeError("metadata write failed")
                return [{"value": self.graph_revision}]
            if "MATCH (cache:GraphExpansionCache)" in query and "DELETE cache" in query:
                self.persistent_cache.clear()
                return []
            if "facts" in params:
                return [{"count": 1, "seed_element_ids": ["seed-alice"]}]
            if "RETURN seed_element_id, paths" in query:
                return [
                    {
                        "seed_element_id": "seed-alice",
                        "seed_name_key": "alice",
                        "seed_type_key": "person",
                        "paths": [
                            {
                                "hop": 2,
                                "rel_ids": [
                                    {
                                        "element_id": "rel-alice-acme",
                                        "fact_key": "person:alice|works_at|company:acme",
                                        "rel_index": 0,
                                    },
                                    {
                                        "element_id": "rel-acme-neo4j",
                                        "fact_key": "company:acme|uses|tool:neo4j",
                                        "rel_index": 1,
                                    },
                                ],
                            }
                        ],
                        "incomplete": False,
                    }
                ]
            if "expansion_cache_entries" in params:
                self.persistent_writes.extend(params["expansion_cache_entries"])
                return []
            return []

    driver = FakeNeo4jDriver()
    driver.session_obj = FailedRevisionBumpSession()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
            "retrieval_depth": 2,
        },
        driver_factory=lambda uri, auth: driver,
    )
    fact = normalize_extracted_facts(
        [
            {
                "subject": "Alice",
                "subject_type": "Person",
                "predicate": "works_at",
                "object": "Acme",
                "object_type": "Company",
            }
        ],
        source_id="chunk-revision-failure",
        source_kind="document",
    )[0]

    client.upsert_facts([fact])

    clear_calls = [
        call
        for call in driver.session_obj.calls
        if "MATCH (cache:GraphExpansionCache)" in call["query"] and "DELETE cache" in call["query"]
    ]
    assert client._graph_revision == 1
    assert clear_calls
    assert driver.session_obj.persistent_cache == {}
    assert driver.session_obj.persistent_writes == []


def test_neo4j_graph_client_caches_fulltext_seed_rows_across_context_cache_misses():
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    client.retrieve_context(
        query="Alice Acme",
        source_ids=[],
        max_facts=2,
        retrieval_depth=2,
    )
    seed_call_count = sum(
        1
        for call in driver.session_obj.calls
        if "db.index.fulltext.queryNodes" in call["query"]
        or "db.index.fulltext.queryRelationships" in call["query"]
    )

    client.retrieve_context(
        query="Alice Acme",
        source_ids=[],
        max_facts=3,
        retrieval_depth=2,
    )

    assert seed_call_count == 2
    assert (
        sum(
            1
            for call in driver.session_obj.calls
            if "db.index.fulltext.queryNodes" in call["query"]
            or "db.index.fulltext.queryRelationships" in call["query"]
        )
        == seed_call_count
    )


def test_neo4j_graph_client_caches_multihop_expansion_across_context_cache_misses():
    class ExpansionCacheSession(FakeNeo4jSession):
        def run(self, query, **params):
            self.calls.append({"query": query, "params": params})
            if "db.index.fulltext.queryNodes" in query:
                return [{"element_id": "seed-alice", "score": 4.0}]
            if "db.index.fulltext.queryRelationships" in query:
                return []
            if "RETURN element_id, seed_score" in query:
                return [{"element_id": "seed-alice", "seed_score": 4.0}]
            if "RETURN seed_element_id, paths" in query:
                return [
                    {
                        "seed_element_id": "seed-alice",
                        "paths": [
                            {
                                "hop": 2,
                                "rel_ids": [
                                    {"element_id": "rel-alice-acme", "rel_index": 0},
                                    {"element_id": "rel-acme-neo4j", "rel_index": 1},
                                ],
                            }
                        ],
                        "incomplete": False,
                    }
                ]
            if "RETURN startNode(r).name AS subject" in query:
                return [
                    {
                        "subject": "Acme",
                        "subject_type": "Company",
                        "predicate": "uses",
                        "object": "Neo4j",
                        "object_type": "Tool",
                        "evidence": "Acme uses Neo4j.",
                        "confidence": 0.8,
                        "hop": 2,
                    }
                ]
            if "RETURN s.name AS subject" in query:
                return [
                    {
                        "subject": "Alice",
                        "subject_type": "Person",
                        "predicate": "works_at",
                        "object": "Acme",
                        "object_type": "Company",
                        "evidence": "Alice works at Acme.",
                        "confidence": 0.9,
                    }
                ]
            return []

    driver = FakeNeo4jDriver()
    driver.session_obj = ExpansionCacheSession()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    first_context = client.retrieve_context(
        query="Alice Acme",
        source_ids=[],
        max_facts=2,
        retrieval_depth=2,
    )
    second_context = client.retrieve_context(
        query="Alice Acme",
        source_ids=[],
        max_facts=3,
        retrieval_depth=2,
    )

    assert second_context == first_context
    expansion_calls = [
        call
        for call in driver.session_obj.calls
        if "RETURN seed_element_id, paths" in call["query"]
    ]
    cached_retrieval_calls = [
        call
        for call in driver.session_obj.calls
        if "UNWIND $cached_expansion_paths AS cached_path" in call["query"]
        and "RETURN startNode(r).name AS subject" in call["query"]
    ]
    direct_expansion_retrieval_calls = [
        call
        for call in driver.session_obj.calls
        if "MATCH path = (seed)-[:FACT*1..2]" in call["query"]
        and "RETURN startNode(r).name AS subject" in call["query"]
    ]

    assert len(expansion_calls) == 1
    assert len(cached_retrieval_calls) == 2
    assert direct_expansion_retrieval_calls == []
    assert cached_retrieval_calls[0]["params"]["cached_expansion_seed_rows"] == [
        {"element_id": "seed-alice", "seed_score": 4.0}
    ]
    assert cached_retrieval_calls[0]["params"]["cached_expansion_paths"] == [
        {
            "seed_element_id": "seed-alice",
            "path_key": "seed-alice:0",
            "hop": 2,
            "rel_ids": [
                {"element_id": "rel-alice-acme", "rel_index": 0},
                {"element_id": "rel-acme-neo4j", "rel_index": 1},
            ],
        }
    ]


def test_neo4j_graph_client_memory_cache_mode_does_not_touch_persistent_expansion_cache():
    class MemoryExpansionCacheSession(FakeNeo4jSession):
        def run(self, query, **params):
            self.calls.append({"query": query, "params": params})
            if "MATCH (cache:GraphExpansionCache)" in query:
                return []
            if "expansion_cache_entries" in params:
                return []
            if "db.index.fulltext.queryNodes" in query:
                return [{"element_id": "seed-alice", "score": 4.0}]
            if "db.index.fulltext.queryRelationships" in query:
                return []
            if "RETURN element_id, seed_score" in query:
                return [{"element_id": "seed-alice", "seed_score": 4.0}]
            if "RETURN seed_element_id" in query and "seed.name_key AS seed_name_key" in query:
                return [
                    {
                        "seed_element_id": "seed-alice",
                        "seed_name_key": "alice",
                        "seed_type_key": "person",
                    }
                ]
            if "RETURN seed_element_id, paths" in query:
                return [
                    {
                        "seed_element_id": "seed-alice",
                        "seed_name_key": "alice",
                        "seed_type_key": "person",
                        "paths": [
                            {
                                "hop": 2,
                                "rel_ids": [
                                    {
                                        "element_id": "rel-alice-acme",
                                        "fact_key": "person:alice|works_at|company:acme",
                                        "rel_index": 0,
                                    },
                                    {
                                        "element_id": "rel-acme-neo4j",
                                        "fact_key": "company:acme|uses|tool:neo4j",
                                        "rel_index": 1,
                                    },
                                ],
                            }
                        ],
                        "incomplete": False,
                    }
                ]
            if "RETURN startNode(r).name AS subject" in query:
                return [
                    {
                        "subject": "Acme",
                        "subject_type": "Company",
                        "predicate": "uses",
                        "object": "Neo4j",
                        "object_type": "Tool",
                        "evidence": "Acme uses Neo4j.",
                        "confidence": 0.8,
                        "hop": 2,
                    }
                ]
            if "RETURN s.name AS subject" in query:
                return []
            return []

    driver = FakeNeo4jDriver()
    driver.session_obj = MemoryExpansionCacheSession()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
            "multi_hop_expansion_cache_mode": "memory",
        },
        driver_factory=lambda uri, auth: driver,
    )

    first_context = client.retrieve_context(
        query="Alice Acme",
        source_ids=[],
        max_facts=2,
        retrieval_depth=2,
    )
    second_context = client.retrieve_context(
        query="Alice Acme",
        source_ids=[],
        max_facts=3,
        retrieval_depth=2,
    )

    assert second_context == first_context
    persistent_calls = [
        call
        for call in driver.session_obj.calls
        if "MATCH (cache:GraphExpansionCache)" in call["query"]
        or "expansion_cache_entries" in call["params"]
    ]
    expansion_calls = [
        call
        for call in driver.session_obj.calls
        if "RETURN seed_element_id, paths" in call["query"]
    ]
    assert persistent_calls == []
    assert len(expansion_calls) == 1


def test_neo4j_graph_client_off_cache_mode_uses_live_multihop_without_expansion_cache():
    class OffExpansionCacheSession(FakeNeo4jSession):
        def run(self, query, **params):
            self.calls.append({"query": query, "params": params})
            if "db.index.fulltext.queryNodes" in query:
                return [{"element_id": "seed-alice", "score": 4.0}]
            if "db.index.fulltext.queryRelationships" in query:
                return []
            if "RETURN element_id, seed_score" in query:
                return [{"element_id": "seed-alice", "seed_score": 4.0}]
            if "RETURN seed_element_id, paths" in query:
                return [
                    {
                        "seed_element_id": "seed-alice",
                        "paths": [
                            {
                                "hop": 2,
                                "rel_ids": [
                                    {"element_id": "rel-alice-acme", "rel_index": 0},
                                    {"element_id": "rel-acme-neo4j", "rel_index": 1},
                                ],
                            }
                        ],
                        "incomplete": False,
                    }
                ]
            if "RETURN startNode(r).name AS subject" in query:
                return [
                    {
                        "subject": "Acme",
                        "subject_type": "Company",
                        "predicate": "uses",
                        "object": "Neo4j",
                        "object_type": "Tool",
                        "evidence": "Acme uses Neo4j.",
                        "confidence": 0.8,
                        "hop": 2,
                    }
                ]
            if "RETURN s.name AS subject" in query:
                return []
            return []

    driver = FakeNeo4jDriver()
    driver.session_obj = OffExpansionCacheSession()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
            "multi_hop_expansion_cache_mode": "off",
        },
        driver_factory=lambda uri, auth: driver,
    )

    context = client.retrieve_context(
        query="Alice Acme",
        source_ids=[],
        max_facts=2,
        retrieval_depth=2,
    )

    expansion_calls = [
        call
        for call in driver.session_obj.calls
        if "RETURN seed_element_id, paths" in call["query"]
    ]
    retrieve_call = _multi_hop_retrieve_call(driver.session_obj.calls)
    assert context == format_graph_context(["- [2-hop] Acme -[uses]-> Neo4j (Acme uses Neo4j.)"])
    assert expansion_calls == []
    assert "UNWIND $cached_expansion_paths AS cached_path" not in retrieve_call["query"]
    assert "MATCH path = (seed)-[:FACT*1..2]" in retrieve_call["query"]


def test_neo4j_graph_client_prewarms_multihop_expansion_after_upsert():
    class PrewarmExpansionSession(FakeNeo4jSession):
        def run(self, query, **params):
            self.calls.append({"query": query, "params": params})
            if "facts" in params:
                return [{"count": 1, "seed_element_ids": ["seed-alice"]}]
            if "RETURN seed_element_id, paths" in query:
                return [
                    {
                        "seed_element_id": "seed-alice",
                        "paths": [
                            {
                                "hop": 2,
                                "rel_ids": [
                                    {"element_id": "rel-alice-acme", "rel_index": 0},
                                    {"element_id": "rel-acme-neo4j", "rel_index": 1},
                                ],
                            }
                        ],
                        "incomplete": False,
                    }
                ]
            if "db.index.fulltext.queryNodes" in query:
                return [{"element_id": "seed-alice", "score": 4.0}]
            if "db.index.fulltext.queryRelationships" in query:
                return []
            if "RETURN element_id, seed_score" in query:
                return [{"element_id": "seed-alice", "seed_score": 4.0}]
            if "RETURN startNode(r).name AS subject" in query:
                return [
                    {
                        "subject": "Acme",
                        "predicate": "uses",
                        "object": "Neo4j",
                        "evidence": "Acme uses Neo4j.",
                        "hop": 2,
                    }
                ]
            if "RETURN s.name AS subject" in query:
                return []
            return []

    driver = FakeNeo4jDriver()
    driver.session_obj = PrewarmExpansionSession()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
            "retrieval_depth": 2,
        },
        driver_factory=lambda uri, auth: driver,
    )
    fact = normalize_extracted_facts(
        [
            {
                "subject": "Alice",
                "subject_type": "Person",
                "predicate": "works_at",
                "object": "Acme",
                "object_type": "Company",
            }
        ],
        source_id="chunk-prewarm",
        source_kind="document",
    )[0]

    client.upsert_facts([fact])
    prewarm_call_count = sum(
        1 for call in driver.session_obj.calls if "RETURN seed_element_id, paths" in call["query"]
    )
    timings: dict[str, Any] = {}
    context = client.retrieve_context(
        query="Alice Acme",
        source_ids=[],
        max_facts=2,
        retrieval_depth=2,
        timings=timings,
    )

    retrieval_call = _multi_hop_retrieve_call(driver.session_obj.calls)
    assert prewarm_call_count == 1
    assert (
        sum(
            1
            for call in driver.session_obj.calls
            if "RETURN seed_element_id, paths" in call["query"]
        )
        == prewarm_call_count
    )
    assert "UNWIND $cached_expansion_paths AS cached_path" in retrieval_call["query"]
    assert "MATCH path = (seed)-[:FACT*1..2]" not in retrieval_call["query"]
    assert retrieval_call["params"]["cached_expansion_paths"] == [
        {
            "seed_element_id": "seed-alice",
            "path_key": "seed-alice:0",
            "hop": 2,
            "rel_ids": [
                {"element_id": "rel-alice-acme", "rel_index": 0},
                {"element_id": "rel-acme-neo4j", "rel_index": 1},
            ],
        }
    ]
    assert timings["graph_multihop_seed_count"] == 1
    assert timings["graph_multihop_cache_hit"] is True
    assert timings["graph_multihop_cached_seed_count"] == 1
    assert timings["graph_multihop_live_seed_limit"] == 0
    assert timings["graph_multihop_partial_cache_hit"] is False
    assert context == format_graph_context(["- [2-hop] Acme -[uses]-> Neo4j (Acme uses Neo4j.)"])


def test_neo4j_graph_client_prewarm_uses_preload_path_budget_per_seed():
    class BudgetedPrewarmSession(FakeNeo4jSession):
        def __init__(self):
            super().__init__()
            self.graph_revision = 0

        def run(self, query, **params):
            self.calls.append({"query": query, "params": params})
            if (
                "MERGE (meta:GraphMetadata {key: $key})" in query
                and "RETURN meta.value AS value" in query
            ):
                if "coalesce(meta.value, 0) + 1" in query:
                    self.graph_revision += 1
                return [{"value": self.graph_revision}]
            if "MATCH (cache:GraphExpansionCache)" in query and "DELETE cache" in query:
                return []
            if "facts" in params:
                return [
                    {
                        "count": 1,
                        "seed_element_ids": [f"seed-{index}" for index in range(80)],
                    }
                ]
            if "RETURN seed_element_id, paths" in query:
                return []
            return []

    driver = FakeNeo4jDriver()
    driver.session_obj = BudgetedPrewarmSession()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
            "retrieval_depth": 3,
        },
        driver_factory=lambda uri, auth: driver,
    )
    fact = normalize_extracted_facts(
        [
            {
                "subject": "Alice",
                "subject_type": "Person",
                "predicate": "works_at",
                "object": "Acme",
                "object_type": "Company",
            }
        ],
        source_id="chunk-prewarm-budget",
        source_kind="document",
    )[0]

    client.upsert_facts([fact])

    expansion_calls = [
        call
        for call in driver.session_obj.calls
        if "RETURN seed_element_id, paths" in call["query"]
    ]
    assert len(expansion_calls) == 2
    for call in expansion_calls:
        assert call["params"]["seed_element_ids"] == [f"seed-{index}" for index in range(64)]
        assert call["params"]["path_limit_plus_one"] == 4


def test_neo4j_graph_client_reuses_prewarm_cache_when_query_seed_window_is_smaller():
    class ManyPrewarmSeedsSession(FakeNeo4jSession):
        def __init__(self):
            super().__init__()
            self.graph_revision = 0

        def run(self, query, **params):
            self.calls.append({"query": query, "params": params})
            if "GraphMetadata" in query and "RETURN meta.value AS value" in query:
                if "coalesce(meta.value, 0) + 1" in query:
                    self.graph_revision += 1
                return [{"value": self.graph_revision}]
            if "facts" in params:
                return [
                    {
                        "count": 1,
                        "seed_element_ids": [f"seed-{index}" for index in range(80)],
                    }
                ]
            if "db.index.fulltext.queryNodes" in query:
                return [{"element_id": "seed-0", "score": 4.0}]
            if "db.index.fulltext.queryRelationships" in query:
                return []
            if "RETURN element_id, seed_score" in query:
                return [{"element_id": "seed-0", "seed_score": 4.0}]
            if "RETURN seed_element_id, paths" in query:
                return [
                    {
                        "seed_element_id": seed_element_id,
                        "paths": [
                            {
                                "hop": 2,
                                "rel_ids": [
                                    {
                                        "element_id": f"rel-{seed_element_id}-0",
                                        "rel_index": 0,
                                    },
                                    {
                                        "element_id": f"rel-{seed_element_id}-1",
                                        "rel_index": 1,
                                    },
                                ],
                            }
                        ],
                        "incomplete": False,
                    }
                    for seed_element_id in params["seed_element_ids"]
                ]
            if "RETURN startNode(r).name AS subject" in query:
                return [
                    {
                        "subject": "Acme",
                        "predicate": "uses",
                        "object": "Neo4j",
                        "evidence": "Acme uses Neo4j.",
                        "hop": 2,
                    }
                ]
            if "RETURN s.name AS subject" in query:
                return []
            return []

    driver = FakeNeo4jDriver()
    driver.session_obj = ManyPrewarmSeedsSession()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
            "retrieval_depth": 2,
            "multi_hop_expansion_cache_mode": "memory",
        },
        driver_factory=lambda uri, auth: driver,
    )
    fact = normalize_extracted_facts(
        [
            {
                "subject": "Alice",
                "subject_type": "Person",
                "predicate": "works_at",
                "object": "Acme",
                "object_type": "Company",
            }
        ],
        source_id="chunk-prewarm-many-seeds",
        source_kind="document",
    )[0]

    client.upsert_facts([fact])
    prewarm_expansion_calls = [
        call
        for call in driver.session_obj.calls
        if "RETURN seed_element_id, paths" in call["query"]
    ]
    timings: dict[str, Any] = {}
    context = client.retrieve_context(
        query="Alice Acme",
        source_ids=[],
        max_facts=2,
        retrieval_depth=2,
        timings=timings,
    )

    expansion_calls = [
        call
        for call in driver.session_obj.calls
        if "RETURN seed_element_id, paths" in call["query"]
    ]
    retrieval_call = _multi_hop_retrieve_call(driver.session_obj.calls)
    assert len(prewarm_expansion_calls) == 1
    assert prewarm_expansion_calls[0]["params"]["path_limit"] == 3
    assert len(expansion_calls) == 1
    assert "UNWIND $cached_expansion_paths AS cached_path" in retrieval_call["query"]
    assert "MATCH path = (seed)-[:FACT*1..2]" not in retrieval_call["query"]
    assert timings["graph_multihop_cache_hit"] is True
    assert context == format_graph_context(["- [2-hop] Acme -[uses]-> Neo4j (Acme uses Neo4j.)"])


def test_neo4j_graph_client_partially_preloads_multihop_expansion_when_seed_count_is_large():
    class LargeSeedExpansionSession(FakeNeo4jSession):
        def run(self, query, **params):
            self.calls.append({"query": query, "params": params})
            if "db.index.fulltext.queryNodes" in query:
                return [
                    {"element_id": f"seed-{index}", "score": 4.0 - index * 0.1}
                    for index in range(3)
                ]
            if "db.index.fulltext.queryRelationships" in query:
                return []
            if "RETURN element_id, seed_score" in query:
                return [
                    {"element_id": f"seed-{index}", "seed_score": 4.0 - index * 0.1}
                    for index in range(3)
                ]
            if "RETURN seed_element_id, paths" in query:
                return [
                    {
                        "seed_element_id": seed_element_id,
                        "paths": [
                            {
                                "hop": 2,
                                "rel_ids": [
                                    {
                                        "element_id": f"rel-{seed_element_id}-0",
                                        "rel_index": 0,
                                    },
                                    {
                                        "element_id": f"rel-{seed_element_id}-1",
                                        "rel_index": 1,
                                    },
                                ],
                            }
                        ],
                        "incomplete": False,
                    }
                    for seed_element_id in params["seed_element_ids"]
                ]
            if "RETURN startNode(r).name AS subject" in query:
                return [
                    {
                        "subject": "Acme",
                        "predicate": "uses",
                        "object": "Neo4j",
                        "evidence": "Acme uses Neo4j.",
                        "hop": 2,
                    }
                ]
            if "RETURN s.name AS subject" in query:
                return []
            return []

    driver = FakeNeo4jDriver()
    driver.session_obj = LargeSeedExpansionSession()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
            "multi_hop_expansion_cache_preload_seed_limit": 2,
        },
        driver_factory=lambda uri, auth: driver,
    )

    timings: dict[str, Any] = {}
    context = client.retrieve_context(
        query="Alice Acme",
        source_ids=[],
        max_facts=2,
        retrieval_depth=2,
        timings=timings,
    )

    assert context == format_graph_context(["- [2-hop] Acme -[uses]-> Neo4j (Acme uses Neo4j.)"])
    expansion_call = next(
        call
        for call in driver.session_obj.calls
        if "RETURN seed_element_id, paths" in call["query"]
    )
    assert expansion_call["params"]["seed_element_ids"] == ["seed-0", "seed-1"]
    seed_probe_call = next(
        call
        for call in driver.session_obj.calls
        if "RETURN element_id, seed_score" in call["query"]
    )
    assert seed_probe_call["params"]["seed_limit"] == 3
    retrieve_call = _multi_hop_retrieve_call(driver.session_obj.calls)
    assert "UNWIND $cached_expansion_paths AS cached_path" in retrieve_call["query"]
    assert "MATCH path = (seed)-[:FACT*1..2]" in retrieve_call["query"]
    assert "LIMIT $live_seed_limit" in retrieve_call["query"]
    assert retrieve_call["params"]["cached_expansion_seed_ids"] == ["seed-0", "seed-1"]
    assert retrieve_call["params"]["live_seed_limit"] == 38
    assert retrieve_call["params"]["cached_expansion_seed_rows"] == [
        {"element_id": "seed-0", "seed_score": 4.0},
        {"element_id": "seed-1", "seed_score": 3.9},
    ]
    assert timings["graph_multihop_seed_count"] == 3
    assert timings["graph_multihop_cache_hit"] is False
    assert timings["graph_multihop_cached_seed_count"] == 2
    assert timings["graph_multihop_partial_cache_hit"] is True
    assert timings["graph_multihop_persistent_cache_hit_count"] == 0


def test_neo4j_graph_client_uses_complete_cached_multihop_seed_subset():
    class PartialCompleteExpansionSession(FakeNeo4jSession):
        def run(self, query, **params):
            self.calls.append({"query": query, "params": params})
            if "db.index.fulltext.queryNodes" in query:
                return [
                    {"element_id": "seed-alice", "score": 4.0},
                    {"element_id": "seed-acme", "score": 3.0},
                ]
            if "db.index.fulltext.queryRelationships" in query:
                return []
            if "RETURN element_id, seed_score" in query:
                return [
                    {"element_id": "seed-alice", "seed_score": 4.0},
                    {"element_id": "seed-acme", "seed_score": 3.0},
                ]
            if "RETURN seed_element_id, paths" in query:
                return [
                    {
                        "seed_element_id": "seed-alice",
                        "paths": [
                            {
                                "hop": 2,
                                "rel_ids": [
                                    {"element_id": "rel-alice-acme", "rel_index": 0},
                                    {"element_id": "rel-acme-neo4j", "rel_index": 1},
                                ],
                            }
                        ],
                        "incomplete": False,
                    },
                    {
                        "seed_element_id": "seed-acme",
                        "paths": [],
                        "incomplete": True,
                    },
                ]
            if "RETURN startNode(r).name AS subject" in query:
                return [
                    {
                        "subject": "Acme",
                        "predicate": "uses",
                        "object": "Neo4j",
                        "evidence": "Acme uses Neo4j.",
                        "hop": 2,
                    }
                ]
            if "RETURN s.name AS subject" in query:
                return []
            return []

    driver = FakeNeo4jDriver()
    driver.session_obj = PartialCompleteExpansionSession()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
            "multi_hop_expansion_cache_mode": "memory",
        },
        driver_factory=lambda uri, auth: driver,
    )

    timings: dict[str, Any] = {}
    context = client.retrieve_context(
        query="Alice Acme",
        source_ids=[],
        max_facts=2,
        retrieval_depth=2,
        timings=timings,
    )

    assert context == format_graph_context(["- [2-hop] Acme -[uses]-> Neo4j (Acme uses Neo4j.)"])
    retrieve_call = _multi_hop_retrieve_call(driver.session_obj.calls)
    assert "UNWIND $cached_expansion_paths AS cached_path" in retrieve_call["query"]
    assert "MATCH path = (seed)-[:FACT*1..2]" in retrieve_call["query"]
    assert retrieve_call["params"]["cached_expansion_seed_ids"] == ["seed-alice"]
    assert retrieve_call["params"]["cached_expansion_seed_rows"] == [
        {"element_id": "seed-alice", "seed_score": 4.0}
    ]
    assert retrieve_call["params"]["live_seed_limit"] == 1
    assert timings["graph_multihop_seed_count"] == 2
    assert timings["graph_multihop_cache_hit"] is False
    assert timings["graph_multihop_cached_seed_count"] == 1
    assert timings["graph_multihop_partial_cache_hit"] is True


def test_neo4j_graph_client_reuses_persistent_multihop_expansion_cache_across_clients():
    class PersistentExpansionSession(FakeNeo4jSession):
        def __init__(self):
            super().__init__()
            self.persistent_cache: dict[str, dict[str, Any]] = {}

        @property
        def seed_element_id(self) -> str:
            return "seed-alice-v2" if self.persistent_cache else "seed-alice-v1"

        def run(self, query, **params):
            self.calls.append({"query": query, "params": params})
            if "GraphMetadata" in query and "RETURN meta.value AS value" in query:
                return [{"value": 0}]
            if "RETURN seed_element_id, paths" in query:
                return [
                    {
                        "seed_element_id": seed_element_id,
                        "seed_name_key": "alice",
                        "seed_type_key": "person",
                        "paths": [
                            {
                                "hop": 2,
                                "rel_ids": [
                                    {
                                        "element_id": f"rel-{seed_element_id}-works-at",
                                        "fact_key": "person:alice|works_at|company:acme",
                                        "rel_index": 0,
                                    },
                                    {
                                        "element_id": f"rel-{seed_element_id}-uses",
                                        "fact_key": "company:acme|uses|tool:neo4j",
                                        "rel_index": 1,
                                    },
                                ],
                            }
                        ],
                        "incomplete": False,
                    }
                    for seed_element_id in params["seed_element_ids"]
                ]
            if "RETURN seed_element_id" in query and "seed.name_key AS seed_name_key" in query:
                return [
                    {
                        "seed_element_id": seed_element_id,
                        "seed_name_key": "alice",
                        "seed_type_key": "person",
                    }
                    for seed_element_id in params["seed_element_ids"]
                ]
            if "MATCH (cache:GraphExpansionCache)" in query:
                rows = []
                for key in params.get("cache_keys", []):
                    cache_entry = self.persistent_cache.get(key)
                    if cache_entry is None:
                        continue
                    rows.append(
                        {
                            **cache_entry,
                            "cache_properties": dict(cache_entry),
                        }
                    )
                return rows
            if "expansion_cache_entries" in params:
                for entry in params["expansion_cache_entries"]:
                    self.persistent_cache[entry["key"]] = dict(entry)
                return []
            if "db.index.fulltext.queryNodes" in query:
                return [{"element_id": self.seed_element_id, "score": 4.0}]
            if "db.index.fulltext.queryRelationships" in query:
                return []
            if "RETURN element_id, seed_score" in query:
                return [{"element_id": self.seed_element_id, "seed_score": 4.0}]
            if "RETURN startNode(r).name AS subject" in query:
                return [
                    {
                        "subject": "Acme",
                        "predicate": "uses",
                        "object": "Neo4j",
                        "evidence": "Acme uses Neo4j.",
                        "hop": 2,
                    }
                ]
            if "RETURN s.name AS subject" in query:
                return []
            return []

    driver = FakeNeo4jDriver()
    driver.session_obj = PersistentExpansionSession()
    config = {
        "enabled": True,
        "uri": "bolt://localhost:7687",
        "username": "neo4j",
        "password": "secret",
        "database": "atri",
    }

    first_client = Neo4jGraphClient(config, driver_factory=lambda uri, auth: driver)
    first_client.retrieve_context(
        query="Alice Acme",
        source_ids=[],
        max_facts=2,
        retrieval_depth=2,
    )
    first_expansion_loads = sum(
        1 for call in driver.session_obj.calls if "RETURN seed_element_id, paths" in call["query"]
    )

    second_client = Neo4jGraphClient(config, driver_factory=lambda uri, auth: driver)
    timings: dict[str, Any] = {}
    context = second_client.retrieve_context(
        query="Alice Acme",
        source_ids=[],
        max_facts=2,
        retrieval_depth=2,
        timings=timings,
    )

    expansion_loads = [
        call
        for call in driver.session_obj.calls
        if "RETURN seed_element_id, paths" in call["query"]
    ]
    persistent_reads = [
        call
        for call in driver.session_obj.calls
        if "MATCH (cache:GraphExpansionCache)" in call["query"]
    ]
    persistent_writes = [
        call for call in driver.session_obj.calls if "expansion_cache_entries" in call["params"]
    ]

    assert first_expansion_loads == 1
    assert len(expansion_loads) == 1
    assert persistent_reads
    persistent_read_query = persistent_reads[0]["query"]
    assert "properties(cache) AS cache_properties" in persistent_read_query
    assert "cache.complete AS complete" not in persistent_read_query
    assert "cache.paths_json AS paths_json" not in persistent_read_query
    persistent_read_cache_keys = [
        call["params"]["cache_keys"][0] for call in persistent_reads if call["params"]["cache_keys"]
    ]
    assert all("seed-alice-v" not in key for key in persistent_read_cache_keys)
    assert persistent_writes
    persistent_entry = persistent_writes[0]["params"]["expansion_cache_entries"][0]
    assert persistent_entry["key"] in persistent_read_cache_keys
    assert "seed_element_id" not in persistent_entry
    assert persistent_entry["seed_name_key"] == "alice"
    assert persistent_entry["seed_type_key"] == "person"
    persistent_paths = json.loads(persistent_entry["paths_json"])
    assert persistent_paths == [
        {
            "hop": 2,
            "rel_ids": [
                {"fact_key": "person:alice|works_at|company:acme", "rel_index": 0},
                {"fact_key": "company:acme|uses|tool:neo4j", "rel_index": 1},
            ],
        }
    ]
    assert context == format_graph_context(["- [2-hop] Acme -[uses]-> Neo4j (Acme uses Neo4j.)"])
    assert timings["graph_multihop_cache_hit"] is True
    assert timings["graph_multihop_cached_seed_count"] == 1
    assert timings["graph_multihop_live_seed_limit"] == 0
    assert timings["graph_multihop_persistent_cache_hit_count"] == 1


def test_neo4j_graph_client_caps_multihop_expansion_preload_with_global_budget():
    class BudgetedExpansionSession(FakeNeo4jSession):
        def run(self, query, **params):
            self.calls.append({"query": query, "params": params})
            if "db.index.fulltext.queryNodes" in query:
                return [
                    {"element_id": "seed-alice", "score": 4.0},
                    {"element_id": "seed-acme", "score": 3.0},
                ]
            if "db.index.fulltext.queryRelationships" in query:
                return []
            if "RETURN element_id, seed_score" in query:
                return [
                    {"element_id": "seed-alice", "seed_score": 4.0},
                    {"element_id": "seed-acme", "seed_score": 3.0},
                ]
            if "RETURN seed_element_id, paths" in query:
                return []
            if "RETURN startNode(r).name AS subject" in query:
                return []
            if "RETURN s.name AS subject" in query:
                return []
            return []

    driver = FakeNeo4jDriver()
    driver.session_obj = BudgetedExpansionSession()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    client.retrieve_context(query="Alice Acme", source_ids=[], max_facts=2, retrieval_depth=2)

    expansion_call = next(
        call
        for call in driver.session_obj.calls
        if "RETURN seed_element_id, paths" in call["query"]
    )
    assert expansion_call["params"]["path_limit"] == 100
    assert expansion_call["params"]["path_limit_plus_one"] == 101
    assert expansion_call["params"]["seed_element_ids"] == ["seed-alice", "seed-acme"]


def test_neo4j_graph_client_invalidates_multihop_expansion_cache_after_upsert():
    class ExpansionCacheInvalidationSession(FakeNeo4jSession):
        def run(self, query, **params):
            self.calls.append({"query": query, "params": params})
            if "db.index.fulltext.queryNodes" in query:
                return [{"element_id": "seed-alice", "score": 4.0}]
            if "db.index.fulltext.queryRelationships" in query:
                return []
            if "RETURN element_id, seed_score" in query:
                return [{"element_id": "seed-alice", "seed_score": 4.0}]
            if "RETURN seed_element_id, paths" in query:
                return [
                    {
                        "seed_element_id": "seed-alice",
                        "paths": [
                            {
                                "hop": 2,
                                "rel_ids": [
                                    {"element_id": "rel-alice-acme", "rel_index": 0},
                                    {"element_id": "rel-acme-neo4j", "rel_index": 1},
                                ],
                            }
                        ],
                        "incomplete": False,
                    }
                ]
            if "RETURN startNode(r).name AS subject" in query:
                return [
                    {
                        "subject": "Acme",
                        "predicate": "uses",
                        "object": "Neo4j",
                        "evidence": "Acme uses Neo4j.",
                        "hop": 2,
                    }
                ]
            if "RETURN s.name AS subject" in query:
                return [
                    {
                        "subject": "Alice",
                        "predicate": "works_at",
                        "object": "Acme",
                        "evidence": "Alice works at Acme.",
                    }
                ]
            return []

    driver = FakeNeo4jDriver()
    driver.session_obj = ExpansionCacheInvalidationSession()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    client.retrieve_context(query="Alice Acme", source_ids=[], max_facts=2, retrieval_depth=2)
    fact = normalize_extracted_facts(
        [
            {
                "subject": "Carol",
                "subject_type": "Person",
                "predicate": "uses",
                "object": "Redis",
                "object_type": "Tool",
            }
        ],
        source_id="chunk-cache-bust",
        source_kind="document",
    )[0]
    client.upsert_facts([fact])
    client.retrieve_context(query="Alice Acme", source_ids=[], max_facts=2, retrieval_depth=2)

    assert (
        sum(
            1
            for call in driver.session_obj.calls
            if "RETURN seed_element_id, paths" in call["query"]
        )
        == 2
    )


def test_neo4j_graph_client_runs_single_and_multi_hop_queries_concurrently_and_merges_order():
    state = ParallelRetrievalState()
    driver = ParallelRetrievalDriver(state)
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    context = client.retrieve_context(
        query="Alice Neo4j",
        source_ids=[],
        max_facts=4,
        retrieval_depth=2,
    )

    assert state.single_saw_multi_started is True
    assert context == format_graph_context(
        [
            "- [1-hop] Alice -[works_at]-> Acme (Alice works at Acme.)",
            "- [2-hop] Bob -[uses]-> Neo4j (Bob uses Neo4j.)",
        ]
    )


def test_neo4j_graph_client_multihop_preserves_one_hop_context_and_dedupes():
    class PreserveOneHopSession(FakeNeo4jSession):
        def run(self, query, **params):
            self.calls.append({"query": query, "params": params})
            if "FACT*1..2" in query:
                return [
                    {
                        "subject": "Lin Wan",
                        "predicate": "uses",
                        "object": "Studio One",
                        "evidence": "duplicate path evidence",
                        "confidence": 0.9,
                        "hop": 2,
                    },
                    {
                        "subject": "Studio One",
                        "predicate": "supports",
                        "object": "scoring",
                        "evidence": "Studio One supports scoring.",
                        "confidence": 0.8,
                        "hop": 2,
                    },
                ]
            if "RETURN s.name AS subject" in query:
                return [
                    {
                        "subject": "Lin Wan",
                        "predicate": "uses",
                        "object": "Studio One",
                        "evidence": "Lin Wan uses Studio One.",
                        "confidence": 0.9,
                    },
                    {
                        "subject": "Lin Wan",
                        "predicate": "prefers",
                        "object": "concise explanations",
                        "evidence": "Lin Wan prefers concise explanations.",
                        "confidence": 0.9,
                    },
                    {
                        "subject": "Lin Wan",
                        "predicate": "has_trait",
                        "object": "podcast post-production",
                        "evidence": "Lin Wan does podcast post-production.",
                        "confidence": 0.8,
                    },
                ]
            return []

    driver = FakeNeo4jDriver()
    driver.session_obj = PreserveOneHopSession()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    context = client.retrieve_context(
        query="Lin Wan Studio One",
        source_ids=[],
        max_facts=6,
        retrieval_depth=2,
    )

    retrieve_calls = _retrieve_calls(driver.session_obj.calls)
    assert len(retrieve_calls) == 3
    assert (
        "MATCH (s:Entity)-[r:FACT]->(o:Entity)"
        in _single_hop_retrieve_call(retrieve_calls)["query"]
    )
    seeded_multi_hop_call = _multi_hop_retrieve_call(retrieve_calls)
    scan_fallback_call = next(
        call
        for call in retrieve_calls
        if "MATCH path = (s:Entity)-[:FACT*1..2]->(o:Entity)" in call["query"]
    )
    assert "FACT*1..2" in seeded_multi_hop_call["query"]
    assert "UNWIND $entity_seed_rows AS entity_seed" in seeded_multi_hop_call["query"]
    assert "MATCH path = (s:Entity)-[:FACT*1..2]->(o:Entity)" in scan_fallback_call["query"]
    assert context == format_graph_context(
        [
            (
                "- [1-hop] Lin Wan -[uses]-> Studio One (Lin Wan uses Studio One.) "
                "| linked: [2-hop] Studio One -[supports]-> scoring "
                "(Studio One supports scoring.)"
            ),
            (
                "- [1-hop] Lin Wan -[prefers]-> concise explanations "
                "(Lin Wan prefers concise explanations.)"
            ),
            (
                "- [1-hop] Lin Wan -[has_trait]-> podcast post-production "
                "(Lin Wan does podcast post-production.)"
            ),
        ]
    )


def test_neo4j_graph_client_nests_three_hop_context_as_one_fact_line():
    class ThreeHopSession(FakeNeo4jSession):
        def run(self, query, **params):
            self.calls.append({"query": query, "params": params})
            if "FACT*1..3" in query:
                return [
                    {
                        "subject": "Lin Wan",
                        "predicate": "uses",
                        "object": "Studio One",
                        "evidence": "duplicate path evidence",
                        "confidence": 0.9,
                        "hop": 2,
                    },
                    {
                        "subject": "Studio One",
                        "predicate": "supports",
                        "object": "scoring",
                        "evidence": "Studio One supports scoring.",
                        "confidence": 0.8,
                        "hop": 2,
                    },
                    {
                        "subject": "scoring",
                        "predicate": "depends_on",
                        "object": "Neo4j",
                        "evidence": "Scoring context depends on Neo4j.",
                        "confidence": 0.7,
                        "hop": 3,
                    },
                    {
                        "subject": "Detached",
                        "predicate": "mentions",
                        "object": "orphan fact",
                        "evidence": "Detached fact was retrieved separately.",
                        "confidence": 0.6,
                        "hop": 3,
                    },
                ]
            if "RETURN s.name AS subject" in query:
                return [
                    {
                        "subject": "Lin Wan",
                        "predicate": "uses",
                        "object": "Studio One",
                        "evidence": "Lin Wan uses Studio One.",
                        "confidence": 0.9,
                    },
                    {
                        "subject": "Lin Wan",
                        "predicate": "prefers",
                        "object": "concise explanations",
                        "evidence": "Lin Wan prefers concise explanations.",
                        "confidence": 0.9,
                    },
                ]
            return []

    driver = FakeNeo4jDriver()
    driver.session_obj = ThreeHopSession()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    context = client.retrieve_context(
        query="Lin Wan Studio One scoring Neo4j",
        source_ids=[],
        max_facts=8,
        retrieval_depth=3,
    )

    assert context == format_graph_context(
        [
            (
                "- [1-hop] Lin Wan -[uses]-> Studio One (Lin Wan uses Studio One.) "
                "| linked: [2-hop] Studio One -[supports]-> scoring "
                "(Studio One supports scoring.) | linked: [3-hop] scoring "
                "-[depends_on]-> Neo4j (Scoring context depends on Neo4j.)"
            ),
            (
                "- [1-hop] Lin Wan -[prefers]-> concise explanations "
                "(Lin Wan prefers concise explanations.)"
            ),
            (
                "- [3-hop] Detached -[mentions]-> orphan fact "
                "(Detached fact was retrieved separately.)"
            ),
        ]
    )


def test_neo4j_graph_client_nests_seven_hop_context_as_one_fact_line():
    class SevenHopSession(FakeNeo4jSession):
        def run(self, query, **params):
            self.calls.append({"query": query, "params": params})
            if "FACT*1..7" in query:
                return [
                    {
                        "subject": "Project",
                        "predicate": "uses",
                        "object": "Neo4j",
                        "evidence": "Project uses Neo4j.",
                        "hop": 2,
                    },
                    {
                        "subject": "Neo4j",
                        "predicate": "runs_on",
                        "object": "Graph Database",
                        "evidence": "Neo4j runs as a graph database.",
                        "hop": 3,
                    },
                    {
                        "subject": "Graph Database",
                        "predicate": "stores",
                        "object": "Facts",
                        "evidence": "Graph databases store facts.",
                        "hop": 4,
                    },
                    {
                        "subject": "Facts",
                        "predicate": "support",
                        "object": "Retrieval",
                        "evidence": "Facts support retrieval.",
                        "hop": 5,
                    },
                    {
                        "subject": "Retrieval",
                        "predicate": "uses",
                        "object": "Context",
                        "evidence": "Retrieval uses context.",
                        "hop": 6,
                    },
                    {
                        "subject": "Context",
                        "predicate": "answers",
                        "object": "Question",
                        "evidence": "Context answers questions.",
                        "hop": 7,
                    },
                    {
                        "subject": "Detached",
                        "predicate": "mentions",
                        "object": "orphan fact",
                        "evidence": "Detached 7-hop fact was retrieved separately.",
                        "hop": 7,
                    },
                ]
            if "RETURN s.name AS subject" in query:
                return [
                    {
                        "subject": "Lin Wan",
                        "predicate": "works_on",
                        "object": "Project",
                        "evidence": "Lin Wan works on Project.",
                    }
                ]
            return []

    driver = FakeNeo4jDriver()
    driver.session_obj = SevenHopSession()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    context = client.retrieve_context(
        query="Lin Wan Project Neo4j Graph Database Facts Retrieval Context Question",
        source_ids=[],
        max_facts=3,
        retrieval_depth=7,
    )

    assert any("FACT*1..7" in call["query"] for call in driver.session_obj.calls)
    assert context == format_graph_context(
        [
            (
                "- [1-hop] Lin Wan -[works_on]-> Project (Lin Wan works on Project.) "
                "| linked: [2-hop] Project -[uses]-> Neo4j (Project uses Neo4j.) "
                "| linked: [3-hop] Neo4j -[runs_on]-> Graph Database "
                "(Neo4j runs as a graph database.) | linked: [4-hop] Graph Database "
                "-[stores]-> Facts (Graph databases store facts.) | linked: [5-hop] Facts "
                "-[support]-> Retrieval (Facts support retrieval.) | linked: [6-hop] Retrieval "
                "-[uses]-> Context (Retrieval uses context.) | linked: [7-hop] Context "
                "-[answers]-> Question (Context answers questions.)"
            ),
            (
                "- [7-hop] Detached -[mentions]-> orphan fact "
                "(Detached 7-hop fact was retrieved separately.)"
            ),
        ]
    )


def test_neo4j_graph_client_chooses_most_specific_parent_for_multihop_child():
    class MultiParentSession(FakeNeo4jSession):
        def run(self, query, **params):
            self.calls.append({"query": query, "params": params})
            if "FACT*1..2" in query:
                return [
                    {
                        "subject": "Shared Topic",
                        "predicate": "uses",
                        "object": "Neo4j",
                        "evidence": "Shared Topic uses Neo4j.",
                        "hop": 2,
                    }
                ]
            if "RETURN s.name AS subject" in query:
                return [
                    {
                        "subject": "Alpha",
                        "predicate": "mentions",
                        "object": "Shared Topic",
                        "evidence": "Alpha mentions it.",
                    },
                    {
                        "subject": "Beta",
                        "predicate": "documents",
                        "object": "Shared Topic",
                        "evidence": (
                            "Beta documents Shared Topic with explicit Graph RAG context."
                        ),
                    },
                ]
            return []

    driver = FakeNeo4jDriver()
    driver.session_obj = MultiParentSession()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    context = client.retrieve_context(
        query="Shared Topic Neo4j",
        source_ids=[],
        max_facts=3,
        retrieval_depth=2,
    )

    assert context == format_graph_context(
        [
            "- [1-hop] Alpha -[mentions]-> Shared Topic (Alpha mentions it.)",
            (
                "- [1-hop] Beta -[documents]-> Shared Topic "
                "(Beta documents Shared Topic with explicit Graph RAG context.) "
                "| linked: [2-hop] Shared Topic -[uses]-> Neo4j "
                "(Shared Topic uses Neo4j.)"
            ),
        ]
    )


def test_neo4j_graph_client_counts_nested_context_as_one_top_level_fact():
    class NestedQuotaSession(FakeNeo4jSession):
        def run(self, query, **params):
            self.calls.append({"query": query, "params": params})
            if "FACT*1..3" in query:
                return [
                    {
                        "subject": "Project",
                        "predicate": "uses",
                        "object": "Neo4j",
                        "evidence": "Project uses Neo4j.",
                        "hop": 2,
                    },
                    {
                        "subject": "Neo4j",
                        "predicate": "configured_with",
                        "object": "5.x",
                        "evidence": "Neo4j runs as 5.x.",
                        "hop": 3,
                    },
                    {
                        "subject": "Detached",
                        "predicate": "mentions",
                        "object": "first orphan",
                        "evidence": "First orphan was retrieved.",
                        "hop": 2,
                    },
                    {
                        "subject": "Detached",
                        "predicate": "mentions",
                        "object": "second orphan",
                        "evidence": "Second orphan was retrieved.",
                        "hop": 2,
                    },
                ]
            if "RETURN s.name AS subject" in query:
                return [
                    {
                        "subject": "Lin Wan",
                        "predicate": "works_on",
                        "object": "Project",
                        "evidence": "Lin Wan works on Project.",
                    },
                    {
                        "subject": "Lin Wan",
                        "predicate": "prefers",
                        "object": "short answers",
                        "evidence": "Lin Wan prefers short answers.",
                    },
                ]
            return []

    driver = FakeNeo4jDriver()
    driver.session_obj = NestedQuotaSession()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    context = client.retrieve_context(
        query="Lin Wan Project Neo4j",
        source_ids=[],
        max_facts=3,
        retrieval_depth=3,
    )

    assert context == format_graph_context(
        [
            (
                "- [1-hop] Lin Wan -[works_on]-> Project (Lin Wan works on Project.) "
                "| linked: [2-hop] Project -[uses]-> Neo4j (Project uses Neo4j.) "
                "| linked: [3-hop] Neo4j -[configured_with]-> 5.x (Neo4j runs as 5.x.)"
            ),
            "- [1-hop] Lin Wan -[prefers]-> short answers (Lin Wan prefers short answers.)",
            "- [2-hop] Detached -[mentions]-> first orphan (First orphan was retrieved.)",
        ]
    )
    assert "second orphan" not in context


def test_neo4j_graph_client_prioritizes_multihop_chain_when_single_hop_roots_exceed_limit():
    class CausalChainSession(FakeNeo4jSession):
        def run(self, query, **params):
            self.calls.append({"query": query, "params": params})
            if "FACT*1..4" in query:
                return [
                    {
                        "subject": "夜间维护策略变更",
                        "predicate": "causes",
                        "object": "DHCP配置批量下发",
                        "evidence": "夜间维护策略变更触发 DHCP 配置批量下发。",
                        "hop": 1,
                    },
                    {
                        "subject": "DHCP配置批量下发",
                        "predicate": "causes",
                        "object": "地址池冲突",
                        "evidence": "DHCP 配置批量下发导致地址池冲突。",
                        "hop": 2,
                    },
                    {
                        "subject": "地址池冲突",
                        "predicate": "causes",
                        "object": "设备批量掉线",
                        "evidence": "地址池冲突最终导致设备批量掉线。",
                        "hop": 3,
                    },
                    {
                        "subject": "夜间维护策略变更",
                        "predicate": "causes",
                        "object": "证书批量刷新",
                        "evidence": "同一夜间维护策略变更还触发证书批量刷新。",
                        "hop": 1,
                    },
                    {
                        "subject": "证书批量刷新",
                        "predicate": "causes",
                        "object": "认证失败",
                        "evidence": "证书批量刷新导致认证失败。",
                        "hop": 2,
                    },
                    {
                        "subject": "认证失败",
                        "predicate": "causes",
                        "object": "设备批量掉线",
                        "evidence": "认证失败最终导致设备批量掉线。",
                        "hop": 3,
                    },
                ]
            if "RETURN s.name AS subject" in query:
                return [
                    {
                        "subject": "客服工单",
                        "predicate": "mentions",
                        "object": "设备批量掉线",
                        "evidence": "客服工单记录设备批量掉线。",
                    },
                    {
                        "subject": "监控报警",
                        "predicate": "mentions",
                        "object": "设备批量掉线",
                        "evidence": "监控报警记录设备批量掉线。",
                    },
                    {
                        "subject": "值班日报",
                        "predicate": "mentions",
                        "object": "设备批量掉线",
                        "evidence": "值班日报记录设备批量掉线。",
                    },
                ]
            return []

    driver = FakeNeo4jDriver()
    driver.session_obj = CausalChainSession()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    context = client.retrieve_context(
        query=(
            '哪些根因事件最终导致了"设备批量掉线", 且这些根因之间是否存在共同的上游'
            "触发因素? 列出完整的因果链及每个环节涉及的责任人"
        ),
        source_ids=[],
        max_facts=3,
        retrieval_depth=4,
    )

    assert context == format_graph_context(
        [
            (
                "- [1-hop] 夜间维护策略变更 -[causes]-> DHCP配置批量下发 "
                "(夜间维护策略变更触发 DHCP 配置批量下发。) | linked: [2-hop] "
                "DHCP配置批量下发 -[causes]-> 地址池冲突 "
                "(DHCP 配置批量下发导致地址池冲突。) | linked: [3-hop] "
                "地址池冲突 -[causes]-> 设备批量掉线 "
                "(地址池冲突最终导致设备批量掉线。)"
            ),
            (
                "- [1-hop] 夜间维护策略变更 -[causes]-> 证书批量刷新 "
                "(同一夜间维护策略变更还触发证书批量刷新。) | linked: [2-hop] "
                "证书批量刷新 -[causes]-> 认证失败 (证书批量刷新导致认证失败。) "
                "| linked: [3-hop] 认证失败 -[causes]-> 设备批量掉线 "
                "(认证失败最终导致设备批量掉线。)"
            ),
            "- [1-hop] 客服工单 -[mentions]-> 设备批量掉线 (客服工单记录设备批量掉线。)",
        ]
    )


def test_neo4j_graph_client_keeps_deep_duplicate_edges_for_chain_stitching():
    class DuplicateShallowHopSession(FakeNeo4jSession):
        def run(self, query, **params):
            self.calls.append({"query": query, "params": params})
            if "FACT*1..4" in query:
                return [
                    {
                        "subject": "地址池冲突",
                        "predicate": "causes",
                        "object": "网关异常",
                        "evidence": "地址池冲突导致网关异常。",
                        "hop": 1,
                    },
                    {
                        "subject": "网关异常",
                        "predicate": "causes",
                        "object": "用户断连",
                        "evidence": "网关异常造成用户断连。",
                        "hop": 2,
                    },
                    {
                        "subject": "DHCP配置批量下发",
                        "predicate": "causes",
                        "object": "地址池冲突",
                        "evidence": "DHCP 配置批量下发导致地址池冲突。",
                        "hop": 2,
                    },
                    {
                        "subject": "地址池冲突",
                        "predicate": "causes",
                        "object": "网关异常",
                        "evidence": "地址池冲突导致网关异常。",
                        "hop": 3,
                    },
                    {
                        "subject": "网关异常",
                        "predicate": "causes",
                        "object": "用户断连",
                        "evidence": "网关异常造成用户断连。",
                        "hop": 4,
                    },
                ]
            if "RETURN s.name AS subject" in query:
                return [
                    {
                        "subject": "夜间维护策略变更",
                        "predicate": "causes",
                        "object": "DHCP配置批量下发",
                        "evidence": "夜间维护策略变更触发 DHCP 配置批量下发。",
                    }
                ]
            return []

    driver = FakeNeo4jDriver()
    driver.session_obj = DuplicateShallowHopSession()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    context = client.retrieve_context(
        query="夜间维护策略变更 DHCP 地址池冲突 网关异常 用户断连",
        source_ids=[],
        max_facts=3,
        retrieval_depth=4,
    )

    assert context == format_graph_context(
        [
            (
                "- [1-hop] 夜间维护策略变更 -[causes]-> DHCP配置批量下发 "
                "(夜间维护策略变更触发 DHCP 配置批量下发。) | linked: [2-hop] "
                "DHCP配置批量下发 -[causes]-> 地址池冲突 "
                "(DHCP 配置批量下发导致地址池冲突。) | linked: [3-hop] "
                "地址池冲突 -[causes]-> 网关异常 (地址池冲突导致网关异常。) "
                "| linked: [4-hop] 网关异常 -[causes]-> 用户断连 "
                "(网关异常造成用户断连。)"
            )
        ]
    )


def test_neo4j_graph_client_prefers_longest_root_over_duplicate_inner_short_chain():
    class ShortDuplicateFirstSession(FakeNeo4jSession):
        def run(self, query, **params):
            self.calls.append({"query": query, "params": params})
            if "FACT*1..4" in query:
                return [
                    {
                        "subject": "网关异常",
                        "predicate": "causes",
                        "object": "用户断连",
                        "evidence": "网关异常造成用户断连。",
                        "hop": 2,
                    },
                    {
                        "subject": "DHCP配置批量下发",
                        "predicate": "causes",
                        "object": "地址池冲突",
                        "evidence": "DHCP 配置批量下发导致地址池冲突。",
                        "hop": 2,
                    },
                    {
                        "subject": "地址池冲突",
                        "predicate": "causes",
                        "object": "网关异常",
                        "evidence": "地址池冲突导致网关异常。",
                        "hop": 3,
                    },
                    {
                        "subject": "网关异常",
                        "predicate": "causes",
                        "object": "用户断连",
                        "evidence": "网关异常造成用户断连。",
                        "hop": 4,
                    },
                ]
            if "RETURN s.name AS subject" in query:
                return [
                    {
                        "subject": "地址池冲突",
                        "predicate": "causes",
                        "object": "网关异常",
                        "evidence": "地址池冲突导致网关异常。",
                    },
                    {
                        "subject": "夜间维护策略变更",
                        "predicate": "causes",
                        "object": "DHCP配置批量下发",
                        "evidence": "夜间维护策略变更触发 DHCP 配置批量下发。",
                    },
                ]
            return []

    driver = FakeNeo4jDriver()
    driver.session_obj = ShortDuplicateFirstSession()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    context = client.retrieve_context(
        query="夜间维护策略变更 DHCP 地址池冲突 网关异常 用户断连",
        source_ids=[],
        max_facts=4,
        retrieval_depth=4,
    )

    assert context == format_graph_context(
        [
            (
                "- [1-hop] 夜间维护策略变更 -[causes]-> DHCP配置批量下发 "
                "(夜间维护策略变更触发 DHCP 配置批量下发。) | linked: [2-hop] "
                "DHCP配置批量下发 -[causes]-> 地址池冲突 "
                "(DHCP 配置批量下发导致地址池冲突。) | linked: [3-hop] "
                "地址池冲突 -[causes]-> 网关异常 (地址池冲突导致网关异常。) "
                "| linked: [4-hop] 网关异常 -[causes]-> 用户断连 "
                "(网关异常造成用户断连。)"
            )
        ]
    )


def test_neo4j_graph_client_multihop_expansion_ignores_one_hop_rows_before_limit():
    class ExpansionLimitSession(FakeNeo4jSession):
        def run(self, query, **params):
            self.calls.append({"query": query, "params": params})
            if "FACT*1..2" in query:
                if "WHERE hop > 1" in query:
                    return [
                        {
                            "subject": "Project",
                            "predicate": "uses",
                            "object": "Neo4j",
                            "evidence": "Project uses Neo4j.",
                            "hop": 2,
                        }
                    ]
                return [
                    {
                        "subject": "Lin Wan",
                        "predicate": "works_on",
                        "object": "Project",
                        "evidence": "duplicate one-hop path",
                        "hop": 1,
                    }
                ][: int(params.get("limit") or 1)]
            if "RETURN s.name AS subject" in query:
                return [
                    {
                        "subject": "Lin Wan",
                        "predicate": "works_on",
                        "object": "Project",
                        "evidence": "Lin Wan works on Project.",
                    }
                ]
            return []

    driver = FakeNeo4jDriver()
    driver.session_obj = ExpansionLimitSession()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    context = client.retrieve_context(
        query="Lin Wan Project Neo4j",
        source_ids=[],
        max_facts=1,
        retrieval_depth=2,
    )

    assert context == format_graph_context(
        [
            (
                "- [1-hop] Lin Wan -[works_on]-> Project (Lin Wan works on Project.) "
                "| linked: [2-hop] Project -[uses]-> Neo4j (Project uses Neo4j.)"
            ),
        ]
    )


def test_neo4j_graph_client_multihop_expansion_uses_larger_candidate_pool_than_output_limit():
    class ExpansionCandidatePoolSession(FakeNeo4jSession):
        def run(self, query, **params):
            self.calls.append({"query": query, "params": params})
            if "FACT*1..2" in query:
                if int(params.get("limit") or 0) < 40:
                    return []
                return [
                    {
                        "subject": "Night Radio",
                        "predicate": "uses",
                        "object": "Neo4j",
                        "evidence": "Night Radio uses Neo4j for Graph RAG.",
                        "hop": 2,
                    }
                ]
            if "RETURN s.name AS subject" in query:
                return [
                    {
                        "subject": "Lin Wan",
                        "predicate": "uses",
                        "object": "Studio One",
                        "evidence": "Lin Wan uses Studio One.",
                    },
                    {
                        "subject": "Lin Wan",
                        "predicate": "no_longer_uses",
                        "object": "Ableton Live",
                        "evidence": "Lin Wan no longer uses Ableton Live.",
                    },
                    {
                        "subject": "Lin Wan",
                        "predicate": "prefers",
                        "object": "concise explanations",
                        "evidence": "Lin Wan prefers concise explanations.",
                    },
                    {
                        "subject": "Lin Wan",
                        "predicate": "has_trait",
                        "object": "podcast post-production",
                        "evidence": "Lin Wan does podcast post-production.",
                    },
                    {
                        "subject": "Lin Wan",
                        "predicate": "avoids",
                        "object": "long tutorials",
                        "evidence": "Lin Wan avoids long tutorials.",
                    },
                    {
                        "subject": "Lin Wan",
                        "predicate": "prefers",
                        "object": "Chinese replies with English technical terms",
                        "evidence": "Lin Wan prefers Chinese replies.",
                    },
                    {
                        "subject": "Lin Wan",
                        "predicate": "works_on",
                        "object": "Night Radio",
                        "evidence": "Lin Wan works on Night Radio.",
                    },
                    {
                        "subject": "Lin Wan",
                        "predicate": "uses",
                        "object": "Reaper",
                        "evidence": "Lin Wan uses Reaper.",
                    },
                ][: int(params.get("limit") or 1)]
            return []

    driver = FakeNeo4jDriver()
    driver.session_obj = ExpansionCandidatePoolSession()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    context = client.retrieve_context(
        query="Lin Wan Night Radio Neo4j",
        source_ids=[],
        max_facts=8,
        retrieval_depth=2,
    )

    expansion_call = next(call for call in driver.session_obj.calls if "FACT*1..2" in call["query"])
    assert expansion_call["params"]["limit"] >= 40
    assert context == format_graph_context(
        [
            "- [1-hop] Lin Wan -[uses]-> Studio One (Lin Wan uses Studio One.)",
            (
                "- [1-hop] Lin Wan -[no_longer_uses]-> Ableton Live "
                "(Lin Wan no longer uses Ableton Live.)"
            ),
            (
                "- [1-hop] Lin Wan -[prefers]-> concise explanations "
                "(Lin Wan prefers concise explanations.)"
            ),
            (
                "- [1-hop] Lin Wan -[has_trait]-> podcast post-production "
                "(Lin Wan does podcast post-production.)"
            ),
            "- [1-hop] Lin Wan -[avoids]-> long tutorials (Lin Wan avoids long tutorials.)",
            (
                "- [1-hop] Lin Wan -[prefers]-> Chinese replies with English technical terms "
                "(Lin Wan prefers Chinese replies.)"
            ),
            (
                "- [1-hop] Lin Wan -[works_on]-> Night Radio (Lin Wan works on Night Radio.) "
                "| linked: [2-hop] Night Radio -[uses]-> Neo4j "
                "(Night Radio uses Neo4j for Graph RAG.)"
            ),
            "- [1-hop] Lin Wan -[uses]-> Reaper (Lin Wan uses Reaper.)",
        ]
    )


def test_neo4j_graph_client_uses_configured_multihop_expansion_candidate_limit():
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
            "expansion_candidate_limit": 72,
        },
        driver_factory=lambda uri, auth: driver,
    )

    client.retrieve_context(
        query="Alice",
        source_ids=[],
        max_facts=8,
        retrieval_depth=2,
    )

    expansion_call = next(call for call in driver.session_obj.calls if "FACT*1..2" in call["query"])
    assert expansion_call["params"]["limit"] == 72


def test_neo4j_graph_client_uses_hybrid_ranking_score_before_limit():
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    client.retrieve_context(
        query="Alice",
        source_ids=["chunk-1"],
        max_facts=4,
        retrieval_depth=1,
        ranking_policy="hybrid",
    )

    query = driver.session_obj.calls[-1]["query"]
    assert "graph_score" in query
    assert "source_match_score" in query
    assert "term_match_score" in query
    assert "structural_role_score" in query
    assert "r[$hyper_role_property]" in query
    assert "r[$chain_id_property]" in query
    assert "r.hyper_role" not in query
    assert "r.chain_id" not in query
    assert "ORDER BY structural_role ASC, graph_score DESC, r.updated_at DESC" in query
    assert driver.session_obj.calls[-1]["params"]["hyper_role_predicate"] == "has_role"
    assert driver.session_obj.calls[-1]["params"]["hyper_role_property"] == "hyper_role"
    assert driver.session_obj.calls[-1]["params"]["ranking_policy"] == "hybrid"


def test_neo4j_graph_client_adds_vector_source_score_to_graph_ranking():
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    client.retrieve_context(
        query="Alice",
        source_ids=["chunk-strong", "chunk-weak"],
        source_scores={"chunk-strong": 0.8, "chunk-weak": 0.2},
        max_facts=4,
        retrieval_depth=2,
        ranking_policy="hybrid",
    )

    single_hop_call = next(
        call for call in driver.session_obj.calls if "RETURN s.name AS subject" in call["query"]
    )
    multi_hop_call = next(
        call
        for call in driver.session_obj.calls
        if "RETURN startNode(r).name AS subject" in call["query"]
    )
    assert single_hop_call["params"]["source_score_rows"] == [
        {"source_id": "chunk-strong", "score": 1.0},
        {"source_id": "chunk-weak", "score": 0.25},
    ]
    assert "source_vector_score" in single_hop_call["query"]
    assert "+ source_vector_score * 2.0" in single_hop_call["query"]
    assert "source_vector_score" in multi_hop_call["query"]
    assert "+ source_vector_score * 2.0" in multi_hop_call["query"]
    assert "3.0 + source_vector_score * 2.0 AS seed_score" in multi_hop_call["query"]


def test_neo4j_graph_client_latest_ranking_preserves_recency_order():
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    client.retrieve_context(
        query="Alice",
        source_ids=[],
        max_facts=4,
        ranking_policy="latest",
        retrieval_depth=2,
    )

    multi_hop_call = _multi_hop_retrieve_call(driver.session_obj.calls)
    query = multi_hop_call["query"]
    assert (
        "ORDER BY structural_role ASC, chain_path_score DESC, "
        "chain_order_score DESC, hop DESC, r.updated_at DESC"
    ) in query
    assert multi_hop_call["params"]["ranking_policy"] == "latest"


def test_neo4j_graph_client_multihop_retrieval_uses_hyper_chain_metadata():
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    client.retrieve_context(
        query="ATRI graph RAG Neo4j adoption",
        source_ids=[],
        max_facts=4,
        retrieval_depth=3,
        ranking_policy="hybrid",
    )

    multi_hop_call = _multi_hop_retrieve_call(driver.session_obj.calls)
    query = multi_hop_call["query"]
    assert "rel[$hyper_event_property]" in query
    assert "rel[$hyper_role_property]" in query
    assert "rel[$chain_id_property]" in query
    assert "rel[$chain_ids_property]" in query
    assert "chain_path_score" in query
    assert "WHEN size(rels) = 1 THEN false" in query
    assert "chain_order_score" in query
    assert "structural_role_score" in query
    assert "structural_role ASC" in query
    assert "left_chain_id IN coalesce(rels[index][$chain_ids_property], [])" in query
    assert "left_key IN coalesce(rels[index][$chain_order_keys_property], [])" in query
    assert "right_key IN coalesce(" in query
    assert "rels[index + 1][$chain_order_keys_property]" in query
    assert "split(right_key, $chain_order_separator)[0]" in query
    assert "split(left_key, $chain_order_separator)[0]" in query
    assert "toInteger(split(right_key, $chain_order_separator)[1])" in query
    assert multi_hop_call["params"]["chain_order_separator"] == CHAIN_ORDER_KEY_SEPARATOR
    assert "toLower(coalesce(r[$hyper_role_property], '')) CONTAINS term" in query
    assert "r[$hyper_role_property] AS hyper_role" in query
    assert "rel.hyper_role" not in query
    assert "r.hyper_role" not in query


def test_neo4j_graph_client_multihop_query_returns_each_path_edge_for_stitching():
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    client.retrieve_context(
        query="root causes that led to device batch offline",
        source_ids=[],
        max_facts=4,
        retrieval_depth=4,
        ranking_policy="hybrid",
    )

    query = _multi_hop_retrieve_call(driver.session_obj.calls)["query"]
    assert "UNWIND range(0, size(rels) - 1) AS rel_index" in query
    assert "rels[rel_index] AS r" in query
    assert "rel_index + 1 AS hop" in query


def test_neo4j_graph_client_multihop_query_dedupes_unwound_edges_by_hop():
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    client.retrieve_context(
        query="complete 4 hop causal chain",
        source_ids=[],
        max_facts=4,
        retrieval_depth=4,
        ranking_policy="hybrid",
    )

    query = _multi_hop_retrieve_call(driver.session_obj.calls)["query"]
    limit_index = query.index("LIMIT $limit")
    unwind_index = query.index("UNWIND range(0, size(rels) - 1) AS rel_index")
    dedupe_index = query.index("WITH r, hop, max(graph_score) AS graph_score")
    return_index = query.index("RETURN startNode(r).name AS subject")
    assert limit_index < unwind_index
    assert unwind_index < dedupe_index
    assert dedupe_index < return_index
    assert "min(structural_role) AS structural_role" in query


def test_neo4j_graph_client_multihop_query_limits_paths_before_unwinding_edges():
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    client.retrieve_context(
        query="complete 4 hop causal chain",
        source_ids=[],
        max_facts=4,
        retrieval_depth=4,
        ranking_policy="hybrid",
    )

    query = _multi_hop_retrieve_call(driver.session_obj.calls)["query"]
    limit_index = query.index("LIMIT $limit")
    unwind_index = query.index("UNWIND range(0, size(rels) - 1) AS rel_index")
    deep_path_order_index = query.index("hop DESC")
    assert deep_path_order_index < limit_index
    assert limit_index < unwind_index


def test_neo4j_graph_client_multihop_query_keeps_path_rels_until_edge_unwind():
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    client.retrieve_context(
        query="complete 4 hop causal chain",
        source_ids=[],
        max_facts=4,
        retrieval_depth=4,
        ranking_policy="hybrid",
    )

    query = _multi_hop_retrieve_call(driver.session_obj.calls)["query"]
    assert "WITH rels, startNode(r) AS s" in query
    assert "WITH rels, s, r, o, hop, confidence_score" in query
    assert "WITH rels, s, r, o, hop, chain_path_score" in query


def test_neo4j_graph_client_multihop_query_keeps_chain_path_flags_until_scoring():
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    client.retrieve_context(
        query="complete 4 hop causal chain",
        source_ids=[],
        max_facts=4,
        retrieval_depth=4,
        ranking_policy="hybrid",
    )

    query = _multi_hop_retrieve_call(driver.session_obj.calls)["query"]
    assert (
        "WITH rels, startNode(r) AS s, r, endNode(r) AS o, hop, chain_path,\n"
        "             chain_order_path, seed_score,"
    ) in query
    assert (
        "WITH rels, s, r, o, hop, chain_path, chain_order_path, seed_score, fact_source_ids,"
    ) in query
    assert "CASE WHEN chain_path THEN 1.5 ELSE 0.0 END AS chain_path_score" in query
    assert "CASE WHEN chain_order_path THEN 1.0 ELSE 0.0 END AS chain_order_score" in query


def test_graph_query_terms_include_cjk_ngrams_for_unsegmented_queries():
    terms = _query_terms("我之前请求截图的时候失败过是什么原因")

    assert "截图" in terms
    assert "失败" in terms
    assert "原因" in terms
    assert _query_terms("Alice Acme")[:2] == ["alice", "acme"]


def test_graph_query_terms_expand_assistant_aliases():
    atri_terms = _query_terms("ATRI")
    assistant_terms = _query_terms("助手")

    assert "助手" in atri_terms
    assert "assistant" in atri_terms
    assert "atri" in assistant_terms


def test_graph_query_terms_preserve_cjk_phrases_and_expand_relation_predicates():
    terms = _query_terms("夜间维护策略变更导致了哪些故障")

    assert "夜间维护策略变更" in terms
    assert "策略变更" in terms
    assert "故障" in terms
    assert "causes" in terms
    assert "triggered_by" in terms
    assert "root_cause" in terms


def test_graph_query_terms_do_not_expand_negated_relation_triggers():
    terms = _query_terms("Alice 不属于 Beta 项目")

    assert "belongs_to" not in terms
    assert "part_of" not in terms
    assert "member_of" not in terms


def test_graph_query_terms_keep_longer_causal_trigger_priority():
    terms = _query_terms("根本原因是什么")

    assert "root_cause" in terms
    assert "causes" in terms


def test_graph_query_terms_expand_dependency_once_without_use_predicates():
    terms = _query_terms("Alice 依赖 Neo4j 图检索")

    assert "depends_on" in terms
    assert "uses" not in terms
    assert "built_with" not in terms

    use_terms = _query_terms("Alice 使用 Neo4j")
    assert "uses" in use_terms
    assert "built_with" in use_terms
    assert "depends_on" not in use_terms


def test_neo4j_graph_client_uses_weighted_query_terms_for_graph_scoring():
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    client.retrieve_context(
        query="谁负责 Neo4j 图检索",
        source_ids=[],
        max_facts=2,
        retrieval_depth=1,
        ranking_policy="hybrid",
    )

    call = next(
        call for call in driver.session_obj.calls if "RETURN s.name AS subject" in call["query"]
    )
    assert "term_row IN $term_rows" in call["query"]
    assert "term_row.term" in call["query"]
    assert "term_row.weight" in call["query"]

    term_rows = call["params"]["term_rows"]
    assert any(row["term"] == "responsible_for" for row in term_rows)
    assert any(row["term"] == "owner" for row in term_rows)
    assert next(row for row in term_rows if row["term"] == "负责")["weight"] >= 1.0
    assert all(row["weight"] > 0 for row in term_rows)


def test_neo4j_graph_client_reconnects_when_connection_config_changes():
    drivers = [FakeNeo4jDriver(), FakeNeo4jDriver()]
    calls = []

    def driver_factory(uri, auth):
        calls.append({"uri": uri, "auth": auth})
        return drivers[len(calls) - 1]

    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://old:7687",
            "username": "neo4j",
            "password": "old-secret",
            "database": "old-db",
        },
        driver_factory=driver_factory,
    )

    client.initialize()
    client.update_config(
        {
            "enabled": True,
            "uri": "bolt://new:7687",
            "username": "neo4j",
            "password": "new-secret",
            "database": "new-db",
        }
    )
    context = client.retrieve_context(query="Alice", source_ids=[], max_facts=1)

    assert drivers[0].closed is True
    assert drivers[1].verified is True
    assert drivers[1].database == "new-db"
    assert calls == [
        {"uri": "bolt://old:7687", "auth": ("neo4j", "old-secret")},
        {"uri": "bolt://new:7687", "auth": ("neo4j", "new-secret")},
    ]
    assert context == format_graph_context(
        ["- [1-hop] Alice -[works_at]-> Acme (Alice works at Acme.)"]
    )


def test_neo4j_graph_client_canonicalizes_assistant_aliases_in_retrieved_context():
    class AliasRetrieveSession(FakeNeo4jSession):
        def run(self, query, **params):
            self.calls.append({"query": query, "params": params})
            if "RETURN s.name AS subject" in query:
                return [
                    {
                        "subject": "助手",
                        "predicate": "can_help_with",
                        "object": "写代码",
                        "evidence": "助手可以写代码。",
                    },
                    {
                        "subject": "ATRI",
                        "predicate": "can_help_with",
                        "object": "写代码",
                        "evidence": "ATRI can help with coding.",
                    },
                ]
            return []

    driver = FakeNeo4jDriver()
    driver.session_obj = AliasRetrieveSession()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    context = client.retrieve_context(query="助手", source_ids=[], max_facts=2)

    assert context == format_graph_context(
        ["- [1-hop] ATRI -[can_help_with]-> 写代码 (助手可以写代码。)"]
    )


def test_neo4j_graph_client_retries_retrieve_after_defunct_connection():
    first_driver = FakeNeo4jDriver()
    first_driver.session_obj = FailingOnceRetrieveSession(
        RuntimeError("Failed to read from defunct connection")
    )
    second_driver = FakeNeo4jDriver()
    drivers = [first_driver, second_driver]
    calls = []

    def driver_factory(uri, auth):
        calls.append({"uri": uri, "auth": auth})
        return drivers[len(calls) - 1]

    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=driver_factory,
    )

    context = client.retrieve_context(query="Alice", source_ids=[], max_facts=1)

    assert first_driver.closed is True
    assert second_driver.verified is True
    assert len(calls) == 2
    assert context == format_graph_context(
        ["- [1-hop] Alice -[works_at]-> Acme (Alice works at Acme.)"]
    )


class FakeExtractor:
    async def extract_facts(self, text, *, source_id, source_kind, metadata=None):
        return normalize_extracted_facts(
            [
                {
                    "subject": "Alice",
                    "subject_type": "Person",
                    "predicate": "works_at",
                    "object": "Acme",
                    "object_type": "Company",
                }
            ],
            source_id=source_id,
            source_kind=source_kind,
        )


class RecordingExtractor:
    def __init__(self):
        self.calls = []

    async def extract_facts(self, text, *, source_id, source_kind, metadata=None):
        self.calls.append(
            {
                "text": text,
                "source_id": source_id,
                "source_kind": source_kind,
                "metadata": dict(metadata or {}),
            }
        )
        return normalize_extracted_facts(
            [
                {
                    "subject": "Alice",
                    "subject_type": "Person",
                    "predicate": "works_at",
                    "object": "Acme",
                    "object_type": "Company",
                }
            ],
            source_id=source_id,
            source_kind=source_kind,
            metadata=metadata,
        )


class HangingExtractor:
    def __init__(self):
        self.started = asyncio.Event()

    async def extract_facts(self, text, *, source_id, source_kind, metadata=None):
        self.started.set()
        await asyncio.Event().wait()
        return []


class RetryThenSucceedExtractor:
    def __init__(self, failures_before_success=1):
        self.failures_before_success = failures_before_success
        self.calls = []

    async def extract_facts(self, text, *, source_id, source_kind, metadata=None):
        self.calls.append({"text": text, "source_id": source_id, "source_kind": source_kind})
        if len(self.calls) <= self.failures_before_success:
            raise ValueError("invalid extraction JSON")
        return normalize_extracted_facts(
            [
                {
                    "subject": "Alice",
                    "subject_type": "Person",
                    "predicate": "works_at",
                    "object": "Acme",
                    "object_type": "Company",
                }
            ],
            source_id=source_id,
            source_kind=source_kind,
            metadata=metadata,
        )


class FailingSecondBatchExtractor:
    def __init__(self):
        self.calls = []

    async def extract_facts(self, text, *, source_id, source_kind, metadata=None):
        self.calls.append({"text": text, "source_id": source_id, "source_kind": source_kind})
        if "Bad batch" in text:
            raise ValueError("invalid extraction JSON")
        return normalize_extracted_facts(
            [
                {
                    "subject": "Alice",
                    "subject_type": "Person",
                    "predicate": "works_at",
                    "object": "Acme",
                    "object_type": "Company",
                }
            ],
            source_id=source_id,
            source_kind=source_kind,
            metadata=metadata,
        )


class ContextRecordingExtractor:
    def __init__(self):
        self.calls = []

    async def extract_facts(
        self,
        text,
        *,
        source_id,
        source_kind,
        metadata=None,
        existing_graph_context="",
    ):
        self.calls.append(
            {
                "text": text,
                "source_id": source_id,
                "source_kind": source_kind,
                "metadata": dict(metadata or {}),
                "existing_graph_context": existing_graph_context,
            }
        )
        return normalize_extracted_facts(
            [
                {
                    "subject": "Alice",
                    "subject_type": "Person",
                    "predicate": "works_on",
                    "object": "Neo4j graph extraction",
                    "object_type": "Project",
                }
            ],
            source_id=source_id,
            source_kind=source_kind,
            metadata=metadata,
        )


class FakeGraphClient:
    def __init__(self):
        self.facts = []
        self.retrieve_calls = []
        self.initialized = 0

    def update_config(self, config):
        self.config = config

    def initialize(self):
        self.initialized += 1
        return None

    def upsert_facts(self, facts):
        self.facts.extend(facts)
        return len(facts)

    def retrieve_context(
        self,
        *,
        query,
        source_ids=None,
        source_scores=None,
        max_facts=8,
        retrieval_depth=1,
        ranking_policy="hybrid",
        expansion_candidate_limit=40,
        include_entity_types=False,
    ):
        call = {
            "query": query,
            "source_ids": source_ids,
            "max_facts": max_facts,
            "retrieval_depth": retrieval_depth,
            "ranking_policy": ranking_policy,
            "expansion_candidate_limit": expansion_candidate_limit,
        }
        if source_scores:
            call["source_scores"] = source_scores
        if include_entity_types:
            call["include_entity_types"] = True
        self.retrieve_calls.append(call)
        return format_graph_context(["- Alice -[works_at]-> Acme"])

    def close(self):
        return None


@pytest.mark.asyncio
async def test_graph_manager_skips_worker_when_graph_disabled(tmp_path):
    store = TaskStore(tmp_path / "runtime")
    graph = FakeGraphClient()
    manager = GraphKnowledgeManager(
        config={
            "knowledge": {
                "graph": {
                    "enabled": False,
                    "extraction_enabled": True,
                    "extraction_sources": ["chat"],
                    "queue_max_size": 10,
                }
            }
        },
        graph_client=cast(Neo4jGraphClient, graph),
        extractor=cast(Any, FakeExtractor()),
        task_store=store,
    )
    try:
        await manager.initialize()

        assert manager.queue is None
        assert manager._worker_task is None
        assert graph.initialized == 0
    finally:
        await manager.close()
        store.close()


@pytest.mark.asyncio
async def test_graph_manager_update_config_starts_worker_after_graph_is_enabled(tmp_path):
    store = TaskStore(tmp_path / "runtime")
    graph = FakeGraphClient()
    manager = GraphKnowledgeManager(
        config={
            "knowledge": {
                "graph": {
                    "enabled": False,
                    "extraction_enabled": True,
                    "extraction_sources": ["chat"],
                    "queue_max_size": 10,
                }
            }
        },
        graph_client=cast(Neo4jGraphClient, graph),
        extractor=cast(Any, FakeExtractor()),
        task_store=store,
    )
    try:
        await manager.initialize()

        assert manager.queue is None
        assert manager._worker_task is None
        manager.update_config(
            {
                "knowledge": {
                    "graph": {
                        "enabled": True,
                        "extraction_enabled": True,
                        "extraction_sources": ["chat"],
                        "queue_max_size": 10,
                    }
                }
            }
        )
        task_id = manager.enqueue_chat_turn(
            user_text="Alice works at Acme.",
            assistant_text="Noted.",
            session_id="webchat:friend:session-1",
            platform="webchat",
            metadata={"message_type": "friend"},
        )
        await manager.drain(wait_seconds=2)

        assert manager.queue is not None
        assert manager._worker_task is not None
        assert task_id is not None
        assert store.get_task(task_id)["status"] == "completed"
    finally:
        await manager.close()
        store.close()


@pytest.mark.asyncio
async def test_graph_manager_processes_document_jobs_in_background(tmp_path):
    store = TaskStore(tmp_path / "runtime")
    graph = FakeGraphClient()
    manager = GraphKnowledgeManager(
        config={
            "knowledge": {
                "graph": {
                    "enabled": True,
                    "extraction_enabled": True,
                    "extraction_sources": ["documents"],
                    "queue_max_size": 10,
                }
            }
        },
        graph_client=cast(Neo4jGraphClient, graph),
        extractor=cast(Any, FakeExtractor()),
        task_store=store,
    )
    try:
        await manager.initialize()
        task_id = manager.enqueue_document(
            kb_id="kb-1",
            doc_id="doc-1",
            doc_name="notes.txt",
            chunks=[{"chunk_id": "chunk-1", "content": "Alice works at Acme."}],
        )
        await manager.drain(wait_seconds=2)

        assert task_id is not None
        task = store.get_task(task_id)
        assert task is not None
        assert task["status"] == "completed"
        assert graph.facts[0]["source_id"] == "chunk-1"
    finally:
        await manager.close()
        store.close()


@pytest.mark.asyncio
async def test_graph_manager_retrieves_existing_graph_context_before_extraction(tmp_path):
    store = TaskStore(tmp_path / "runtime")
    graph = FakeGraphClient()
    extractor = ContextRecordingExtractor()
    manager = GraphKnowledgeManager(
        config={
            "knowledge": {
                "graph": {
                    "enabled": True,
                    "extraction_enabled": True,
                    "extraction_sources": ["documents"],
                    "retrieval_enabled": True,
                    "retrieval_depth": 3,
                    "max_facts": 5,
                    "expansion_candidate_limit": 24,
                    "ranking_policy": "latest",
                    "queue_max_size": 10,
                }
            }
        },
        graph_client=cast(Neo4jGraphClient, graph),
        extractor=cast(Any, extractor),
        task_store=store,
    )
    try:
        await manager.initialize()
        task_id = manager.enqueue_document(
            kb_id="kb-1",
            doc_id="doc-1",
            doc_name="notes.txt",
            chunks=[
                {
                    "chunk_id": "chunk-context",
                    "content": "Alice now works on Neo4j graph extraction.",
                }
            ],
        )
        await manager.drain(wait_seconds=2)

        assert task_id is not None
        assert graph.retrieve_calls == [
            {
                "query": "Alice now works on Neo4j graph extraction.",
                "source_ids": [],
                "max_facts": 5,
                "retrieval_depth": 3,
                "ranking_policy": "latest",
                "expansion_candidate_limit": 24,
                "include_entity_types": True,
            }
        ]
        assert extractor.calls[0]["existing_graph_context"] == format_graph_context(
            ["- Alice -[works_at]-> Acme"]
        )
        assert EXISTING_GRAPH_CONTEXT_METADATA_KEY not in graph.facts[0]["metadata"]
    finally:
        await manager.close()
        store.close()


def test_plain_text_extraction_batches_keeps_short_text_single():
    text = "Alice works at Acme."

    batches = _plain_text_extraction_batches(text)

    assert batches == [text]


def test_plain_text_extraction_batches_overlap_increases_total_coverage():
    text = ("Alpha. " * 6000).strip()

    batches = _plain_text_extraction_batches(text)

    assert len(batches) > 1
    assert sum(len(batch) for batch in batches) > len(text)


def test_plain_text_extraction_batches_uses_tuple_aligned_window():
    text = ("Alpha stores one fact. " * 700).strip()

    batches = _plain_text_extraction_batches(text)

    assert len(text) < GRAPH_EXTRACTION_BATCH_CHARS
    assert len(batches) > 1
    assert all(len(batch) <= MAX_EXTRACTION_TUPLES * 400 for batch in batches)


def test_document_extraction_batches_uses_tuple_aligned_window():
    chunks = [
        {"chunk_id": f"chunk-{index}", "content": ("Fact sentence. " * 70).strip()}
        for index in range(20)
    ]

    batches = _document_extraction_batches(chunks, "doc-task")

    assert len(batches) > 1
    assert all(len(_document_batch_text(batch)) <= MAX_EXTRACTION_TUPLES * 400 for batch in batches)


def test_build_segmented_user_content_adds_part_annotation():
    content = _build_segmented_user_content(
        "Alice works at Acme.",
        {"text_part_index": 2, "text_part_count": 3},
    )

    assert content.startswith("[文本分段 2/3]")
    assert "仅抽取本段文本中明确支持的事实" in content
    assert content.endswith("Alice works at Acme.")


def test_build_segmented_user_content_omits_annotation_for_single_part():
    content = _build_segmented_user_content(
        "Alice works at Acme.",
        {"text_part_index": 1, "text_part_count": 1},
    )

    assert content == "Alice works at Acme."


def test_build_segmented_user_content_includes_reference_date_for_chat():
    content = _build_segmented_user_content(
        "今天下午开会。",
        {REFERENCE_DATE_METADATA_KEY: "2026-06-08"},
    )

    assert content.startswith("[参考日期] 2026-06-08")
    assert "仅用于本段 chat 对话" in content
    assert "不要用于 document/叙述性文本" in content
    assert content.endswith("今天下午开会。")


def test_extraction_text_segments_uses_semantic_chunk_markers_for_long_manual_text():
    paragraphs = [f"Paragraph {index}: Alice works at Acme." for index in range(1, 1200)]
    text = "\n\n".join(paragraphs)

    segments = _extraction_text_segments(text, semantic_chunking=True)

    assert len(text) > GRAPH_EXTRACTION_BATCH_CHARS
    assert len(segments) > 1
    assert any("[Chunk " in segment for segment in segments)


def test_plain_text_extraction_batches_splits_text_over_batch_limit():
    text = ("Alpha. " * 6000).strip()

    batches = _plain_text_extraction_batches(text)

    assert len(text) > GRAPH_EXTRACTION_BATCH_CHARS
    assert len(batches) > 1
    assert all(len(batch) <= GRAPH_EXTRACTION_BATCH_CHARS for batch in batches)


@pytest.mark.asyncio
async def test_graph_manager_manual_ingest_queues_direct_document_extraction(tmp_path):
    store = TaskStore(tmp_path / "runtime")
    graph = FakeGraphClient()
    extractor = RecordingExtractor()
    manager = GraphKnowledgeManager(
        config={
            "knowledge": {
                "graph": {
                    "enabled": True,
                    "extraction_enabled": False,
                    "extraction_sources": ["chat"],
                    "queue_max_size": 10,
                }
            }
        },
        graph_client=cast(Neo4jGraphClient, graph),
        extractor=cast(Any, extractor),
        task_store=store,
    )
    try:
        await manager.initialize()
        task_id = manager.enqueue_manual_ingest(
            text="Alice works at Acme.",
            source_name="manual-notes.md",
        )
        await manager.drain(wait_seconds=2)

        assert task_id is not None
        task = store.get_task(task_id)
        assert task is not None
        assert task["status"] == "completed"
        assert task["metadata"]["source"] == "manual"
        assert task["metadata"]["source_name"] == "manual-notes.md"
        assert extractor.calls == [
            {
                "text": "Alice works at Acme.",
                "source_id": f"manual:{task_id}",
                "source_kind": "document",
                "metadata": {
                    "source": "manual",
                    "source_name": "manual-notes.md",
                    "source_id": f"manual:{task_id}",
                },
            }
        ]
        assert graph.facts[0]["source_id"] == f"manual:{task_id}"
        assert graph.facts[0]["source_kind"] == "document"
        assert graph.facts[0]["metadata"]["source"] == "manual"
    finally:
        await manager.close()
        store.close()


@pytest.mark.asyncio
async def test_graph_manager_manual_ingest_batches_text_over_batch_limit(tmp_path):
    store = TaskStore(tmp_path / "runtime")
    graph = FakeGraphClient()
    extractor = RecordingExtractor()
    manager = GraphKnowledgeManager(
        config={
            "knowledge": {
                "graph": {
                    "enabled": True,
                    "extraction_enabled": False,
                    "extraction_sources": ["chat"],
                    "queue_max_size": 10,
                }
            }
        },
        graph_client=cast(Neo4jGraphClient, graph),
        extractor=cast(Any, extractor),
        task_store=store,
    )
    long_text = ("Alice works at Acme. " * 2000).strip()
    try:
        await manager.initialize()
        task_id = manager.enqueue_manual_ingest(
            text=long_text,
            source_name="manual-notes.md",
        )
        await manager.drain(wait_seconds=2)

        assert task_id is not None
        assert len(long_text) > GRAPH_EXTRACTION_BATCH_CHARS
        assert len(extractor.calls) > 1
        assert all(len(call["text"]) <= GRAPH_EXTRACTION_BATCH_CHARS for call in extractor.calls)
        assert extractor.calls[0]["source_id"] == f"manual:{task_id}:part-1"
        assert extractor.calls[1]["source_id"] == f"manual:{task_id}:part-2"
        assert extractor.calls[0]["metadata"]["text_part_count"] == len(extractor.calls)
    finally:
        await manager.close()
        store.close()


@pytest.mark.asyncio
async def test_graph_manager_batches_document_chunks_without_losing_source_ids(tmp_path):
    store = TaskStore(tmp_path / "runtime")
    graph = FakeGraphClient()
    extractor = RecordingExtractor()
    manager = GraphKnowledgeManager(
        config={
            "knowledge": {
                "graph": {
                    "enabled": True,
                    "extraction_enabled": True,
                    "extraction_sources": ["documents"],
                    "queue_max_size": 10,
                }
            }
        },
        graph_client=cast(Neo4jGraphClient, graph),
        extractor=cast(Any, extractor),
        task_store=store,
    )
    try:
        await manager.initialize()
        task_id = manager.enqueue_document(
            kb_id="kb-1",
            doc_id="doc-1",
            doc_name="notes.txt",
            chunks=[
                {"chunk_id": "chunk-1", "content": "Alice works at Acme."},
                {"chunk_id": "chunk-2", "content": "Acme uses Neo4j."},
                {"chunk_id": "chunk-3", "content": "Neo4j stores facts."},
            ],
        )
        await manager.drain(wait_seconds=2)

        assert task_id is not None
        task = store.get_task(task_id)
        assert task is not None
        assert task["status"] == "completed"
        assert len(extractor.calls) == 1
        assert "[Chunk 1]" in extractor.calls[0]["text"]
        assert "[Chunk 3]" in extractor.calls[0]["text"]
        assert "chunk-1" not in extractor.calls[0]["text"]
        assert "chunk-3" not in extractor.calls[0]["text"]
        assert extractor.calls[0]["source_id"] == "chunk-1"
        assert extractor.calls[0]["metadata"]["chunk_ids"] == [
            "chunk-1",
            "chunk-2",
            "chunk-3",
        ]
        assert graph.facts[0]["source_id"] == "chunk-1"
        assert graph.facts[0]["source_ids"] == ["chunk-1", "chunk-2", "chunk-3"]
    finally:
        await manager.close()
        store.close()


@pytest.mark.asyncio
async def test_graph_manager_chat_facts_use_stable_source_id_not_task_id(tmp_path):
    store = TaskStore(tmp_path / "runtime")
    graph = FakeGraphClient()
    manager = GraphKnowledgeManager(
        config={
            "knowledge": {
                "graph": {
                    "enabled": True,
                    "extraction_enabled": True,
                    "extraction_sources": ["chat"],
                    "queue_max_size": 10,
                }
            }
        },
        graph_client=cast(Neo4jGraphClient, graph),
        extractor=cast(Any, FakeExtractor()),
        task_store=store,
    )
    try:
        await manager.initialize()
        task_id = manager.enqueue_chat_turn(
            user_text="Alice works at Acme.",
            assistant_text="Noted.",
            session_id="webchat:friend:session-1",
            platform="webchat",
            metadata={"message_type": "friend"},
        )
        await manager.drain(wait_seconds=2)

        assert task_id is not None
        task = store.get_task(task_id)
        assert task is not None
        assert task["status"] == "completed"
        assert graph.facts[0]["source_id"] == "chat:webchat:friend:session-1"
        assert graph.facts[0]["source_id"] != task_id
        assert "extraction_task_id" not in graph.facts[0]["metadata"]
    finally:
        await manager.close()
        store.close()


@pytest.mark.asyncio
async def test_graph_manager_retries_transient_extraction_failures(tmp_path):
    store = TaskStore(tmp_path / "runtime")
    graph = FakeGraphClient()
    extractor = RetryThenSucceedExtractor(failures_before_success=2)
    manager = GraphKnowledgeManager(
        config={
            "knowledge": {
                "graph": {
                    "enabled": True,
                    "extraction_enabled": True,
                    "extraction_sources": ["chat"],
                    "queue_max_size": 10,
                }
            }
        },
        graph_client=cast(Neo4jGraphClient, graph),
        extractor=cast(Any, extractor),
        task_store=store,
    )
    try:
        await manager.initialize()
        task_id = manager.enqueue_chat_turn(
            user_text="Alice works at Acme.",
            assistant_text="Noted.",
            session_id="webchat:friend:session-1",
            platform="webchat",
            metadata={"message_type": "friend"},
        )
        await manager.drain(wait_seconds=2)

        assert task_id is not None
        task = store.get_task(task_id)
        assert task is not None
        assert task["status"] == "completed"
        assert len(extractor.calls) == 3
        assert len(graph.facts) == 1
        assert graph.facts[0]["subject"] == "Alice"
    finally:
        await manager.close()
        store.close()


@pytest.mark.asyncio
async def test_graph_manager_marks_chat_extraction_timed_out_and_drains(tmp_path, caplog):
    store = TaskStore(tmp_path / "runtime")
    extractor = HangingExtractor()
    graph = FakeGraphClient()
    manager = GraphKnowledgeManager(
        config={
            "knowledge": {
                "graph": {
                    "enabled": True,
                    "extraction_enabled": True,
                    "extraction_sources": ["chat"],
                    "extraction_timeout_seconds": 0.01,
                    "queue_max_size": 10,
                }
            }
        },
        graph_client=cast(Neo4jGraphClient, graph),
        extractor=cast(Any, extractor),
        task_store=store,
    )
    caplog.set_level(logging.WARNING, logger="atri")
    try:
        await manager.initialize()
        task_id = manager.enqueue_chat_turn(
            user_text="Alice works at Acme.",
            assistant_text="Noted.",
            session_id="webchat:friend:session-1",
            platform="webchat",
            metadata={"message_type": "friend"},
        )

        await asyncio.wait_for(manager.drain(wait_seconds=0.5), timeout=1)

        assert task_id is not None
        task = store.get_task(task_id)
        events = store.events(task_id)
        assert task is not None
        assert task["status"] == "completed"
        assert task["metadata"]["failed_extraction_count"] == 1
        assert "timed out" in task["metadata"]["failed_extractions"][0]["error"]
        assert graph.facts == []
        assert any(event.event_type == "graph_extraction_skipped" for event in events)
        assert "Graph extraction timed out" in caplog.text
    finally:
        await manager.close(drain_seconds=0.01)
        store.close()


@pytest.mark.asyncio
async def test_graph_manager_skips_document_batches_after_retries_fail(tmp_path):
    store = TaskStore(tmp_path / "runtime")
    graph = FakeGraphClient()
    extractor = FailingSecondBatchExtractor()
    manager = GraphKnowledgeManager(
        config={
            "knowledge": {
                "graph": {
                    "enabled": True,
                    "extraction_enabled": True,
                    "extraction_sources": ["documents"],
                    "queue_max_size": 10,
                }
            }
        },
        graph_client=cast(Neo4jGraphClient, graph),
        extractor=cast(Any, extractor),
        task_store=store,
    )
    try:
        await manager.initialize()
        task_id = manager.enqueue_document(
            kb_id="kb-1",
            doc_id="doc-1",
            doc_name="notes.txt",
            chunks=[
                {"chunk_id": "chunk-1", "content": "Alice works at Acme."},
                {"chunk_id": "chunk-2", "content": "Bad batch. " * 3635},
            ],
        )
        await manager.drain(wait_seconds=2)

        assert task_id is not None
        task = store.get_task(task_id)
        events = store.events(task_id)
        assert task is not None
        assert task["status"] == "completed"
        failed_count = task["metadata"]["failed_extraction_count"]
        assert failed_count > 1
        assert len(extractor.calls) == 1 + failed_count * 3
        assert all(
            item["source_id"].startswith("chunk-2:part-")
            for item in task["metadata"]["failed_extractions"]
        )
        assert len(graph.facts) == 1
        assert graph.facts[0]["source_id"] == "chunk-1"
        assert any(event.event_type == "graph_extraction_skipped" for event in events)
    finally:
        await manager.close()
        store.close()


def test_graph_manager_enqueue_is_safe_when_disabled_or_full(tmp_path):
    store = TaskStore(tmp_path / "runtime")
    manager = GraphKnowledgeManager(
        config={
            "knowledge": {
                "graph": {
                    "enabled": True,
                    "extraction_enabled": True,
                    "extraction_sources": ["chat"],
                    "queue_max_size": 0,
                }
            }
        },
        graph_client=cast(Neo4jGraphClient, FakeGraphClient()),
        extractor=cast(Any, FakeExtractor()),
        task_store=store,
    )
    try:
        assert (
            manager.enqueue_chat_turn(
                user_text="Alice works at Acme.",
                assistant_text="Noted.",
                session_id="webchat:friend:default",
                platform="webchat",
                metadata={},
            )
            is None
        )
        assert (
            manager.enqueue_manual_ingest(
                text="Alice works at Acme.",
            )
            is None
        )
    finally:
        store.close()


@pytest.mark.asyncio
async def test_graph_manager_close_marks_running_extraction_interrupted(tmp_path):
    store = TaskStore(tmp_path / "runtime")
    extractor = HangingExtractor()
    manager = GraphKnowledgeManager(
        config={
            "knowledge": {
                "graph": {
                    "enabled": True,
                    "extraction_enabled": True,
                    "extraction_sources": ["chat"],
                    "queue_max_size": 10,
                }
            }
        },
        graph_client=cast(Neo4jGraphClient, FakeGraphClient()),
        extractor=cast(Any, extractor),
        task_store=store,
    )
    unrelated = store.create_task(kind="sub_agent", title="agent", input_text="work")
    store.start_task(unrelated)
    try:
        await manager.initialize()
        task_id = manager.enqueue_chat_turn(
            user_text="Alice works at Acme.",
            assistant_text="Noted.",
            session_id="webchat:friend:session-1",
            platform="webchat",
            metadata={"message_type": "friend"},
        )
        await asyncio.wait_for(extractor.started.wait(), timeout=2)

        assert task_id is not None
        running_task = store.get_task(task_id)
        assert running_task is not None
        assert running_task["status"] == "running"

        await manager.close(drain_seconds=0.01)

        interrupted_task = store.get_task(task_id)
        unrelated_task = store.get_task(unrelated)
        assert interrupted_task is not None
        assert unrelated_task is not None
        assert interrupted_task["status"] == "interrupted"
        assert "graph extraction interrupted" in interrupted_task["error"]
        assert unrelated_task["status"] == "running"
    finally:
        if manager._worker_task is not None:
            await manager.close(drain_seconds=0)
        store.close()


def test_graph_manager_uses_configured_extraction_model_from_chat_pool(monkeypatch, tmp_path):
    captured = {}

    class FakeLLM:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("core.knowledge.graph_worker.LLM", FakeLLM)
    store = TaskStore(tmp_path / "runtime")
    manager = GraphKnowledgeManager(
        config={
            "model": "chat-current",
            "model_provider": "Fallback",
            "api_key": "fallback-root-key",
            "base_url": "https://fallback.test/v1",
            "api_format": "openai",
            "providers": {
                "Fallback": {
                    "api_key": "fallback-provider-key",
                    "base_url": "https://fallback-provider.test/v1",
                    "api_format": "openai",
                },
                "OpenAI": {
                    "api_key": "graph-key",
                    "base_url": "https://graph.test/v1",
                    "api_format": "openai",
                },
            },
            "active_models": [
                {
                    "model": "graph-chat",
                    "provider": "OpenAI",
                    "config": {"temperature": 0.2, "max_tokens": 2048},
                }
            ],
            "knowledge": {
                "graph": {
                    "enabled": True,
                    "extraction_model": "graph-chat",
                    "extraction_provider": "OpenAI",
                }
            },
        },
        graph_client=cast(Neo4jGraphClient, FakeGraphClient()),
        extractor=cast(Any, FakeExtractor()),
        task_store=store,
    )
    try:
        manager._create_llm()

        assert captured["model"] == "graph-chat"
        assert captured["api_key"] == "graph-key"
        assert captured["base_url"] == "https://graph.test/v1"
        assert captured["api_format"] == "openai"
        assert captured["temperature"] == 0.2
        assert captured["max_tokens"] == 2048
    finally:
        store.close()


def test_graph_manager_uses_larger_default_token_budget_for_inherited_extraction_model(
    monkeypatch,
    tmp_path,
):
    captured = {}

    class FakeLLM:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("core.knowledge.graph_worker.LLM", FakeLLM)
    store = TaskStore(tmp_path / "runtime")
    manager = GraphKnowledgeManager(
        config={
            "model": "deepseek-v4-pro",
            "model_provider": "DS_A",
            "api_key": "root-key",
            "base_url": "https://root.test/anthropic",
            "api_format": "anthropic",
            "providers": {
                "DS_A": {
                    "api_key": "provider-key",
                    "base_url": "https://provider.test/anthropic",
                    "api_format": "anthropic",
                },
            },
            "active_models": [
                {
                    "model": "deepseek-v4-pro",
                    "provider": "DS_A",
                    "config": {"temperature": 0.5, "max_tokens": 20000},
                }
            ],
            "knowledge": {"graph": {"enabled": True}},
        },
        graph_client=cast(Neo4jGraphClient, FakeGraphClient()),
        extractor=cast(Any, FakeExtractor()),
        task_store=store,
    )
    try:
        manager._create_llm()

        assert captured["model"] == "deepseek-v4-pro"
        assert captured["api_format"] == "anthropic"
        assert captured["temperature"] == 0.0
        assert captured["max_tokens"] == 4096
    finally:
        store.close()


@pytest.mark.asyncio
async def test_graph_manager_passes_retrieval_depth_to_graph_client(tmp_path):
    store = TaskStore(tmp_path / "runtime")
    graph = FakeGraphClient()
    manager = GraphKnowledgeManager(
        config={
            "knowledge": {
                "graph": {
                    "enabled": True,
                    "retrieval_enabled": True,
                    "retrieval_depth": 7,
                }
            }
        },
        graph_client=cast(Neo4jGraphClient, graph),
        extractor=cast(Any, FakeExtractor()),
        task_store=store,
    )
    try:
        context = await manager.retrieve_context(query="Alice", source_ids=[], max_facts=5)

        assert context == format_graph_context(["- Alice -[works_at]-> Acme"])
        assert graph.retrieve_calls == [
            {
                "query": "Alice",
                "source_ids": [],
                "max_facts": 5,
                "retrieval_depth": 7,
                "ranking_policy": "hybrid",
                "expansion_candidate_limit": 40,
            }
        ]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_graph_manager_passes_source_scores_to_graph_client(tmp_path):
    store = TaskStore(tmp_path / "runtime")
    graph = FakeGraphClient()
    manager = GraphKnowledgeManager(
        config={
            "knowledge": {
                "graph": {
                    "enabled": True,
                    "retrieval_enabled": True,
                }
            }
        },
        graph_client=cast(Neo4jGraphClient, graph),
        extractor=cast(Any, FakeExtractor()),
        task_store=store,
    )
    try:
        context = await manager.retrieve_context(
            query="Alice",
            source_ids=["chunk-1"],
            source_scores={"chunk-1": 0.82},
            max_facts=5,
        )

        assert context == format_graph_context(["- Alice -[works_at]-> Acme"])
        assert graph.retrieve_calls == [
            {
                "query": "Alice",
                "source_ids": ["chunk-1"],
                "source_scores": {"chunk-1": 0.82},
                "max_facts": 5,
                "retrieval_depth": 3,
                "ranking_policy": "hybrid",
                "expansion_candidate_limit": 40,
            }
        ]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_graph_manager_logs_retrieval_metrics(caplog, tmp_path):
    caplog.set_level(logging.INFO, logger="atri")
    store = TaskStore(tmp_path / "runtime")
    graph = FakeGraphClient()
    manager = GraphKnowledgeManager(
        config={
            "knowledge": {
                "graph": {
                    "enabled": True,
                    "retrieval_enabled": True,
                    "retrieval_depth": 3,
                }
            }
        },
        graph_client=cast(Neo4jGraphClient, graph),
        extractor=cast(Any, FakeExtractor()),
        task_store=store,
    )
    try:
        await manager.retrieve_context(
            query="Alice",
            source_ids=["chunk-1"],
            max_facts=5,
        )

        assert "Graph knowledge retrieval done" not in caplog.text

        caplog.clear()
        caplog.set_level(logging.DEBUG, logger="atri")
        await manager.retrieve_context(
            query="Alice",
            source_ids=["chunk-1"],
            max_facts=5,
        )

        assert "Graph knowledge retrieval done" in caplog.text
        assert "depth=3" in caplog.text
        assert "source_ids_count=1" in caplog.text
        assert "max_facts=5" in caplog.text
    finally:
        store.close()


@pytest.mark.asyncio
async def test_graph_manager_logs_graph_timing_segments_for_direct_retrieval(caplog, tmp_path):
    class TimedGraphClient(FakeGraphClient):
        def retrieve_context(
            self,
            *,
            query,
            source_ids=None,
            source_scores=None,
            max_facts=8,
            retrieval_depth=1,
            ranking_policy="hybrid",
            expansion_candidate_limit=40,
            include_entity_types=False,
            timings=None,
        ):
            if timings is not None:
                timings.update(
                    {
                        "graph_total_ms": 13.7,
                        "graph_single_hop_ms": 4.1,
                        "graph_multi_hop_ms": 8.2,
                        "graph_scan_fallback_ms": 0.0,
                        "graph_format_ms": 1.4,
                        "graph_rows": 1686,
                        "graph_returned_facts": 75,
                        "graph_multihop_seed_count": 11,
                        "graph_multihop_cache_hit": True,
                        "graph_multihop_cached_seed_count": 9,
                        "graph_multihop_live_seed_limit": 31,
                        "graph_multihop_partial_cache_hit": True,
                        "graph_multihop_persistent_cache_hit_count": 4,
                    }
                )
            return super().retrieve_context(
                query=query,
                source_ids=source_ids,
                source_scores=source_scores,
                max_facts=max_facts,
                retrieval_depth=retrieval_depth,
                ranking_policy=ranking_policy,
                expansion_candidate_limit=expansion_candidate_limit,
                include_entity_types=include_entity_types,
            )

    caplog.set_level(logging.DEBUG, logger="atri")
    store = TaskStore(tmp_path / "runtime")
    manager = GraphKnowledgeManager(
        config={
            "knowledge": {
                "graph": {
                    "enabled": True,
                    "retrieval_enabled": True,
                    "retrieval_depth": 3,
                }
            }
        },
        graph_client=cast(Neo4jGraphClient, TimedGraphClient()),
        extractor=cast(Any, FakeExtractor()),
        task_store=store,
    )
    try:
        await manager.retrieve_context(
            query="Alice",
            source_ids=[],
            max_facts=75,
        )

        assert "Graph knowledge retrieval done" in caplog.text
        for field in (
            "graph_total_ms=13.7",
            "graph_single_hop_ms=4.1",
            "graph_multi_hop_ms=8.2",
            "graph_scan_fallback_ms=0.0",
            "graph_format_ms=1.4",
            "graph_rows=1686",
            "graph_returned_facts=75",
            "graph_multihop_seed_count=11",
            "graph_multihop_cache_hit=True",
            "graph_multihop_cached_seed_count=9",
            "graph_multihop_live_seed_limit=31",
            "graph_multihop_partial_cache_hit=True",
            "graph_multihop_persistent_cache_hit_count=4",
        ):
            assert field in caplog.text
    finally:
        store.close()


@pytest.mark.asyncio
async def test_graph_manager_passes_timing_dict_to_var_keyword_graph_client(tmp_path):
    class KwargsTimedGraphClient(FakeGraphClient):
        def retrieve_context(self, **kwargs):
            timings = kwargs.pop("timings", None)
            if timings is not None:
                timings["graph_total_ms"] = 9.9
            return super().retrieve_context(**kwargs)

    store = TaskStore(tmp_path / "runtime")
    manager = GraphKnowledgeManager(
        config={
            "knowledge": {
                "graph": {
                    "enabled": True,
                    "retrieval_enabled": True,
                    "retrieval_depth": 3,
                }
            }
        },
        graph_client=cast(Neo4jGraphClient, KwargsTimedGraphClient()),
        extractor=cast(Any, FakeExtractor()),
        task_store=store,
    )
    timings: dict[str, Any] = {}
    try:
        await manager.retrieve_context(
            query="Alice",
            source_ids=[],
            max_facts=5,
            timings=timings,
        )

        assert timings["graph_total_ms"] == 9.9
    finally:
        store.close()


@pytest.mark.asyncio
async def test_graph_manager_passes_configured_ranking_policy_to_graph_client(tmp_path):
    store = TaskStore(tmp_path / "runtime")
    graph = FakeGraphClient()
    manager = GraphKnowledgeManager(
        config={
            "knowledge": {
                "graph": {
                    "enabled": True,
                    "retrieval_enabled": True,
                    "ranking_policy": "relevance",
                }
            }
        },
        graph_client=cast(Neo4jGraphClient, graph),
        extractor=cast(Any, FakeExtractor()),
        task_store=store,
    )
    try:
        context = await manager.retrieve_context(query="Alice", source_ids=[], max_facts=5)

        assert context == format_graph_context(["- Alice -[works_at]-> Acme"])
        assert graph.retrieve_calls == [
            {
                "query": "Alice",
                "source_ids": [],
                "max_facts": 5,
                "retrieval_depth": 3,
                "ranking_policy": "relevance",
                "expansion_candidate_limit": 40,
            }
        ]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_graph_manager_passes_configured_expansion_candidate_limit_to_graph_client(tmp_path):
    store = TaskStore(tmp_path / "runtime")
    graph = FakeGraphClient()
    manager = GraphKnowledgeManager(
        config={
            "knowledge": {
                "graph": {
                    "enabled": True,
                    "retrieval_enabled": True,
                    "expansion_candidate_limit": 72,
                }
            }
        },
        graph_client=cast(Neo4jGraphClient, graph),
        extractor=cast(Any, FakeExtractor()),
        task_store=store,
    )
    try:
        context = await manager.retrieve_context(query="Alice", source_ids=[], max_facts=5)

        assert context == format_graph_context(["- Alice -[works_at]-> Acme"])
        assert graph.retrieve_calls == [
            {
                "query": "Alice",
                "source_ids": [],
                "max_facts": 5,
                "retrieval_depth": 3,
                "ranking_policy": "hybrid",
                "expansion_candidate_limit": 72,
            }
        ]
    finally:
        store.close()
