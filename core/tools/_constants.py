"""Shared tool constants to avoid duplication across tool modules."""

# Directories skipped during file walking
SKIP_DIRS: set[str] = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    ".tox", "dist", "build", ".mypy_cache", ".pytest_cache",
}

# File extensions considered text/searchable
TEXT_EXTS: set[str] = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".scss",
    ".json", ".yaml", ".yml", ".toml", ".md", ".txt", ".rst",
    ".xml", ".csv", ".sh", ".bat", ".ps1", ".cfg", ".ini", ".conf",
    ".java", ".c", ".cpp", ".h", ".hpp", ".go", ".rs", ".rb",
    ".sql", ".r", ".lua", ".swift", ".kt", ".dart", ".vue", ".svelte",
}
