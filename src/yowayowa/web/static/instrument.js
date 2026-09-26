(() => {
  const { api, escapeHtml, localeTag, t } = window.Yowayowa;
  const host = document.querySelector('.instrument-header');
  const symbol = host?.dataset.symbol;
  let chart;
  let mainWatchlist = null;
  let fundamentalsData = null;
  let financialView = 'annual';

  const flowMetricKeys = [
    'revenue',
    'gross_profit',
    'operating_income',
    'net_income',
    'eps_diluted',
    'operating_cash_flow',
    'capex',
  ];
  const financialRowOrder = [
    'revenue',
    'gross_profit',
    'operating_income',
    'net_income',
    'eps_diluted',
    'operating_cash_flow',
    'capex',
    'cash',
    'current_assets',
    'current_liabilities',
    'assets',
    'liabilities',
    'equity',
  ];
  const healthMetricKeys = [
    'revenue_growth_yoy',
    'operating_margin',
    'net_margin',
    'return_on_equity',
    'current_ratio',
    'free_cash_flow_margin',
    'liabilities_to_equity',
  ];
  const percentMetrics = new Set([
    'revenue_growth_yoy',
    'gross_profit_growth_yoy',
    'operating_income_growth_yoy',
    'net_income_growth_yoy',
    'eps_growth_yoy',
    'operating_cash_flow_growth_yoy',
    'gross_margin',
    'operating_margin',
    'net_margin',
    'operating_cash_flow_margin',
    'free_cash_flow_margin',
    'capex_to_revenue',
    'return_on_assets',
    'return_on_equity',
    'equity_to_assets',
    'liabilities_to_assets',
    'cash_to_assets',
  ]);

  function latestPoint(metric) {
    if (!metric?.points?.length) return null;
    return metric.points[metric.points.length - 1];
  }

  function compactNumber(value, unit) {
    const n = Number(value);
    if (!Number.isFinite(n)) return '—';
    if (unit === 'USD' || unit === 'JPY') {
      return new Intl.NumberFormat(localeTag, { notation: 'compact', maximumFractionDigits: 2 }).format(n);
    }
    return new Intl.NumberFormat(localeTag, { maximumFractionDigits: 3 }).format(n);
  }

  function derivedValue(key, value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
    const number = Number(value);
    if (percentMetrics.has(key)) {
      return new Intl.NumberFormat(localeTag, {
        style: 'percent',
        maximumFractionDigits: 1,
        signDisplay: key.endsWith('_growth_yoy') ? 'always' : 'auto',
      }).format(number);
    }
    return new Intl.NumberFormat(localeTag, { maximumFractionDigits: 2 }).format(number);
  }

  const metricLabel = (key, fallback) => t(`metric.${key}`, {}, fallback || key);

  function renderMetrics(data) {
    const keys = ['revenue', 'operating_income', 'net_income', 'cash', 'assets', 'eps_diluted'];
    const target = document.querySelector('#key-metrics');
    target.innerHTML = keys.map(key => {
      const metric = data.metrics[key];
      const point = latestPoint(metric);
      return `<div class="metric-card"><small>${escapeHtml(metricLabel(key, metric?.label))}</small><strong>${point ? escapeHtml(compactNumber(point.value, point.unit)) : '—'}</strong></div>`;
    }).join('');
  }

  function durationDays(point) {
    if (!point?.period_start || !point?.period_end) return null;
    const start = Date.parse(`${point.period_start}T00:00:00Z`);
    const end = Date.parse(`${point.period_end}T00:00:00Z`);
    if (!Number.isFinite(start) || !Number.isFinite(end)) return null;
    return Math.round((end - start) / 86_400_000);
  }

  function pointMatchesView(point, view) {
    const fiscalPeriod = String(point.fiscal_period || '').toUpperCase();
    const days = durationDays(point);
    if (view === 'annual') {
      return fiscalPeriod === 'FY' && days !== null && days >= 250;
    }
    return /^Q[1-4]$/.test(fiscalPeriod) && days !== null && days <= 130;
  }

  function availableFinancialPeriods(data, view) {
    const periods = new Map();
    for (const key of flowMetricKeys) {
      for (const point of data.metrics[key]?.points || []) {
        if (!pointMatchesView(point, view)) continue;
        const fiscalPeriod = String(point.fiscal_period || '').toUpperCase();
        const id = `${fiscalPeriod}:${point.period_end}`;
        periods.set(id, {
          id,
          fiscalPeriod,
          fiscalYear: point.fiscal_year,
          periodEnd: point.period_end,
        });
      }
    }
    const limit = view === 'annual' ? 6 : 8;
    return [...periods.values()]
      .sort((left, right) => left.periodEnd.localeCompare(right.periodEnd))
      .slice(-limit);
  }

  function financialPoint(metric, period) {
    if (!metric?.points?.length) return null;
    const sameEnd = metric.points.filter(point => point.period_end === period.periodEnd);
    if (!sameEnd.length) return null;
    const sameFiscalPeriod = sameEnd.filter(
      point => String(point.fiscal_period || '').toUpperCase() === period.fiscalPeriod,
    );
    const candidates = sameFiscalPeriod.length ? sameFiscalPeriod : sameEnd;
    const flowCandidates = candidates.filter(point => point.period_start);
    if (flowCandidates.length) {
      const matchingView = flowCandidates.filter(point => pointMatchesView(point, financialView));
      if (matchingView.length) return matchingView.at(-1);
    }
    return candidates.at(-1);
  }

  function periodLabel(period) {
    const year = period.fiscalYear || period.periodEnd.slice(0, 4);
    return period.fiscalPeriod === 'FY' ? `FY ${year}` : `${period.fiscalPeriod} ${year}`;
  }

  function renderFinancialTable() {
    const target = document.querySelector('#fundamentals-table');
    if (!target || !fundamentalsData) return;
    const periods = availableFinancialPeriods(fundamentalsData, financialView);
    if (!periods.length) {
      target.innerHTML = `<div class="list-state muted">${escapeHtml(t('research.none', {}, t('screener.prompt')))}</div>`;
      return;
    }
    const rows = financialRowOrder
      .filter(key => fundamentalsData.metrics[key])
      .map(key => {
        const metric = fundamentalsData.metrics[key];
        const cells = periods.map(period => {
          const point = financialPoint(metric, period);
          return `<td>${point ? escapeHtml(compactNumber(point.value, point.unit)) : '—'}</td>`;
        }).join('');
        return `<tr><td><strong>${escapeHtml(metricLabel(key, metric.label))}</strong></td>${cells}</tr>`;
      }).join('');
    target.innerHTML = `<table><thead><tr><th>${escapeHtml(t('common.metric'))}</th>${periods.map(period => `<th>${escapeHtml(periodLabel(period))}<small>${escapeHtml(period.periodEnd)}</small></th>`).join('')}</tr></thead><tbody>${rows}</tbody></table>`;
  }

  function renderFundamentals(data) {
    fundamentalsData = data;
    document.querySelector('#instrument-name').textContent = `${data.company_name} · ${t('instrument.cik')} ${data.cik}`;
    const p = data.provenance;
    const license = t(`license.${p.license_class}`, {}, p.license_class);
    document.querySelector('#provenance').innerHTML = `
      <div><strong>${escapeHtml(p.source)}</strong></div>
      <div>${escapeHtml(t('common.provider'))} <code>${escapeHtml(p.provider)}</code> · ${escapeHtml(license)}</div>
      <div>${escapeHtml(t('common.retrieved'))} ${escapeHtml(new Date(p.retrieved_at).toLocaleString(localeTag))}</div>
      <div>${p.source_url ? `<a href="${escapeHtml(p.source_url)}" rel="noreferrer">${escapeHtml(t('common.open_source'))}</a>` : ''}</div>`;
    renderMetrics(data);
    renderFinancialTable();
  }

  function renderFinancialHealth(metrics) {
    const target = document.querySelector('#financial-health-metrics');
    if (!target) return;
    target.innerHTML = healthMetricKeys.map(key => `
      <div class="metric-card">
        <small>${escapeHtml(metricLabel(key))}</small>
        <strong>${escapeHtml(derivedValue(key, metrics[key]))}</strong>
      </div>`).join('');
  }

  async function loadFinancialHealth() {
    const target = document.querySelector('#financial-health-metrics');
    if (!target) return;
    target.innerHTML = `<div class="metric-card"><small>${escapeHtml(t('instrument.financials'))}</small><strong>${escapeHtml(t('common.loading_ellipsis'))}</strong></div>`;
    try {
      const payload = await api('/v1/screen', {
        method: 'POST',
        body: JSON.stringify({ symbols: [symbol], filters: [] }),
      });
      renderFinancialHealth(payload.rows[0]?.metrics || {});
    } catch (error) {
      target.innerHTML = `<div class="metric-card"><small>${escapeHtml(t('instrument.financials'))}</small><strong>—</strong><small>${escapeHtml(error.message)}</small></div>`;
    }
  }

  function valuationValue(key, value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
    const number = Number(value);
    if (key === 'market_cap') {
      return new Intl.NumberFormat(localeTag, { notation: 'compact', maximumFractionDigits: 2 }).format(number);
    }
    if (key.endsWith('_yield')) {
      return new Intl.NumberFormat(localeTag, { style: 'percent', maximumFractionDigits: 2 }).format(number);
    }
    return `${new Intl.NumberFormat(localeTag, { maximumFractionDigits: 2 }).format(number)}×`;
  }

  function renderValuation(data) {
    const metrics = {
      market_cap: data.market_cap,
      price_to_sales: data.metrics.price_to_sales,
      price_to_earnings: data.metrics.price_to_earnings,
      price_to_book: data.metrics.price_to_book,
      price_to_free_cash_flow: data.metrics.price_to_free_cash_flow,
      earnings_yield: data.metrics.earnings_yield,
      free_cash_flow_yield: data.metrics.free_cash_flow_yield,
    };
    document.querySelector('#valuation-metrics').innerHTML = Object.entries(metrics).map(([key, value]) => `
      <div class="metric-card"><small>${escapeHtml(metricLabel(key))}</small><strong>${escapeHtml(valuationValue(key, value))}</strong></div>`).join('');
    const basis = data.annual_period_end ? `${t('instrument.valuation_basis')} · ${data.annual_period_end}` : t('instrument.valuation_basis');
    document.querySelector('#valuation-basis').textContent = basis;
  }

  async function loadValuation() {
    const target = document.querySelector('#valuation-metrics');
    try {
      renderValuation(await api(`/v1/valuation/${encodeURIComponent(symbol)}`));
    } catch (error) {
      target.innerHTML = `<div class="metric-card"><small>${escapeHtml(t('instrument.valuation'))}</small><strong>—</strong></div>`;
      document.querySelector('#valuation-basis').textContent = error.message;
    }
  }

  function indicatorPoints(indicator) {
    return indicator.points
      .filter(([, value]) => value !== null)
      .map(([time, value]) => ({ time: time.slice(0, 10), value }));
  }

  function renderChart(data) {
    const container = document.querySelector('#chart');
    if (chart) chart.remove();

    const indicatorPanes = new Set(
      data.indicators
        .map(indicator => indicator.pane || 'price')
        .filter(pane => pane !== 'price'),
    );
    const hasVolume = data.bars.some(bar => bar.volume !== null);
    const extraPanes = indicatorPanes.size + (hasVolume ? 1 : 0);
    container.style.height = `${Math.max(440, 360 + extraPanes * 125)}px`;

    chart = LightweightCharts.createChart(container, {
      autoSize: true,
      layout: {
        background: { type: 'solid', color: 'transparent' },
        textColor: '#6c6962',
        attributionLogo: true,
        panes: {
          separatorColor: '#d1ccc2',
          separatorHoverColor: '#a9a49a',
          enableResize: true,
        },
      },
      grid: {
        vertLines: { color: 'rgba(23,23,22,.055)' },
        horzLines: { color: 'rgba(23,23,22,.055)' },
      },
      rightPriceScale: { borderColor: '#d1ccc2' },
      timeScale: { borderColor: '#d1ccc2', timeVisible: false, minBarSpacing: 0.05 },
      crosshair: { mode: LightweightCharts.CrosshairMode.MagnetOHLC },
    });

    const candles = chart.addSeries(LightweightCharts.CandlestickSeries, {
      upColor: '#087a54',
      downColor: '#bc3145',
      wickUpColor: '#087a54',
      wickDownColor: '#bc3145',
      borderVisible: false,
    });
    candles.setData(data.bars.map(bar => ({
      time: bar.timestamp.slice(0, 10),
      open: bar.open,
      high: bar.high,
      low: bar.low,
      close: bar.close,
    })));

    const volumeBars = data.bars.filter(bar => bar.volume !== null);
    let nextPaneIndex = 1;
    if (volumeBars.length) {
      const volumePaneIndex = nextPaneIndex++;
      const volume = chart.addSeries(LightweightCharts.HistogramSeries, {
        priceFormat: { type: 'volume' },
        priceLineVisible: false,
        lastValueVisible: false,
        title: 'Volume',
      }, volumePaneIndex);
      volume.setData(volumeBars.map(bar => ({
        time: bar.timestamp.slice(0, 10),
        value: bar.volume,
        color: bar.close >= bar.open ? 'rgba(8,122,84,.28)' : 'rgba(188,49,69,.28)',
      })));
      chart.panes()[volumePaneIndex]?.setHeight(95);
    }

    const paneIndices = new Map();
    const paneHeights = new Map([
      ['rsi', 130],
      ['macd', 145],
      ['atr', 120],
      ['stoch', 130],
      ['adx', 135],
      ['willr', 130],
      ['obv', 120],
    ]);
    const paneIndexFor = pane => {
      if (!pane || pane === 'price') return 0;
      if (!paneIndices.has(pane)) paneIndices.set(pane, nextPaneIndex++);
      return paneIndices.get(pane);
    };
    const palette = ['#2456d8', '#7254a7', '#9a6b24', '#59636f', '#13736d', '#9a4567'];
    const referenceLines = new Set();

    data.indicators.forEach((indicator, index) => {
      const pane = indicator.pane || 'price';
      const paneIndex = paneIndexFor(pane);
      const color = palette[index % palette.length];
      let series;
      if (indicator.render === 'histogram') {
        series = chart.addSeries(LightweightCharts.HistogramSeries, {
          color,
          priceLineVisible: false,
          lastValueVisible: true,
          title: indicator.name,
        }, paneIndex);
        series.setData(indicatorPoints(indicator).map(point => ({
          ...point,
          color: point.value >= 0 ? 'rgba(8,122,84,.46)' : 'rgba(188,49,69,.46)',
        })));
      } else {
        series = chart.addSeries(LightweightCharts.LineSeries, {
          color,
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: true,
          title: indicator.name,
        }, paneIndex);
        series.setData(indicatorPoints(indicator));
      }

      for (const price of indicator.reference_lines || []) {
        const key = `${pane}:${price}`;
        if (referenceLines.has(key)) continue;
        referenceLines.add(key);
        series.createPriceLine({
          price,
          color: 'rgba(108,105,98,.5)',
          lineWidth: 1,
          lineStyle: LightweightCharts.LineStyle.Dashed,
          axisLabelVisible: true,
          title: pane === 'rsi' ? `RSI ${price}` : String(price),
        });
      }
    });

    for (const [pane, paneIndex] of paneIndices.entries()) {
      chart.panes()[paneIndex]?.setHeight(paneHeights.get(pane) || 120);
    }

    chart.timeScale().fitContent();
    const p = data.provenance;
    const license = t(`license.${p.license_class}`, {}, p.license_class);
    document.querySelector('#chart-source').textContent = `${p.source} · ${license} · ${t('common.as_of')} ${new Date(p.as_of || p.retrieved_at).toLocaleString(localeTag)}`;
  }

  function renderLiveQuote(quote) {
    const price = document.querySelector('#instrument-price');
    const change = document.querySelector('#instrument-change');
    if (!quote) {
      price.textContent = '—';
      change.textContent = t('dashboard.quote_unavailable');
      change.className = 'muted';
      return;
    }
    price.textContent = new Intl.NumberFormat(localeTag, { maximumFractionDigits: 4 }).format(Number(quote.price));
    const previous = quote.previous_close;
    const delta = previous === null || Number(previous) === 0 ? null : Number(quote.price) / Number(previous) - 1;
    if (delta === null) {
      change.textContent = '—';
      change.className = 'muted';
    } else {
      change.textContent = new Intl.NumberFormat(localeTag, {
        style: 'percent',
        maximumFractionDigits: 2,
        signDisplay: 'always',
      }).format(delta);
      change.className = delta > 0 ? 'market-positive' : delta < 0 ? 'market-negative' : 'muted';
    }
    document.querySelector('#instrument-live-quote').title = new Date(quote.as_of).toLocaleString(localeTag);
  }

  async function loadLiveQuote() {
    try {
      const data = await api(`/v1/markets/quotes?symbols=${encodeURIComponent(symbol)}`);
      renderLiveQuote(data.quotes[symbol] || null);
    } catch (_) {
      renderLiveQuote(null);
    }
  }

  function updateWatchlistButton() {
    const button = document.querySelector('#toggle-watchlist');
    if (!button) return;
    const contained = Boolean(mainWatchlist?.symbols?.includes(symbol));
    button.dataset.contained = contained ? 'true' : 'false';
    button.textContent = `${contained ? t('common.remove') : t('common.add')} · ${t('dashboard.watchlist')}`;
  }

  async function loadWatchlistState() {
    try {
      const lists = await api('/v1/watchlists');
      mainWatchlist = lists.find(item => item.name === 'Main') || lists[0] || null;
      updateWatchlistButton();
    } catch (_) {
      const button = document.querySelector('#toggle-watchlist');
      if (button) button.disabled = true;
    }
  }

  async function toggleWatchlist() {
    if (!mainWatchlist) return;
    const contained = mainWatchlist.symbols.includes(symbol);
    if (contained) {
      mainWatchlist = await api(
        `/v1/watchlists/${mainWatchlist.id}/symbols/${encodeURIComponent(symbol)}`,
        { method: 'DELETE' },
      );
    } else {
      mainWatchlist = await api(`/v1/watchlists/${mainWatchlist.id}/symbols`, {
        method: 'POST',
        body: JSON.stringify([symbol]),
      });
    }
    updateWatchlistButton();
  }

  async function loadFundamentals() {
    try { renderFundamentals(await api(`/v1/fundamentals/${encodeURIComponent(symbol)}`)); }
    catch (error) { document.querySelector('#fundamentals-table').textContent = error.message; }
  }

  async function loadChart() {
    const period = document.querySelector('#period-select').value;
    const indicators = document.querySelector('#indicator-input').value;
    const source = document.querySelector('#chart-source');
    source.textContent = t('instrument.loading_market');
    try {
      const data = await api(`/v1/markets/${encodeURIComponent(symbol)}/history?period=${encodeURIComponent(period)}&interval=1d&indicators=${encodeURIComponent(indicators)}`);
      renderChart(data);
    } catch (error) { source.textContent = error.message; }
  }

  document.querySelector('#reload-chart')?.addEventListener('click', loadChart);
  document.querySelector('#toggle-watchlist')?.addEventListener('click', () => {
    toggleWatchlist().catch(error => {
      const button = document.querySelector('#toggle-watchlist');
      if (button) button.title = error.message;
    });
  });
  document.querySelectorAll('[data-financial-view]').forEach(button => {
    button.addEventListener('click', () => {
      financialView = button.dataset.financialView;
      document.querySelectorAll('[data-financial-view]').forEach(item => {
        const active = item === button;
        item.classList.toggle('is-active', active);
        item.setAttribute('aria-pressed', active ? 'true' : 'false');
      });
      renderFinancialTable();
    });
  });
  loadFundamentals();
  loadFinancialHealth();
  loadValuation();
  loadChart();
  loadLiveQuote();
  loadWatchlistState();
})();
