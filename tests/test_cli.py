from typer.testing import CliRunner

from yowayowa.cli_entry import app

runner = CliRunner()


def test_cli_builds_with_calendar_iso_date_options() -> None:
    result = runner.invoke(app, ["calendar", "--help"])

    assert result.exit_code == 0, result.output
    assert result.exception is None


def test_cli_exposes_saved_calendar_command() -> None:
    result = runner.invoke(app, ["calendar-saved", "--help"])

    assert result.exit_code == 0, result.output
    assert result.exception is None


def test_cli_exposes_saved_news_command() -> None:
    result = runner.invoke(app, ["news-saved", "--help"])

    assert result.exit_code == 0, result.output
    assert result.exception is None


def test_cli_exposes_event_subscription_commands() -> None:
    result = runner.invoke(app, ["events", "--help"])
    subscribe = runner.invoke(app, ["events", "subscribe", "--help"])

    assert result.exit_code == 0, result.output
    assert result.exception is None
    assert subscribe.exit_code == 0, subscribe.output
    assert subscribe.exception is None


def test_cli_exposes_chart_composer_commands() -> None:
    result = runner.invoke(app, ["chart", "--help"])
    compose = runner.invoke(app, ["chart", "compose", "--help"])

    assert result.exit_code == 0, result.output
    assert result.exception is None
    assert compose.exit_code == 0, compose.output
    assert compose.exception is None


def test_cli_exposes_treasury_rate_commands() -> None:
    result = runner.invoke(app, ["rates", "--help"])
    curve = runner.invoke(app, ["rates", "curve", "--help"])

    assert result.exit_code == 0, result.output
    assert result.exception is None
    assert curve.exit_code == 0, curve.output
    assert curve.exception is None


def test_cli_exposes_portfolio_risk_command() -> None:
    result = runner.invoke(app, ["portfolio", "risk", "--help"])

    assert result.exit_code == 0, result.output
    assert result.exception is None


def test_cli_exposes_research_preset_commands() -> None:
    result = runner.invoke(app, ["preset", "--help"])
    save = runner.invoke(app, ["preset", "save", "--help"])

    assert result.exit_code == 0, result.output
    assert result.exception is None
    assert save.exit_code == 0, save.output
    assert save.exception is None


def test_cli_exposes_license_policy_commands() -> None:
    result = runner.invoke(app, ["license", "--help"])
    listing = runner.invoke(app, ["license", "list", "--help"])

    assert result.exit_code == 0, result.output
    assert result.exception is None
    assert listing.exit_code == 0, listing.output
    assert listing.exception is None


def test_cli_exposes_public_macro_commands() -> None:
    result = runner.invoke(app, ["macro", "--help"])
    bls_catalog = runner.invoke(app, ["macro", "bls-catalog", "--help"])
    bls_series = runner.invoke(app, ["macro", "bls", "--help"])
    bea_catalog = runner.invoke(app, ["macro", "bea-catalog", "--help"])
    bea_table = runner.invoke(app, ["macro", "bea", "--help"])

    assert result.exit_code == 0, result.output
    assert result.exception is None
    assert bls_catalog.exit_code == 0, bls_catalog.output
    assert bls_catalog.exception is None
    assert bls_series.exit_code == 0, bls_series.output
    assert bls_series.exception is None
    assert bea_catalog.exit_code == 0, bea_catalog.output
    assert bea_catalog.exception is None
    assert bea_table.exit_code == 0, bea_table.output
    assert bea_table.exception is None
