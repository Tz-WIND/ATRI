"""Web search and fetch tools for the agent.

WebSearchTool — search the web via Tavily API (when key is configured)
                or DuckDuckGo HTML (free fallback, no API key).
WebFetchTool  — fetch and extract text content from a URL.
"""

import html as _html
import http.client
import ipaddress
import json as _json
import queue
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from core.research.evidence import canonicalize_url

from .base import Tool, ToolCapabilities

# ---------------------------------------------------------------------------
# Tavily API key — set by lifecycle on startup / config hot-reload
# ---------------------------------------------------------------------------

_tavily_api_key: str | None = None


def set_tavily_key(key: str | None) -> None:
    """Set the Tavily API key. Pass None to disable."""
    global _tavily_api_key
    _tavily_api_key = key.strip() if key else None


def get_tavily_key() -> str | None:
    return _tavily_api_key


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DDG_HTML = "https://html.duckduckgo.com/html/"
_TAVILY_SEARCH = "https://api.tavily.com/search"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)
_TIMEOUT = 20
_ALLOWED_URL_SCHEMES = {"http", "https"}
_MAX_FETCH_RESPONSE_BYTES = 1024 * 1024
_MAX_SEARCH_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_WEB_FETCH_CHARS = 30000
_MAX_WEB_CACHE_CHARS = _MAX_WEB_FETCH_CHARS + 1
_READ_CHUNK_BYTES = 64 * 1024
_RESOLVER_ERRORS = (OSError, RuntimeError, ValueError)
_WEB_TRANSPORT_ERRORS = (
    AttributeError,
    OSError,
    ValueError,
    http.client.HTTPException,
)
_SENSITIVE_REDIRECT_HEADERS = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "cookie2",
    "x-api-key",
    "x-auth-token",
}

# Tags we strip when extracting text from HTML
_SKIP_TAGS = {"script", "style", "noscript", "iframe", "svg", "head", "meta", "link"}


@dataclass(frozen=True)
class _CachedWebPage:
    canonical_url: str
    title: str
    text: str
    citation_id: str = ""


def _truncate_web_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 200] + "\n\n... (truncated)"


def _render_cached_web_page(page: _CachedWebPage, max_chars: int) -> str:
    label = f"[{page.citation_id}] " if page.citation_id else ""
    text = _truncate_web_text(page.text, max_chars)
    return f"{label}Content from {page.canonical_url}:\n\n{text.strip()}"


# ---------------------------------------------------------------------------
# URL opening
# ---------------------------------------------------------------------------


def _validated_http_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    scheme = parsed.scheme.lower()
    if scheme not in _ALLOWED_URL_SCHEMES or not parsed.netloc:
        raise ValueError("Only http and https URLs are supported")
    if parsed.username or parsed.password:
        raise ValueError("URLs with embedded credentials are not supported")
    return urllib.parse.urlunsplit(parsed)


def _resolve_public_addresses(host: str, port: int) -> list[str]:
    """Resolve a host and reject every non-global address in the answer set."""

    try:
        records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"URL host could not be resolved: {host}") from exc
    addresses: list[str] = []
    for record in records:
        address = str(record[4][0]).split("%", 1)[0]
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError as exc:
            raise ValueError("URL host did not resolve to a valid public address") from exc
        if (
            not parsed.is_global
            or parsed.is_private
            or parsed.is_loopback
            or parsed.is_link_local
            or parsed.is_multicast
            or parsed.is_reserved
            or parsed.is_unspecified
        ):
            raise ValueError("URL must resolve only to public addresses")
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise ValueError("URL host did not resolve to a public address")
    return addresses


def _validated_public_http_url(url: str) -> str:
    return _validated_public_target(url).url


@dataclass(frozen=True)
class _PublicTarget:
    url: str
    scheme: str
    host: str
    port: int
    addresses: tuple[str, ...]


def _validated_public_target(
    url: str,
    *,
    controller: Any = None,
) -> _PublicTarget:
    safe_url = _validated_http_url(url)
    parsed = urllib.parse.urlsplit(safe_url)
    host = parsed.hostname or ""
    try:
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except ValueError as exc:
        raise ValueError("URL has an invalid port") from exc
    addresses = tuple(_resolve_public_addresses_until(host, port, controller))
    return _PublicTarget(
        url=safe_url,
        scheme=parsed.scheme.lower(),
        host=host,
        port=port,
        addresses=addresses,
    )


def _request_target(
    request: urllib.request.Request,
    controller: Any = None,
) -> _PublicTarget:
    target = getattr(request, "_atri_public_target", None)
    if isinstance(target, _PublicTarget):
        return target
    target = _validated_public_target(request.full_url, controller=controller)
    request._atri_public_target = target
    return target


def _origin(url: str) -> tuple[str, str, int]:
    parsed = urllib.parse.urlsplit(url)
    scheme = parsed.scheme.lower()
    port = parsed.port or (443 if scheme == "https" else 80)
    return scheme, (parsed.hostname or "").lower(), port


def _strip_cross_origin_credentials(request: urllib.request.Request) -> None:
    for collection in (request.headers, request.unredirected_hdrs):
        for header in list(collection):
            if header.lower() in _SENSITIVE_REDIRECT_HEADERS:
                collection.pop(header, None)


def _shutdown_socket(sock: socket.socket) -> None:
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


class _RequestController:
    """Abort a synchronous urllib request at an absolute deadline or on cancel."""

    def __init__(
        self,
        seconds_remaining: Callable[[], float] | None = None,
    ) -> None:
        self._seconds_remaining = seconds_remaining
        self._lock = threading.Lock()
        self._sockets: set[socket.socket] = set()
        self._closers: set[Callable[[], None]] = set()
        self._reason: str | None = None
        self._closed = False
        self._deadline: float | None = None
        self._timer: threading.Timer | None = None
        if seconds_remaining is not None:
            remaining = max(0.0, float(seconds_remaining()))
            self._deadline = time.monotonic() + remaining
            if remaining <= 0:
                self._reason = "synthesis"
            else:
                self._timer = threading.Timer(
                    remaining,
                    lambda: self.abort("synthesis"),
                )
                self._timer.daemon = True
                self._timer.start()

    def clamp_timeout(self, timeout: object) -> object:
        if self._deadline is None:
            return timeout
        remaining = max(0.001, self._deadline - time.monotonic())
        if timeout is socket._GLOBAL_DEFAULT_TIMEOUT:
            return remaining
        return min(max(0.001, float(timeout)), remaining)

    def register_socket(self, sock: socket.socket) -> None:
        self.check()
        with self._lock:
            should_abort = self._closed or self._reason is not None
            if not should_abort:
                self._sockets.add(sock)
        if should_abort:
            _shutdown_socket(sock)
            self.check()

    def unregister_socket(self, sock: socket.socket) -> None:
        with self._lock:
            self._sockets.discard(sock)

    def replace_socket(self, old: socket.socket, new: socket.socket) -> None:
        self.unregister_socket(old)
        self.register_socket(new)

    def register_closer(self, closer: Callable[[], None]) -> None:
        with self._lock:
            should_abort = self._closed or self._reason is not None
            if not should_abort:
                self._closers.add(closer)
        if should_abort:
            closer()

    def unregister_closer(self, closer: Callable[[], None]) -> None:
        with self._lock:
            self._closers.discard(closer)

    def abort(self, reason: str) -> None:
        with self._lock:
            if self._closed or self._reason is not None:
                return
            self._reason = reason
            sockets = list(self._sockets)
            closers = list(self._closers)
        for sock in sockets:
            _shutdown_socket(sock)
        for closer in closers:
            try:
                closer()
            except (AttributeError, OSError):
                pass

    def check(self) -> None:
        if self._seconds_remaining is not None:
            if float(self._seconds_remaining()) <= 0:
                self.abort("synthesis")
        if self._deadline is not None and time.monotonic() >= self._deadline:
            self.abort("synthesis")
        with self._lock:
            reason = self._reason
            closed = self._closed
        if reason == "cancelled" or (closed and reason is None):
            raise OSError("request cancelled")
        if reason == "synthesis":
            raise TimeoutError("synthesis deadline reached during web request")

    def close(self, *, abort_sockets: bool = False) -> None:
        timer = self._timer
        if timer is not None:
            timer.cancel()
        with self._lock:
            self._closed = True
            sockets = list(self._sockets) if abort_sockets else []
            self._sockets.clear()
            self._closers.clear()
        for sock in sockets:
            _shutdown_socket(sock)


class _ResolutionJob:
    def __init__(self, work: Callable[[], list[str]]) -> None:
        self.work = work
        self.done = threading.Event()
        self.completed = False
        self.result: list[str] | None = None
        self.error: BaseException | None = None


class _DeadlineResolverPool:
    """Bounded daemon workers keep synchronous DNS off deadline-bound callers."""

    def __init__(self, workers: int = 4, max_pending: int = 32) -> None:
        self._workers = workers
        self._queue: queue.Queue[_ResolutionJob] = queue.Queue(maxsize=max_pending)
        self._start_lock = threading.Lock()
        self._started = False

    def resolve(
        self,
        work: Callable[[], list[str]],
        controller: _RequestController,
    ) -> list[str]:
        self._ensure_started()
        controller.check()
        job = _ResolutionJob(work)
        wake = job.done.set
        controller.register_closer(wake)
        try:
            controller.check()
            try:
                self._queue.put_nowait(job)
            except queue.Full as exc:
                raise OSError("DNS resolver queue is full") from exc
            job.done.wait()
            controller.check()
            if not job.completed:
                raise OSError("DNS resolution was interrupted")
            if job.error is not None:
                raise job.error
            return list(job.result or [])
        finally:
            controller.unregister_closer(wake)

    def _ensure_started(self) -> None:
        with self._start_lock:
            if self._started:
                return
            for index in range(self._workers):
                worker = threading.Thread(
                    target=self._run,
                    name=f"atri-dns-{index + 1}",
                    daemon=True,
                )
                worker.start()
            self._started = True

    def _run(self) -> None:
        while True:
            job = self._queue.get()
            try:
                job.result = job.work()
            except _RESOLVER_ERRORS as exc:
                job.error = exc
            finally:
                job.completed = True
                job.done.set()
                self._queue.task_done()


_DEADLINE_RESOLVER = _DeadlineResolverPool()


def _resolve_public_addresses_until(
    host: str,
    port: int,
    controller: _RequestController | None,
) -> list[str]:
    if controller is None:
        return _resolve_public_addresses(host, port)
    resolver = _resolve_public_addresses
    return _DEADLINE_RESOLVER.resolve(
        lambda: resolver(host, port),
        controller,
    )


def _connect_pinned_socket(
    addresses: tuple[str, ...],
    port: int,
    timeout: object,
    source_address: tuple[str, int] | None = None,
    *,
    controller: _RequestController | None = None,
):
    last_error: OSError | None = None
    for address in addresses:
        if controller is not None:
            controller.check()
        parsed = ipaddress.ip_address(address)
        if not parsed.is_global:
            raise OSError("refusing to connect to a non-public pinned address")
        family = socket.AF_INET6 if parsed.version == 6 else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            if controller is not None:
                controller.register_socket(sock)
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                effective_timeout = (
                    controller.clamp_timeout(timeout) if controller is not None else timeout
                )
                sock.settimeout(effective_timeout)
            if source_address:
                sock.bind(source_address)
            destination = (address, port, 0, 0) if family == socket.AF_INET6 else (address, port)
            sock.connect(destination)
            peer = str(sock.getpeername()[0]).split("%", 1)[0]
            peer_ip = ipaddress.ip_address(peer)
            if not peer_ip.is_global or peer_ip != parsed:
                raise OSError("connected peer does not match the validated public address")
            return sock
        except OSError as exc:
            last_error = exc
            if controller is not None:
                controller.unregister_socket(sock)
            sock.close()
            if controller is not None:
                controller.check()
    raise last_error or OSError("no validated public address was connectable")


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host, port, *, pinned_addresses, controller=None, **kwargs):
        self._pinned_addresses = tuple(pinned_addresses)
        self._controller = controller
        super().__init__(host, port=port, **kwargs)

    def connect(self):
        self.sock = _connect_pinned_socket(
            self._pinned_addresses,
            self.port,
            self.timeout,
            self.source_address,
            controller=self._controller,
        )
        if self._tunnel_host:
            self._tunnel()


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, port, *, pinned_addresses, controller=None, **kwargs):
        self._pinned_addresses = tuple(pinned_addresses)
        self._controller = controller
        super().__init__(host, port=port, **kwargs)

    def connect(self):
        self.sock = _connect_pinned_socket(
            self._pinned_addresses,
            self.port,
            self.timeout,
            self.source_address,
            controller=self._controller,
        )
        raw_socket = self.sock
        server_hostname = self.host
        if self._tunnel_host:
            self._tunnel()
            server_hostname = self._tunnel_host
        self.sock = self._context.wrap_socket(
            self.sock,
            server_hostname=server_hostname,
        )
        if self._controller is not None:
            self._controller.replace_socket(raw_socket, self.sock)


class PinnedHTTPHandler(urllib.request.HTTPHandler):
    """Connect HTTP requests only to the address validated for that request."""

    def __init__(self, controller: _RequestController | None = None) -> None:
        super().__init__()
        self._controller = controller

    def http_open(self, req):
        target = _request_target(req, self._controller)

        def factory(_host, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, **kwargs):
            return _PinnedHTTPConnection(
                target.host,
                target.port,
                pinned_addresses=target.addresses,
                controller=self._controller,
                timeout=timeout,
                **kwargs,
            )

        return self.do_open(factory, req)


class PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    """Pin the TCP peer while preserving hostname-based TLS verification and SNI."""

    def __init__(self, controller: _RequestController | None = None) -> None:
        super().__init__()
        self._controller = controller

    def https_open(self, req):
        target = _request_target(req, self._controller)

        def factory(_host, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, **kwargs):
            return _PinnedHTTPSConnection(
                target.host,
                target.port,
                pinned_addresses=target.addresses,
                controller=self._controller,
                timeout=timeout,
                **kwargs,
            )

        return self.do_open(factory, req, context=self._context)


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-run public-network validation before following every redirect."""

    def __init__(self, controller: _RequestController | None = None) -> None:
        super().__init__()
        self._controller = controller

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if self._controller is not None:
            self._controller.check()
        target = _validated_public_target(newurl, controller=self._controller)
        if self._controller is not None:
            self._controller.check()
        redirected = super().redirect_request(req, fp, code, msg, headers, target.url)
        if redirected is None:
            return None
        redirected._atri_public_target = target
        if _origin(req.full_url) != _origin(target.url):
            _strip_cross_origin_credentials(redirected)
        return redirected


class _ResponseCall:
    """Cancellation and resources belonging to one parallel tool invocation."""

    def __init__(self) -> None:
        self.cancelled = threading.Event()
        self._lock = threading.Lock()
        self._controllers: set[_RequestController] = set()
        self._responses: dict[int, Any] = {}
        self._finished = False

    def add_controller(self, controller: _RequestController) -> None:
        with self._lock:
            should_abort = self.cancelled.is_set() or self._finished
            if not should_abort:
                self._controllers.add(controller)
        if should_abort:
            controller.abort("cancelled")

    def remove_controller(self, controller: _RequestController) -> None:
        with self._lock:
            self._controllers.discard(controller)

    def add_response(self, response: Any) -> None:
        with self._lock:
            should_abort = self.cancelled.is_set() or self._finished
            if not should_abort:
                self._responses[id(response)] = response
        if should_abort:
            _abort_response(response, "cancelled")

    def release_response(self, response: Any) -> None:
        controller = getattr(response, "_atri_request_controller", None)
        with self._lock:
            self._responses.pop(id(response), None)
            if isinstance(controller, _RequestController):
                self._controllers.discard(controller)
        _close_response(response)
        if isinstance(controller, _RequestController):
            controller.close()

    def cancel(self) -> None:
        self.cancelled.set()
        with self._lock:
            controllers = list(self._controllers)
            responses = list(self._responses.values())
        for controller in controllers:
            controller.abort("cancelled")
        for response in responses:
            if not isinstance(
                getattr(response, "_atri_request_controller", None),
                _RequestController,
            ):
                _close_response(response)

    def finish(self) -> None:
        with self._lock:
            self._finished = True
            controllers = list(self._controllers)
            responses = list(self._responses.values())
            self._controllers.clear()
            self._responses.clear()
        for response in responses:
            _close_response(response)
        for controller in controllers:
            controller.close(abort_sockets=True)


def _open_url(
    url: str,
    data: bytes | None = None,
    timeout: float = _TIMEOUT,
    extra_headers: dict | None = None,
    *,
    seconds_remaining: Callable[[], float] | None = None,
    call: _ResponseCall | None = None,
):
    """Open a URL with standard SSL verification."""
    headers = {"User-Agent": _USER_AGENT}
    if data:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if extra_headers:
        headers.update(extra_headers)

    controller = (
        _RequestController(seconds_remaining)
        if seconds_remaining is not None or call is not None
        else None
    )
    response = None
    ready = False
    if controller is not None and call is not None:
        call.add_controller(controller)
    try:
        if controller is not None:
            controller.check()
        target = _validated_public_target(url, controller=controller)
        if controller is not None:
            controller.check()
        # S310 is suppressed after scheme validation; HTTPS uses default TLS checks.
        req = urllib.request.Request(target.url, data=data, headers=headers)  # noqa: S310
        req._atri_public_target = target
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            PinnedHTTPHandler(controller),
            PinnedHTTPSHandler(controller),
            SafeRedirectHandler(controller),
        )
        effective_timeout = controller.clamp_timeout(timeout) if controller is not None else timeout
        response = opener.open(req, timeout=effective_timeout)
        if controller is not None:
            controller.check()
            response._atri_request_controller = controller
        ready = True
        return response
    except _WEB_TRANSPORT_ERRORS as exc:
        if controller is not None:
            try:
                controller.check()
            except OSError as abort_exc:
                raise abort_exc from exc
        raise
    finally:
        if not ready and controller is not None:
            if response is not None:
                _close_response(response)
            if call is not None:
                call.remove_controller(controller)
            controller.close(abort_sockets=True)


class _ResponseTracker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls: set[_ResponseCall] = set()

    def begin(self) -> _ResponseCall:
        call = _ResponseCall()
        with self._lock:
            self._calls.add(call)
        return call

    def finish(self, call: _ResponseCall) -> None:
        with self._lock:
            self._calls.discard(call)
        call.finish()

    @staticmethod
    def add(call: _ResponseCall, response: Any) -> None:
        call.add_response(response)

    @staticmethod
    def release(call: _ResponseCall, response: Any) -> None:
        call.release_response(response)

    def cancel(self) -> None:
        with self._lock:
            calls = list(self._calls)
        for call in calls:
            call.cancel()


def _close_response(response: Any) -> None:
    close = getattr(response, "close", None)
    if callable(close):
        try:
            close()
        except (AttributeError, OSError):
            return


def _abort_response(response: Any, reason: str) -> None:
    controller = getattr(response, "_atri_request_controller", None)
    if isinstance(controller, _RequestController):
        controller.abort(reason)
    else:
        _close_response(response)


def _read_limited_response(
    response: Any,
    max_bytes: int,
    *,
    call: _ResponseCall | None = None,
    seconds_remaining: Callable[[], float] | None = None,
) -> bytes:
    chunks: list[bytes] = []
    total = 0
    controller = getattr(response, "_atri_request_controller", None)
    owns_controller = not isinstance(controller, _RequestController)
    if owns_controller:
        controller = _RequestController(seconds_remaining)
        controller.register_closer(lambda: _close_response(response))

    def check_interruption() -> None:
        if call is not None and call.cancelled.is_set():
            raise OSError("request cancelled")
        controller.check()

    try:
        while True:
            check_interruption()
            try:
                chunk = response.read(min(_READ_CHUNK_BYTES, max_bytes - total + 1))
            except _WEB_TRANSPORT_ERRORS as exc:
                try:
                    check_interruption()
                except OSError as abort_exc:
                    raise abort_exc from exc
                raise
            check_interruption()
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"response body exceeds {max_bytes}-byte limit")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        if owns_controller:
            controller.close()


def _request_timeout(session: Any, default: float = _TIMEOUT) -> float:
    if session is None:
        return float(default)
    return min(float(default), max(0.0, session.budget.seconds_until_synthesis()))


# ---------------------------------------------------------------------------
# WebSearchTool
# ---------------------------------------------------------------------------


class WebSearchTool(Tool):
    name = "web_search"
    description = (
        "Search the web for up-to-date information. "
        "Returns titles, snippets, and URLs for each result. "
        "Use this when you need information beyond your knowledge cutoff, "
        "current events, or to verify facts."
    )
    parameters = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query string"},
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results to return (default: 5, max: 10)",
            },
        },
        "required": ["query"],
    }
    capabilities = ToolCapabilities(
        capability="network.search",
        read_only=True,
        network=True,
        supports_parallel=True,
    )

    def __init__(
        self,
        workspace: str = ".",
        *,
        research_session_provider: Callable[[], Any] | None = None,
        research_branch_provider: Callable[[], str] | None = None,
    ) -> None:
        super().__init__(workspace)
        self.research_session_provider = research_session_provider
        self.research_branch_provider = research_branch_provider
        self._response_tracker = _ResponseTracker()

    def execute(self, query: str, max_results: int = 5, **kwargs: Any) -> str:
        call = self._response_tracker.begin()
        try:
            return self._execute(call, query, max_results, **kwargs)
        finally:
            self._response_tracker.finish(call)

    def _execute(
        self,
        call: _ResponseCall,
        query: str,
        max_results: int = 5,
        **kwargs: Any,
    ) -> str:
        del kwargs
        query = str(query or "").strip()
        if not query:
            return "Error: query is required."
        try:
            max_results = min(max(1, int(max_results)), 10)
        except (TypeError, ValueError):
            max_results = 5
        session = self._session()
        seconds_remaining = session.budget.seconds_until_synthesis if session is not None else None
        if session is not None:
            decision = session.reserve_tool_call(self.name)
            if not decision.allowed:
                return f"Web search blocked: {decision.reason}."
        key = get_tavily_key()
        if key:
            try:
                timeout = _request_timeout(session)
                if timeout <= 0:
                    return "Web search blocked: synthesis_reserved."
                results = self._tavily_results(
                    query,
                    max_results,
                    key,
                    call=call,
                    timeout=timeout,
                    seconds_remaining=seconds_remaining,
                )
            except (OSError, ValueError, urllib.error.URLError) as exc:
                if not _is_transient_network_error(exc):
                    return f"Tavily search failed: {exc}"
                try:
                    timeout = _request_timeout(session)
                    if timeout <= 0:
                        return "Web search blocked: synthesis_reserved."
                    results = self._ddg_results(
                        query,
                        max_results,
                        call=call,
                        timeout=timeout,
                        seconds_remaining=seconds_remaining,
                    )
                except (OSError, ValueError, urllib.error.URLError) as fallback_exc:
                    return f"Web search failed: {fallback_exc}"
        else:
            try:
                timeout = _request_timeout(session)
                if timeout <= 0:
                    return "Web search blocked: synthesis_reserved."
                results = self._ddg_results(
                    query,
                    max_results,
                    call=call,
                    timeout=timeout,
                    seconds_remaining=seconds_remaining,
                )
            except (OSError, ValueError, urllib.error.URLError) as exc:
                return f"Web search failed: {exc}"
        return self._format_results(query, results, session)

    def _session(self):
        return self.research_session_provider() if self.research_session_provider else None

    def _branch_id(self) -> str:
        if self.research_branch_provider:
            return str(self.research_branch_provider() or "main")
        return "main"

    def cancel(self):
        self._response_tracker.cancel()

    # ------------------------------------------------------------------
    # Tavily backend
    # ------------------------------------------------------------------

    def _search_tavily(self, query: str, max_results: int, api_key: str) -> str:
        call = self._response_tracker.begin()
        try:
            return self._format_results(
                query,
                self._tavily_results(query, max_results, api_key, call=call),
                None,
            )
        except (OSError, ValueError) as e:
            return f"Tavily search failed: {e}"
        finally:
            self._response_tracker.finish(call)

    def _tavily_results(
        self,
        query: str,
        max_results: int,
        api_key: str,
        *,
        call: _ResponseCall,
        timeout: float = _TIMEOUT,
        seconds_remaining: Callable[[], float] | None = None,
    ) -> list[dict[str, str]]:
        payload = _json.dumps(
            {
                "query": query,
                "max_results": max_results,
                "search_depth": "basic",
                "include_answer": False,
                "include_raw_content": False,
                "include_images": False,
            }
        ).encode("utf-8")
        resp = _open_url(
            _TAVILY_SEARCH,
            data=payload,
            extra_headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            timeout=timeout,
            seconds_remaining=seconds_remaining,
            call=call,
        )
        self._response_tracker.add(call, resp)
        try:
            raw = _read_limited_response(
                resp,
                _MAX_SEARCH_RESPONSE_BYTES,
                call=call,
                seconds_remaining=seconds_remaining,
            )
        finally:
            self._response_tracker.release(call, resp)
        body = _json.loads(raw.decode("utf-8", errors="ignore"))
        return [
            {
                "title": str(item.get("title") or "Untitled"),
                "snippet": str(item.get("content") or item.get("snippet") or "(no snippet)"),
                "url": str(item.get("url") or ""),
            }
            for item in body.get("results", [])
            if isinstance(item, dict)
        ]

    # ------------------------------------------------------------------
    # DuckDuckGo fallback
    # ------------------------------------------------------------------

    def _search_ddg(self, query: str, max_results: int) -> str:
        call = self._response_tracker.begin()
        try:
            results = self._ddg_results(query, max_results, call=call)
        except (OSError, ValueError, urllib.error.URLError) as e:
            return f"Web search failed: {e}"
        finally:
            self._response_tracker.finish(call)

        return self._format_results(query, results, None)

    def _ddg_results(
        self,
        query: str,
        max_results: int,
        *,
        call: _ResponseCall,
        timeout: float = _TIMEOUT,
        seconds_remaining: Callable[[], float] | None = None,
    ) -> list[dict[str, str]]:
        data = urllib.parse.urlencode({"q": query}).encode("utf-8")
        resp = _open_url(
            _DDG_HTML,
            data=data,
            timeout=timeout,
            seconds_remaining=seconds_remaining,
            call=call,
        )
        self._response_tracker.add(call, resp)
        try:
            raw_bytes = _read_limited_response(
                resp,
                _MAX_SEARCH_RESPONSE_BYTES,
                call=call,
                seconds_remaining=seconds_remaining,
            )
        finally:
            self._response_tracker.release(call, resp)
        raw = raw_bytes.decode("utf-8", errors="ignore")
        return self._parse_ddg(raw, max_results)

    def _format_results(self, query: str, results: list[dict], session) -> str:
        if not results:
            return f"No results found for: {query}"

        lines = [f"Web search — {query}\n"]
        for i, r in enumerate(results, 1):
            title = str(r.get("title") or "Untitled")
            snippet = str(r.get("snippet") or r.get("content") or "(no snippet)")
            url = _normalize_search_result_url(str(r.get("url") or ""))
            label = str(i)
            if session is not None and url:
                try:
                    item = session.ledger.add_web(
                        query=query,
                        url=url,
                        title=title,
                        excerpt=snippet,
                        strength="discovery",
                        branch_id=self._branch_id(),
                    )
                    label = f"[{item.citation_id}]"
                except ValueError:
                    label = str(i)
            lines.append(f"{label}. {title}")
            lines.append(f"   {snippet}")
            lines.append(f"   URL: {url}")
            lines.append("")
        return "\n".join(lines).strip()

    @staticmethod
    def _parse_ddg(html_text: str, max_results: int) -> list[dict]:
        """Extract search results from DuckDuckGo HTML."""
        results = []

        link_pat = re.compile(
            r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
            re.DOTALL,
        )
        snippet_pat = re.compile(
            r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
            re.DOTALL,
        )

        blocks = re.split(r'<div[^>]*class="[^"]*result[^"]*"[^>]*>', html_text)

        for block in blocks:
            link_m = link_pat.search(block)
            if not link_m:
                continue

            url = _html.unescape(link_m.group(1)).strip()
            title = _html.unescape(re.sub(r"<[^>]*>", "", link_m.group(2))).strip()

            if not url or not title:
                continue

            # Skip ad / tracker redirects
            if "bing.com/aclick" in url or "ad_domain" in url or "ad_provider" in url:
                continue

            snippet = "(no snippet)"
            snip_m = snippet_pat.search(block)
            if snip_m:
                snippet = _html.unescape(re.sub(r"<[^>]*>", "", snip_m.group(1))).strip()
                snippet = snippet if snippet else "(no snippet)"

            results.append({"title": title, "url": url, "snippet": snippet})

            if len(results) >= max_results:
                break

        return results


def _normalize_search_result_url(url: str) -> str:
    value = _html.unescape(str(url or "").strip())
    if value.startswith("//"):
        value = "https:" + value
    parsed = urllib.parse.urlsplit(value)
    if (parsed.hostname or "").lower().endswith("duckduckgo.com"):
        target = urllib.parse.parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            value = urllib.parse.unquote(target)
    try:
        return _validated_http_url(value)
    except ValueError:
        return ""


def _is_transient_network_error(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code == 429 or 500 <= exc.code <= 599
    return isinstance(exc, (urllib.error.URLError, OSError, TimeoutError))


# ---------------------------------------------------------------------------
# WebFetchTool
# ---------------------------------------------------------------------------


class WebFetchTool(Tool):
    name = "web_fetch"
    description = (
        "Fetch and extract readable text from a URL. "
        "Use this to read the full content of a web page found via web_search, "
        "or to retrieve information from a specific URL. "
        "Returns the page's text content (HTML tags stripped)."
    )
    parameters = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The URL to fetch"},
            "max_chars": {
                "type": "integer",
                "description": "Max characters to return (default: 8000, max: 30000)",
            },
        },
        "required": ["url"],
    }
    capabilities = ToolCapabilities(
        capability="network.fetch",
        read_only=True,
        network=True,
        supports_parallel=True,
    )

    def __init__(
        self,
        workspace: str = ".",
        *,
        research_session_provider: Callable[[], Any] | None = None,
        research_branch_provider: Callable[[], str] | None = None,
    ) -> None:
        super().__init__(workspace)
        self.research_session_provider = research_session_provider
        self.research_branch_provider = research_branch_provider
        self._response_tracker = _ResponseTracker()

    def execute(self, url: str, max_chars: int = 8000, **kwargs: Any) -> str:
        call = self._response_tracker.begin()
        try:
            return self._execute(call, url, max_chars, **kwargs)
        finally:
            self._response_tracker.finish(call)

    def _execute(
        self,
        call: _ResponseCall,
        url: str,
        max_chars: int = 8000,
        **kwargs: Any,
    ) -> str:
        del kwargs
        try:
            max_chars = min(max(500, int(max_chars)), 30000)
        except (TypeError, ValueError):
            max_chars = 8000
        try:
            safe_url = _validated_http_url(str(url or "").strip())
            canonical_url = canonicalize_url(safe_url)
        except ValueError as exc:
            return f"Fetch failed for {url}: {exc}"
        session = self._session()
        seconds_remaining = session.budget.seconds_until_synthesis if session is not None else None
        if session is not None:
            cached = session.get_cached_web(canonical_url)
            if cached is not None:
                if isinstance(cached, _CachedWebPage):
                    return _render_cached_web_page(cached, max_chars)
                return str(cached)
            decision = session.reserve_web_fetch(canonical_url)
            if not decision.allowed:
                return f"Web fetch blocked: {decision.reason}."

        attempts = 3 if session is not None else 1
        response = None
        for attempt in range(attempts):
            if call.cancelled.is_set():
                return "Web fetch cancelled."
            timeout = _request_timeout(session)
            if timeout <= 0:
                return "Web fetch blocked: synthesis_reserved."
            try:
                response = _open_url(
                    safe_url,
                    timeout=timeout,
                    seconds_remaining=seconds_remaining,
                    call=call,
                )
                self._response_tracker.add(call, response)
                break
            except urllib.error.HTTPError as exc:
                if call.cancelled.is_set():
                    return "Web fetch cancelled."
                if attempt + 1 < attempts and _is_transient_network_error(exc):
                    if not self._sleep_before_retry(session, 0.2 * (2**attempt)):
                        return "Web fetch blocked: synthesis_reserved."
                    continue
                return f"HTTP error fetching {safe_url}: {exc.code} {exc.reason}"
            except (urllib.error.URLError, OSError) as exc:
                if call.cancelled.is_set():
                    return "Web fetch cancelled."
                if attempt + 1 < attempts:
                    if not self._sleep_before_retry(session, 0.2 * (2**attempt)):
                        return "Web fetch blocked: synthesis_reserved."
                    continue
                reason = getattr(exc, "reason", exc)
                return f"Connection error fetching {safe_url}: {reason}"
            except ValueError as exc:
                return f"Fetch failed for {safe_url}: {exc}"
        if response is None:
            return f"Fetch failed for {safe_url}: no response"

        try:
            content_type = response.headers.get("Content-Type", "")
            response_url = response.geturl() if hasattr(response, "geturl") else safe_url
            raw = _read_limited_response(
                response,
                _MAX_FETCH_RESPONSE_BYTES,
                call=call,
                seconds_remaining=seconds_remaining,
            )
            charset = "utf-8"
            if "charset=" in content_type:
                charset = content_type.split("charset=")[-1].split(";")[0].strip()
            try:
                html_text = raw.decode(charset, errors="ignore")
            except (UnicodeDecodeError, LookupError):
                html_text = raw.decode("utf-8", errors="ignore")
            title = self._extract_title(html_text)
            text = self._extract_text(html_text)
        except (OSError, ValueError) as exc:
            if call.cancelled.is_set():
                return "Web fetch cancelled."
            return f"Fetch failed for {safe_url}: {exc}"
        finally:
            self._response_tracker.release(call, response)
        if not text.strip():
            return f"Could not extract readable text from {safe_url}"

        final_url = safe_url
        try:
            final_url = _validated_public_http_url(response_url)
        except ValueError as exc:
            return f"Fetch failed for {safe_url}: {exc}"
        final_canonical = canonicalize_url(final_url)
        citation_id = ""
        if session is not None:
            item = session.ledger.add_web(
                query=safe_url,
                url=final_canonical,
                title=title or final_canonical,
                excerpt=_truncate_web_text(text, _MAX_WEB_FETCH_CHARS),
                strength="full",
                branch_id=self._branch_id(),
            )
            citation_id = item.citation_id
        page = _CachedWebPage(
            canonical_url=final_canonical,
            title=title or final_canonical,
            text=text[:_MAX_WEB_CACHE_CHARS],
            citation_id=citation_id,
        )
        result = _render_cached_web_page(page, max_chars)
        if session is not None:
            session.cache_web(canonical_url, page)
            if final_canonical != canonical_url:
                session.cache_web(final_canonical, page)
        return result

    def _session(self):
        return self.research_session_provider() if self.research_session_provider else None

    def _branch_id(self) -> str:
        if self.research_branch_provider:
            return str(self.research_branch_provider() or "main")
        return "main"

    def cancel(self):
        self._response_tracker.cancel()

    @staticmethod
    def _sleep_before_retry(session: Any, delay: float) -> bool:
        if session is None:
            time.sleep(delay)
            return True
        remaining = session.budget.seconds_until_synthesis()
        if remaining <= 0:
            return False
        time.sleep(min(delay, remaining))
        return True

    # ------------------------------------------------------------------
    # Text extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_text(html_text: str) -> str:
        """Strip HTML tags and return plain text, keeping basic structure."""
        for tag in _SKIP_TAGS:
            html_text = re.sub(rf"<{tag}[^>]*>.*?</{tag}>", "", html_text, flags=re.DOTALL | re.I)

        html_text = re.sub(r"\s+", " ", html_text)

        for tag in ("p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "br"):
            html_text = re.sub(rf"<\s*/?\s*{tag}[^>]*>", "\n", html_text, flags=re.I)

        text = re.sub(r"<[^>]*>", "", html_text)
        text = _html.unescape(text)
        text = re.sub(r"\n\s*\n", "\n\n", text)
        text = re.sub(r"[ \t]+", " ", text)

        return text.strip()

    @staticmethod
    def _extract_title(html_text: str) -> str:
        match = re.search(r"<title[^>]*>(.*?)</title>", html_text, flags=re.DOTALL | re.I)
        if not match:
            return ""
        title = _html.unescape(re.sub(r"<[^>]*>", "", match.group(1)))
        return re.sub(r"\s+", " ", title).strip()
