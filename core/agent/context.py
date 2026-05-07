"""Multi-layer context compression.

Three layers inspired by Claude Code:
  Layer 1 (tool_snip)     - truncate verbose tool results
  Layer 2 (summarize)     - LLM-powered summary of old conversation
  Layer 3 (hard_collapse) - last resort: drop everything except summary + recent
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .llm import LLM

# CJK + fullwidth ranges: ideographs, kana, hangul, punctuation, fullwidth forms
_CJK_RE = re.compile(
    r"[一-鿿㐀-䶿"       # CJK unified ideographs
    r"぀-ヿ가-힯"         # Hiragana, Katakana, Hangul
    r"　-〿＀-￯"         # CJK punctuation, fullwidth forms
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


def estimate_tokens(messages: list[dict], system_prompt: str = "") -> int:
    """Estimate total tokens including optional system prompt."""
    total = _approx_tokens(system_prompt) if system_prompt else 0
    for m in messages:
        if m.get("content"):
            total += _approx_tokens(m["content"])
        if m.get("tool_calls"):
            total += _approx_tokens(str(m["tool_calls"]))
    return total


class ContextManager:
    def __init__(self, max_tokens: int = 128_000):
        self.set_max_tokens(max_tokens)

    def set_max_tokens(self, max_tokens: int):
        self.max_tokens = max_tokens
        self._snip_at = int(max_tokens * 0.50)
        self._summarize_at = int(max_tokens * 0.70)
        self._collapse_at = int(max_tokens * 0.90)

    def maybe_compress(
        self, messages: list[dict], llm: "LLM | None" = None, system_prompt: str = ""
    ) -> bool:
        """Apply compression layers as needed. Returns True if any compression happened.

        system_prompt is included in the token budget calculation so that we don't
        exceed the model's context window after adding the system prompt at call time.
        """
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

    @staticmethod
    def _snip_tool_outputs(messages: list[dict]) -> bool:
        """Layer 1: Truncate tool results over 1500 chars."""
        changed = False
        for m in messages:
            if m.get("role") != "tool":
                continue
            content = m.get("content", "")
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

    def _summarize_old(
        self, messages: list[dict], llm: "LLM | None", keep_recent: int = 8
    ) -> bool:
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

    def _hard_collapse(self, messages: list[dict], llm: "LLM | None"):
        """Layer 3: Emergency compression. Keep only last 4 messages + summary."""
        tail = messages[-4:] if len(messages) > 4 else messages[-2:]
        summary = self._get_summary(messages[: -len(tail)], llm)

        messages.clear()
        messages.append(
            {"role": "user", "content": f"[Hard context reset]\n{summary}"}
        )
        messages.append(
            {
                "role": "assistant",
                "content": "Context restored. Continuing from where we left off.",
            }
        )
        messages.extend(tail)

    def _get_summary(self, messages: list[dict], llm: "LLM | None") -> str:
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
                pass
        return self._extract_key_info(messages)

    @staticmethod
    def _flatten(messages: list[dict]) -> str:
        parts = []
        for m in messages:
            role = m.get("role", "?")
            text = m.get("content", "") or ""
            if text:
                parts.append(f"[{role}] {text[:400]}")
        return "\n".join(parts)

    @staticmethod
    def _extract_key_info(messages: list[dict]) -> str:
        files_seen: set[str] = set()
        errors: list[str] = []
        for m in messages:
            text = m.get("content", "") or ""
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
