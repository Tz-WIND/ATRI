"""Web search and fetch tools for the agent.

WebSearchTool — search the web via Tavily API (when key is configured)
                or DuckDuckGo HTML (free fallback, no API key).
WebFetchTool  — fetch and extract text content from a URL.
"""

import html as _html
import json as _json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request

from .base import Tool

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

# Tags we strip when extracting text from HTML
_SKIP_TAGS = {"script", "style", "noscript", "iframe", "svg", "head", "meta", "link"}

# ---------------------------------------------------------------------------
# shared SSL context
# ---------------------------------------------------------------------------

_SSL_CONTEXT = ssl.create_default_context()


def _open_url(
    url: str,
    data: bytes | None = None,
    timeout: int = _TIMEOUT,
    extra_headers: dict | None = None,
):
    """Open a URL with standard SSL verification."""
    headers = {"User-Agent": _USER_AGENT}
    if data:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if extra_headers:
        headers.update(extra_headers)

    req = urllib.request.Request(url, data=data, headers=headers)
    return urllib.request.urlopen(req, timeout=timeout, context=_SSL_CONTEXT)


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
    parameters = {
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

    def execute(self, query: str, max_results: int = 5, **kwargs) -> str:
        max_results = min(max(1, max_results), 10)

        key = get_tavily_key()
        if key:
            return self._search_tavily(query, max_results, key)
        return self._search_ddg(query, max_results)

    # ------------------------------------------------------------------
    # Tavily backend
    # ------------------------------------------------------------------

    def _search_tavily(self, query: str, max_results: int, api_key: str) -> str:
        try:
            payload = _json.dumps({
                "query": query,
                "max_results": max_results,
                "search_depth": "basic",
                "include_answer": False,
                "include_raw_content": False,
                "include_images": False,
            }).encode("utf-8")

            resp = _open_url(
                _TAVILY_SEARCH,
                data=payload,
                extra_headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
            )
            body = _json.loads(resp.read().decode("utf-8", errors="ignore"))
            results = body.get("results", [])

            if not results:
                answer = body.get("answer", "")
                return f"Web search — {query}\n\n{answer}" if answer else f"No results found for: {query}"

            lines = [f"Web search — {query}\n"]
            for i, r in enumerate(results, 1):
                lines.append(f"{i}. {r.get('title', 'Untitled')}")
                lines.append(f"   {r.get('content', r.get('snippet', '(no snippet)'))}")
                lines.append(f"   URL: {r.get('url', '')}")
                lines.append("")
            return "\n".join(lines).strip()

        except Exception as e:
            return f"Tavily search failed: {e}"

    # ------------------------------------------------------------------
    # DuckDuckGo fallback
    # ------------------------------------------------------------------

    def _search_ddg(self, query: str, max_results: int) -> str:
        try:
            data = urllib.parse.urlencode({"q": query}).encode("utf-8")
            resp = _open_url(_DDG_HTML, data=data)
            raw = resp.read().decode("utf-8", errors="ignore")
            results = self._parse_ddg(raw, max_results)
        except Exception as e:
            return f"Web search failed: {e}"

        if not results:
            return f"No results found for: {query}"

        lines = [f"Web search — {query}\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r['title']}")
            lines.append(f"   {r['snippet']}")
            lines.append(f"   URL: {r['url']}")
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
                snippet = _html.unescape(
                    re.sub(r"<[^>]*>", "", snip_m.group(1))
                ).strip()
                snippet = snippet if snippet else "(no snippet)"

            results.append({"title": title, "url": url, "snippet": snippet})

            if len(results) >= max_results:
                break

        return results


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
    parameters = {
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

    def execute(self, url: str, max_chars: int = 8000, **kwargs) -> str:
        max_chars = min(max(500, max_chars), 30000)

        try:
            resp = _open_url(url)
            content_type = resp.headers.get("Content-Type", "")
            raw = resp.read()

            charset = "utf-8"
            if "charset=" in content_type:
                charset = content_type.split("charset=")[-1].split(";")[0].strip()

            try:
                html_text = raw.decode(charset, errors="ignore")
            except (UnicodeDecodeError, LookupError):
                html_text = raw.decode("utf-8", errors="ignore")

            text = self._extract_text(html_text)
            if len(text) > max_chars:
                text = text[: max_chars - 200] + "\n\n... (truncated)"

            if not text.strip():
                return f"Could not extract readable text from {url}"

            return f"Content from {url}:\n\n{text.strip()}"

        except urllib.error.HTTPError as e:
            return f"HTTP error fetching {url}: {e.code} {e.reason}"
        except urllib.error.URLError as e:
            return f"Connection error fetching {url}: {e.reason}"
        except Exception as e:
            return f"Fetch failed for {url}: {e}"

    # ------------------------------------------------------------------
    # Text extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_text(html_text: str) -> str:
        """Strip HTML tags and return plain text, keeping basic structure."""
        for tag in _SKIP_TAGS:
            html_text = re.sub(
                rf"<{tag}[^>]*>.*?</{tag}>", "", html_text, flags=re.DOTALL | re.I
            )

        html_text = re.sub(r"\s+", " ", html_text)

        for tag in ("p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "br"):
            html_text = re.sub(
                rf"<\s*/?\s*{tag}[^>]*>", "\n", html_text, flags=re.I
            )

        text = re.sub(r"<[^>]*>", "", html_text)
        text = _html.unescape(text)
        text = re.sub(r"\n\s*\n", "\n\n", text)
        text = re.sub(r"[ \t]+", " ", text)

        return text.strip()
