"""LLM provider layer - thin wrapper over OpenAI-compatible APIs.

Supports any provider exposing an OpenAI-compatible endpoint (DeepSeek, Qwen,
Kimi, GLM, Ollama, etc.) by switching OPENAI_BASE_URL + OPENAI_API_KEY.
"""

import json
import time
from dataclasses import dataclass, field

import httpx
from openai import OpenAI, APIError, RateLimitError, APITimeoutError, APIConnectionError

from core import logger


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    content: str = ""
    reasoning_content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def message(self) -> dict:
        """Convert to OpenAI message format for appending to history."""
        msg: dict = {"role": "assistant", "content": self.content or None}
        if self.reasoning_content:
            msg["reasoning_content"] = self.reasoning_content
        if self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }
                for tc in self.tool_calls
            ]
        return msg


_PRICING = {
    "gpt-4o": (2.5, 10),
    "gpt-4o-mini": (0.15, 0.6),
    "gpt-4.1": (2, 8),
    "gpt-4.1-mini": (0.4, 1.6),
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
    "claude-sonnet-4-6": (3, 15),
    "claude-haiku-4-5": (1, 5),
    "qwen3-max": (0.78, 3.9),
    "qwen3-plus": (0.26, 0.78),
    "kimi-k2.5": (0.6, 3),
}


class LLM:
    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str | None = None,
        **kwargs,
    ):
        self.model = model
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=httpx.Timeout(connect=10, read=300, write=10, pool=10),
        )
        self.extra = kwargs
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0

    @property
    def estimated_cost(self) -> float | None:
        pricing = _PRICING.get(self.model)
        if not pricing:
            return None
        input_rate, output_rate = pricing
        return (
            self.total_prompt_tokens * input_rate / 1_000_000
            + self.total_completion_tokens * output_rate / 1_000_000
        )

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        on_token=None,
        on_thinking=None,
        cancel_event=None,  # threading.Event | None — set to interrupt mid-stream
    ) -> LLMResponse:
        """Send messages, stream response, handle tool calls.

        If cancel_event is set during streaming, the loop exits early and
        returns whatever content has been accumulated so far.
        """
        params: dict = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            **self.extra,
        }
        if tools:
            params["tools"] = tools

        if cancel_event and cancel_event.is_set():
            return LLMResponse(content="[Interrupted by user]")

        try:
            params["stream_options"] = {"include_usage": True}
            stream = self._call_with_retry(params)
        except Exception:
            params.pop("stream_options", None)
            stream = self._call_with_retry(params)

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tc_map: dict[int, dict] = {}
        prompt_tok = 0
        completion_tok = 0
        chunk_count = 0
        t0 = time.time()

        logger.info(f"LLM stream started for {self.model}")

        for chunk in stream:
            if cancel_event and cancel_event.is_set():
                logger.info("LLM stream cancelled by user")
                break

            if chunk_count == 0:
                logger.info(f"LLM first chunk received after {time.time() - t0:.1f}s")
            chunk_count += 1

            if chunk.usage:
                prompt_tok = chunk.usage.prompt_tokens
                completion_tok = chunk.usage.completion_tokens

            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            rc = _delta_value(delta, "reasoning_content")
            if rc:
                reasoning_parts.append(rc)
                if on_thinking:
                    on_thinking(rc)

            content = _delta_value(delta, "content")
            if content:
                content_parts.append(content)
                if on_token:
                    on_token(content)

            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tc_map:
                        tc_map[idx] = {"id": "", "name": "", "args": ""}
                    if tc_delta.id:
                        tc_map[idx]["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            tc_map[idx]["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            tc_map[idx]["args"] += tc_delta.function.arguments

        elapsed = time.time() - t0
        logger.info(f"LLM stream done: {chunk_count} chunks in {elapsed:.1f}s")

        parsed: list[ToolCall] = []
        for idx in sorted(tc_map):
            raw = tc_map[idx]
            try:
                args = json.loads(raw["args"])
            except (json.JSONDecodeError, KeyError):
                args = {}
            parsed.append(ToolCall(id=raw["id"], name=raw["name"], arguments=args))

        self.total_prompt_tokens += prompt_tok
        self.total_completion_tokens += completion_tok

        return LLMResponse(
            content="".join(content_parts),
            reasoning_content="".join(reasoning_parts),
            tool_calls=parsed,
            prompt_tokens=prompt_tok,
            completion_tokens=completion_tok,
        )

    def _call_with_retry(self, params: dict, max_retries: int = 3):
        for attempt in range(max_retries):
            try:
                return self.client.chat.completions.create(**params)
            except (RateLimitError, APITimeoutError, APIConnectionError):
                if attempt == max_retries - 1:
                    raise
                time.sleep(2 ** attempt)
            except APIError as e:
                if e.status_code and e.status_code >= 500 and attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    raise


def _delta_value(delta, key: str):
    value = getattr(delta, key, None)
    if value is None and hasattr(delta, "model_extra"):
        value = (delta.model_extra or {}).get(key)
    if value is None and hasattr(delta, "model_dump"):
        value = delta.model_dump(exclude_none=True).get(key)
    if isinstance(value, list):
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in value
        )
    return value
