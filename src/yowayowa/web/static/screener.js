(() => {
  const { api, escapeHtml, localeTag, t } = window.Yowayowa;
  const appMode = window.YOWAYOWA_MODE || 'personal';

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
  const initialFilter = document.querySelector('.screen-filter-row');
  const filterTemplate = initialFilter?.cloneNode(true) || null;
  const presetSelect = document.querySelector('#screen-preset-select');
  const presetName = document.querySelector('#screen-preset-name');
  const presetStatus = document.querySelector('#screen-preset-status');
  let presets = [];

  const parseSymbols = (value) => [...new Set(
    value.toUpperCase().split(/[\s,;]+/).map(item => item.trim()).filter(Boolean)
  )];
  const metricLabel = (key) => t(`metric.${key}`, {}, key.replaceAll('_', ' '));
  const valueFmt = (key, value) => {
    if (value === null || value === undefined) return '—';
    if (percentMetrics.has(key)) {
      return new Intl.NumberFormat(localeTag, {
        style: 'percent',
        maximumFractionDigits: 1,
        signDisplay: key.endsWith('_growth_yoy') ? 'always' : 'auto',
      }).format(Number(value));
    }
    return new Intl.NumberFormat(localeTag, {
      notation: Math.abs(value) > 1e6 ? 'compact' : 'standard',
      maximumFractionDigits: 2,
    }).format(value);
  };

  function filterRows() {
    return [...document.querySelectorAll('.screen-filter-row')];
  }

  function updateRemoveButtons() {
    const rows = filterRows();
    rows.forEach(row => {
      const button = row.querySelector('.filter-remove');
      if (button) button.disabled = rows.length === 1;
    });
  }

  function setFilterValues(row, initial = {}) {
    const metric = row.querySelector('.screen-filter-metric');
    const operator = row.querySelector('.screen-filter-operator');
    const value = row.querySelector('.screen-filter-value');
    if ([...metric.options].some(option => option.value === initial.metric)) metric.value = initial.metric;
    if ([...operator.options].some(option => option.value === initial.operator)) operator.value = initial.operator;
    const rawValue = Array.isArray(initial.value) ? initial.value[0] : initial.value;
    value.value = Number.isFinite(Number(rawValue)) ? String(rawValue) : '0';
  }

  function installRemoveButton(row) {
    const button = row.querySelector('.filter-remove');
    if (!button) return;
    button.addEventListener('click', () => {
      if (filterRows().length <= 1) return;
      row.remove();
      updateRemoveButtons();
    });
  }

  function addFilter(initial = {}) {
    if (!filterTemplate) return null;
    const row = filterTemplate.cloneNode(true);
    row.querySelectorAll('[id]').forEach(element => element.removeAttribute('id'));
    setFilterValues(row, initial);
    const remove = row.querySelector('.filter-remove');
    remove.textContent = t('common.remove');
    document.querySelector('#screen-filters').append(row);
    installRemoveButton(row);
    updateRemoveButtons();
    return row;
  }

  function collectFilters() {
    return filterRows().map(row => ({
      metric: row.querySelector('.screen-filter-metric').value,
      operator: row.querySelector('.screen-filter-operator').value,
      value: Number(row.querySelector('.screen-filter-value').value),
    }));
  }

  function currentScreenPayload() {
    return {
      symbols: parseSymbols(document.querySelector('#screen-symbols').value),
      filters: collectFilters(),
    };
  }

  function applyScreenPayload(payload) {
    if (Array.isArray(payload.symbols)) {
      document.querySelector('#screen-symbols').value = payload.symbols.slice(0, 250).join(', ');
    }

    while (filterRows().length > 1) filterRows().at(-1)?.remove();
    let first = filterRows()[0];
    if (!first && filterTemplate) {
      first = filterTemplate.cloneNode(true);
      document.querySelector('#screen-filters').append(first);
      installRemoveButton(first);
    }
    if (!first) return;

    const requested = (Array.isArray(payload.filters) ? payload.filters : []).slice(0, 12);
    setFilterValues(first, requested[0] || {
      metric: 'revenue_growth_yoy',
      operator: 'gt',
      value: 0,
    });
    requested.slice(1).forEach(filter => addFilter(filter));
    updateRemoveButtons();
  }

  async function useMainWatchlist() {
    const lists = await api('/v1/watchlists');
    const main = lists.find(item => item.name === 'Main') || lists[0];
    if (!main?.symbols?.length) {
      document.querySelector('#screen-results').textContent = t('dashboard.no_symbols');
      return;
    }
    document.querySelector('#screen-symbols').value = main.symbols.slice(0, 250).join(', ');
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
    presets = await api('/v1/research-presets?kind=screener');
    renderPresets(selectedId);
  }

  async function savePreset() {
    if (!presetName || !presetStatus) return;
    const name = presetName.value.trim();
    if (!name) {
      presetStatus.textContent = xPresetNameRequired();
      return;
    }
    const saved = await api('/v1/research-presets', {
      method: 'POST',
      body: JSON.stringify({ name, kind: 'screener', payload: currentScreenPayload() }),
    });
    presetName.value = '';
    presetStatus.textContent = saved.name;
    await loadPresetCatalog(saved.id);
  }

  function xPresetNameRequired() {
    return localeTag.startsWith('ja') ? '設定名を入力してください。' : 'Enter a preset name.';
  }

  async function applySelectedPreset() {
    if (!presetSelect || !presetStatus) return;
    const selected = presets.find(item => String(item.id) === presetSelect.value);
    if (!selected) return;
    applyScreenPayload(selected.payload);
    presetStatus.textContent = selected.name;
  }

  async function deleteSelectedPreset() {
    if (!presetSelect || !presetStatus) return;
    const selected = presets.find(item => String(item.id) === presetSelect.value);
    if (!selected) return;
    await api(`/v1/research-presets/${selected.id}`, { method: 'DELETE' });
    presetStatus.textContent = selected.name;
    await loadPresetCatalog();
  }

  function applyStoredProposal() {
    if (!new URLSearchParams(location.search).has('proposal')) return;
    let proposal;
    try {
      proposal = JSON.parse(sessionStorage.getItem('yowayowa.screener.proposal') || 'null');
    } catch (_) {
      proposal = null;
    }
    sessionStorage.removeItem('yowayowa.screener.proposal');
    if (proposal) applyScreenPayload(proposal);
  }

  function symbolCell(symbol) {
    const content = `<strong>${escapeHtml(symbol)}</strong>`;
    if (appMode === 'public') return content;
    return `<a href="/instrument/${encodeURIComponent(symbol)}">${content}</a>`;
  }

  document.querySelector('#add-filter')?.addEventListener('click', () => addFilter());
  document.querySelector('#screen-use-watchlist')?.addEventListener('click', () => {
    useMainWatchlist().catch(error => {
      document.querySelector('#screen-results').textContent = error.message;
    });
  });
  document.querySelector('#screen-preset-save')?.addEventListener('click', () => {
    savePreset().catch(error => { if (presetStatus) presetStatus.textContent = error.message; });
  });
  document.querySelector('#screen-preset-load')?.addEventListener('click', () => {
    applySelectedPreset().catch(error => {
      if (presetStatus) presetStatus.textContent = error.message;
    });
  });
  document.querySelector('#screen-preset-delete')?.addEventListener('click', () => {
    deleteSelectedPreset().catch(error => {
      if (presetStatus) presetStatus.textContent = error.message;
    });
  });
  document.querySelectorAll('.screen-filter-row').forEach(installRemoveButton);
  updateRemoveButtons();
  applyStoredProposal();
  if (appMode === 'personal') {
    loadPresetCatalog().catch(error => {
      if (presetStatus) presetStatus.textContent = error.message;
    });
  }

  document.querySelector('#screen-form')?.addEventListener('submit', async event => {
    event.preventDefault();
    const target = document.querySelector('#screen-results');
    target.textContent = t('screener.evaluating');
    const { symbols, filters } = currentScreenPayload();
    try {
      const result = await api('/v1/screen', {
        method: 'POST',
        body: JSON.stringify({ symbols, filters }),
      });
      const metricKeys = [...new Set(result.rows.flatMap(row => Object.keys(row.metrics)))];
      const selected = filters.map(filter => filter.metric);
      const preferred = [
        ...selected,
        'revenue_growth_yoy',
        'operating_margin',
        'net_margin',
        'return_on_equity',
        'current_ratio',
        'free_cash_flow_margin',
        'free_cash_flow',
        'revenue',
        'net_income',
      ];
      const columns = [...new Set(preferred.filter(key => metricKeys.includes(key)))];
      target.innerHTML = `<table><thead><tr><th>${escapeHtml(t('common.symbol'))}</th><th>${escapeHtml(t('common.result'))}</th>${columns.map(key => `<th>${escapeHtml(metricLabel(key))}</th>`).join('')}</tr></thead><tbody>${result.rows.map(row => `
        <tr><td>${symbolCell(row.symbol)}</td><td class="${row.matched ? 'badge-ok' : 'badge-fail'}">${escapeHtml(row.matched ? t('common.match') : t('common.fail'))}</td>${columns.map(key => `<td>${escapeHtml(valueFmt(key, row.metrics[key]))}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
    } catch (error) {
      target.textContent = error.message;
    }
  });
})();
