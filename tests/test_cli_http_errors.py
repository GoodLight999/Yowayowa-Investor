"""CLI boundary: httpx failures exit 1 with one fixed stderr line.

Covers the dead-end audit 2026-10-23 R1 display symptom (``valuation 29010``
printed an internal rich traceback): transport-level API failures must surface
as a single operator-safe sentence on stderr with exit code 1. The conversion
lives only at the ``yowayowa.cli_shim.main`` boundary; successes, exits that
commands handle themselves, and non-HTTP errors must stay exactly as before.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast

import httpx
import pytest

from yowayowa import cli_shim

AUTH_LINE = "Yowayowa API request failed: HTTP 401 (authentication or permission required)"
FORBIDDEN_LINE = "Yowayowa API request failed: HTTP 403 (authentication or permission required)"
NOT_FOUND_LINE = "Yowayowa API request failed: HTTP 404 (no such target)"
INPUT_LINE = "Yowayowa API request failed: HTTP 422 (the API rejected the input)"
RATE_LINE = "Yowayowa API request failed: HTTP 429 (API rate limit reached)"
OTHER_STATUS_LINE = "Yowayowa API request failed: HTTP 418 (request failed)"
TIMEOUT_LINE = "Yowayowa API request failed: the API did not respond in time (timeout)"
CONNECT_LINE = "Yowayowa API request failed: could not connect to the API"
REQUEST_LINE = "Yowayowa API request failed: request could not be completed"

# Every needle that must never reach the operator: URL userinfo/query, the
# fake token from the fixture headers, response body HTML/detail, terminal
# control characters and Rich markup. Only fixtures are used here.
LEAK_NEEDLES = (
    "user:secret",
    "127.0.0.1:9",
    "?q=leak",
    "Bearer fake-token",
    "<html>",
    "server-detail",
    "\n",
    "\x1b",
    "[bold]",
    "[red]",
)

FIXTURE_VALUATION_PAYLOAD = {
    "price": 2786.5,
    "market_cap": 38420000000000,
    "metrics": {"pe": 10.4, "pbr": 1.0},
    "annual_period_end": "2026-03-31",
}


def _fixture_request() -> httpx.Request:
    return httpx.Request(
        "GET",
        "http://user:secret@127.0.0.1:9/v1/valuation/29010?q=leak",
        headers={"Authorization": "Bearer fake-token-abc123"},
    )


def _nasty_body() -> str:
    return (
        "<html>server-detail \x1b[31mboom\x1b[0m [bold]leak[/bold]\n"
        "http://user:secret@127.0.0.1:9/v1/valuation/29010?q=leak "
        "Bearer fake-token-abc123</html>"
    )


def _status_error(status: int) -> httpx.HTTPStatusError:
    request = _fixture_request()
    response = httpx.Response(
        status,
        request=request,
        headers={"Content-Type": "text/html", "X-Trace": "Bearer fake-token-abc123"},
        text=_nasty_body(),
    )
    return httpx.HTTPStatusError(
        f"Client error '{status} Not Found' for '{request.url}': {_nasty_body()}",
        request=request,
        response=response,
    )


def _request_error(exc_cls: type[httpx.RequestError]) -> httpx.RequestError:
    return exc_cls(f"boom-{exc_cls.__name__} {_nasty_body()}", request=_fixture_request())


def _raising_app(exc: BaseException) -> Callable[[], None]:
    def app() -> None:
        raise exc

    return app


class _CountingServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler]) -> None:
        super().__init__(address, handler)
        self.requests_served = 0


class _FixtureAPIHandler(BaseHTTPRequestHandler):
    """Deterministic fixture API: nasty 404 and a fixed 200 valuation payload."""

    def do_GET(self) -> None:
        # Count on receipt, before the response is written: the client can
        # observe the finished response before the handler thread finishes,
        # so a post-write increment would race with the assertions.
        server = cast("_CountingServer", self.server)
        server.requests_served += 1
        if self.path.startswith("/v1/valuation/29010"):
            body = _nasty_body().encode()
            self.send_response(404)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/v1/valuation/7203.T"):
            body = json.dumps(FIXTURE_VALUATION_PAYLOAD).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture()
def fixture_api() -> Iterator[_CountingServer]:
    server = _CountingServer(("127.0.0.1", 0), _FixtureAPIHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize(
    ("status", "expected_line"),
    [
        (401, AUTH_LINE),
        (403, FORBIDDEN_LINE),
        (404, NOT_FOUND_LINE),
        (422, INPUT_LINE),
        (429, RATE_LINE),
        (500, "Yowayowa API request failed: HTTP 500 (API-side failure)"),
        (502, "Yowayowa API request failed: HTTP 502 (API-side failure)"),
        (503, "Yowayowa API request failed: HTTP 503 (API-side failure)"),
        (418, OTHER_STATUS_LINE),
    ],
)
def test_main_converts_http_status_errors_to_one_fixed_exit(
    status: int, expected_line: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli_shim, "_load_app", lambda: _raising_app(_status_error(status)))
    with pytest.raises(SystemExit) as excinfo:
        cli_shim.main()
    assert excinfo.value.code == expected_line


@pytest.mark.parametrize(
    ("exc_cls", "expected_line"),
    [
        (httpx.ConnectTimeout, TIMEOUT_LINE),
        (httpx.ReadTimeout, TIMEOUT_LINE),
        (httpx.ConnectError, CONNECT_LINE),
        (httpx.ReadError, REQUEST_LINE),
    ],
)
def test_main_converts_transport_errors_to_one_fixed_exit(
    exc_cls: type[httpx.RequestError], expected_line: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli_shim, "_load_app", lambda: _raising_app(_request_error(exc_cls)))
    with pytest.raises(SystemExit) as excinfo:
        cli_shim.main()
    assert excinfo.value.code == expected_line


@pytest.mark.parametrize(
    ("status", "needle"),
    [
        (401, "authentication"),
        (403, "authentication"),
        (404, "no such target"),
        (422, "rejected the input"),
        (429, "rate limit"),
        (500, "API-side failure"),
        (418, "request failed"),
    ],
)
def test_fixed_lines_carry_the_status_and_never_leak_fixture_secrets(
    status: int, needle: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli_shim, "_load_app", lambda: _raising_app(_status_error(status)))
    with pytest.raises(SystemExit) as excinfo:
        cli_shim.main()
    line = str(excinfo.value.code)
    assert needle in line
    for leak in LEAK_NEEDLES:
        assert leak not in line


def test_main_does_not_hide_non_http_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_shim, "_load_app", lambda: _raising_app(ValueError("boom-value")))
    with pytest.raises(ValueError, match="boom-value"):
        cli_shim.main()


def test_main_does_not_swallow_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_shim, "_load_app", lambda: _raising_app(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        cli_shim.main()


def test_main_keeps_click_and_typer_exits_as_is(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_shim, "_load_app", lambda: _raising_app(SystemExit(2)))
    with pytest.raises(SystemExit) as excinfo:
        cli_shim.main()
    assert excinfo.value.code == 2


def test_real_app_404_through_main_boundary_is_one_line_without_retry(
    fixture_api: _CountingServer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from yowayowa.cli import app as core_app

    monkeypatch.setattr(cli_shim, "_load_app", lambda: core_app)
    base_url = f"http://127.0.0.1:{fixture_api.server_address[1]}"
    monkeypatch.setattr(sys, "argv", ["yowayowa", "valuation", "29010", "--base-url", base_url])
    with pytest.raises(SystemExit) as excinfo:
        cli_shim.main()
    assert excinfo.value.code == NOT_FOUND_LINE
    # Nothing reached stdout/stderr while the command ran; the fixed line is
    # printed by the interpreter from the SystemExit argument (fd-level split
    # is proven by the subprocess tests below).
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    for leak in LEAK_NEEDLES:
        assert leak not in str(excinfo.value.code)
    assert fixture_api.requests_served == 1  # single request, no retry/resend


def _run_entry(args: list[str], base_url: str) -> subprocess.CompletedProcess[bytes]:
    entry = Path(sys.executable).with_name("yowayowa")
    if not entry.is_file():
        pytest.skip("yowayowa console script is not installed in this venv")
    src = Path(__file__).resolve().parents[1] / "src"
    env = os.environ.copy()
    env["PYTHONPATH"] = (
        os.pathsep.join([str(src), env["PYTHONPATH"]]) if env.get("PYTHONPATH") else str(src)
    )
    return subprocess.run(
        [str(entry), *args, "--base-url", base_url],
        capture_output=True,
        timeout=120,
        env=env,
        check=False,
    )


def test_formal_entrypoint_404_is_one_line_exit_1_stdout_empty(
    fixture_api: _CountingServer,
) -> None:
    base_url = f"http://127.0.0.1:{fixture_api.server_address[1]}"
    result = _run_entry(["valuation", "29010"], base_url)
    assert result.returncode == 1
    assert result.stdout == b""
    assert result.stderr == NOT_FOUND_LINE.encode() + b"\n"
    stderr_line = result.stderr.decode()
    assert stderr_line.count("\n") == 1
    for leak in LEAK_NEEDLES:
        if leak == "\n":
            continue  # the single trailing newline is asserted above
        assert leak not in stderr_line
    assert fixture_api.requests_served == 1  # no retry/resend


def test_formal_entrypoint_success_output_is_unchanged(
    fixture_api: _CountingServer,
) -> None:
    base_url = f"http://127.0.0.1:{fixture_api.server_address[1]}"
    result = _run_entry(["valuation", "7203.T"], base_url)
    assert result.returncode == 0
    assert b"2786.5" in result.stdout
    assert b"Annual basis: 2026-03-31" in result.stdout
    assert result.stderr == b""
    assert fixture_api.requests_served == 1
