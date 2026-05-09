"""LLM provider layer.

Supports any provider exposing an OpenAI-compatible endpoint (DeepSeek, Qwen,
Kimi, GLM, Ollama, etc.) by switching OPENAI_BASE_URL + OPENAI_API_KEY.
Also supports Anthropic's native Messages API format.
"""

import json
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from openai import APIConnectionError, APIError, APITimeoutError, OpenAI, RateLimitError

from core import logger
from core.agent.context import content_to_text


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    content: str = ""
    reasoning_content: str = ""
    anthropic_content: list[dict] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def message(self) -> dict:
        """Convert to OpenAI message format for appending to history."""
        msg: dict = {"role": "assistant", "content": self.content or None}
        if self.reasoning_content:
            msg["reasoning_content"] = self.reasoning_content
        if self.anthropic_content:
            msg["anthropic_content"] = self.anthropic_content
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


_CHAT_COMPLETIONS_OPTION_KEYS = {
    "audio",
    "extra_body",
    "extra_headers",
    "extra_query",
    "frequency_penalty",
    "function_call",
    "functions",
    "logit_bias",
    "logprobs",
    "max_completion_tokens",
    "max_tokens",
    "metadata",
    "modalities",
    "n",
    "parallel_tool_calls",
    "prediction",
    "presence_penalty",
    "prompt_cache_key",
    "prompt_cache_retention",
    "reasoning_effort",
    "response_format",
    "safety_identifier",
    "seed",
    "service_tier",
    "stop",
    "store",
    "stream_options",
    "temperature",
    "timeout",
    "tool_choice",
    "top_logprobs",
    "top_p",
    "user",
    "verbosity",
    "web_search_options",
}

_ANTHROPIC_OPTION_KEYS = {
    "container",
    "context_management",
    "max_tokens",
    "metadata",
    "mcp_servers",
    "service_tier",
    "stop_sequences",
    "temperature",
    "thinking",
    "tool_choice",
    "top_k",
    "top_p",
}

_ANTHROPIC_VERSION = "2023-06-01"
_DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
_HTTP_TIMEOUT = httpx.Timeout(connect=10, read=300, write=10, pool=10)
_API_KEY_SENTINEL = "***"


class LLM:
    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str | None = None,
        api_format: str = "openai",
        **kwargs,
    ):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.api_format = _normalize_api_format(api_format)
        self._raw_options = dict(kwargs)
        self.client: OpenAI | httpx.Client | None = None
        self.extra: dict = {}
        self._configure_client()
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0

    def _configure_client(self):
        self.close()
        if self.api_format == "anthropic":
            self.client = httpx.Client(
                base_url=_normalize_anthropic_base_url(self.base_url) + "/",
                timeout=_HTTP_TIMEOUT,
            )
        else:
            self.client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=_HTTP_TIMEOUT,
            )
        self.extra = self._filtered_options(self._raw_options)

    def close(self):
        if isinstance(self.client, httpx.Client):
            self.client.close()

    def to_config(self) -> dict:
        """Return constructor kwargs for a fresh LLM with the same settings."""
        return {
            "model": self.model,
            "api_key": self.api_key,
            "base_url": self.base_url,
            "api_format": self.api_format,
            **self._raw_options,
        }

    def clone(self, **overrides) -> "LLM":
        """Create a fresh LLM instance without sharing client or token state."""
        cfg = self.to_config()
        cfg.update({k: v for k, v in overrides.items() if v is not None})
        return LLM(**cfg)

    def reconfigure(self, **kwargs):
        """Hot-update model/provider settings and rebuild the client if needed."""
        client_keys = {"api_key", "base_url", "api_format"}
        rebuild = False
        for key, value in kwargs.items():
            if key == "model":
                self.model = value
            elif key == "api_key" and value != _API_KEY_SENTINEL:
                if value != self.api_key:
                    self.api_key = value
                    rebuild = True
            elif key == "base_url":
                if value != self.base_url:
                    self.base_url = value
                    rebuild = True
            elif key == "api_format":
                normalized = _normalize_api_format(value)
                if normalized != self.api_format:
                    self.api_format = normalized
                    rebuild = True
            elif key not in client_keys:
                self._raw_options[key] = value

        if rebuild:
            self._configure_client()
        else:
            self.extra = self._filtered_options(self._raw_options)

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
        cancel_event=None,  # threading.Event | None; set to interrupt mid-stream
        stream: bool = True,
    ) -> LLMResponse:
        """Send messages, stream response, handle tool calls.

        If cancel_event is set during streaming, the loop exits early and
        returns whatever content has been accumulated so far.
        """
        if self.api_format == "anthropic":
            return self._chat_anthropic(
                messages=messages,
                tools=tools,
                on_token=on_token,
                on_thinking=on_thinking,
                cancel_event=cancel_event,
                stream=stream,
            )
        return self._chat_openai(
            messages=messages,
            tools=tools,
            on_token=on_token,
            on_thinking=on_thinking,
            cancel_event=cancel_event,
            stream=stream,
        )

    def _chat_openai(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        on_token=None,
        on_thinking=None,
        cancel_event=None,
        stream: bool = True,
    ) -> LLMResponse:
        params: dict = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            **self.extra,
        }
        if tools:
            params["tools"] = tools

        if cancel_event and cancel_event.is_set():
            return LLMResponse(content="[Interrupted by user]")

        if not stream:
            return self._chat_openai_non_stream(
                params,
                on_token=on_token,
                on_thinking=on_thinking,
            )

        try:
            params["stream_options"] = {"include_usage": True}
            stream_response = self._call_with_retry(params)
        except Exception:
            params.pop("stream_options", None)
            stream_response = self._call_with_retry(params)

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        thinking_tags = _ThinkingTagSplitter()
        tc_map: dict[int, dict] = {}
        prompt_tok = 0
        completion_tok = 0
        chunk_count = 0
        t0 = time.time()

        logger.info(f"LLM stream started for {self.model}")

        for chunk in stream_response:
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
            choice = chunk.choices[0]
            delta = _raw_delta_value(choice, "delta") or choice
            message = _raw_delta_value(choice, "message")

            rc = (
                _delta_reasoning_text(delta)
                + _delta_reasoning_text(choice)
                + _delta_reasoning_text(message)
            )
            if rc:
                reasoning_parts.append(rc)
                if on_thinking:
                    on_thinking(rc)

            content = _delta_value(delta, "content") or _delta_value(message, "content")
            if content:
                visible_content, tagged_reasoning = thinking_tags.feed(content)
                if tagged_reasoning:
                    reasoning_parts.append(tagged_reasoning)
                    if on_thinking:
                        on_thinking(tagged_reasoning)
                if visible_content:
                    content_parts.append(visible_content)
                    if on_token:
                        on_token(visible_content)

            tool_call_deltas = (
                _raw_delta_value(delta, "tool_calls")
                or _raw_delta_value(message, "tool_calls")
                or []
            )
            if tool_call_deltas:
                for tc_delta in tool_call_deltas:
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

        visible_content, tagged_reasoning = thinking_tags.finish()
        if tagged_reasoning:
            reasoning_parts.append(tagged_reasoning)
            if on_thinking:
                on_thinking(tagged_reasoning)
        if visible_content:
            content_parts.append(visible_content)
            if on_token:
                on_token(visible_content)

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

    def _chat_openai_non_stream(
        self,
        params: dict,
        on_token=None,
        on_thinking=None,
    ) -> LLMResponse:
        t0 = time.time()
        logger.info(f"LLM request started for {self.model}")
        response = self._call_with_retry(params)
        logger.info(f"LLM request done for {self.model} in {time.time() - t0:.1f}s")
        llm_response = _openai_completion_to_response(
            response,
            on_token=on_token,
            on_thinking=on_thinking,
        )
        self.total_prompt_tokens += llm_response.prompt_tokens
        self.total_completion_tokens += llm_response.completion_tokens
        return llm_response

    def _call_with_retry(self, params: dict, max_retries: int = 3):
        for attempt in range(max_retries):
            try:
                if not isinstance(self.client, OpenAI):
                    raise RuntimeError("OpenAI client is not configured")
                return self.client.chat.completions.create(**params)
            except (RateLimitError, APITimeoutError, APIConnectionError):
                if attempt == max_retries - 1:
                    raise
                time.sleep(2**attempt)
            except APIError as e:
                status = getattr(e, "status_code", None)
                if status and status >= 500 and attempt < max_retries - 1:
                    time.sleep(2**attempt)
                else:
                    raise

    def _chat_anthropic(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        on_token=None,
        on_thinking=None,
        cancel_event=None,
        stream: bool = True,
    ) -> LLMResponse:
        if cancel_event and cancel_event.is_set():
            return LLMResponse(content="[Interrupted by user]")

        system, anthropic_messages = _messages_to_anthropic(messages)
        params: dict = {
            "model": self.model,
            "messages": anthropic_messages,
            "stream": stream,
            **self.extra,
        }
        if "max_tokens" not in params:
            params["max_tokens"] = 4096
        if system:
            params["system"] = system
        if tools:
            params["tools"] = _tools_to_anthropic(tools)

        if not stream:
            return self._chat_anthropic_non_stream(
                params,
                on_token=on_token,
                on_thinking=on_thinking,
            )

        for attempt in range(3):
            try:
                return self._chat_anthropic_once(
                    params,
                    on_token=on_token,
                    on_thinking=on_thinking,
                    cancel_event=cancel_event,
                )
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                if attempt == 2:
                    raise
                logger.debug(f"Anthropic call failed, retrying: {e}")
                time.sleep(2**attempt)
            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                if status in {408, 409, 429} or status >= 500:
                    if attempt < 2:
                        logger.debug(f"Anthropic HTTP {status}, retrying")
                        time.sleep(2**attempt)
                        continue
                raise

        raise RuntimeError("Anthropic call failed")

    def _chat_anthropic_once(
        self,
        params: dict,
        on_token=None,
        on_thinking=None,
        cancel_event=None,
    ) -> LLMResponse:
        if not isinstance(self.client, httpx.Client):
            raise RuntimeError("Anthropic client is not configured")

        headers = {
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        if self.api_key:
            headers["x-api-key"] = self.api_key

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        content_blocks: dict[int, dict] = {}
        tc_map: dict[int, dict] = {}
        prompt_tok = 0
        completion_tok = 0
        event_count = 0
        _delta_seen: set[int] = set()
        t0 = time.time()
        token_state: dict[str, int] = {"prompt": 0, "completion": 0}

        logger.info(f"Anthropic stream started for {self.model}")

        with self.client.stream("POST", "messages", json=params, headers=headers) as resp:
            if resp.status_code >= 400:
                body = resp.read().decode(errors="replace")[:1000]
                logger.error(f"Anthropic HTTP {resp.status_code}: {body}")
            resp.raise_for_status()
            for event_name, data in _iter_sse_json(resp):
                if cancel_event and cancel_event.is_set():
                    logger.info("Anthropic stream cancelled by user")
                    break

                if event_count == 0:
                    logger.info(f"Anthropic first event received after {time.time() - t0:.1f}s")
                event_count += 1

                _process_anthropic_sse(
                    data,
                    event_name,
                    content_blocks,
                    tc_map,
                    content_parts,
                    reasoning_parts,
                    on_token,
                    on_thinking,
                    token_state,
                    _delta_seen,
                )

        prompt_tok = token_state["prompt"]
        completion_tok = token_state["completion"]

        elapsed = time.time() - t0
        logger.info(f"Anthropic stream done: {event_count} events in {elapsed:.1f}s")

        for idx in sorted(content_blocks):
            if idx in _delta_seen:
                continue
            block = content_blocks[idx]
            block_type = block.get("type")
            if block_type == "text":
                text = block.get("text") or ""
                if text:
                    content_parts.append(text)
                    if on_token:
                        on_token(text)
            elif block_type == "thinking":
                thinking = block.get("thinking") or ""
                if thinking:
                    reasoning_parts.append(thinking)
                    if on_thinking:
                        on_thinking(thinking)

        anthropic_content = _finalize_anthropic_content(content_blocks)

        parsed: list[ToolCall] = []
        for idx in sorted(tc_map):
            raw = tc_map[idx]
            args_text = raw.get("args") or "{}"
            try:
                args = json.loads(args_text)
            except json.JSONDecodeError:
                args = {}
            parsed.append(
                ToolCall(
                    id=raw.get("id") or f"toolu_{idx}",
                    name=raw.get("name") or "",
                    arguments=args if isinstance(args, dict) else {},
                )
            )

        self.total_prompt_tokens += prompt_tok
        self.total_completion_tokens += completion_tok

        return LLMResponse(
            content="".join(content_parts),
            reasoning_content="".join(reasoning_parts),
            anthropic_content=anthropic_content,
            tool_calls=parsed,
            prompt_tokens=prompt_tok,
            completion_tokens=completion_tok,
        )

    def _chat_anthropic_non_stream(
        self,
        params: dict,
        on_token=None,
        on_thinking=None,
    ) -> LLMResponse:
        if not isinstance(self.client, httpx.Client):
            raise RuntimeError("Anthropic client is not configured")

        headers = {
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        if self.api_key:
            headers["x-api-key"] = self.api_key

        t0 = time.time()
        logger.info(f"Anthropic request started for {self.model}")
        for attempt in range(3):
            try:
                resp = self.client.post("messages", json=params, headers=headers)
                if resp.status_code >= 400:
                    body = resp.read().decode(errors="replace")[:1000]
                    logger.error(f"Anthropic HTTP {resp.status_code}: {body}")
                resp.raise_for_status()
                data = resp.json()
                break
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                if attempt == 2:
                    raise
                logger.debug(f"Anthropic call failed, retrying: {e}")
                time.sleep(2**attempt)
            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                if status in {408, 409, 429} or status >= 500:
                    if attempt < 2:
                        logger.debug(f"Anthropic HTTP {status}, retrying")
                        time.sleep(2**attempt)
                        continue
                raise
        else:
            raise RuntimeError("Anthropic call failed")
        logger.info(f"Anthropic request done for {self.model} in {time.time() - t0:.1f}s")

        llm_response = _anthropic_message_to_response(
            data,
            on_token=on_token,
            on_thinking=on_thinking,
        )
        self.total_prompt_tokens += llm_response.prompt_tokens
        self.total_completion_tokens += llm_response.completion_tokens
        return llm_response

    def _filtered_options(self, kwargs: dict) -> dict:
        if self.api_format == "anthropic":
            extra: dict[str, Any] = {}
            ignored: list[str] = []
            for key, value in kwargs.items():
                if key == "tool_choice" and isinstance(value, str):
                    extra[key] = {"type": value}
                elif key in _ANTHROPIC_OPTION_KEYS:
                    extra[key] = value
                elif key == "max_completion_tokens":
                    extra["max_tokens"] = value
                elif key == "stop":
                    extra["stop_sequences"] = value if isinstance(value, list) else [value]
                elif key not in {"stream_options"}:
                    ignored.append(key)
            if ignored:
                logger.debug(
                    "Ignoring unsupported Anthropic LLM option(s): " + ", ".join(sorted(ignored))
                )
            return extra

        ignored = sorted(set(kwargs) - _CHAT_COMPLETIONS_OPTION_KEYS)
        if ignored:
            logger.debug(f"Ignoring unsupported LLM option(s): {', '.join(ignored)}")
        return {key: value for key, value in kwargs.items() if key in _CHAT_COMPLETIONS_OPTION_KEYS}


def _openai_completion_to_response(response, on_token=None, on_thinking=None) -> LLMResponse:
    choices = _raw_delta_value(response, "choices") or []
    usage = _raw_delta_value(response, "usage")
    prompt_tok = int(_raw_delta_value(usage, "prompt_tokens") or 0) if usage else 0
    completion_tok = int(_raw_delta_value(usage, "completion_tokens") or 0) if usage else 0

    if not choices:
        return LLMResponse(prompt_tokens=prompt_tok, completion_tokens=completion_tok)

    choice = choices[0]
    message = _raw_delta_value(choice, "message") or choice
    raw_content = _delta_value(message, "content")
    visible_content, tagged_reasoning = _split_thinking_text(
        raw_content,
        on_token=on_token,
        on_thinking=on_thinking,
    )

    reasoning_content = (
        _delta_reasoning_text(message) + _delta_reasoning_text(choice) + tagged_reasoning
    )
    if reasoning_content and on_thinking:
        on_thinking(reasoning_content)

    tool_calls = [
        _openai_tool_call_to_tool_call(raw, idx)
        for idx, raw in enumerate(_raw_delta_value(message, "tool_calls") or [])
    ]

    return LLMResponse(
        content=visible_content,
        reasoning_content=reasoning_content,
        tool_calls=tool_calls,
        prompt_tokens=prompt_tok,
        completion_tokens=completion_tok,
    )


def _openai_tool_call_to_tool_call(raw, idx: int) -> ToolCall:
    function = _raw_delta_value(raw, "function") or {}
    raw_args = _raw_delta_value(function, "arguments") or "{}"
    try:
        args = json.loads(raw_args)
    except (TypeError, json.JSONDecodeError):
        args = {}
    return ToolCall(
        id=_raw_delta_value(raw, "id") or f"call_{idx}",
        name=_raw_delta_value(function, "name") or "",
        arguments=args if isinstance(args, dict) else {},
    )


def _anthropic_message_to_response(data: dict, on_token=None, on_thinking=None) -> LLMResponse:
    usage = data.get("usage") or {}
    prompt_tok = int(usage.get("input_tokens") or 0)
    completion_tok = int(usage.get("output_tokens") or 0)

    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    content_blocks: dict[int, dict] = {}
    tool_calls: list[ToolCall] = []

    for idx, block in enumerate(data.get("content") or []):
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text") or ""
            if text:
                content_parts.append(text)
                content_blocks[idx] = {"type": "text", "text": text}
                if on_token:
                    on_token(text)
        elif block_type == "thinking":
            thinking = block.get("thinking") or ""
            if thinking:
                reasoning_parts.append(thinking)
                content_blocks[idx] = {"type": "thinking", "thinking": thinking}
                if block.get("signature"):
                    content_blocks[idx]["signature"] = block["signature"]
                if on_thinking:
                    on_thinking(thinking)
        elif block_type == "redacted_thinking":
            content_blocks[idx] = {
                key: value for key, value in block.items() if key in {"type", "data"}
            }
        elif block_type == "tool_use":
            raw_input = block.get("input")
            args = raw_input if isinstance(raw_input, dict) else {}
            content_blocks[idx] = {
                "type": "tool_use",
                "id": block.get("id") or f"toolu_{idx}",
                "name": block.get("name") or "",
                "input": args,
            }
            tool_calls.append(
                ToolCall(
                    id=block.get("id") or f"toolu_{idx}",
                    name=block.get("name") or "",
                    arguments=args,
                )
            )

    return LLMResponse(
        content="".join(content_parts),
        reasoning_content="".join(reasoning_parts),
        anthropic_content=_finalize_anthropic_content(content_blocks),
        tool_calls=tool_calls,
        prompt_tokens=prompt_tok,
        completion_tokens=completion_tok,
    )


def _split_thinking_text(text: str, on_token=None, on_thinking=None) -> tuple[str, str]:
    if not text:
        return "", ""
    splitter = _ThinkingTagSplitter()
    visible_content, tagged_reasoning = splitter.feed(text)
    remaining_content, remaining_reasoning = splitter.finish()
    visible_content += remaining_content
    tagged_reasoning += remaining_reasoning
    if visible_content and on_token:
        on_token(visible_content)
    return visible_content, tagged_reasoning


def _raw_delta_value(delta, key: str):
    if isinstance(delta, dict):
        value = delta.get(key)
    else:
        value = getattr(delta, key, None)
    if value is None and hasattr(delta, "model_extra"):
        value = (delta.model_extra or {}).get(key)
    if value is None and hasattr(delta, "model_dump"):
        value = delta.model_dump(exclude_none=True).get(key)
    return value


def _delta_value(delta, key: str) -> str:
    return _value_to_text(_raw_delta_value(delta, key))


def _delta_reasoning_text(delta) -> str:
    parts: list[str] = []
    for key in (
        "reasoning_content",
        "reasoning",
        "thinking",
        "reasoning_text",
        "reasoning_summary",
        "reasoning_details",
    ):
        text = _reasoning_value_to_text(_raw_delta_value(delta, key))
        if text:
            parts.append(text)
    return "".join(parts)


def _value_to_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_value_to_text(part) for part in value)
    if isinstance(value, dict):
        for key in ("text", "content"):
            if key in value:
                return _value_to_text(value[key])
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _reasoning_value_to_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_reasoning_value_to_text(part) for part in value)
    if isinstance(value, dict):
        for key in (
            "text",
            "content",
            "summary",
            "reasoning",
            "reasoning_content",
            "thinking",
            "delta",
        ):
            if key in value:
                return _reasoning_value_to_text(value[key])
        return ""
    return str(value)


class _ThinkingTagSplitter:
    START = "<think>"
    END = "</think>"

    def __init__(self):
        self._buffer = ""
        self._in_thinking = False
        self._thinking_depth = 0

    def feed(self, text: str) -> tuple[str, str]:
        self._buffer += text
        return self._drain(final=False)

    def finish(self) -> tuple[str, str]:
        return self._drain(final=True)

    def _drain(self, final: bool) -> tuple[str, str]:
        content_parts: list[str] = []
        reasoning_parts: list[str] = []

        while self._buffer:
            if self._in_thinking:
                end_idx = self._buffer.lower().find(self.END)
                nested_idx = self._buffer.lower().find(self.START)

                if nested_idx >= 0 and (end_idx < 0 or nested_idx < end_idx):
                    reasoning_parts.append(self._buffer[:nested_idx])
                    self._buffer = self._buffer[nested_idx + len(self.START) :]
                    self._thinking_depth += 1
                    continue

                if end_idx >= 0:
                    reasoning_parts.append(self._buffer[:end_idx])
                    self._buffer = self._buffer[end_idx + len(self.END) :]
                    self._thinking_depth -= 1
                    if self._thinking_depth == 0:
                        self._in_thinking = False
                    continue

                keep = 0 if final else _marker_prefix_suffix_len(self._buffer, self.END)
                reasoning_parts.append(self._buffer[:-keep] if keep else self._buffer)
                self._buffer = self._buffer[-keep:] if keep else ""
                break

            idx = self._buffer.lower().find(self.START)
            if idx >= 0:
                content_parts.append(self._buffer[:idx])
                self._buffer = self._buffer[idx + len(self.START) :]
                self._in_thinking = True
                self._thinking_depth = 1
                continue

            keep = 0 if final else _marker_prefix_suffix_len(self._buffer, self.START)
            content_parts.append(self._buffer[:-keep] if keep else self._buffer)
            self._buffer = self._buffer[-keep:] if keep else ""
            break

        return "".join(content_parts), "".join(reasoning_parts)


def _marker_prefix_suffix_len(text: str, marker: str) -> int:
    lower_text = text.lower()
    for size in range(min(len(lower_text), len(marker) - 1), 0, -1):
        if marker.startswith(lower_text[-size:]):
            return size
    return 0


def _normalize_api_format(api_format: str | None) -> str:
    value = (api_format or "openai").strip().lower()
    return "anthropic" if value == "anthropic" else "openai"


def _ensure_https_url(url: str) -> str:
    cleaned = (url or "").strip().rstrip("/")
    if cleaned and "://" not in cleaned:
        cleaned = "https://" + cleaned
    return cleaned


def _normalize_anthropic_base_url(base_url: str | None) -> str:
    cleaned = _ensure_https_url(base_url or _DEFAULT_ANTHROPIC_BASE_URL)
    parsed = urlsplit(cleaned)
    path = parsed.path.rstrip("/")
    if path.lower().endswith("/messages"):
        path = path[: -len("/messages")]
        cleaned = urlunsplit(parsed._replace(path=path))
    return cleaned.rstrip("/")


def _messages_to_anthropic(messages: list[dict]) -> tuple[str, list[dict]]:
    system_parts: list[str] = []
    converted: list[dict] = []
    pending_tool_results: list[dict] = []

    def flush_tool_results():
        nonlocal pending_tool_results
        if pending_tool_results:
            _append_anthropic_message(converted, "user", pending_tool_results)
            pending_tool_results = []

    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")

        if role == "system":
            text = content_to_text(content)
            if text:
                system_parts.append(text)
            continue

        if role == "tool":
            tool_call_id = msg.get("tool_call_id")
            if tool_call_id:
                pending_tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_call_id,
                        "content": content_to_text(content),
                    }
                )
            continue

        flush_tool_results()

        if role == "assistant":
            anthropic_content = msg.get("anthropic_content")
            if isinstance(anthropic_content, list) and anthropic_content:
                blocks = [
                    _clean_anthropic_content_block(block)
                    for block in anthropic_content
                    if isinstance(block, dict)
                ]
                blocks = [block for block in blocks if block]
                if blocks:
                    _append_anthropic_message(converted, "assistant", blocks)
                    continue

            blocks = []
            text = content_to_text(content)
            if text:
                blocks.append({"type": "text", "text": text})
            for tool_call in msg.get("tool_calls") or []:
                func = tool_call.get("function") or {}
                args_text = func.get("arguments") or "{}"
                try:
                    args = json.loads(args_text)
                except json.JSONDecodeError:
                    args = {}
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tool_call.get("id") or f"toolu_{len(blocks)}",
                        "name": func.get("name") or "",
                        "input": args if isinstance(args, dict) else {},
                    }
                )
            if blocks:
                _append_anthropic_message(converted, "assistant", blocks)
        elif role == "user":
            blocks = _content_to_anthropic_blocks(content)
            if blocks:
                _append_anthropic_message(converted, "user", blocks)

    flush_tool_results()
    return "\n\n".join(system_parts), converted


def _append_anthropic_message(messages: list[dict], role: str, content_blocks: list[dict]):
    if messages and messages[-1]["role"] == role:
        messages[-1]["content"].extend(content_blocks)
    else:
        messages.append({"role": role, "content": list(content_blocks)})


def _content_to_anthropic_blocks(content) -> list[dict]:
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    if isinstance(content, list):
        blocks = []
        for part in content:
            if isinstance(part, str):
                if part:
                    blocks.append({"type": "text", "text": part})
            elif isinstance(part, dict):
                block = _content_part_to_anthropic_block(part)
                if isinstance(block, list):
                    blocks.extend(block)
                elif block:
                    blocks.append(block)
            else:
                blocks.append({"type": "text", "text": str(part)})
        return blocks
    return [{"type": "text", "text": str(content)}]


def _content_part_to_anthropic_block(part: dict):
    block_type = part.get("type")
    if block_type == "text":
        text = part.get("text")
        return {"type": "text", "text": text} if isinstance(text, str) and text else None
    if block_type == "image_url":
        image = _image_url_to_anthropic_block(part.get("image_url"))
        return image or {"type": "text", "text": "[Image attachment]"}
    if block_type == "image":
        return _clean_anthropic_content_block(part)
    return part


def _image_url_to_anthropic_block(image_url):
    url = image_url if isinstance(image_url, str) else (image_url or {}).get("url")
    if not isinstance(url, str) or not url:
        return None
    if not url.startswith("data:") or "," not in url:
        return None

    header, data = url.split(",", 1)
    meta = header[5:].split(";")
    media_type = (meta[0] or "image/png").lower()
    if "base64" not in {part.lower() for part in meta[1:]}:
        return None
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": data,
        },
    }


def _tools_to_anthropic(tools: list[dict]) -> list[dict]:
    converted = []
    for tool in tools:
        if tool.get("type") == "function":
            func = tool.get("function") or {}
            converted.append(
                {
                    "name": func.get("name") or "",
                    "description": func.get("description") or "",
                    "input_schema": func.get("parameters") or {"type": "object"},
                }
            )
        elif "input_schema" in tool and "name" in tool:
            converted.append(tool)
    return converted


def _finalize_anthropic_content(content_blocks: dict[int, dict]) -> list[dict]:
    finalized = []
    for idx in sorted(content_blocks):
        finalized_block = _clean_anthropic_content_block(content_blocks[idx])
        if finalized_block:
            finalized.append(finalized_block)
    return finalized


def _clean_anthropic_content_block(block: dict) -> dict:
    block_type = block.get("type")
    if block_type == "text":
        text = block.get("text") or ""
        return {"type": "text", "text": text} if text else {}
    if block_type == "image":
        source = block.get("source")
        if not isinstance(source, dict):
            return {}
        if source.get("type") == "base64" and source.get("data") and source.get("media_type"):
            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": source["media_type"],
                    "data": source["data"],
                },
            }
        return {}
    if block_type == "thinking":
        thinking = block.get("thinking") or ""
        if not thinking:
            return {}
        cleaned = {"type": "thinking", "thinking": thinking}
        signature = block.get("signature")
        if signature:
            cleaned["signature"] = signature
        return cleaned
    if block_type == "redacted_thinking":
        data = block.get("data")
        return {"type": "redacted_thinking", "data": data} if data else {}
    if block_type == "tool_use":
        raw_input = block.get("input")
        partial_json = block.get("_partial_json") or ""
        if partial_json:
            try:
                parsed_input = json.loads(partial_json)
            except json.JSONDecodeError:
                parsed_input = {}
        else:
            parsed_input = raw_input if isinstance(raw_input, dict) else {}
        return {
            "type": "tool_use",
            "id": block.get("id") or "",
            "name": block.get("name") or "",
            "input": parsed_input if isinstance(parsed_input, dict) else {},
        }
    return {}


def _process_anthropic_sse(
    data: dict,
    event_name: str | None,
    content_blocks: dict[int, dict],
    tc_map: dict[int, dict],
    content_parts: list[str],
    reasoning_parts: list[str],
    on_token,
    on_thinking,
    token_state: dict[str, int],
    delta_seen: set[int],
) -> None:
    """Process a single Anthropic SSE event, mutating state containers in place."""
    event_type = data.get("type") or event_name

    if event_type == "message_start":
        usage = (data.get("message") or {}).get("usage") or {}
        token_state["prompt"] = usage.get("input_tokens", token_state["prompt"]) or 0
        token_state["completion"] = usage.get("output_tokens", token_state["completion"]) or 0

    elif event_type == "message_delta":
        usage = data.get("usage") or {}
        token_state["completion"] = usage.get("output_tokens", token_state["completion"]) or 0

    elif event_type == "content_block_start":
        idx = int(data.get("index", 0))
        block = data.get("content_block") or {}
        block_type = block.get("type")
        if block_type == "text":
            content_blocks[idx] = {"type": "text", "text": block.get("text") or ""}
        elif block_type == "thinking":
            content_blocks[idx] = {
                "type": "thinking",
                "thinking": block.get("thinking") or "",
            }
            if block.get("signature"):
                content_blocks[idx]["signature"] = block["signature"]
        elif block_type == "redacted_thinking":
            content_blocks[idx] = {
                key: value for key, value in block.items() if key in {"type", "data"}
            }
        elif block_type == "tool_use":
            raw_input = block.get("input")
            content_blocks[idx] = {
                "type": "tool_use",
                "id": block.get("id") or f"toolu_{idx}",
                "name": block.get("name") or "",
                "input": raw_input if isinstance(raw_input, dict) else {},
                "_partial_json": "",
            }
            tc_map[idx] = {
                "id": block.get("id") or f"toolu_{idx}",
                "name": block.get("name") or "",
                "args": json.dumps(raw_input) if raw_input else "",
            }

    elif event_type == "content_block_delta":
        idx = int(data.get("index", 0))
        delta = data.get("delta") or {}
        delta_type = delta.get("type")
        if delta_type == "text_delta":
            text = delta.get("text") or ""
            if text:
                delta_seen.add(idx)
                if idx in content_blocks:
                    content_blocks[idx]["text"] = content_blocks[idx].get("text", "") + text
                content_parts.append(text)
                if on_token:
                    on_token(text)
        elif delta_type == "thinking_delta":
            thinking = delta.get("thinking") or ""
            if thinking:
                delta_seen.add(idx)
                if idx in content_blocks:
                    content_blocks[idx]["thinking"] = (
                        content_blocks[idx].get("thinking", "") + thinking
                    )
                reasoning_parts.append(thinking)
                if on_thinking:
                    on_thinking(thinking)
        elif delta_type == "signature_delta":
            signature = delta.get("signature")
            if signature and idx in content_blocks:
                content_blocks[idx]["signature"] = signature
        elif delta_type == "input_json_delta":
            partial = delta.get("partial_json") or ""
            if idx in content_blocks:
                content_blocks[idx]["_partial_json"] = (
                    content_blocks[idx].get("_partial_json", "") + partial
                )
            if idx not in tc_map:
                tc_map[idx] = {"id": f"toolu_{idx}", "name": "", "args": ""}
            tc_map[idx]["args"] += partial

    elif event_type == "error":
        error = data.get("error") or {}
        raise RuntimeError(error.get("message") or "Anthropic stream error")


def _iter_sse_json(resp: httpx.Response):
    event_name = None
    data_lines: list[str] = []
    for line in resp.iter_lines():
        if not line:
            if data_lines:
                raw = "\n".join(data_lines)
                try:
                    yield event_name, json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning(
                        "Anthropic SSE: dropping malformed JSON event (%d bytes)", len(raw)
                    )
            event_name = None
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:") :].lstrip())

    if data_lines:
        raw = "\n".join(data_lines)
        try:
            yield event_name, json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(
                "Anthropic SSE: dropping malformed final JSON event (%d bytes)", len(raw)
            )
