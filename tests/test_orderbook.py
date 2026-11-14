from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from yowayowa.api.app import app
from yowayowa.cli_entry import app as cli_app
from yowayowa.domain import LicenseClass, Provenance
from yowayowa.orderbook_models import (
    OrderbookLevel,
    OrderbookSnapshot,
)
from yowayowa.services.orderbook_service import (
    CORRUPT_READ_LEDGER,
    SYNTHETIC_PROVIDER,
    OrderbookService,
    compute_depth_ladder,
    compute_orderbook_metrics,
    simulate_execution_impact,
)

runner = CliRunner()
client = TestClient(app)


def test_compute_orderbook_metrics_spread_and_imbalance() -> None:
    bids = [
        OrderbookLevel(price=100.0, size=50.0, order_count=3),
        OrderbookLevel(price=99.0, size=150.0, order_count=5),
    ]
    asks = [
        OrderbookLevel(price=101.0, size=50.0, order_count=2),
        OrderbookLevel(price=102.0, size=50.0, order_count=4),
    ]
    metrics = compute_orderbook_metrics(bids, asks)

    assert metrics.best_bid == 100.0
    assert metrics.best_ask == 101.0
    assert metrics.mid_price == 100.5
    assert metrics.spread == 1.0
    assert metrics.spread_bps == round((1.0 / 100.5) * 10000.0, 2)
    assert metrics.bid_depth_total == 200.0
    assert metrics.ask_depth_total == 100.0
    assert metrics.total_depth == 300.0
    # (200 - 100) / 300 = +0.3333
    assert metrics.order_flow_imbalance == 0.3333
    # micro price = (100 * 50 + 101 * 50) / 100 = 100.5
    assert metrics.micro_price == 100.5


def test_compute_depth_ladder() -> None:
    service = OrderbookService()
    snapshot = service.get_snapshot("7203")
    ladder = compute_depth_ladder(snapshot, max_levels=5)

    assert ladder.symbol == "7203"
    assert len(ladder.bids_cumulative) <= 5
    assert len(ladder.asks_cumulative) <= 5
    # Verify cumulative monotonicity
    for i in range(1, len(ladder.bids_cumulative)):
        prev_sz = ladder.bids_cumulative[i - 1].cumulative_size
        assert ladder.bids_cumulative[i].cumulative_size > prev_sz
    for i in range(1, len(ladder.asks_cumulative)):
        prev_sz = ladder.asks_cumulative[i - 1].cumulative_size
        assert ladder.asks_cumulative[i].cumulative_size > prev_sz


def test_simulate_execution_impact_buy_and_sell() -> None:
    bids = [
        OrderbookLevel(price=100.0, size=10.0),
        OrderbookLevel(price=99.0, size=20.0),
        OrderbookLevel(price=98.0, size=50.0),
    ]
    asks = [
        OrderbookLevel(price=101.0, size=10.0),
        OrderbookLevel(price=102.0, size=20.0),
        OrderbookLevel(price=103.0, size=50.0),
    ]
    metrics = compute_orderbook_metrics(bids, asks)
    snapshot = OrderbookSnapshot(symbol="TEST", bids=bids, asks=asks, metrics=metrics)

    res_buy = simulate_execution_impact(snapshot, side="buy", quantity=15.0)
    assert res_buy.fully_filled is True
    assert res_buy.fillable_quantity == 15.0
    assert res_buy.levels_swept == 2
    assert res_buy.best_quote_price == 101.0
    assert res_buy.average_price == 101.333333
    assert res_buy.slippage == 0.333333

    res_sell = simulate_execution_impact(snapshot, side="sell", quantity=25.0)
    assert res_sell.fully_filled is True
    assert res_sell.fillable_quantity == 25.0
    assert res_sell.levels_swept == 2
    assert res_sell.best_quote_price == 100.0
    assert res_sell.average_price == 99.4
    assert res_sell.slippage == 0.6


def test_simulate_execution_impact_insufficient_liquidity() -> None:
    bids = [OrderbookLevel(price=100.0, size=10.0)]
    asks = [OrderbookLevel(price=101.0, size=5.0)]
    snapshot = OrderbookSnapshot(
        symbol="TINY",
        bids=bids,
        asks=asks,
        metrics=compute_orderbook_metrics(bids, asks),
    )
    res = simulate_execution_impact(snapshot, side="buy", quantity=20.0)
    assert res.fully_filled is False
    assert res.fillable_quantity == 5.0
    assert res.warning is not None
    assert "Liquidity exhausted" in res.warning


def test_orderbook_api_endpoints() -> None:
    resp = client.get("/v1/orderbook/7203")
    assert resp.status_code == 200
    data = resp.json()
    assert data["symbol"] == "7203"
    assert len(data["bids"]) > 0
    assert len(data["asks"]) > 0
    assert data["metrics"]["best_bid"] is not None

    resp_depth = client.get("/v1/orderbook/7203/depth?max_levels=5")
    assert resp_depth.status_code == 200
    depth_data = resp_depth.json()
    assert len(depth_data["bids_cumulative"]) <= 5

    resp_metrics = client.get("/v1/orderbook/7203/metrics")
    assert resp_metrics.status_code == 200
    metrics_data = resp_metrics.json()
    assert "spread" in metrics_data
    assert "order_flow_imbalance" in metrics_data

    resp_impact = client.post(
        "/v1/orderbook/7203/impact",
        json={"side": "buy", "quantity": 500.0},
    )
    assert resp_impact.status_code == 200
    impact_data = resp_impact.json()
    assert impact_data["fully_filled"] is True
    assert impact_data["average_price"] is not None


def test_orderbook_cli_commands() -> None:
    res_show = runner.invoke(cli_app, ["orderbook", "show", "7203", "--depth", "5"])
    assert res_show.exit_code == 0
    assert "Orderbook Market Depth" in res_show.stdout

    res_metrics = runner.invoke(cli_app, ["orderbook", "metrics", "7203"])
    assert res_metrics.exit_code == 0
    assert "Microstructure Metrics" in res_metrics.stdout

    res_impact = runner.invoke(
        cli_app,
        ["orderbook", "impact", "7203", "--side", "buy", "--qty", "200"],
    )
    assert res_impact.exit_code == 0
    assert "Execution Impact Estimation" in res_impact.stdout


def test_ai_agent_orderbook_tools() -> None:
    from unittest.mock import Mock

    from yowayowa.config import Settings
    from yowayowa.services.ai_agent import InvestmentResearchAgent

    agent = InvestmentResearchAgent(Settings(database_url="sqlite:///:memory:"), Mock())
    assert "get_orderbook" in agent.tools
    assert "estimate_orderbook_impact" in agent.tools

    tool_ob = agent.tools["get_orderbook"]
    ob_res = tool_ob.handler({"symbol": "7203", "depth": 5})
    assert ob_res["symbol"] == "7203"
    assert "best_bid" in ob_res
    assert "order_flow_imbalance" in ob_res

    tool_impact = agent.tools["estimate_orderbook_impact"]
    impact_res = tool_impact.handler({"symbol": "7203", "side": "buy", "quantity": 100.0})
    assert impact_res["fully_filled"] is True
    assert "average_price" in impact_res


CORRUPT_SNAPSHOT_BYTES = b'{"symbol": "7203", "bids": [{"price": 2850.0, "size": 1000.0}], "as'


def _write_corrupt_snapshot(base: Path) -> tuple[Path, str]:
    path = base / "7203" / "latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(CORRUPT_SNAPSHOT_BYTES)
    return path, hashlib.sha256(CORRUPT_SNAPSHOT_BYTES).hexdigest()


def test_corrupt_snapshot_is_preserved_and_reported(tmp_path: Path) -> None:
    path, original_sha = _write_corrupt_snapshot(tmp_path)

    snapshot = OrderbookService(tmp_path).get_snapshot("7203")

    # (a) the persisted bytes are never overwritten with fabricated data
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original_sha
    # (b) the returned board is advertised as synthetic, not as an exchange feed
    assert snapshot.provenance.provider == SYNTHETIC_PROVIDER
    assert snapshot.provenance.license_class is LicenseClass.SYNTHETIC
    assert any(note.startswith("degraded:") for note in snapshot.provenance.notes)
    # (c) the corruption survives as a durable record keyed by the damaged bytes
    ledger = tmp_path / CORRUPT_READ_LEDGER
    records = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    assert records[0]["symbol"] == "7203"
    assert records[0]["sha256"] == original_sha
    assert records[0]["error_type"] == "JSONDecodeError"


def test_repeated_corrupt_reads_neither_grow_ledger_nor_touch_file(tmp_path: Path) -> None:
    path, original_sha = _write_corrupt_snapshot(tmp_path)
    service = OrderbookService(tmp_path)

    for _ in range(3):
        service.get_snapshot("7203")

    assert hashlib.sha256(path.read_bytes()).hexdigest() == original_sha
    ledger = tmp_path / CORRUPT_READ_LEDGER
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 1


def test_schema_mismatch_snapshot_is_not_trusted(tmp_path: Path) -> None:
    path = tmp_path / "7203" / "latest.json"
    path.parent.mkdir(parents=True)
    # Valid JSON, invalid orderbook: a negative price fails OrderbookLevel validation.
    path.write_bytes(b'{"symbol": "7203", "bids": [{"price": -1.0, "size": 10.0}], "asks": []}')
    original_sha = hashlib.sha256(path.read_bytes()).hexdigest()

    snapshot = OrderbookService(tmp_path).get_snapshot("7203")

    assert snapshot.provenance.license_class is LicenseClass.SYNTHETIC
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original_sha
    assert (tmp_path / CORRUPT_READ_LEDGER).exists()


def test_valid_snapshot_path_is_unchanged(tmp_path: Path) -> None:
    service = OrderbookService(tmp_path)
    bids = [OrderbookLevel(price=100.0, size=10.0)]
    asks = [OrderbookLevel(price=101.0, size=10.0)]
    recorded = service.record_snapshot(
        OrderbookSnapshot(
            symbol="7203",
            bids=bids,
            asks=asks,
            metrics=compute_orderbook_metrics(bids, asks),
            provenance=Provenance(
                provider="jpx-tse-feed",
                source="captured feed",
                license_class=LicenseClass.OFFICIAL_PUBLIC,
                retrieved_at=datetime.now(UTC),
            ),
        )
    )

    loaded = service.get_snapshot("7203")

    assert loaded.provenance.provider == "jpx-tse-feed"
    assert loaded.provenance.license_class is LicenseClass.OFFICIAL_PUBLIC
    assert loaded.bids == recorded.bids
    assert loaded.as_of == recorded.as_of
    assert not (tmp_path / CORRUPT_READ_LEDGER).exists()


def test_missing_snapshot_bootstraps_synthetic_board(tmp_path: Path) -> None:
    service = OrderbookService(tmp_path)

    snapshot = service.get_snapshot("BTC/USD")

    assert snapshot.provenance.provider == SYNTHETIC_PROVIDER
    assert snapshot.provenance.license_class is LicenseClass.SYNTHETIC
    persisted = json.loads((tmp_path / "BTC_USD" / "latest.json").read_text(encoding="utf-8"))
    assert persisted["provenance"]["provider"] == SYNTHETIC_PROVIDER
    assert persisted["provenance"]["license_class"] == "synthetic"
    assert not (tmp_path / CORRUPT_READ_LEDGER).exists()
