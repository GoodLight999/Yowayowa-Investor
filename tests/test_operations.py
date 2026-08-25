from yowayowa.domain import OperationKind
from yowayowa.services.operations import deterministic_plan


def test_watchlist_japanese_command() -> None:
    plan = deterministic_plan("RKLB、ASTS、SOFI、HOODをウォッチリストに入れて")
    assert plan is not None
    assert plan.operations[0].kind == OperationKind.WATCHLIST_ADD
    assert plan.operations[0].arguments["symbols"] == ["RKLB", "ASTS", "SOFI", "HOOD"]


def test_chart_indicator_command() -> None:
    plan = deterministic_plan("200日移動平均とRSIを追加して")
    assert plan is not None
    indicators = plan.operations[0].arguments["indicators"]
    assert "sma200" in indicators
    assert "rsi14" in indicators
