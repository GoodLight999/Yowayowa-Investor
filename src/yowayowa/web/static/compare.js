(() => {
  const { api, escapeHtml, localeTag, t } = window.Yowayowa;
  const appMode = window.YOWAYOWA_MODE || 'personal';
  const presetSelect = document.querySelector('#compare-preset-select');
  const presetName = document.querySelector('#compare-preset-name');
  const presetStatus = document.querySelector('#compare-preset-status');
  let presets = [];

  const growthMetrics = new Set([
    'revenue_growth_yoy',
    'gross_profit_growth_yoy',
    'operating_income_growth_yoy',
    'net_income_growth_yoy',
    'eps_growth_yoy',
    'operating_cash_flow_growth_yoy',
  ]);
  const profitabilityMetrics = new Set([
    'gross_margin',
    'operating_margin',
    'net_margin',
    'operating_cash_flow_margin',
    'free_cash_flow_margin',
  ]);
  const efficiencyMetrics = new Set([
    'return_on_assets',
    'return_on_equity',
    'asset_turnover',
    'capex_to_revenue',
  ]);
  const liquidityMetrics = new Set([
    'current_ratio',
    'cash_ratio',
    'liabilities_to_equity',
    'equity_to_assets',
    'liabilities_to_assets',
    'cash_to_assets',
    'working_capital',
  ]);

  const parseSymbols = (value) => [...new Set(
    value
      .toUpperCase()
      .split(/[\s,;]+/)
      .map(token => token.trim())
      .filter(Boolean)
  )];

  function selectedMetrics() {
    return [...document.querySelectorAll('#metric-picker input:checked')].map(input => input.value);
  }

  function metricGroup(key) {
    if (growthMetrics.has(key)) return 'growth';
    if (profitabilityMetrics.has(key)) return 'profitability';
    if (efficiencyMetrics.has(key)) return 'efficiency';
    if (liquidityMetrics.has(key)) return 'liquidity';
    return 'absolute';
  }

  function enforceMetricSelection(changed = null) {
    const inputs = [...document.querySelectorAll('#metric-picker input[type="checkbox"]')];
    let checked = inputs.filter(input => input.checked);
    if (!checked.length && changed) {
      changed.checked = true;
      checked = [changed];
    }
    const atLimit = checked.length >= 20;
    inputs.forEach(input => { input.disabled = atLimit && !input.checked; });
  }

  function applyMetricSelection(metrics) {
    if (!Array.isArray(metrics) || !metrics.length) return;
    const requested = new Set(metrics.slice(0, 20));
    const inputs = [...document.querySelectorAll('#metric-picker input[type="checkbox"]')];
    if (!inputs.some(input => requested.has(input.value))) return;
    inputs.forEach(input => { input.checked = requested.has(input.value); });
    enforceMetricSelection();
  }

  function formatValue(value, display) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
    const n = Number(value);
    if (display === 'percent') {
      return new Intl.NumberFormat(localeTag, {
        style: 'percent',
        maximumFractionDigits: 1,
        signDisplay: 'exceptZero',
      }).format(n);
    }
    if (display === 'compact') {
      return new Intl.NumberFormat(localeTag, {
        notation: 'compact',
        maximumFractionDigits: 2,
      }).format(n);
    }
    return new Intl.NumberFormat(localeTag, { maximumFractionDigits: 2 }).format(n);
  }

  function bestValue(rows, metric) {
    if (metric.higher_is_better === null) return null;
    const values = rows
      .map(row => row.metrics[metric.key])
      .filter(value => value !== null && Number.isFinite(Number(value)))
      .map(Number);
    if (!values.length) return null;
    return metric.higher_is_better ? Math.max(...values) : Math.min(...values);
  }

  function metricLabel(metric) {
    return t(`metric.${metric.key}`, {}, metric.label);
  }

  function symbolHeading(row) {
    const label = `<strong>${escapeHtml(row.symbol)}</strong><small>${escapeHtml(row.company_name)}</small>`;
    if (appMode === 'public') return `<span class="compare-symbol">${label}</span>`;
    return `<a class="compare-symbol" href="/instrument/${encodeURIComponent(row.symbol)}">${label}</a>`;
  }

  function renderComparison(data) {
    const target = document.querySelector('#compare-results');
    const meta = document.querySelector('#compare-meta');
    if (!data.rows.length) {
      target.textContent = t('compare.no_data');
      return;
    }
    const best = Object.fromEntries(data.metrics.map(metric => [metric.key, bestValue(data.rows, metric)]));
    const header = data.rows.map(row => `<th>${symbolHeading(row)}</th>`).join('');
    const body = data.metrics.map(metric => {
      const cells = data.rows.map(row => {
        const value = row.metrics[metric.key];
        const isBest = value !== null && best[metric.key] !== null && Number(value) === best[metric.key];
        return `<td class="${isBest ? 'compare-best' : ''}">${escapeHtml(formatValue(value, metric.display))}</td>`;
      }).join('');
      return `<tr><td>${escapeHtml(metricLabel(metric))}</td>${cells}</tr>`;
    }).join('');
    const sources = data.rows.map(row => `
      <div class="compare-source">
        <strong>${escapeHtml(row.symbol)}</strong>
        <span>${escapeHtml(row.provenance.source)} · ${escapeHtml(t(`license.${row.provenance.license_class}`, {}, row.provenance.license_class))}</span>
      </div>`).join('');
    target.innerHTML = `
      <table class="compare-table">
        <thead><tr><th>${escapeHtml(t('common.metric'))}</th>${header}</tr></thead>
        <tbody>${body}</tbody>
      </table>
      <div class="compare-sources">${sources}</div>`;
    meta.textContent = t('compare.issuers_metrics', { issuers: data.rows.length, metrics: data.metrics.length });
  }

  async function loadMetricCatalog() {
    const picker = document.querySelector('#metric-picker');
    const metrics = await api('/v1/compare/metrics');
    let group = null;
    const html = [];
    for (const metric of metrics) {
      const nextGroup = metricGroup(metric.key);
      if (nextGroup !== group) {
        group = nextGroup;
        html.push(`<div class="metric-group-heading">${escapeHtml(t(`screener.group.${group}`, {}, group))}</div>`);
      }
      html.push(`
        <label class="metric-option">
          <input type="checkbox" value="${escapeHtml(metric.key)}" ${metric.default_selected ? 'checked' : ''}>
          <span>${escapeHtml(metricLabel(metric))}</span>
        </label>`);
    }
    picker.innerHTML = html.join('');
    picker.addEventListener('change', event => enforceMetricSelection(event.target));
    enforceMetricSelection();
  }

  function currentComparisonPayload() {
    return {
      symbols: parseSymbols(document.querySelector('#compare-symbols').value),
      metrics: selectedMetrics(),
    };
  }

  function applyComparisonPayload(payload) {
    if (Array.isArray(payload.symbols)) {
      document.querySelector('#compare-symbols').value = payload.symbols.slice(0, 20).join(', ');
    }
    applyMetricSelection(payload.metrics);
  }

  function renderPresets(selectedId = null) {
    if (!presetSelect) return;
    const previous = selectedId === null ? presetSelect.value : String(selectedId);
    presetSelect.innerHTML = '<option value="">—</option>' + presets.map(item =>
      `<option value="${item.id}">${escapeHtml(item.name)}</option>`
    ).join('');
    if (presets.some(item => String(item.id) === previous)) presetSelect.value = previous;
  }

  async function loadPresetCatalog(selectedId = null) {
    if (appMode !== 'personal') return;
    presets = await api('/v1/research-presets?kind=compare');
    renderPresets(selectedId);
  }

  async function savePreset() {
    if (!presetName || !presetStatus) return;
    const payload = currentComparisonPayload();
    if (payload.symbols.length < 2) throw new Error(t('compare.min_two'));
    const saved = await api('/v1/research-presets', {
      method: 'POST',
      body: JSON.stringify({
        name: presetName.value.trim(),
        kind: 'compare',
        payload,
      }),
    });
    presetName.value = '';
    presetStatus.textContent = saved.name;
    await loadPresetCatalog(saved.id);
  }

  async function deleteSelectedPreset() {
    if (!presetSelect || !presetStatus) return;
    const selected = presets.find(item => String(item.id) === presetSelect.value);
    if (!selected) return;
    await api(`/v1/research-presets/${selected.id}`, { method: 'DELETE' });
    presetStatus.textContent = '—';
    await loadPresetCatalog();
  }

  async function runComparison() {
    const symbols = parseSymbols(document.querySelector('#compare-symbols').value);
    const metrics = selectedMetrics();
    if (symbols.length < 2) throw new Error(t('compare.min_two'));
    if (symbols.length > 20) throw new Error(t('compare.max_twenty'));
    const target = document.querySelector('#compare-results');
    const meta = document.querySelector('#compare-meta');
    target.textContent = t('compare.loading_sec');
    meta.textContent = t('common.loading');
    const data = await api('/v1/compare', {
      method: 'POST',
      body: JSON.stringify({ symbols, metrics }),
    });
    renderComparison(data);
    const params = new URLSearchParams(location.search);
    params.set('symbols', symbols.join(','));
    params.set('metrics', metrics.join(','));
    history.replaceState(null, '', `${location.pathname}?${params}`);
  }

  async function applySelectedPreset() {
    if (!presetSelect || !presetStatus) return;
    const selected = presets.find(item => String(item.id) === presetSelect.value);
    if (!selected) return;
    applyComparisonPayload(selected.payload);
    presetStatus.textContent = selected.name;
    await runComparison();
  }

  async function useMainWatchlist() {
    const lists = await api('/v1/watchlists');
    const main = lists.find(item => item.name === 'Main') || lists[0];
    if (!main || main.symbols.length < 2) throw new Error(t('compare.watchlist_min_two'));
    document.querySelector('#compare-symbols').value = main.symbols.slice(0, 20).join(', ');
  }

  document.addEventListener('DOMContentLoaded', async () => {
    const target = document.querySelector('#compare-results');
    try {
      await loadMetricCatalog();
      if (appMode === 'personal') await loadPresetCatalog();
      const params = new URLSearchParams(location.search);
      const initial = params.get('symbols');
      const initialMetrics = (params.get('metrics') || '').split(',').filter(Boolean);
      applyMetricSelection(initialMetrics);
      if (initial) {
        document.querySelector('#compare-symbols').value = initial;
        if (parseSymbols(initial).length >= 2) await runComparison();
      }
    } catch (error) {
      target.textContent = error.message;
    }

    document.querySelector('#compare-form')?.addEventListener('submit', async event => {
      event.preventDefault();
      try { await runComparison(); }
      catch (error) { target.textContent = error.message; }
    });

    document.querySelector('#use-main-watchlist')?.addEventListener('click', async () => {
      try { await useMainWatchlist(); }
      catch (error) { target.textContent = error.message; }
    });

    document.querySelector('#compare-preset-form')?.addEventListener('submit', async event => {
      event.preventDefault();
      try { await savePreset(); }
      catch (error) { if (presetStatus) presetStatus.textContent = error.message; }
    });

    document.querySelector('#compare-preset-load')?.addEventListener('click', async () => {
      try { await applySelectedPreset(); }
      catch (error) { if (presetStatus) presetStatus.textContent = error.message; }
    });

    document.querySelector('#compare-preset-delete')?.addEventListener('click', async () => {
      try { await deleteSelectedPreset(); }
      catch (error) { if (presetStatus) presetStatus.textContent = error.message; }
    });
  });
})();
