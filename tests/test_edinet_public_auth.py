from starlette.requests import Request

from yowayowa.api.deps import _anonymous_public_research_allowed


def _request(method: str, path: str) -> Request:
    return Request({"type": "http", "method": method, "path": path, "headers": []})


def test_edinet_history_is_public_read_only_but_index_sync_is_not() -> None:
    assert _anonymous_public_research_allowed(_request("GET", "/v1/filings/edinet/index/history"))
    assert not _anonymous_public_research_allowed(_request("POST", "/v1/filings/edinet/index/sync"))
