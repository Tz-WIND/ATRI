import pytest

from core.tools import web_search


def test_open_url_uses_default_tls_verification(monkeypatch):
    response = object()
    captured = {}

    def fake_urlopen(request, **kwargs):
        captured["url"] = request.full_url
        captured["kwargs"] = kwargs
        return response

    monkeypatch.setattr(web_search.urllib.request, "urlopen", fake_urlopen)

    result = web_search._open_url("https://example.com/search?q=test", timeout=3)

    assert result is response
    assert captured == {
        "url": "https://example.com/search?q=test",
        "kwargs": {"timeout": 3},
    }


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
