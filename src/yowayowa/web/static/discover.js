(() => {
  const { api, escapeHtml, fmt, t, localeTag } = window.Yowayowa;
  let catalog = null;
  let strategies = [];
  let activeStrategy = null;
  let rows = [];
  const selected = new Set();

  const fieldLabel = (field) => field
    .replaceAll('.lasttwelvemonths', ' · TTM')
    .replaceAll('.quarterly', ' · Q')
    .replaceAll('_', ' ')
    .replaceAll('.', ' / ');

  const number = (value, percent = false) => {
    if (value === null || value === undefined || value === '') return '—';
    const n = Number(value);
    if (!Number.isFinite(n)) return String(value);
    if (percent) return new Intl.NumberFormat(localeTag, { style: 'percent', maximumFractionDigits: 2 }).format(n / 100);
    if (Math.abs(n) >= 1e9) return new Intl.NumberFormat(localeTag, { notation: 'compact', maximumFractionDigits: 2 }).format(n);
    return fmt.format(n);
  };

  const strategyName = (strategy) => localeTag.startsWith('ja') ? strategy.name_ja : strategy.name_en;
  const strategyDescription = (strategy) => localeTag.startsWith('ja') ? strategy.description_ja : strategy.description_en;

  function populateCatalog(data, builtinStrategies = []) {
    catalog = data;
    strategies = builtinStrategies;
    const preset = document.querySelector('#discover-preset');
    if (strategies.length) {
      const group = document.createElement('optgroup');
      group.label = localeTag.startsWith('ja') ? '組み込み戦略' : 'Built-in strategies';
      for (const strategy of strategies) {
        const option = document.createElement('option');
        option.value = `builtin:${strategy.id}`;
        option.textContent = strategyName(strategy);
        group.append(option);
      }
      preset.append(group);
    }
    if ((data.predefined || []).length) {
      const group = document.createElement('optgroup');
      group.label = localeTag.startsWith('ja') ? 'Yahoo既定スクリーナー' : 'Yahoo screeners';
      for (const name of data.predefined || []) {
        const option = document.createElement('option');
        option.value = name;
        option.textContent = name.replaceAll('_', ' ');
        group.append(option);
      }
      preset.append(group);
    }
    const region = document.querySelector('#discover-region');
    for (const code of data.regions || []) {
      const option = document.createElement('option');
      option.value = code;
      option.textContent = code.toUpperCase();
      region.append(option);
    }
    const sort = document.querySelector('#discover-sort');
    for (const [group, fields] of Object.entries(data.fields || {})) {
      const optgroup = document.createElement('optgroup');
      optgroup.label = group.replaceAll('_', ' ');
      for (const field of fields) {
        const option = document.createElement('option');
        option.value = field;
        option.textContent = fieldLabel(field);
        optgroup.append(option);
      }
      sort.append(optgroup);
    }
    document.querySelector('#discover-status').textContent = '';
    addFilter('intradaymarketcap', 'gt', '100000000');
  }

  function fieldOptions(select, selectedField = '') {
    for (const [group, fields] of Object.entries(catalog?.fields || {})) {
      const optgroup = document.createElement('optgroup');
      optgroup.label = group.replaceAll('_', ' ');
      for (const field of fields) {
        const option = document.createElement('option');
        option.value = field;
        option.textContent = fieldLabel(field);
        option.selected = field === selectedField;
        optgroup.append(option);
      }
      select.append(optgroup);
    }
  }

  function addFilter(field = '', operator = 'gt', value = '') {
    const container = document.querySelector('#discover-filters');
    const row = document.createElement('div');
    row.className = 'research-filter-row';
    const fieldWrap = document.createElement('label');
    fieldWrap.textContent = t('common.metric');
    const fieldSelect = document.createElement('select');
    fieldSelect.className = 'filter-field';
    fieldOptions(fieldSelect, field);
    fieldWrap.append(fieldSelect);
    const opWrap = document.createElement('label');
    opWrap.textContent = 'OP';
    const opSelect = document.createElement('select');
    opSelect.className = 'filter-operator';
    for (const op of catalog?.operators || ['eq', 'is-in', 'btwn', 'gt', 'lt', 'gte', 'lte']) {
      const option = document.createElement('option');
      option.value = op;
      option.textContent = op;
      option.selected = op === operator;
      opSelect.append(option);
    }
    opWrap.append(opSelect);
    const valueWrap = document.createElement('label');
    valueWrap.className = 'filter-value';
    valueWrap.textContent = 'VALUE';
    const input = document.createElement('input');
    input.value = Array.isArray(value) ? value.join(', ') : value;
    input.placeholder = operator === 'btwn' ? '10, 20' : '10';
    valueWrap.append(input);
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'ghost filter-remove';
    remove.textContent = '×';
    remove.addEventListener('click', () => row.remove());
    row.append(fieldWrap, opWrap, valueWrap, remove);
    container.append(row);
  }

  function parseValue(raw, operator) {
    const tokens = raw.split(',').map(item => item.trim()).filter(Boolean);
    const convert = (token) => {
      const n = Number(token);
      return Number.isFinite(n) && token !== '' ? n : token;
    };
    if (operator === 'btwn' || operator === 'is-in') return tokens.map(convert);
    return convert(tokens[0] ?? raw.trim());
  }

  function clearFilters() {
    document.querySelector('#discover-filters').replaceChildren();
  }

  function setStrategyNote(strategy) {
    const note = document.querySelector('#discover-strategy-note');
    if (!strategy) {
      note.hidden = true;
      note.textContent = '';
      return;
    }
    const boundary = localeTag.startsWith('ja')
      ? '投資有価証券を自動取得できない銘柄では、清原式ネットキャッシュ比率を投資有価証券抜きの下限として表示します。Yowayowa保守NC比率は常に同じ保守式で併記します。'
      : 'Where investment securities are unavailable, the Kiyohara net-cash ratio is shown as a lower bound excluding them. Yowayowa conservative NCR is always shown using the same conservative formula.';
    note.textContent = `${strategyDescription(strategy)} ${boundary}`;
    note.hidden = false;
  }

  function applyBuiltinStrategy(strategy) {
    activeStrategy = strategy;
    const discovery = strategy.discovery || {};
    const region = document.querySelector('#discover-region');
    region.value = strategy.default_region || '';
    clearFilters();
    for (const filter of discovery.filters || []) {
      addFilter(filter.field, filter.operator, filter.value);
    }
    document.querySelector('#discover-sort').value = discovery.sort_field || '';
    document.querySelector('#discover-order').value = discovery.sort_ascending ? 'asc' : 'desc';
    const size = String(discovery.size || 25);
    const sizeSelect = document.querySelector('#discover-size');
    if (![...sizeSelect.options].some(option => option.value === size)) {
      const option = document.createElement('option');
      option.value = size;
      option.textContent = size;
      sizeSelect.append(option);
    }
    sizeSelect.value = size;
    setStrategyNote(strategy);
  }

  function onPresetChange() {
    const value = document.querySelector('#discover-preset').value;
    if (value.startsWith('builtin:')) {
      const id = value.slice('builtin:'.length);
      const strategy = strategies.find(item => item.id === id);
      if (strategy) applyBuiltinStrategy(strategy);
      return;
    }
    activeStrategy = null;
    setStrategyNote(null);
  }

  function requestBody() {
    const preset = document.querySelector('#discover-preset').value;
    if (preset && !preset.startsWith('builtin:')) {
      return {
        predefined: preset,
        size: Number(document.querySelector('#discover-size').value),
        offset: 0,
      };
    }
    const filters = [...document.querySelectorAll('.research-filter-row')].map(row => {
      const operator = row.querySelector('.filter-operator').value;
      return {
        field: row.querySelector('.filter-field').value,
        operator,
        value: parseValue(row.querySelector('.filter-value input').value, operator),
      };
    });
    const region = document.querySelector('#discover-region').value;
    if (activeStrategy?.region_required && !region) {
      throw new Error(localeTag.startsWith('ja')
        ? 'この戦略では市場地域を1つ選択してください。'
        : 'Choose one market region for this strategy.');
    }
    if (region) filters.unshift({ field: 'region', operator: 'is-in', value: [region] });
    return {
      filters,
      sort_field: document.querySelector('#discover-sort').value || null,
      sort_ascending: document.querySelector('#discover-order').value === 'asc',
      size: Number(document.querySelector('#discover-size').value),
      offset: 0,
    };
  }

  const cell = (row, keys) => {
    for (const key of keys) {
      if (row[key] !== undefined && row[key] !== null) return row[key];
    }
    return null;
  };

  const conservativeRatioText = (evaluation) => {
    const value = evaluation?.yowayowa_conservative_net_cash_ratio;
    if (value === null || value === undefined) return '—';
    return `${Number(value).toFixed(2)}×`;
  };

  const ratioText = (evaluation) => {
    const value = evaluation?.net_cash_ratio;
    if (value === null || value === undefined) return '—';
    const prefix = evaluation.net_cash_ratio_is_lower_bound ? '≥' : '';
    return `${prefix}${Number(value).toFixed(2)}×`;
  };

  const cashNeutralPeText = (evaluation) => {
    if (!evaluation) return '—';
    if (evaluation.deep_value_net_cash && evaluation.cash_neutral_pe === null) {
      return localeTag.startsWith('ja') ? 'NCR≥1' : 'NCR≥1';
    }
    const value = evaluation.cash_neutral_pe;
    if (value === null || value === undefined) return '—';
    const prefix = evaluation.cash_neutral_pe_is_upper_bound ? '≤' : '';
    return `${prefix}${Number(value).toFixed(2)}×`;
  };

  function attachStrategyEvaluation(data, evaluation) {
    const bySymbol = new Map((evaluation?.evaluations || []).map(item => [String(item.symbol).toUpperCase(), item]));
    data.quotes = (data.quotes || []).map(row => ({
      ...row,
      __strategy: bySymbol.get(String(row.symbol || '').toUpperCase()) || null,
    }));
    data.quotes.sort((left, right) => {
      const a = left.__strategy;
      const b = right.__strategy;
      const ar = a?.net_cash_ratio;
      const br = b?.net_cash_ratio;
      if (ar !== null && ar !== undefined && br !== null && br !== undefined && ar !== br) return br - ar;
      if (ar !== null && ar !== undefined) return -1;
      if (br !== null && br !== undefined) return 1;
      const ac = a?.cash_neutral_pe;
      const bc = b?.cash_neutral_pe;
      if (ac !== null && ac !== undefined && bc !== null && bc !== undefined && ac !== bc) return ac - bc;
      return String(left.symbol || '').localeCompare(String(right.symbol || ''));
    });
  }

  async function evaluateActiveStrategy(data) {
    if (!activeStrategy) return null;
    const candidates = [];
    for (const row of (data.quotes || []).slice(0, 50)) {
      const symbol = String(row.symbol || '').trim();
      const marketCap = cell(row, ['marketCap', 'intradaymarketcap']);
      if (!symbol || !Number.isFinite(Number(marketCap)) || Number(marketCap) <= 0) continue;
      const pe = cell(row, ['trailingPE', 'peratio.lasttwelvemonths']);
      candidates.push({
        symbol,
        market_cap: Number(marketCap),
        pe_ratio: Number.isFinite(Number(pe)) && Number(pe) > 0 ? Number(pe) : null,
      });
    }
    if (!candidates.length) return null;
    return api(`/v1/strategy-presets/${encodeURIComponent(activeStrategy.id)}/evaluate`, {
      method: 'POST',
      body: JSON.stringify({ candidates }),
    });
  }

  function render(data) {
    rows = data.quotes || [];
    selected.clear();
    updateActions();
    const target = document.querySelector('#discover-results');
    if (!rows.length) {
      target.innerHTML = `<span class="muted">${escapeHtml(t('discover.none'))}</span>`;
      return;
    }
    const strategyHeaders = activeStrategy
      ? `<th>${escapeHtml(localeTag.startsWith('ja') ? 'Yowayowa保守NC比率' : 'Yowayowa conservative NCR')}</th><th>${escapeHtml(localeTag.startsWith('ja') ? '清原NC比率' : 'Kiyohara NCR')}</th><th>${escapeHtml(localeTag.startsWith('ja') ? 'キャッシュ中立PER' : 'Cash-neutral P/E')}</th>`
      : '';
    const tableRows = rows.map((row, index) => {
      const symbol = String(row.symbol || '');
      const name = cell(row, ['shortName', 'longName', 'displayName']) || symbol;
      const price = cell(row, ['regularMarketPrice', 'intradayprice']);
      const change = cell(row, ['regularMarketChangePercent', 'percentchange']);
      const marketCap = cell(row, ['marketCap', 'intradaymarketcap']);
      const trailingPE = cell(row, ['trailingPE', 'peratio.lasttwelvemonths']);
      const forwardPE = cell(row, ['forwardPE']);
      const roe = cell(row, ['returnOnEquity', 'returnonequity.lasttwelvemonths']);
      const revenueGrowth = cell(row, ['revenueGrowth', 'totalrevenues1yrgrowth.lasttwelvemonths']);
      const shortFloat = cell(row, ['shortPercentOfFloat', 'short_percentage_of_float.value']);
      const strategyCells = activeStrategy
        ? `<td>${escapeHtml(conservativeRatioText(row.__strategy))}</td><td>${escapeHtml(ratioText(row.__strategy))}</td><td>${escapeHtml(cashNeutralPeText(row.__strategy))}</td>`
        : '';
      return `<tr>
        <td><input type="checkbox" class="discover-select" data-index="${index}" aria-label="${escapeHtml(symbol)}"></td>
        <td><a href="/instrument/${encodeURIComponent(symbol)}"><strong>${escapeHtml(symbol)}</strong></a></td>
        <td>${escapeHtml(name)}</td>
        <td>${escapeHtml(row.exchange || row.fullExchangeName || '—')}</td>
        <td>${escapeHtml(number(price))}</td>
        <td>${escapeHtml(number(change, true))}</td>
        <td>${escapeHtml(number(marketCap))}</td>
        <td>${escapeHtml(number(trailingPE))}</td>
        ${strategyCells}
        <td>${escapeHtml(number(forwardPE))}</td>
        <td>${escapeHtml(number(roe, true))}</td>
        <td>${escapeHtml(number(revenueGrowth, true))}</td>
        <td>${escapeHtml(number(shortFloat, true))}</td>
        <td><a href="/research/${encodeURIComponent(symbol)}">${escapeHtml(t('research.title'))}</a></td>
      </tr>`;
    }).join('');
    target.innerHTML = `<div class="research-table-wrap"><table class="research-table"><thead><tr>
      <th></th><th>Symbol</th><th>Name</th><th>Exchange</th><th>Price</th><th>Change</th><th>Market cap</th><th>P/E</th>${strategyHeaders}<th>Fwd P/E</th><th>ROE</th><th>Revenue growth</th><th>Short float</th><th></th>
    </tr></thead><tbody>${tableRows}</tbody></table></div>`;
    target.querySelectorAll('.discover-select').forEach(input => {
      input.addEventListener('change', () => {
        const row = rows[Number(input.dataset.index)];
        const symbol = String(row?.symbol || '');
        if (!symbol) return;
        if (input.checked) selected.add(symbol); else selected.delete(symbol);
        updateActions();
      });
    });
    const provenance = data.provenance || {};
    const source = provenance.source ? `${provenance.source} · ${provenance.license_class || ''}` : '';
    const strategy = activeStrategy ? ` · ${strategyName(activeStrategy)}` : '';
    document.querySelector('#discover-provenance').textContent = `${source}${strategy}`.replace(/^ · /, '');
    document.querySelector('#discover-export').disabled = false;
  }

  function updateActions() {
    const count = selected.size;
    document.querySelector('#discover-compare').disabled = count < 2;
    document.querySelector('#discover-watchlist').disabled = count < 1;
    document.querySelector('#discover-ai').disabled = count < 1;
  }

  async function run(event) {
    event?.preventDefault();
    const status = document.querySelector('#discover-status');
    status.textContent = t('discover.loading');
    try {
      const data = await api('/v1/discover/screen', {
        method: 'POST',
        body: JSON.stringify(requestBody()),
      });
      if (activeStrategy) {
        status.textContent = localeTag.startsWith('ja') ? '財務を追加評価中…' : 'Evaluating normalized financials…';
        const evaluation = await evaluateActiveStrategy(data);
        if (evaluation) attachStrategyEvaluation(data, evaluation);
        const unavailable = Object.keys(evaluation?.errors || {}).length;
        status.textContent = `${data.total ?? data.quotes?.length ?? 0}${unavailable ? ` · ${unavailable} unavailable` : ''}`;
      } else {
        status.textContent = `${data.total ?? data.quotes?.length ?? 0}`;
      }
      render(data);
    } catch (error) {
      status.textContent = error.message;
    }
  }

  async function addSelectedToWatchlist() {
    const lists = await api('/v1/watchlists');
    const main = lists.find(item => item.name === 'Main') || lists[0];
    if (!main) return;
    await api(`/v1/watchlists/${main.id}/symbols`, {
      method: 'POST',
      body: JSON.stringify([...selected]),
    });
    document.querySelector('#discover-status').textContent = `${selected.size} ✓`;
  }

  function exportCsv() {
    if (!rows.length) return;
    const exportRows = rows.map(row => {
      const copy = { ...row };
      if (copy.__strategy) {
        copy.yowayowa_conservative_net_cash = copy.__strategy.yowayowa_conservative_net_cash;
        copy.yowayowa_conservative_net_cash_ratio = copy.__strategy.yowayowa_conservative_net_cash_ratio;
        copy.strategy_net_cash_ratio = copy.__strategy.net_cash_ratio;
        copy.strategy_net_cash_ratio_is_lower_bound = copy.__strategy.net_cash_ratio_is_lower_bound;
        copy.strategy_cash_neutral_pe = copy.__strategy.cash_neutral_pe;
        copy.strategy_cash_neutral_pe_is_upper_bound = copy.__strategy.cash_neutral_pe_is_upper_bound;
        copy.strategy_formula_basis = copy.__strategy.basis;
      }
      delete copy.__strategy;
      return copy;
    });
    const keys = [...new Set(exportRows.flatMap(row => Object.keys(row)))];
    const quote = value => `"${String(value ?? '').replaceAll('"', '""')}"`;
    const csv = [keys.map(quote).join(','), ...exportRows.map(row => keys.map(key => quote(row[key])).join(','))].join('\n');
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = `yowayowa-discover-${new Date().toISOString().slice(0, 10)}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  }

  document.addEventListener('DOMContentLoaded', async () => {
    document.querySelector('#discover-form')?.addEventListener('submit', run);
    document.querySelector('#discover-preset')?.addEventListener('change', onPresetChange);
    document.querySelector('#discover-add-filter')?.addEventListener('click', () => addFilter());
    document.querySelector('#discover-compare')?.addEventListener('click', () => {
      location.assign(`/compare?symbols=${encodeURIComponent([...selected].join(','))}`);
    });
    document.querySelector('#discover-ai')?.addEventListener('click', () => {
      location.assign(`/ai?symbols=${encodeURIComponent([...selected].join(','))}`);
    });
    document.querySelector('#discover-watchlist')?.addEventListener('click', () => addSelectedToWatchlist().catch(error => {
      document.querySelector('#discover-status').textContent = error.message;
    }));
    document.querySelector('#discover-export')?.addEventListener('click', exportCsv);
    try {
      const [marketCatalog, builtinStrategies] = await Promise.all([
        api('/v1/discover/catalog'),
        api('/v1/strategy-presets'),
      ]);
      populateCatalog(marketCatalog, builtinStrategies);
    } catch (error) {
      document.querySelector('#discover-status').textContent = error.message;
    }
  });
})();