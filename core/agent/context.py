"""Multi-layer context compression.

Three layers inspired by Claude Code:
  Layer 1 (tool_snip)     - truncate verbose tool results
  Layer 2 (summarize)     - LLM-powered summary of old conversation
  Layer 3 (hard_collapse) - last resort: drop everything except summary + recent
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from core import logger
from core.utils import atomic_write_text, format_bytes

if TYPE_CHECKING:
    from .llm import LLM

TOOL_OUTPUT_COMPRESSED_MARKER = "<persisted-output>"
DEFAULT_TOOL_OUTPUTS_DIR = Path("data/tool_outputs")
DEFAULT_TOOL_OUTPUT_SPILL_CHARS = 12_000
DEFAULT_TOOL_OUTPUT_SPILL_LINES = 300
DEFAULT_TOOL_OUTPUT_HEAD_CHARS = 2_048
DEFAULT_TOOL_OUTPUT_TAIL_CHARS = 0

# CJK + fullwidth ranges: ideographs, kana, hangul, punctuation, fullwidth forms
_CJK_RE = re.compile(
    r"[一-鿿㐀-䶿"  # CJK unified ideographs
    r"぀-ヿ가-힯"  # Hiragana, Katakana, Hangul
    r"　-〿＀-￯"  # CJK punctuation, fullwidth forms
    r"]"
)


def _approx_tokens(text: str) -> int:
    """Estimate token count with CJK-aware heuristics.

    CJK characters average ~1.5 tokens each in most tokenizers;
    Latin/ASCII characters average ~0.25 tokens each (4 chars ≈ 1 token).
    """
    cjk_count = len(_CJK_RE.findall(text))
    latin_count = len(text) - cjk_count
    return int(cjk_count * 1.5 + latin_count * 0.25)


def content_to_text(content) -> str:
    """Return text-only content for estimates, summaries, and previews."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(content_to_text(part) for part in content)
    if isinstance(content, dict):
        block_type = content.get("type")
        text = content.get("text")
        if block_type == "text" and isinstance(text, str):
            return text
        if block_type in {"image", "image_url"}:
            return "\n[Image attachment]\n"
        if "content" in content:
            return content_to_text(content["content"])
        return json.dumps(content, ensure_ascii=False)
    return str(content)


def estimate_tokens(messages: list[dict], system_prompt: str = "") -> int:
    """Estimate total tokens including optional system prompt."""
    total = _approx_tokens(system_prompt) if system_prompt else 0
    for m in messages:
        if m.get("content"):
            total += _approx_tokens(content_to_text(m["content"]))
        if m.get("tool_calls"):
            total += _approx_tokens(str(m["tool_calls"]))
    return total


@dataclass
class StoredToolResult:
    result_id: str
    tool: str
    tool_call_id: str
    output_chars: int
    output_lines: int
    path: str
    summary: str


@dataclass
class ToolResultForContext:
    content: str
    stored: StoredToolResult | None = None


class ToolResultStore:
    """Stores large tool outputs outside the LLM conversation context."""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        spill_chars: int = DEFAULT_TOOL_OUTPUT_SPILL_CHARS,
        spill_lines: int = DEFAULT_TOOL_OUTPUT_SPILL_LINES,
        head_chars: int = DEFAULT_TOOL_OUTPUT_HEAD_CHARS,
        tail_chars: int = DEFAULT_TOOL_OUTPUT_TAIL_CHARS,
    ):
        self.root = (Path(root) if root else DEFAULT_TOOL_OUTPUTS_DIR).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.spill_chars = spill_chars
        self.spill_lines = spill_lines
        self.head_chars = head_chars
        self.tail_chars = tail_chars
        self._lock = threading.Lock()

    def should_spill(self, text: str) -> bool:
        if len(text) > self.spill_chars:
            return True
        return len(text.splitlines()) > self.spill_lines

    def prepare(
        self,
        *,
        tool: str,
        tool_call_id: str,
        args: dict | None,
        output: str,
    ) -> ToolResultForContext:
        if not isinstance(output, str):
            output = str(output)
        if not self.should_spill(output):
            return ToolResultForContext(content=output)

        record = self.store(tool=tool, tool_call_id=tool_call_id, args=args or {}, output=output)
        compact = self._compact_output(record, output)
        return ToolResultForContext(content=compact, stored=record)

    def store(
        self,
        *,
        tool: str,
        tool_call_id: str,
        args: dict,
        output: str,
    ) -> StoredToolResult:
        lines = output.splitlines()
        summary = self._summarize(output, max_lines=12)
        result_id = self._new_result_id()
        text_path = self.root / f"{result_id}.txt"
        meta_path = self.root / f"{result_id}.json"
        payload = {
            "id": result_id,
            "tool": tool,
            "tool_call_id": tool_call_id,
            "args": args,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "output_chars": len(output),
            "output_lines": len(lines),
            "summary": summary,
            "output_path": str(text_path),
        }
        self._atomic_write_text(text_path, output)
        self._atomic_write_json(meta_path, payload)
        return StoredToolResult(
            result_id=result_id,
            tool=tool,
            tool_call_id=tool_call_id,
            output_chars=len(output),
            output_lines=len(lines),
            path=str(text_path),
            summary=summary,
        )

    def retrieve(
        self,
        result_id: str,
        *,
        mode: str = "summary",
        start_line: int | None = None,
        end_line: int | None = None,
        max_lines: int = 120,
        query: str | None = None,
    ) -> str:
        record = self._load(result_id)
        output = self._read_output(record)
        lines = output.splitlines()
        mode = (mode or "summary").strip().lower()
        max_lines = max(1, min(int(max_lines or 120), 500))

        if mode == "summary":
            return self._format_summary(record)
        if mode == "head":
            return self._format_lines(record, lines[:max_lines], first_line=1)
        if mode == "tail":
            start = max(0, len(lines) - max_lines)
            return self._format_lines(record, lines[start:], first_line=start + 1)
        if mode == "lines":
            if start_line is None:
                raise ValueError("start_line is required for mode='lines'.")
            start = max(1, int(start_line))
            end = int(end_line) if end_line is not None else start + max_lines - 1
            if end < start:
                raise ValueError("end_line must be greater than or equal to start_line.")
            chunk = lines[start - 1 : end]
            return self._format_lines(record, chunk, first_line=start)
        if mode == "query":
            needle = (query or "").strip()
            if not needle:
                raise ValueError("query is required for mode='query'.")
            return self._query_lines(record, lines, needle, max_lines=max_lines)
        raise ValueError("mode must be one of: summary, head, tail, lines, query.")

    def _load(self, result_id: str) -> dict:
        if not re.fullmatch(r"tr_[0-9a-f]{16}", str(result_id or "")):
            raise ValueError("Invalid tool result id.")
        path = self.root / f"{result_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"Tool result not found: {result_id}")
        data: dict = json.loads(path.read_text(encoding="utf-8"))
        return data

    def _read_output(self, record: dict) -> str:
        output_path = record.get("output_path")
        if output_path:
            path = Path(str(output_path))
            if path.exists():
                return path.read_text(encoding="utf-8", errors="replace")
        return str(record.get("output") or "")

    def _compact_output(self, record: StoredToolResult, output: str) -> str:
        head = output[: self.head_chars].rstrip()
        preview_kb = format_bytes(self.head_chars)
        return (
            f"{TOOL_OUTPUT_COMPRESSED_MARKER}\n"
            f"Output too large ({format_bytes(len(output.encode('utf-8')))}). "
            f"Full output saved to: {record.path}\n"
            f"Tool result id: {record.result_id}\n\n"
            f"Preview (first {preview_kb}):\n"
            f"{head}\n\n"
            "Use `retrieve_tool_result` with this result id for summary/head/tail/lines/query.\n"
            "</persisted-output>"
        )

    def _summarize(self, output: str, *, max_lines: int) -> str:
        lines = [line.rstrip() for line in output.splitlines()]
        nonempty = [line for line in lines if line.strip()]
        interesting = [
            line
            for line in nonempty
            if any(token in line.lower() for token in ("error", "failed", "warning", "traceback"))
        ]
        chosen = interesting[: max_lines // 2] + nonempty[:max_lines]
        deduped: list[str] = []
        seen: set[str] = set()
        for line in chosen:
            trimmed = line[:240]
            if trimmed in seen:
                continue
            seen.add(trimmed)
            deduped.append(f"- {trimmed}")
            if len(deduped) >= max_lines:
                break
        return "\n".join(deduped) or "- (output contained no non-empty lines)"

    def _format_summary(self, record: dict) -> str:
        return (
            f"Tool result {record.get('id')} from `{record.get('tool')}`\n"
            f"Size: {record.get('output_lines')} lines, {record.get('output_chars')} chars\n\n"
            f"Summary:\n{record.get('summary') or '(no summary)'}"
        )

    def _format_lines(self, record: dict, lines: list[str], *, first_line: int) -> str:
        if not lines:
            return f"Tool result {record.get('id')}: no lines in requested range."
        numbered = [f"{first_line + i}\t{line}" for i, line in enumerate(lines)]
        return "\n".join(numbered)

    def _query_lines(
        self,
        record: dict,
        lines: list[str],
        needle: str,
        *,
        max_lines: int,
    ) -> str:
        needle_lower = needle.lower()
        matches = [
            (idx, line) for idx, line in enumerate(lines, start=1) if needle_lower in line.lower()
        ]
        if not matches:
            return f"Tool result {record.get('id')}: no matches for {needle!r}."
        shown = matches[:max_lines]
        rendered = [f"{idx}\t{line}" for idx, line in shown]
        if len(matches) > len(shown):
            rendered.append(f"... ({len(matches) - len(shown)} additional matches omitted)")
        return "\n".join(rendered)

    def _new_result_id(self) -> str:
        with self._lock:
            while True:
                result_id = f"tr_{uuid.uuid4().hex[:16]}"
                if not (self.root / f"{result_id}.json").exists():
                    return result_id

    def _atomic_write_json(self, path: Path, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False, indent=2)
        atomic_write_text(path, data, prefix=".tool_result_")

    def _atomic_write_text(self, path: Path, data: str) -> None:
        atomic_write_text(path, data, errors="replace", prefix=".tool_output_")


class ContextManager:
    def __init__(
        self,
        max_tokens: int = 128_000,
        *,
        tool_result_store: ToolResultStore | None = None,
    ):
        self._lock = threading.Lock()
        self.tool_result_store = tool_result_store or ToolResultStore()
        self.set_max_tokens(max_tokens)

    def set_max_tokens(self, max_tokens: int):
        self.max_tokens = max_tokens
        self._snip_at = int(max_tokens * 0.50)
        self._summarize_at = int(max_tokens * 0.70)
        self._collapse_at = int(max_tokens * 0.90)

    def maybe_compress(
        self, messages: list[dict], llm: LLM | None = None, system_prompt: str = ""
    ) -> bool:
        """Apply compression layers as needed. Returns True if any compression happened.

        system_prompt is included in the token budget calculation so that we don't
        exceed the model's context window after adding the system prompt at call time.

        Acquires self._lock to prevent races with the Agent thread that may be
        appending new messages to the same list concurrently.
        """
        with self._lock:
            current = estimate_tokens(messages, system_prompt)
            compressed = False

            if current > self._snip_at:
                if self._snip_tool_outputs(messages):
                    compressed = True
                    current = estimate_tokens(messages, system_prompt)

            if current > self._summarize_at and len(messages) > 10:
                if self._summarize_old(messages, llm, keep_recent=8):
                    compressed = True
                    current = estimate_tokens(messages, system_prompt)

            if current > self._collapse_at and len(messages) > 4:
                self._hard_collapse(messages, llm)
                compressed = True

            return compressed

    def prepare_tool_result(
        self,
        *,
        tool: str,
        tool_call_id: str,
        args: dict | None,
        output: str,
    ) -> ToolResultForContext:
        """Return the result text that should be inserted into LLM context."""
        return self.tool_result_store.prepare(
            tool=tool,
            tool_call_id=tool_call_id,
            args=args,
            output=output,
        )

    @staticmethod
    def _snip_tool_outputs(messages: list[dict]) -> bool:
        """Layer 1: Truncate tool results over 1500 chars."""
        changed = False
        for m in messages:
            if m.get("role") != "tool":
                continue
            content = m.get("content", "")
            if not isinstance(content, str):
                content = content_to_text(content)
                m["content"] = content
            if content.startswith(TOOL_OUTPUT_COMPRESSED_MARKER):
                continue
            if len(content) <= 1500:
                continue
            lines = content.splitlines()
            if len(lines) <= 6:
                continue
            snipped = (
                "\n".join(lines[:3])
                + f"\n... ({len(lines)} lines, snipped to save context) ...\n"
                + "\n".join(lines[-3:])
            )
            m["content"] = snipped
            changed = True
        return changed

    def _summarize_old(self, messages: list[dict], llm: LLM | None, keep_recent: int = 8) -> bool:
        """Layer 2: Summarize old conversation, keep recent messages intact."""
        if len(messages) <= keep_recent:
            return False

        old = messages[:-keep_recent]
        tail = messages[-keep_recent:]
        summary = self._get_summary(old, llm)

        messages.clear()
        messages.append(
            {
                "role": "user",
                "content": f"[Context compressed - conversation summary]\n{summary}",
            }
        )
        messages.append(
            {
                "role": "assistant",
                "content": "Got it, I have the context from our earlier conversation.",
            }
        )
        messages.extend(tail)
        return True

    def _hard_collapse(self, messages: list[dict], llm: LLM | None):
        """Layer 3: Emergency compression. Keep only last 4 messages + summary."""
        tail = messages[-4:] if len(messages) > 4 else messages[-2:]
        summary = self._get_summary(messages[: -len(tail)], llm)

        messages.clear()
        messages.append({"role": "user", "content": f"[Hard context reset]\n{summary}"})
        messages.append(
            {
                "role": "assistant",
                "content": "Context restored. Continuing from where we left off.",
            }
        )
        messages.extend(tail)

    def _get_summary(self, messages: list[dict], llm: LLM | None) -> str:
        flat = self._flatten(messages)
        if llm:
            try:
                resp = llm.chat(
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "Compress this conversation into a brief summary. "
                                "Preserve: file paths edited, key decisions made, "
                                "errors encountered, current task state. "
                                "Drop: verbose command output, code listings, "
                                "redundant back-and-forth."
                            ),
                        },
                        {"role": "user", "content": flat[:15000]},
                    ],
                )
                return resp.content
            except Exception:
                logger.debug("LLM summary failed, falling back to heuristics")
        return self._extract_key_info(messages)

    @staticmethod
    def _flatten(messages: list[dict]) -> str:
        parts = []
        for m in messages:
            role = m.get("role", "?")
            text = content_to_text(m.get("content", ""))
            if text:
                parts.append(f"[{role}] {text[:400]}")
        return "\n".join(parts)

    @staticmethod
    def _extract_key_info(messages: list[dict]) -> str:
        files_seen: set[str] = set()
        errors: list[str] = []
        for m in messages:
            text = content_to_text(m.get("content", ""))
            for match in re.finditer(r"[\w./\-]+\.\w{1,5}", text):
                files_seen.add(match.group())
            for line in text.splitlines():
                if "error" in line.lower():
                    errors.append(line.strip()[:150])
        parts = []
        if files_seen:
            parts.append(f"Files touched: {', '.join(sorted(files_seen)[:20])}")
        if errors:
            parts.append(f"Errors seen: {'; '.join(errors[:5])}")
        return "\n".join(parts) or "(no extractable context)"
