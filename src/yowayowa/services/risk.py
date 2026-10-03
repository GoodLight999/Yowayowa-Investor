from __future__ import annotations

from datetime import UTC, datetime
from math import isfinite, sqrt

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from yowayowa.domain import Portfolio, PortfolioAnalytics
from yowayowa.providers.yahoo_risk import HistoricalReturnData
from yowayowa.risk_models import CorrelationCell, PortfolioRiskAnalytics, PositionRisk

TRADING_DAYS_PER_YEAR = 252.0
_EPSILON = 1e-15
FloatArray = NDArray[np.float64]


def _array(values: pd.Series) -> FloatArray:
    return np.asarray(values.to_numpy(dtype=float), dtype=np.float64)


def _sample_volatility(values: FloatArray) -> float | None:
    if values.size < 2:
        return None
    value = float(np.std(values, ddof=1))
    return value if isfinite(value) else None


def _annualized_return(values: FloatArray) -> float | None:
    if values.size == 0:
        return None
    growth = float(np.prod(1.0 + values))
    if not isfinite(growth) or growth <= 0:
        return None
    value = growth ** (TRADING_DAYS_PER_YEAR / float(values.size)) - 1.0
    return value if isfinite(value) else None


def _annualized_volatility(values: FloatArray) -> float | None:
    daily = _sample_volatility(values)
    if daily is None:
        return None
    return daily * sqrt(TRADING_DAYS_PER_YEAR)


def _max_drawdown(values: FloatArray) -> float | None:
    if values.size == 0:
        return None
    wealth = np.cumprod(1.0 + values)
    running_peak = np.maximum.accumulate(np.concatenate((np.array([1.0]), wealth)))[1:]
    drawdowns = wealth / running_peak - 1.0
    value = float(np.min(drawdowns))
    return value if isfinite(value) else None


def _historical_tail_risk(values: FloatArray) -> tuple[float | None, float | None]:
    if values.size == 0:
        return None, None
    threshold = float(np.quantile(values, 0.05))
    tail = values[values <= threshold]
    expected_shortfall = float(np.mean(tail)) if tail.size else threshold
    var_95 = max(0.0, -threshold)
    es_95 = max(0.0, -expected_shortfall)
    return var_95, es_95


def _beta_and_correlation(left: FloatArray, right: FloatArray) -> tuple[float | None, float | None]:
    if left.size < 2 or right.size < 2 or left.size != right.size:
        return None, None
    right_variance = float(np.var(right, ddof=1))
    left_std = float(np.std(left, ddof=1))
    right_std = float(np.std(right, ddof=1))
    beta: float | None = None
    correlation: float | None = None
    if isfinite(right_variance) and right_variance > _EPSILON:
        covariance = float(np.cov(left, right, ddof=1)[0, 1])
        if isfinite(covariance):
            beta = covariance / right_variance
    if left_std > _EPSILON and right_std > _EPSILON:
        candidate = float(np.corrcoef(left, right)[0, 1])
        if isfinite(candidate):
            correlation = candidate
    return beta, correlation


def _empty_result(
    portfolio: Portfolio,
    valuation: PortfolioAnalytics,
    history: HistoricalReturnData,
    benchmark: str,
    period: str,
    risk_free_rate: float,
    unavailable: list[str],
) -> PortfolioRiskAnalytics:
    return PortfolioRiskAnalytics(
        portfolio_id=portfolio.id,
        name=portfolio.name,
        base_currency=portfolio.base_currency,
        benchmark=benchmark,
        period=period,
        observations=0,
        gross_exposure=valuation.gross_market_value,
        net_exposure=valuation.net_market_value,
        largest_position_weight=valuation.largest_position_weight,
        concentration_hhi=valuation.concentration_hhi,
        covered_gross_weight=0.0,
        unavailable_symbols=unavailable,
        risk_free_rate=risk_free_rate,
        notes=[
            "Historical risk metrics are unavailable because no priced position has usable "
            "historical return coverage.",
        ],
        provenance=[valuation.provenance, history.provenance],
        evaluated_at=datetime.now(UTC),
    )


def portfolio_risk_analytics(
    portfolio: Portfolio,
    valuation: PortfolioAnalytics,
    history: HistoricalReturnData,
    benchmark: str,
    period: str,
    risk_free_rate: float = 0.0,
) -> PortfolioRiskAnalytics:
    """Calculate historical risk using current gross-exposure weights.

    Position returns are already translated into the portfolio base currency by the
    historical provider. Long positions receive positive weights and short positions
    negative weights. If historical coverage is partial, aggregate statistics describe
    the covered sleeve and the result explicitly reports its gross-weight coverage.
    """

    directions = {
        position.symbol: (-1.0 if position.quantity < 0 else 1.0)
        for position in portfolio.positions
    }
    signed_weights = {
        item.symbol: item.weight * directions.get(item.symbol, 1.0)
        for item in valuation.positions
        if item.weight > 0
    }

    covered_symbols: list[str] = []
    missing_history: list[str] = []
    for symbol in signed_weights:
        series = history.returns.get(symbol)
        if series is None or series.dropna().empty:
            missing_history.append(symbol)
        else:
            covered_symbols.append(symbol)

    unavailable = sorted(
        set(valuation.unavailable_symbols) | set(history.unavailable_symbols) | set(missing_history)
    )
    covered_gross_weight = sum(abs(signed_weights[symbol]) for symbol in covered_symbols)
    if not covered_symbols or covered_gross_weight <= _EPSILON:
        return _empty_result(
            portfolio,
            valuation,
            history,
            benchmark,
            period,
            risk_free_rate,
            unavailable,
        )

    common = history.returns.loc[:, covered_symbols].dropna(how="any")
    normalized_weights = np.asarray(
        [signed_weights[symbol] / covered_gross_weight for symbol in covered_symbols],
        dtype=np.float64,
    )
    matrix = np.asarray(common.to_numpy(dtype=float), dtype=np.float64)
    if matrix.shape[0] == 0:
        result = _empty_result(
            portfolio,
            valuation,
            history,
            benchmark,
            period,
            risk_free_rate,
            unavailable,
        )
        return result.model_copy(update={"covered_gross_weight": covered_gross_weight})

    portfolio_values = np.asarray(matrix @ normalized_weights, dtype=np.float64)
    portfolio_series = pd.Series(portfolio_values, index=common.index, dtype=float)
    annualized_return = _annualized_return(portfolio_values)
    annualized_volatility = _annualized_volatility(portfolio_values)
    sharpe_ratio = None
    if annualized_return is not None and annualized_volatility not in {None, 0.0}:
        assert annualized_volatility is not None
        sharpe_ratio = (annualized_return - risk_free_rate) / annualized_volatility
    max_drawdown = _max_drawdown(portfolio_values)
    value_at_risk_95, expected_shortfall_95 = _historical_tail_risk(portfolio_values)

    benchmark_pair = pd.concat(
        [portfolio_series.rename("portfolio"), history.benchmark_returns.rename("benchmark")],
        axis=1,
    ).dropna()
    benchmark_matrix = np.asarray(benchmark_pair.to_numpy(dtype=float), dtype=np.float64)
    beta: float | None = None
    benchmark_correlation: float | None = None
    if benchmark_matrix.shape[0] >= 2:
        beta, benchmark_correlation = _beta_and_correlation(
            benchmark_matrix[:, 0], benchmark_matrix[:, 1]
        )

    covariance_matrix = np.atleast_2d(np.cov(matrix, rowvar=False, ddof=1))
    portfolio_variance = float(normalized_weights @ covariance_matrix @ normalized_weights)
    marginal_variance = covariance_matrix @ normalized_weights

    benchmark_on_common = history.benchmark_returns.reindex(common.index)
    benchmark_values = _array(benchmark_on_common)
    benchmark_mask = np.isfinite(benchmark_values)

    position_results: list[PositionRisk] = []
    for index, symbol in enumerate(covered_symbols):
        position_values = np.asarray(matrix[:, index], dtype=np.float64)
        position_beta: float | None = None
        if int(np.count_nonzero(benchmark_mask)) >= 2:
            position_beta, _ = _beta_and_correlation(
                position_values[benchmark_mask], benchmark_values[benchmark_mask]
            )
        _, portfolio_correlation = _beta_and_correlation(position_values, portfolio_values)
        variance_contribution = None
        if portfolio_variance > _EPSILON:
            candidate = float(
                normalized_weights[index] * marginal_variance[index] / portfolio_variance
            )
            if isfinite(candidate):
                variance_contribution = candidate
        position_results.append(
            PositionRisk(
                symbol=symbol,
                signed_weight=signed_weights[symbol],
                volatility_annualized=_annualized_volatility(position_values),
                beta=position_beta,
                correlation_to_portfolio=portfolio_correlation,
                variance_contribution=variance_contribution,
                observations=int(position_values.size),
            )
        )

    correlations: list[CorrelationCell] = []
    for left_index, left_symbol in enumerate(covered_symbols):
        for right_index in range(left_index + 1, len(covered_symbols)):
            right_symbol = covered_symbols[right_index]
            _, correlation = _beta_and_correlation(
                np.asarray(matrix[:, left_index], dtype=np.float64),
                np.asarray(matrix[:, right_index], dtype=np.float64),
            )
            correlations.append(
                CorrelationCell(
                    left=left_symbol,
                    right=right_symbol,
                    correlation=correlation,
                )
            )

    notes = [
        "Returns use current position weights held constant over the sampled period.",
        "Long/short portfolio return is measured on gross exposure; short weights are signed.",
        "VaR 95% and expected shortfall 95% are one-day historical loss fractions.",
        "Historical statistics describe the sampled period and are not forecasts.",
    ]
    if covered_gross_weight < 1.0 - 1e-9:
        notes.append(
            "Aggregate risk statistics describe only the historically covered sleeve; "
            "covered gross weight is reported explicitly and the sleeve is normalized to 100%."
        )

    return PortfolioRiskAnalytics(
        portfolio_id=portfolio.id,
        name=portfolio.name,
        base_currency=portfolio.base_currency,
        benchmark=benchmark,
        period=period,
        observations=int(portfolio_values.size),
        annualized_return=annualized_return,
        annualized_volatility=annualized_volatility,
        sharpe_ratio=sharpe_ratio,
        max_drawdown=max_drawdown,
        value_at_risk_95=value_at_risk_95,
        expected_shortfall_95=expected_shortfall_95,
        beta=beta,
        benchmark_correlation=benchmark_correlation,
        gross_exposure=valuation.gross_market_value,
        net_exposure=valuation.net_market_value,
        largest_position_weight=valuation.largest_position_weight,
        concentration_hhi=valuation.concentration_hhi,
        covered_gross_weight=covered_gross_weight,
        positions=position_results,
        correlations=correlations,
        unavailable_symbols=unavailable,
        risk_free_rate=risk_free_rate,
        notes=notes,
        provenance=[valuation.provenance, history.provenance],
        evaluated_at=datetime.now(UTC),
    )
