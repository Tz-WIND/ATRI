import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

from core.research import ResearchPolicy, ResearchSession
from core.tools import web_search


def _start_slow_http_server(*, drip_headers: bool):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(2)
    port = listener.getsockname()[1]

    def serve():
        try:
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(1)
                request = b""
                while b"\r\n\r\n" not in request:
                    chunk = connection.recv(4096)
                    if not chunk:
                        return
                    request += chunk
                headers = (
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: text/html; charset=utf-8\r\n"
                    b"Content-Length: 80\r\n"
                    b"Connection: close\r\n\r\n"
                )
                if drip_headers:
                    for value in headers:
                        connection.sendall(bytes([value]))
                        time.sleep(0.015)
                    connection.sendall(b"{}")
                    return
                connection.sendall(headers)
                for _ in range(80):
                    connection.sendall(b"x")
                    time.sleep(0.015)
        except (BrokenPipeError, ConnectionError, OSError):
            return
        finally:
            listener.close()

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    return port, worker


def _route_web_connection_to_local_server(monkeypatch, port):
    monkeypatch.setattr(
        web_search,
        "_resolve_public_addresses",
        lambda host, requested_port: ["93.184.216.34"],
    )

    def connect_local(
        addresses,
        requested_port,
        timeout,
        source_address=None,
        **kwargs,
    ):
        del addresses, requested_port, source_address
        connect_timeout = None if timeout is socket._GLOBAL_DEFAULT_TIMEOUT else timeout
        connection = socket.create_connection(("127.0.0.1", port), timeout=connect_timeout)
        controller = kwargs.get("controller")
        if controller is not None:
            controller.register_socket(connection)
        return connection

    monkeypatch.setattr(web_search, "_connect_pinned_socket", connect_local)


def test_open_url_uses_default_tls_verification(monkeypatch):
    response = object()
    captured = {}

    class FakeOpener:
        def open(self, request, **kwargs):
            captured["url"] = request.full_url
            captured["kwargs"] = kwargs
            captured["request_target"] = getattr(request, "_atri_public_target", None)
            return response

    def fake_build_opener(*handlers):
        captured["handlers"] = handlers
        return FakeOpener()

    def public_addresses(host, port):
        assert (host, port) == ("example.com", 443)
        return ["93.184.216.34"]

    def fail_urlopen(request, **kwargs):
        captured["url"] = request.full_url
        captured["kwargs"] = kwargs
        raise AssertionError("the safe redirect opener must be used")

    monkeypatch.setattr(web_search, "_resolve_public_addresses", public_addresses)
    monkeypatch.setattr(web_search.urllib.request, "build_opener", fake_build_opener)
    monkeypatch.setattr(web_search.urllib.request, "urlopen", fail_urlopen)

    result = web_search._open_url("https://example.com/search?q=test", timeout=3)

    assert result is response
    assert captured["url"] == "https://example.com/search?q=test"
    assert captured["kwargs"] == {"timeout": 3}
    assert any(isinstance(item, urllib.request.ProxyHandler) for item in captured["handlers"])
    assert any(isinstance(item, web_search.PinnedHTTPHandler) for item in captured["handlers"])
    assert any(isinstance(item, web_search.PinnedHTTPSHandler) for item in captured["handlers"])
    assert any(isinstance(item, web_search.SafeRedirectHandler) for item in captured["handlers"])
    request_target = captured["request_target"]
    assert request_target is not None
    assert request_target.addresses == ("93.184.216.34",)


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/secret",
        "ftp://example.com/file",
        "javascript:alert(1)",
        "https:///missing-host",
        "https://user:pass@example.com/",
        "example.com/page",
    ],
)
def test_open_url_rejects_unsupported_urls_before_request(monkeypatch, url):
    def fail_urlopen(*args, **kwargs):
        raise AssertionError("urlopen should not be called")

    monkeypatch.setattr(web_search.urllib.request, "urlopen", fail_urlopen)

    with pytest.raises(ValueError):
        web_search._open_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/x",
        "http://10.0.0.1/x",
        "http://169.254.1.1/x",
        "http://224.0.0.1/x",
        "http://[::1]/x",
    ],
)
def test_web_fetch_blocks_non_public_networks(url):
    with pytest.raises(ValueError, match="public"):
        web_search._validated_public_http_url(url)


def test_web_fetch_blocks_hostname_resolving_to_private_address(monkeypatch):
    monkeypatch.setattr(
        web_search.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("192.168.1.20", 443))],
    )

    with pytest.raises(ValueError, match="public"):
        web_search._validated_public_http_url("https://internal.example/path")


def test_fetch_redirect_is_revalidated(monkeypatch):
    monkeypatch.setattr(
        web_search,
        "_resolve_public_addresses",
        lambda host, port: (
            ["93.184.216.34"]
            if host == "example.com"
            else (_ for _ in ()).throw(ValueError("URL must resolve only to public addresses"))
        ),
    )
    handler = web_search.SafeRedirectHandler()

    with pytest.raises(ValueError, match="public"):
        handler.redirect_request(
            urllib.request.Request("https://example.com"),
            None,
            302,
            "Found",
            {},
            "http://127.0.0.1/admin",
        )


class _FakeResponse:
    def __init__(self, body: str, url: str = "https://example.com/page"):
        self._body = body.encode("utf-8")
        self._url = url
        self.headers = {"Content-Type": "text/html; charset=utf-8"}

    def read(self, size=-1):
        if size is None or size < 0:
            size = len(self._body)
        chunk, self._body = self._body[:size], self._body[size:]
        return chunk

    def close(self):
        return

    def geturl(self):
        return self._url


@pytest.fixture
def research_session(tmp_path):
    return ResearchSession(
        policy=ResearchPolicy.from_config({}, tmp_path),
        turn_id="turn-1",
        session_id="session-1",
        original_user_request="research current facts",
    )


def test_deep_research_fetch_retries_transient_errors_twice(monkeypatch, research_session):
    attempts = iter(
        [
            urllib.error.HTTPError("https://example.com", 503, "busy", {}, None),
            urllib.error.URLError("temporary"),
            _FakeResponse("<title>Example</title><p>ok</p>"),
        ]
    )
    monkeypatch.setattr(
        web_search, "_resolve_public_addresses", lambda host, port: ["93.184.216.34"]
    )

    def open_next(*args, **kwargs):
        value = next(attempts)
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr(web_search, "_open_url", open_next)
    sleeps = []
    monkeypatch.setattr(web_search.time, "sleep", sleeps.append)
    tool = web_search.WebFetchTool(".", research_session_provider=lambda: research_session)

    result = tool.execute(url="https://example.com/page")

    assert "[W1]" in result
    assert "ok" in result
    assert sleeps == [0.2, 0.4]
    assert research_session.budget.snapshot()["web_fetches"] == 1
    assert research_session.budget.snapshot()["research_tool_calls"] == 1


def test_deep_research_fetch_reuses_cache_and_upgrades_discovery(monkeypatch, research_session):
    research_session.ledger.add_web(
        query="example",
        url="https://example.com/page",
        title="Search result",
        excerpt="snippet",
        strength="discovery",
    )
    calls = []
    monkeypatch.setattr(
        web_search, "_resolve_public_addresses", lambda host, port: ["93.184.216.34"]
    )
    monkeypatch.setattr(
        web_search,
        "_open_url",
        lambda *args, **kwargs: (
            calls.append(args[0]) or _FakeResponse("<title>Example</title><p>full body</p>")
        ),
    )
    tool = web_search.WebFetchTool(".", research_session_provider=lambda: research_session)

    first = tool.execute(url="https://example.com/page")
    second = tool.execute(url="https://example.com/page")

    assert first == second
    assert calls == ["https://example.com/page"]
    assert research_session.ledger.get("W1").strength == "full"
    assert research_session.budget.snapshot()["web_fetches"] == 1
    assert research_session.budget.snapshot()["research_tool_calls"] == 1


def test_deep_research_fetch_cache_is_independent_of_max_chars(monkeypatch, research_session):
    calls = []
    body = "start-" + ("x" * 1800) + "-end"
    monkeypatch.setattr(
        web_search, "_resolve_public_addresses", lambda host, port: ["93.184.216.34"]
    )
    monkeypatch.setattr(
        web_search,
        "_open_url",
        lambda *args, **kwargs: (
            calls.append(args[0]) or _FakeResponse(f"<title>Example</title><p>{body}</p>")
        ),
    )
    tool = web_search.WebFetchTool(".", research_session_provider=lambda: research_session)

    short = tool.execute(url="https://example.com/page", max_chars=500)
    long = tool.execute(url="https://example.com/page", max_chars=30000)

    assert "... (truncated)" in short
    assert "-end" not in short
    assert "-end" in long
    assert len(long) > len(short)
    assert calls == ["https://example.com/page"]


def test_deep_research_search_falls_back_and_registers_discovery(monkeypatch, research_session):
    monkeypatch.setattr(web_search, "get_tavily_key", lambda: "secret")
    monkeypatch.setattr(
        web_search.WebSearchTool,
        "_tavily_results",
        lambda *args, **kwargs: (_ for _ in ()).throw(urllib.error.URLError("temporary")),
    )
    monkeypatch.setattr(
        web_search.WebSearchTool,
        "_ddg_results",
        lambda *args, **kwargs: [
            {
                "title": "Example",
                "snippet": "A current result",
                "url": "https://example.com/page",
            }
        ],
    )
    tool = web_search.WebSearchTool(".", research_session_provider=lambda: research_session)

    result = tool.execute(query="example")

    assert "[W1]" in result
    assert research_session.ledger.get("W1").strength == "discovery"
    assert research_session.budget.snapshot()["research_tool_calls"] == 1


def test_https_handler_connects_to_the_validated_ip_without_second_dns_lookup(monkeypatch):
    resolutions = []

    def resolve(host, port):
        resolutions.append((host, port))
        if len(resolutions) == 1:
            return ["93.184.216.34"]
        return ["127.0.0.1"]

    monkeypatch.setattr(web_search, "_resolve_public_addresses", resolve)
    target = web_search._validated_public_target("https://example.com/research")
    request = urllib.request.Request(target.url)  # noqa: S310 - validated HTTPS target
    request._atri_public_target = target
    handler = web_search.PinnedHTTPSHandler()
    captured = {}

    def fake_do_open(factory, req, **kwargs):
        captured["connection"] = factory(req.host, timeout=3, **kwargs)
        return object()

    monkeypatch.setattr(handler, "do_open", fake_do_open)

    handler.https_open(request)

    connection = captured["connection"]
    assert resolutions == [("example.com", 443)]
    assert connection.host == "example.com"
    assert connection._pinned_addresses == ("93.184.216.34",)


def test_cross_origin_redirect_strips_sensitive_credentials(monkeypatch):
    monkeypatch.setattr(
        web_search,
        "_resolve_public_addresses",
        lambda host, port: ["93.184.216.34"],
    )
    request = urllib.request.Request(
        "https://api.tavily.com/search",
        headers={
            "Authorization": "Bearer secret",
            "Cookie": "session=secret",
            "X-Api-Key": "secret",
            "Accept": "application/json",
        },
    )

    redirected = web_search.SafeRedirectHandler().redirect_request(
        request,
        None,
        302,
        "Found",
        {},
        "https://attacker.example/collect",
    )

    assert redirected.get_header("Authorization") is None
    assert redirected.get_header("Cookie") is None
    assert redirected.get_header("X-api-key") is None
    assert redirected.get_header("Accept") == "application/json"


def test_web_fetch_stops_at_hard_response_body_limit(monkeypatch, research_session):
    class _OversizedResponse:
        def __init__(self):
            self.remaining = web_search._MAX_FETCH_RESPONSE_BYTES + 1
            self.headers = {"Content-Type": "text/html; charset=utf-8"}
            self.closed = False

        def read(self, size=-1):
            size = min(max(1, size), self.remaining)
            self.remaining -= size
            return b"x" * size

        def close(self):
            self.closed = True

        def geturl(self):
            return "https://example.com/large"

    response = _OversizedResponse()
    monkeypatch.setattr(web_search, "_open_url", lambda *args, **kwargs: response)
    tool = web_search.WebFetchTool(".", research_session_provider=lambda: research_session)

    result = tool.execute(url="https://example.com/large", max_chars=30000)

    assert "response body exceeds" in result.lower()
    assert response.closed is True


def test_web_fetch_uses_remaining_synthesis_time_for_every_attempt(monkeypatch, research_session):
    remaining = iter([0.5, 0.2, 0.1])
    monkeypatch.setattr(
        research_session.budget,
        "seconds_until_synthesis",
        lambda: next(remaining, 0.1),
    )
    timeouts = []

    def open_url(*args, **kwargs):
        timeouts.append(kwargs["timeout"])
        if len(timeouts) == 1:
            raise urllib.error.URLError("retry")
        return _FakeResponse("<p>ok</p>")

    monkeypatch.setattr(web_search, "_open_url", open_url)
    monkeypatch.setattr(web_search.time, "sleep", lambda seconds: None)
    tool = web_search.WebFetchTool(".", research_session_provider=lambda: research_session)

    assert "ok" in tool.execute(url="https://example.com/page")
    assert timeouts == [0.5, 0.1]


def test_web_fetch_stops_reading_when_synthesis_deadline_arrives(monkeypatch, research_session):
    class _ChunkedResponse:
        def __init__(self):
            self.chunks = [b"<p>", b"late evidence", b"</p>", b""]
            self.headers = {"Content-Type": "text/html; charset=utf-8"}
            self.closed = False

        def read(self, size=-1):
            del size
            return self.chunks.pop(0)

        def close(self):
            self.closed = True

        def geturl(self):
            return "https://example.com/slow-drip"

    response = _ChunkedResponse()
    remaining = iter([0.05, 0.04, 0.0])
    monkeypatch.setattr(
        research_session.budget,
        "seconds_until_synthesis",
        lambda: next(remaining, 0.0),
    )
    monkeypatch.setattr(web_search, "_open_url", lambda *args, **kwargs: response)
    tool = web_search.WebFetchTool(".", research_session_provider=lambda: research_session)

    result = tool.execute(url="https://example.com/slow-drip")

    assert "synthesis" in result.lower()
    assert research_session.ledger.get("W1") is None
    assert response.closed is True


def test_limited_response_closes_blocking_read_at_synthesis_deadline():
    class _BlockingResponse:
        def __init__(self):
            self.closed = threading.Event()

        def read(self, size=-1):
            del size
            self.closed.wait(1)
            if self.closed.is_set():
                raise OSError("closed")
            return b""

        def close(self):
            self.closed.set()

    response = _BlockingResponse()

    with pytest.raises(TimeoutError, match="synthesis"):
        web_search._read_limited_response(
            response,
            1024,
            seconds_remaining=lambda: 0.05,
        )

    assert response.closed.is_set()


@pytest.mark.parametrize("backend", ["web_fetch", "ddg", "tavily"])
def test_deep_research_deadline_covers_slow_response_headers(
    monkeypatch, research_session, backend
):
    port, server = _start_slow_http_server(drip_headers=True)
    _route_web_connection_to_local_server(monkeypatch, port)
    deadline = time.monotonic() + 0.08
    monkeypatch.setattr(
        research_session.budget,
        "seconds_until_synthesis",
        lambda: max(0.0, deadline - time.monotonic()),
    )

    if backend == "web_fetch":
        tool = web_search.WebFetchTool(".", research_session_provider=lambda: research_session)

        def run_tool():
            return tool.execute(url=f"http://deadline.example:{port}/page")

    else:
        monkeypatch.setattr(
            web_search,
            "_DDG_HTML",
            f"http://deadline.example:{port}/ddg",
        )
        monkeypatch.setattr(
            web_search,
            "_TAVILY_SEARCH",
            f"http://deadline.example:{port}/tavily",
        )
        monkeypatch.setattr(
            web_search,
            "get_tavily_key",
            (lambda: "secret") if backend == "tavily" else (lambda: None),
        )
        tool = web_search.WebSearchTool(".", research_session_provider=lambda: research_session)

        def run_tool():
            return tool.execute(query="deadline")

    started = time.monotonic()
    result = run_tool()
    elapsed = time.monotonic() - started
    server.join(timeout=2)

    assert "synthesis" in result.lower()
    assert elapsed < 0.5


def test_open_url_stops_waiting_when_dns_resolution_exceeds_deadline(monkeypatch):
    resolver_finished = threading.Event()

    def slow_resolver(host, port):
        del host, port
        time.sleep(0.3)
        resolver_finished.set()
        return ["93.184.216.34"]

    monkeypatch.setattr(web_search, "_resolve_public_addresses", slow_resolver)
    deadline = time.monotonic() + 0.05

    started = time.monotonic()
    with pytest.raises(TimeoutError, match="synthesis"):
        web_search._open_url(
            "https://deadline.example/slow-dns",
            seconds_remaining=lambda: max(0.0, deadline - time.monotonic()),
        )
    elapsed = time.monotonic() - started

    assert elapsed < 0.2
    assert resolver_finished.wait(1)


def test_deep_research_deadline_interrupts_real_slow_response_body(monkeypatch, research_session):
    port, server = _start_slow_http_server(drip_headers=False)
    _route_web_connection_to_local_server(monkeypatch, port)
    deadline = time.monotonic() + 0.08
    monkeypatch.setattr(
        research_session.budget,
        "seconds_until_synthesis",
        lambda: max(0.0, deadline - time.monotonic()),
    )
    tool = web_search.WebFetchTool(".", research_session_provider=lambda: research_session)

    started = time.monotonic()
    result = tool.execute(url=f"http://deadline.example:{port}/body")
    elapsed = time.monotonic() - started
    server.join(timeout=2)

    assert "synthesis" in result.lower()
    assert elapsed < 0.5


def test_web_fetch_parallel_call_does_not_clear_prior_call_cancellation(monkeypatch):
    first_started = threading.Event()
    release_first = threading.Event()

    class _Response:
        def __init__(self, label, *, blocks=False):
            self.label = label
            self.blocks = blocks
            self.read_count = 0
            self.headers = {"Content-Type": "text/html; charset=utf-8"}

        def read(self, size=-1):
            del size
            if self.read_count:
                return b""
            self.read_count += 1
            if self.blocks:
                first_started.set()
                release_first.wait(2)
            return f"<p>{self.label}</p>".encode()

        def close(self):
            return

        def geturl(self):
            return f"https://example.com/{self.label}"

    responses = iter([_Response("first", blocks=True), _Response("second")])
    monkeypatch.setattr(web_search, "_open_url", lambda *args, **kwargs: next(responses))
    tool = web_search.WebFetchTool(".")
    first_result = []
    first_worker = threading.Thread(
        target=lambda: first_result.append(tool.execute(url="https://example.com/first"))
    )

    first_worker.start()
    assert first_started.wait(2)
    tool.cancel()
    second_result = tool.execute(url="https://example.com/second")
    release_first.set()
    first_worker.join(timeout=2)

    assert first_result and "cancelled" in first_result[0].lower()
    assert "second" in second_result.lower()


def test_web_fetch_cancel_closes_active_response(monkeypatch, research_session):
    class _BlockingResponse:
        def __init__(self):
            self.headers = {"Content-Type": "text/html; charset=utf-8"}
            self.started = threading.Event()
            self.closed = threading.Event()

        def read(self, size=-1):
            self.started.set()
            self.closed.wait(2)
            raise OSError("closed")

        def close(self):
            self.closed.set()

        def geturl(self):
            return "https://example.com/slow"

    response = _BlockingResponse()
    monkeypatch.setattr(web_search, "_open_url", lambda *args, **kwargs: response)
    tool = web_search.WebFetchTool(".", research_session_provider=lambda: research_session)
    outcome = []
    worker = threading.Thread(
        target=lambda: outcome.append(tool.execute(url="https://example.com/slow"))
    )

    worker.start()
    assert response.started.wait(2)
    tool.cancel()
    worker.join(timeout=2)

    assert response.closed.is_set()
    assert outcome and "cancelled" in outcome[0].lower()
