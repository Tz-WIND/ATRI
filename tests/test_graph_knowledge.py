import asyncio
import json
from pathlib import Path
from typing import Any, cast

import pytest

from core.knowledge.extraction import (
    MAX_HYPER_CHAIN_EDGES,
    MAX_HYPER_ROLES,
    MAX_HYPER_TUPLES,
    GraphTupleExtractor,
    normalize_extracted_facts,
)
from core.knowledge.graph import Neo4jGraphClient, _query_terms
from core.knowledge.graph_constants import CHAIN_ORDER_KEY_SEPARATOR
from core.knowledge.graph_worker import GraphKnowledgeManager, _chat_turn_text
from core.runtime.tasks import TaskStore


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

    assert ("Bob", "configured", "Neo4j") in by_edge
    assert ("Alice", "configured", "Neo4j") not in by_edge


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
    assert "durable, useful, explicitly supported facts" in system_prompt
    assert "Do NOT extract tuples like user asked/requested/said" in system_prompt
    assert "For chat text:" in system_prompt
    assert "Example skip: User -[requested]-> screenshot" in system_prompt
    assert "hyper_tuples" in system_prompt
    assert "chain" in system_prompt
    assert f"At most {MAX_HYPER_TUPLES} hyper_tuples" in system_prompt
    assert f"at most {MAX_HYPER_ROLES} roles" in system_prompt
    assert f"at most {MAX_HYPER_CHAIN_EDGES} chain edges" in system_prompt
    assert "Limit output to the 12 most useful tuples" in system_prompt


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
    assert context == "[Graph context]\n- Alice -[works_at]-> Acme (Alice works at Acme.)"
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

    assert "FACT*1..2" in driver.session_obj.calls[-1]["query"]
    assert driver.session_obj.calls[-1]["params"]["limit"] == 4
    assert context == (
        "[Graph context]\n"
        "- [1-hop] Alice -[works_at]-> Acme (Alice works at Acme.)\n"
        "- [2-hop] Acme -[uses]-> Neo4j (Acme uses Neo4j.)"
    )


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
    assert "ORDER BY structural_role ASC, graph_score DESC, r.updated_at DESC" in query
    assert driver.session_obj.calls[-1]["params"]["hyper_role_predicate"] == "has_role"
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
    assert "rel.hyper_event" in query
    assert "rel.hyper_role" in query
    assert "rel.chain_id" in query
    assert "rel.chain_ids" in query
    assert "chain_path_score" in query
    assert "WHEN size(rels) = 1 THEN false" in query
    assert "chain_order_score" in query
    assert "structural_role_score" in query
    assert "structural_role ASC" in query
    assert "left_chain_id IN coalesce(rels[index].chain_ids, [])" in query
    assert "left_key IN coalesce(rels[index].chain_order_keys, [])" in query
    assert "right_key IN coalesce(rels[index + 1].chain_order_keys, [])" in query
    assert "split(right_key, $chain_order_separator)[0]" in query
    assert "split(left_key, $chain_order_separator)[0]" in query
    assert "toInteger(split(right_key, $chain_order_separator)[1])" in query
    assert (
        driver.session_obj.calls[-1]["params"]["chain_order_separator"] == CHAIN_ORDER_KEY_SEPARATOR
    )
    assert "toLower(coalesce(r.hyper_role, '')) CONTAINS term" in query
    assert "r.hyper_role AS hyper_role" in query


def test_graph_query_terms_include_cjk_ngrams_for_unsegmented_queries():
    terms = _query_terms("我之前请求截图的时候失败过是什么原因")

    assert "截图" in terms
    assert "失败" in terms
    assert "原因" in terms
    assert _query_terms("Alice Acme")[:2] == ["alice", "acme"]


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
    assert context == "[Graph context]\n- Alice -[works_at]-> Acme (Alice works at Acme.)"


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
    ):
        self.retrieve_calls.append(
            {
                "query": query,
                "source_ids": source_ids,
                "max_facts": max_facts,
                "retrieval_depth": retrieval_depth,
                "ranking_policy": ranking_policy,
            }
        )
        return "[Graph context]\n- Alice -[works_at]-> Acme"

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
                    "retrieval_depth": 3,
                }
            }
        },
        graph_client=cast(Neo4jGraphClient, graph),
        extractor=cast(Any, FakeExtractor()),
        task_store=store,
    )
    try:
        context = await manager.retrieve_context(query="Alice", source_ids=[], max_facts=5)

        assert context == "[Graph context]\n- Alice -[works_at]-> Acme"
        assert graph.retrieve_calls == [
            {
                "query": "Alice",
                "source_ids": [],
                "max_facts": 5,
                "retrieval_depth": 3,
                "ranking_policy": "hybrid",
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

        assert context == "[Graph context]\n- Alice -[works_at]-> Acme"
        assert graph.retrieve_calls == [
            {
                "query": "Alice",
                "source_ids": [],
                "max_facts": 5,
                "retrieval_depth": 1,
                "ranking_policy": "relevance",
            }
        ]
    finally:
        store.close()
