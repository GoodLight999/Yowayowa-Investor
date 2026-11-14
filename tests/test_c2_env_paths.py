"""C2 のパス解決: 既定は本番 `/root/...` のまま・環境変数で差し替え・読めなくても落ちない。

2026-11-14 の ``verify`` (SHA 9537788) は 27 件が PermissionError で落ちた。
GitHub ランナーでは ``/root`` を stat することすら許されず、Python 3.13 の
``Path.exists()`` / ``Path.is_dir()`` は PermissionError を握りつぶさないため、
**既定値の存在確認だけ**でテストが落ちていた。ここでは

1. 既定値が現行の本番パスから動いていないこと (cron / systemd の前提を壊さない)、
2. ``YOWAYOWA_DATA_ROOT`` / ``YOWAYOWA_HERMES_ROOT`` / ``YOWAYOWA_SECRETS_ROOT``
   で根だけ差し替えられること、
3. ``/root`` 配下が一切 stat/open できないホストでも、探索側が「無い」として
   既定へフォールバックし例外を投げないこと

を固定する。3 は CI の失敗条件そのものの再現 (``/root`` 参照を EACCES にする)。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from scripts.paper_c2 import c2_features, c2_gap_watch
from scripts.paper_c2 import paths as c2_paths
from scripts.paper_c2.alpaca_paper_client import AlpacaPaperClient

LIVE_DATA_ROOT = Path("/root/current-work/yowayowa-investor/data")
LIVE_HERMES_ROOT = Path("/root/.hermes")
LIVE_SECRETS_ROOT = Path("/root/.secrets")


@pytest.fixture(autouse=True)
def _clear_root_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("YOWAYOWA_DATA_ROOT", "YOWAYOWA_HERMES_ROOT", "YOWAYOWA_SECRETS_ROOT"):
        monkeypatch.delenv(name, raising=False)


def test_defaults_stay_on_the_live_vps_paths() -> None:
    """既定値は本番の現行パスのまま (ここを変えると VPS の cron が壊れる)。"""
    assert c2_paths.data_root() == LIVE_DATA_ROOT
    assert c2_paths.hermes_root() == LIVE_HERMES_ROOT
    assert c2_paths.secrets_root() == LIVE_SECRETS_ROOT
    assert c2_features._default_dgs1_paths() == [
        LIVE_DATA_ROOT / "macro-observations" / "fred.jsonl",
        LIVE_DATA_ROOT / "macro-observations" / "treasury.jsonl",
        LIVE_DATA_ROOT / "gates" / "dgs1.jsonl",
    ]
    assert c2_gap_watch.default_output_dir() == (
        LIVE_HERMES_ROOT / "cron" / "output" / "9f57d9154cfb"
    )
    assert c2_gap_watch.default_output_dir() == c2_gap_watch.DEFAULT_OUTPUT_DIR


def test_roots_can_be_redirected_by_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("YOWAYOWA_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("YOWAYOWA_HERMES_ROOT", str(tmp_path / "hermes"))
    monkeypatch.setenv("YOWAYOWA_SECRETS_ROOT", str(tmp_path / "secrets"))

    assert c2_paths.data_root() == tmp_path / "data"
    assert c2_paths.hermes_root() == tmp_path / "hermes"
    assert c2_paths.secrets_root() == tmp_path / "secrets"
    assert c2_features._default_dgs1_paths()[0] == (
        tmp_path / "data" / "macro-observations" / "fred.jsonl"
    )
    assert c2_gap_watch.default_output_dir() == (
        tmp_path / "hermes" / "cron" / "output" / "9f57d9154cfb"
    )


def test_unreadable_root_falls_back_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/root`` を stat も open もできないホスト (CI ランナー) で落ちないこと。"""
    real_stat = Path.stat
    real_open = Path.open

    def _deny_stat(self, *args, **kwargs):
        if str(self).startswith("/root"):
            raise PermissionError(13, "Permission denied", str(self))
        return real_stat(self, *args, **kwargs)

    def _deny_open(self, *args, **kwargs):
        if str(self).startswith("/root"):
            raise PermissionError(13, "Permission denied", str(self))
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", _deny_stat)
    monkeypatch.setattr(Path, "open", _deny_open)
    monkeypatch.delenv("C2_DGS1_PATHS", raising=False)

    # DGS1: どの既定ファイルも読めない -> 既定レートへフォールバック。
    assert c2_features._load_latest_dgs1(date(2026, 11, 6)) == 0.0412
    # cron 出力証跡: ディレクトリを stat できない -> 「本日分なし」。
    assert (
        c2_gap_watch._today_output_file(c2_gap_watch.default_output_dir(), date(2026, 11, 6))
        is None
    )
    # Alpaca 実アダプタ: 秘密ファイルが無い -> 紙トレードの既定 URL へフォールバック。
    client = AlpacaPaperClient(key_id="k", secret_key="s")
    assert client.key_id == "k"
    assert client.base_url == "https://paper-api.alpaca.markets/v2"
