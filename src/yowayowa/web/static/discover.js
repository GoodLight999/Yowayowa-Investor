(() => {
  const { api, escapeHtml, fmt, t, localeTag } = window.Yowayowa;
  let catalog = null;
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

  function populateCatalog(data) {
    catalog = data;
    const preset = document.querySelector('#discover-preset');
    for (const name of data.predefined || []) {
      const option = document.createElement('option');
      option.value = name;
      option.textContent = name.replaceAll('_', ' ');
      preset.append(option);
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
    input.value = value;
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

  function requestBody() {
    const preset = document.querySelector('#discover-preset').value;
    if (preset) {
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

  function render(data) {
    rows = data.quotes || [];
    selected.clear();
    updateActions();
    const target = document.querySelector('#discover-results');
    if (!rows.length) {
      target.innerHTML = `<span class="muted">${escapeHtml(t('discover.none'))}</span>`;
      return;
    }
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
      return `<tr>
        <td><input type="checkbox" class="discover-select" data-index="${index}" aria-label="${escapeHtml(symbol)}"></td>
        <td><a href="/instrument/${encodeURIComponent(symbol)}"><strong>${escapeHtml(symbol)}</strong></a></td>
        <td>${escapeHtml(name)}</td>
        <td>${escapeHtml(row.exchange || row.fullExchangeName || '—')}</td>
        <td>${escapeHtml(number(price))}</td>
        <td>${escapeHtml(number(change, true))}</td>
        <td>${escapeHtml(number(marketCap))}</td>
        <td>${escapeHtml(number(trailingPE))}</td>
        <td>${escapeHtml(number(forwardPE))}</td>
        <td>${escapeHtml(number(roe, true))}</td>
        <td>${escapeHtml(number(revenueGrowth, true))}</td>
        <td>${escapeHtml(number(shortFloat, true))}</td>
        <td><a href="/research/${encodeURIComponent(symbol)}">${escapeHtml(t('research.title'))}</a></td>
      </tr>`;
    }).join('');
    target.innerHTML = `<div class="research-table-wrap"><table class="research-table"><thead><tr>
      <th></th><th>Symbol</th><th>Name</th><th>Exchange</th><th>Price</th><th>Change</th><th>Market cap</th><th>P/E</th><th>Fwd P/E</th><th>ROE</th><th>Revenue growth</th><th>Short float</th><th></th>
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
    document.querySelector('#discover-provenance').textContent = provenance.source
      ? `${provenance.source} · ${provenance.license_class || ''}` : '';
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
      render(data);
      status.textContent = `${data.total ?? data.quotes?.length ?? 0}`;
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
    const keys = [...new Set(rows.flatMap(row => Object.keys(row)))];
    const quote = value => `"${String(value ?? '').replaceAll('"', '""')}"`;
    const csv = [keys.map(quote).join(','), ...rows.map(row => keys.map(key => quote(row[key])).join(','))].join('\n');
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = `yowayowa-discover-${new Date().toISOString().slice(0, 10)}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  }

  document.addEventListener('DOMContentLoaded', async () => {
    document.querySelector('#discover-form')?.addEventListener('submit', run);
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
      populateCatalog(await api('/v1/discover/catalog'));
    } catch (error) {
      document.querySelector('#discover-status').textContent = error.message;
    }
  });
})();
