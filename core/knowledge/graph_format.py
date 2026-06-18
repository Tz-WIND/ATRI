"""Formatting and ranking helpers for Neo4j graph retrieval results."""

from __future__ import annotations

import math
from typing import Any

from core.knowledge.graph_constants import (
    ASSISTANT_CANONICAL_NAME,
    ASSISTANT_ENTITY_ALIAS_KEYS,
)
from core.knowledge.graph_values import _retrieval_depth


def _rank_retrieved_rows(rows: list[Any]) -> list[Any]:
    if not any(_has_retrieval_rank_metadata(row) for row in rows):
        return list(rows)
    indexed_rows = list(enumerate(rows))
    indexed_rows.sort(key=lambda item: _retrieved_row_sort_key(item[1], item[0]))
    return [row for _, row in indexed_rows]


def _has_retrieval_rank_metadata(row: Any) -> bool:
    return any(
        _row_value(row, key) is not None for key in ("graph_score", "updated_at", "structural_role")
    )


def _retrieved_row_sort_key(row: Any, index: int) -> tuple[Any, ...]:
    return (
        _row_int(row, "structural_role", 0),
        -_row_float(row, "graph_score", 0.0),
        -_row_float(row, "updated_at", 0.0),
        -_row_float(row, "confidence", 0.0),
        _row_int(row, "hop", 1),
        index,
    )


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    try:
        return row.get(key, default)
    except AttributeError:
        return default


def _row_float(row: Any, key: str, default: float = 0.0) -> float:
    try:
        value = float(_row_value(row, key, default))
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _row_int(row: Any, key: str, default: int = 0) -> int:
    try:
        return int(_row_value(row, key, default))
    except (TypeError, ValueError):
        return default


def _canonical_retrieved_entity_name(value: Any) -> str:
    text = str(value or "").strip()
    if _entity_alias_key(text) in ASSISTANT_ENTITY_ALIAS_KEYS:
        return ASSISTANT_CANONICAL_NAME
    return text


def _context_entity_label(name: str, entity_type: str, include_entity_types: bool) -> str:
    cleaned_name = str(name or "").strip()
    cleaned_type = str(entity_type or "").strip()
    if include_entity_types and cleaned_name and cleaned_type:
        return f"{cleaned_name} ({cleaned_type})"
    return cleaned_name


def _entity_alias_key(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _format_retrieved_fact_lines(
    entries: list[dict[str, Any]],
    *,
    depth: int,
    limit: int,
) -> list[str]:
    line_limit = max(1, int(limit or 1))
    if depth <= 1:
        return ["- " + str(entry["detail"]) for entry in entries[:line_limit]]

    tree = _retrieved_fact_tree(entries)
    rendered_keys = set()

    def render_node(index: int, path: set[int]) -> str:
        if index in path:
            return str(entries[index]["detail"])
        entry_key = entries[index].get("dedupe_key")
        if entry_key and entry_key in rendered_keys:
            return ""
        if entry_key:
            rendered_keys.add(entry_key)
        node = tree["nodes"][index]
        detail = str(entries[index]["detail"])
        children = node["children"]
        if children:
            child_path = {*path, index}
            linked_parts = [
                child_detail
                for child_index in children
                if (child_detail := render_node(child_index, child_path))
            ]
            if linked_parts:
                detail = f"{detail} | linked: {'; '.join(linked_parts)}"
        return detail

    roots = list(tree["roots"])
    if len(roots) > line_limit or _retrieved_fact_roots_overlap(tree["nodes"], entries, roots):
        roots.sort(
            key=lambda index: _retrieved_fact_root_sort_key(
                tree["nodes"],
                index,
            )
        )

    lines: list[str] = []
    for index in roots:
        rendered = render_node(index, set())
        if not rendered:
            continue
        lines.append("- " + rendered)
        if len(lines) >= line_limit:
            break
    return lines


def _retrieved_fact_tree(entries: list[dict[str, Any]]) -> dict[str, Any]:
    nodes: list[dict[str, list[int]]] = [{"children": []} for _ in entries]
    parent_indexes_by_object: dict[tuple[int, str], list[int]] = {}
    for index, entry in enumerate(entries):
        hop = _retrieval_depth(entry.get("hop", 1))
        object_key = str(entry.get("object_key") or "")
        if object_key:
            parent_indexes_by_object.setdefault((hop, object_key), []).append(index)

    attached: set[int] = set()
    for index, entry in enumerate(entries):
        hop = _retrieval_depth(entry.get("hop", 1))
        if hop <= 1:
            continue
        subject_key = str(entry.get("subject_key") or "")
        if not subject_key:
            continue
        parent_indexes = parent_indexes_by_object.get((hop - 1, subject_key), [])
        if not parent_indexes:
            continue
        parent_index = _best_retrieved_fact_parent_index(entries, parent_indexes)
        if parent_index == index:
            continue
        nodes[parent_index]["children"].append(index)
        attached.add(index)
    roots = [index for index in range(len(entries)) if index not in attached]
    return {"nodes": nodes, "roots": roots}


def _retrieved_fact_roots_overlap(
    nodes: list[dict[str, list[int]]],
    entries: list[dict[str, Any]],
    roots: list[int],
) -> bool:
    seen_keys: set[Any] = set()
    for root_index in roots:
        root_keys = _retrieved_fact_subtree_keys(nodes, entries, root_index, set())
        if seen_keys.intersection(root_keys):
            return True
        seen_keys.update(root_keys)
    return False


def _retrieved_fact_subtree_keys(
    nodes: list[dict[str, list[int]]],
    entries: list[dict[str, Any]],
    index: int,
    path: set[int],
) -> set[Any]:
    if index in path:
        return set()
    keys = set()
    entry_key = entries[index].get("dedupe_key")
    if entry_key:
        keys.add(entry_key)
    child_path = {*path, index}
    for child_index in nodes[index]["children"]:
        keys.update(_retrieved_fact_subtree_keys(nodes, entries, child_index, child_path))
    return keys


def _best_retrieved_fact_parent_index(
    entries: list[dict[str, Any]],
    parent_indexes: list[int],
) -> int:
    return min(
        parent_indexes,
        key=lambda index: _retrieved_fact_parent_sort_key(entries[index], index),
    )


def _retrieved_fact_parent_sort_key(entry: dict[str, Any], index: int) -> tuple[Any, ...]:
    detail = str(entry.get("detail") or "")
    return (
        -_retrieval_depth(entry.get("hop", 1)),
        -int(entry.get("evidence_length") or 0),
        -len(detail),
        str(entry.get("subject_key") or ""),
        str(entry.get("object_key") or ""),
        detail,
        index,
    )


def _retrieved_fact_root_sort_key(
    nodes: list[dict[str, list[int]]],
    index: int,
) -> tuple[Any, ...]:
    descendant_count, descendant_depth = _retrieved_fact_descendant_stats(nodes, index, set())
    return (
        0 if descendant_count else 1,
        -descendant_depth,
        -descendant_count,
        index,
    )


def _retrieved_fact_descendant_stats(
    nodes: list[dict[str, list[int]]],
    index: int,
    path: set[int],
) -> tuple[int, int]:
    if index in path:
        return (0, 0)
    children = nodes[index]["children"]
    if not children:
        return (0, 0)
    child_path = {*path, index}
    descendant_count = 0
    descendant_depth = 0
    for child_index in children:
        child_count, child_depth = _retrieved_fact_descendant_stats(
            nodes,
            child_index,
            child_path,
        )
        descendant_count += 1 + child_count
        descendant_depth = max(descendant_depth, 1 + child_depth)
    return (descendant_count, descendant_depth)
