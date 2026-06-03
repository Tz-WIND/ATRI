import asyncio
import json
from pathlib import Path
from typing import Any, cast

import pytest

from core.knowledge.extraction import (
    MAX_EXTRACTION_TUPLES,
    MAX_HYPER_CHAIN_EDGES,
    MAX_HYPER_ROLES,
    MAX_HYPER_TUPLES,
    GraphTupleExtractor,
    build_extraction_prompt,
    normalize_extracted_facts,
    parse_extraction_json,
)
from core.knowledge.graph import Neo4jGraphClient, _query_terms
from core.knowledge.graph_constants import CHAIN_ORDER_KEY_SEPARATOR, format_graph_context
from core.knowledge.graph_worker import GraphKnowledgeManager, _chat_turn_text
from core.runtime.tasks import TaskStore


def test_build_extraction_prompt_prefers_person_tool_links_for_chat():
    prompt = build_extraction_prompt("chat")

    assert "Person 为 subject 的边" in prompt
    assert "不要用 User、用户" in prompt
    assert "林晚 -[uses]-> Ableton Live" in prompt
    assert "孤立 Concept" in prompt
    assert "专有名词保持英文" in prompt
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


def test_format_graph_context_includes_usage_guidance():
    context = format_graph_context(["- Alice -[works_at]-> Acme"])

    assert context.startswith("[Graph context]\n")
    assert "跨跳串联" in context
    assert "predicate 保持原文" in context
    assert context.endswith("- Alice -[works_at]-> Acme")


def test_chain_order_separator_is_shared_and_parameterized_in_cypher():
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
                    "predicate": "works_at",
                    "object": "Acme",
                    "evidence": "Alice works at Acme.",
                    "confidence": 0.9,
                    "hop": 1,
                },
                {
                    "subject": "Acme",
                    "predicate": "uses",
                    "object": "Neo4j",
                    "evidence": "Acme uses Neo4j.",
                    "confidence": 0.8,
                    "hop": 2,
                },
            ]
        if "RETURN s.name AS subject" in query:
            return [
                {
                    "subject": "Alice",
                    "predicate": "works_at",
                    "object": "Acme",
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
    assert context == format_graph_context(["- Alice -[works_at]-> Acme (Alice works at Acme.)"])
    assert driver.closed is True


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

    retrieve_calls = [
        call
        for call in driver.session_obj.calls
        if "RETURN s.name AS subject" in call["query"]
        or "RETURN startNode(r).name AS subject" in call["query"]
    ]
    assert retrieve_calls[0]["params"]["limit"] == 4
    assert "FACT*1..2" in retrieve_calls[1]["query"]
    assert retrieve_calls[1]["params"]["limit"] == 40
    assert context == format_graph_context(
        [
            (
                "- [1-hop] Alice -[works_at]-> Acme (Alice works at Acme.) "
                "| linked: [2-hop] Acme -[uses]-> Neo4j (Acme uses Neo4j.)"
            ),
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

    retrieve_calls = [
        call
        for call in driver.session_obj.calls
        if "RETURN s.name AS subject" in call["query"]
        or "RETURN startNode(r).name AS subject" in call["query"]
    ]
    assert len(retrieve_calls) == 2
    assert "MATCH (s:Entity)-[r:FACT]->(o:Entity)" in retrieve_calls[0]["query"]
    assert "FACT*1..2" in retrieve_calls[1]["query"]
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

    query = driver.session_obj.calls[-1]["query"]
    assert "ORDER BY structural_role ASC, hop ASC, r.updated_at DESC" in query
    assert driver.session_obj.calls[-1]["params"]["ranking_policy"] == "latest"


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

    query = driver.session_obj.calls[-1]["query"]
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
    assert (
        driver.session_obj.calls[-1]["params"]["chain_order_separator"] == CHAIN_ORDER_KEY_SEPARATOR
    )
    assert "toLower(coalesce(r[$hyper_role_property], '')) CONTAINS term" in query
    assert "r[$hyper_role_property] AS hyper_role" in query
    assert "rel.hyper_role" not in query
    assert "r.hyper_role" not in query


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
    assert context == format_graph_context(["- Alice -[works_at]-> Acme (Alice works at Acme.)"])


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

    assert context == format_graph_context(["- ATRI -[can_help_with]-> 写代码 (助手可以写代码。)"])


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
    assert context == format_graph_context(["- Alice -[works_at]-> Acme (Alice works at Acme.)"])


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
        max_facts=8,
        retrieval_depth=1,
        ranking_policy="hybrid",
        expansion_candidate_limit=40,
    ):
        self.retrieve_calls.append(
            {
                "query": query,
                "source_ids": source_ids,
                "max_facts": max_facts,
                "retrieval_depth": retrieval_depth,
                "ranking_policy": ranking_policy,
                "expansion_candidate_limit": expansion_candidate_limit,
            }
        )
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
                {"chunk_id": "chunk-2", "content": "Bad batch. " * 1200},
            ],
        )
        await manager.drain(wait_seconds=2)

        assert task_id is not None
        task = store.get_task(task_id)
        events = store.events(task_id)
        assert task is not None
        assert task["status"] == "completed"
        assert task["metadata"]["failed_extraction_count"] == 1
        assert len(extractor.calls) == 4
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
                "retrieval_depth": 1,
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
                "retrieval_depth": 1,
                "ranking_policy": "hybrid",
                "expansion_candidate_limit": 72,
            }
        ]
    finally:
        store.close()
