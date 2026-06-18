"""Query parsing helpers for Neo4j graph retrieval."""

from __future__ import annotations

from core.knowledge.graph_constants import (
    ASSISTANT_ENTITY_ALIAS_KEYS,
    GRAPH_QUERY_ENUMERATION_TERMS,
)

_MAX_QUERY_TERMS = 32

_QUERY_RELATION_EXPANSIONS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (
        ("根本原因", "导致", "造成", "引起", "触发", "原因", "为什么", "为何"),
        ("caused_by", "causes", "triggered_by", "leads_to", "root_cause", "trigger"),
    ),
    (
        ("影响", "依赖", "关联"),
        ("affects", "impacts", "depends_on", "related_to"),
    ),
    (
        ("负责", "负责人", "归谁", "谁管"),
        ("owner", "responsible_for", "has_owner"),
    ),
    (
        ("属于", "归属", "隶属", "组成", "部分"),
        ("belongs_to", "part_of", "member_of"),
    ),
    (
        ("使用", "用到", "基于"),
        ("uses", "built_with"),
    ),
    (
        GRAPH_QUERY_ENUMERATION_TERMS,
        (
            "count",
            "contains",
            "has",
            "includes",
            "member_of",
            "part_of",
            "has_attribute",
            "related_to",
        ),
    ),
)

_CJK_LEADING_QUERY_FILLERS = (
    "有哪些",
    "有多少",
    "多少个",
    "分别",
    "各自",
    "每个",
    "逐个",
    "列出",
    "哪些",
    "哪个",
    "什么",
    "几个",
    "数量",
    "总共",
    "一共",
    "请",
    "帮我",
    "了",
    "的",
    "是",
    "谁",
)

_CJK_TRAILING_QUERY_FILLERS = (
    "是什么原因",
    "有哪些",
    "是什么",
    "分别",
    "各自",
    "哪些",
    "什么",
    "原因",
    "吗",
    "呢",
    "么",
    "了",
    "的",
)

_QUERY_TRIGGER_NEGATION_PREFIXES = (
    "没有",
    "无需",
    "不是",
    "并非",
    "并不",
    "不再",
    "不",
    "非",
    "未",
    "无",
    "没",
)


def _query_terms(query: str) -> list[str]:
    return [str(row["term"]) for row in _query_term_rows(query)]


def _query_term_rows(query: str) -> list[dict[str, str | float]]:
    rows: list[dict[str, str | float]] = []
    normalized = str(query or "").lower().replace("_", " ")
    raw_terms = []
    for raw in normalized.split():
        term = _clean_query_token(raw)
        if term:
            raw_terms.append(term)
    compact_query = _clean_query_token(normalized)

    for term in raw_terms:
        _append_query_term_row(rows, term, weight=1.0, kind="token")
        if term in ASSISTANT_ENTITY_ALIAS_KEYS:
            for alias in ASSISTANT_ENTITY_ALIAS_KEYS:
                _append_query_term_row(rows, alias, weight=1.0, kind="alias")
        if len(rows) >= _MAX_QUERY_TERMS:
            return rows

    _append_relation_query_terms(rows, [compact_query, *raw_terms])

    for term in raw_terms:
        for run in _cjk_runs(term):
            for size in (2, 3, 4):
                if len(run) < size:
                    continue
                for index in range(0, len(run) - size + 1):
                    _append_query_term_row(
                        rows,
                        run[index : index + size],
                        weight=0.35,
                        kind="cjk_ngram",
                    )
                    if len(rows) >= _MAX_QUERY_TERMS:
                        break
                if len(rows) >= _MAX_QUERY_TERMS:
                    break
            if len(rows) >= _MAX_QUERY_TERMS:
                break
        if len(rows) >= _MAX_QUERY_TERMS:
            break
    return rows


def _fulltext_query(terms: list[str]) -> str:
    return " OR ".join(f'"{_escape_fulltext_term(term)}"' for term in terms if term)


def _escape_fulltext_term(term: str) -> str:
    return str(term).replace("\\", "\\\\").replace('"', '\\"')


def _clean_query_token(value: str, *, allow_underscore: bool = False) -> str:
    return "".join(
        char
        for char in str(value or "")
        if char.isalnum() or "\u4e00" <= char <= "\u9fff" or (allow_underscore and char == "_")
    )


def _append_query_term_row(
    rows: list[dict[str, str | float]],
    term: str,
    *,
    weight: float,
    kind: str,
) -> None:
    cleaned = _clean_query_token(term, allow_underscore=kind == "predicate")
    if len(cleaned) <= 1:
        return
    for row in rows:
        if row["term"] != cleaned:
            continue
        if float(row["weight"]) < weight:
            row["weight"] = weight
            row["kind"] = kind
        return
    if len(rows) < _MAX_QUERY_TERMS:
        rows.append({"term": cleaned, "weight": weight, "kind": kind})


def _append_relation_query_terms(
    rows: list[dict[str, str | float]],
    values: list[str],
) -> None:
    seen_values = []
    for value in values:
        if value and value not in seen_values:
            seen_values.append(value)
    for value in seen_values:
        for triggers, expansions in _QUERY_RELATION_EXPANSIONS:
            matched_triggers = _matching_relation_triggers(value, triggers)
            if not matched_triggers:
                continue
            for trigger in matched_triggers:
                _append_query_term_row(rows, trigger, weight=1.2, kind="predicate")
            for phrase in _relation_query_phrases(value, matched_triggers):
                _append_query_term_row(rows, phrase, weight=1.8, kind="phrase")
            for expansion in expansions:
                _append_query_term_row(rows, expansion, weight=1.35, kind="predicate")
            if len(rows) >= _MAX_QUERY_TERMS:
                return


def _matching_relation_triggers(value: str, triggers: tuple[str, ...]) -> list[str]:
    matched: list[tuple[int, int, str]] = []
    for trigger in sorted(triggers, key=len, reverse=True):
        start = 0
        while start < len(value):
            index = value.find(trigger, start)
            if index < 0:
                break
            end = index + len(trigger)
            start = index + 1
            if _trigger_has_word_fragment_boundary(value, index, end):
                continue
            if _trigger_has_negation_prefix(value, index):
                continue
            overlaps_longer_trigger = any(
                index < matched_end and end > matched_start
                for matched_start, matched_end, _ in matched
            )
            if overlaps_longer_trigger:
                continue
            matched.append((index, end, trigger))
            break
    return [trigger for _, _, trigger in sorted(matched)]


def _trigger_has_word_fragment_boundary(value: str, start: int, end: int) -> bool:
    trigger = value[start:end]
    if not trigger.isascii() or not trigger.isalnum():
        return False
    if start > 0 and value[start - 1].isalnum():
        return True
    return end < len(value) and value[end].isalnum()


def _trigger_has_negation_prefix(value: str, start: int) -> bool:
    prefix = value[max(0, start - 3) : start]
    return any(prefix.endswith(marker) for marker in _QUERY_TRIGGER_NEGATION_PREFIXES)


def _relation_query_phrases(value: str, triggers: list[str]) -> list[str]:
    phrases: list[str] = []
    for trigger in sorted(triggers, key=len, reverse=True):
        if trigger not in value:
            continue
        before, _, after = value.partition(trigger)
        for phrase in (_clean_cjk_query_phrase(before), _clean_cjk_query_phrase(after)):
            _append_phrase(phrases, phrase)
            if _has_cjk(phrase) and len(phrase) > 4:
                _append_phrase(phrases, phrase[-4:])
                _append_phrase(phrases, phrase[:4])
    return phrases


def _clean_cjk_query_phrase(value: str) -> str:
    phrase = _clean_query_token(value)
    changed = True
    while changed:
        changed = False
        for filler in _CJK_LEADING_QUERY_FILLERS:
            if phrase.startswith(filler) and len(phrase) > len(filler):
                phrase = phrase[len(filler) :]
                changed = True
                break
    changed = True
    while changed:
        changed = False
        for filler in _CJK_TRAILING_QUERY_FILLERS:
            if phrase.endswith(filler) and len(phrase) > len(filler):
                phrase = phrase[: -len(filler)]
                changed = True
                break
    if phrase in _CJK_LEADING_QUERY_FILLERS or phrase in _CJK_TRAILING_QUERY_FILLERS:
        return ""
    return phrase


def _append_phrase(phrases: list[str], phrase: str) -> None:
    if len(phrase) > 1 and phrase not in phrases:
        phrases.append(phrase)


def _has_cjk(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def _append_query_term(terms: list[str], term: str) -> None:
    if len(term) > 1 and term not in terms and len(terms) < _MAX_QUERY_TERMS:
        terms.append(term)


def _cjk_runs(value: str) -> list[str]:
    runs = []
    current = []
    for char in value:
        if "\u4e00" <= char <= "\u9fff":
            current.append(char)
            continue
        if current:
            runs.append("".join(current))
            current = []
    if current:
        runs.append("".join(current))
    return runs
