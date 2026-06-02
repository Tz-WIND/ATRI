"""Shared constants for graph knowledge extraction and retrieval."""

CHAIN_ORDER_KEY_SEPARATOR = "::order::"
HYPER_ROLE_PREDICATE = "has_role"
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
