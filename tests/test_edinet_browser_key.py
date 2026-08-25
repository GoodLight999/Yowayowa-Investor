from starlette.requests import Request

from yowayowa.api.edinet_routes import _request_client


def _request(headers: list[tuple[bytes, bytes]]) -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers})


def test_request_scoped_edinet_key_is_used_without_server_persistence(monkeypatch) -> None:
    monkeypatch.delenv("YOWAYOWA_EDINET_API_KEY", raising=False)
    request = _request([(b"x-yowayowa-edinet-key", b"browser-only-key")])

    client = _request_client(request)

    assert client.settings.edinet_api_key == "browser-only-key"
    assert client._api_key() == "browser-only-key"
