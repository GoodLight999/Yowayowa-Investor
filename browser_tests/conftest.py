from __future__ import annotations

from collections.abc import Mapping

import pytest


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args: Mapping[str, object]) -> dict[str, object]:
    args = dict(browser_context_args)
    headers = dict(args.get("extra_http_headers", {}))
    headers["Accept-Language"] = "en-US,en;q=0.9"
    args["extra_http_headers"] = headers
    return args
