"""Shared constants for graph knowledge extraction and retrieval."""

CHAIN_ORDER_KEY_SEPARATOR = "::order::"
HYPER_ROLE_PREDICATE = "has_role"
GRAPH_RETRIEVAL_MAX_DEPTH = 7
GRAPH_CYPHER_QUERY_TIMEOUT_SECONDS = 9
GRAPH_EXTRACTION_BATCH_CHARS = 40_000
GRAPH_EXTRACTION_INPUT_MAX_CHARS = 40_000
GRAPH_EXTRACTION_BATCH_OVERLAP_CHARS = 1_000
GRAPH_EXTRACTION_SEMANTIC_CHUNK_CHARS = 4_000
GRAPH_EXTRACTION_SEMANTIC_CHUNK_OVERLAP_CHARS = 400
ASSISTANT_CANONICAL_NAME = "ATRI"
ASSISTANT_CANONICAL_TYPE = "System"
ASSISTANT_ENTITY_ALIAS_KEYS = frozenset(
    {
        "atri",
        "assistant",
        "bot",
        "助手",
        "助理",
        "atri assistant",
        "atri 助手",
    }
)

GRAPH_CONTEXT_MARKER = "[Graph context]"

GRAPH_CONTEXT_GUIDANCE = (
    "以下为检索到的长期记忆（按关键词匹配的子图，可能不完整）。"
    "每行格式为 Subject -[predicate]-> Object，括号内为 evidence。"
    "可跨跳串联相关事实（例如 Person→Project 与 Project→Tool）。"
    "若这些事实能回答问题则据此作答；仅当确实没有相关内容时才说明未知。"
    "实体名保持原文语言（中文/英文/日文等，不翻译）；predicate 保持 lower_snake_case 英文。"
)


def format_graph_context(fact_lines: list[str]) -> str:
    if not fact_lines:
        return ""
    return f"{GRAPH_CONTEXT_MARKER}\n{GRAPH_CONTEXT_GUIDANCE}\n" + "\n".join(fact_lines)
