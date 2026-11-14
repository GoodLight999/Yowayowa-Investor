"""Acceptance tests for the isolated S-07 forward runner."""

from unittest.mock import patch

from yowayowa.hl_exec.s07_forward.runner import decide_and_maybe_order


def test_default_never_enters_order_path():
    with patch("yowayowa.hl_exec.s07_forward.runner.order_path") as order:
        assert decide_and_maybe_order({"signal": "LONG"}) == "LONG"
        order.assert_not_called()
