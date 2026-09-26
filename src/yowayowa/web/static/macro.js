(() => {
  const { api, escapeHtml, fmt, t } = window.Yowayowa;
  let currentEstatId = null;
  let currentEstatDimensions = [];
  const estatLang = document.documentElement.lang === 'ja' ? 'J' : 'E';

  function seriesChart(observations) {
    const points = (observations || [])
      .map(item => ({ date: item.date, value: Number(item.value) }))
      .filter(item => item.date && Number.isFinite(item.value));
    if (points.length < 2) return '';
    const values = points.map(item => item.value);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min || 1;
    const coords = points.map((item, index) => {
      const x = points.length === 1 ? 0 : (index / (points.length - 1)) * 1000;
      const y = 210 - ((item.value - min) / span) * 190;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    }).join(' ');
    return `<svg class="macro-chart" viewBox="0 0 1000 220" preserveAspectRatio="none" aria-hidden="true"><polyline points="${coords}"></polyline></svg>
      <div class="portfolio-history-meta"><span>${escapeHtml(points[0].date)}</span><span>${escapeHtml(points.at(-1).date)}</span></div>`;
  }

  function metadata(object) {
    if (!object) return '';
    const preferred = [
      'title', 'id', 'units', 'frequency', 'seasonal_adjustment',
      'observation_start', 'observation_end', 'last_updated', 'notes',
    ];
    const rows = preferred.filter(key => object[key] !== undefined).map(key => `
      <dt>${escapeHtml(key.replaceAll('_', ' '))}</dt><dd>${escapeHtml(String(object[key] ?? '—'))}</dd>`).join('');
    return `<dl class="research-kv">${rows}</dl>`;
  }

  function provenance(data) {
    const target = document.querySelector('#macro-provenance');
    if (!target) return;
    const item = data?.provenance;
    if (!item) {
      target.textContent = '';
      return;
    }
    const parts = [item.source, item.license_class, item.retrieved_at].filter(Boolean);
    target.textContent = parts.join(' · ');
  }

  function renderSeries(data, observations, seriesMetadata = null) {
    const target = document.querySelector('#macro-series');
    const latest = [...observations]
      .reverse()
      .find(item => Number.isFinite(Number(item.value)) && item.date);
    const title = data.title || seriesMetadata?.title || data.series_id || '';
    const unit = data.unit || seriesMetadata?.units || '';
    target.innerHTML = `${title ? `<h3>${escapeHtml(title)}</h3>` : ''}
      ${latest ? `<div class="metrics-strip"><div class="metric-card"><span>Latest</span><strong>${escapeHtml(fmt.format(Number(latest.value)))}</strong><small>${escapeHtml([latest.date, unit].filter(Boolean).join(' · '))}</small></div></div>` : ''}
      ${seriesChart(observations)}${metadata(seriesMetadata)}`;
    provenance(data);
  }

  async function loadBlsSeries(id) {
    const target = document.querySelector('#macro-series');
    target.textContent = t('macro.loading');
    document.querySelector('#macro-series-id').textContent = id;
    try {
      const data = await api(`/v1/macro/bls/${encodeURIComponent(id)}`);
      renderSeries(data, data.observations || [], {
        title: data.title,
        units: data.unit,
        seasonal_adjustment: data.seasonal_adjustment,
      });
    } catch (error) {
      target.textContent = error.message;
    }
  }

  async function loadBlsCatalog() {
    const target = document.querySelector('#bls-catalog');
    if (!target) return;
    target.textContent = t('macro.loading');
    try {
      const data = await api('/v1/macro/bls/catalog');
      const rows = data.series || [];
      target.innerHTML = rows.length ? rows.map(row => `<button class="macro-result bls-result" type="button" data-id="${escapeHtml(row.series_id || '')}">
        <strong>${escapeHtml(row.series_id || '')} · ${escapeHtml(row.title || '')}</strong>
        <small>${escapeHtml([row.category, row.unit, row.seasonal_adjustment].filter(Boolean).join(' · '))}</small>
      </button>`).join('') : '<span class="muted">—</span>';
      target.querySelectorAll('.bls-result').forEach(button => {
        button.addEventListener('click', () => loadBlsSeries(button.dataset.id));
      });
      provenance(data);
    } catch (error) {
      target.textContent = error.message;
    }
  }

  function beaDate(timePeriod) {
    const quarter = /^(\d{4})Q([1-4])$/.exec(timePeriod || '');
    if (quarter) {
      const month = 1 + (Number(quarter[2]) - 1) * 3;
      return `${quarter[1]}-${String(month).padStart(2, '0')}-01`;
    }
    const month = /^(\d{4})M(\d{2})$/.exec(timePeriod || '');
    if (month && Number(month[2]) >= 1 && Number(month[2]) <= 12) {
      return `${month[1]}-${month[2]}-01`;
    }
    if (/^\d{4}$/.test(timePeriod || '')) return `${timePeriod}-01-01`;
    return null;
  }

  async function loadBeaTable(tableName, frequency, lineNumber, title) {
    const target = document.querySelector('#macro-series');
    target.textContent = t('macro.loading');
    document.querySelector('#macro-series-id').textContent = tableName;
    const params = new URLSearchParams({ frequency });
    if (lineNumber) params.set('line_number', lineNumber);
    try {
      const data = await api(`/v1/macro/bea/nipa/${encodeURIComponent(tableName)}?${params}`);
      const rows = data.rows || [];
      const observations = rows.map(row => ({
        date: beaDate(row.time_period),
        value: row.value,
      })).filter(row => row.date && Number.isFinite(Number(row.value)));
      const first = rows.find(row => row.line_description) || {};
      const resolvedTitle = title || first.line_description || tableName;
      const unit = first.unit || '';
      renderSeries(
        { title: resolvedTitle, unit, provenance: data.provenance },
        observations,
        {
          title: resolvedTitle,
          units: unit,
          frequency: data.frequency,
          notes: (data.notes || []).join(' '),
        },
      );
    } catch (error) {
      target.textContent = error.message;
    }
  }

  async function loadBeaCatalog() {
    const target = document.querySelector('#bea-catalog');
    if (!target) return;
    target.textContent = t('macro.loading');
    try {
      const data = await api('/v1/macro/bea/nipa/catalog');
      const rows = data.tables || [];
      target.innerHTML = rows.length ? rows.map(row => `<button class="macro-result bea-result" type="button"
        data-table="${escapeHtml(row.table_name || '')}"
        data-frequency="${escapeHtml(row.default_frequency || 'Q')}"
        data-line="${escapeHtml(String(row.default_line_number || ''))}"
        data-title="${escapeHtml(row.title || '')}">
        <strong>${escapeHtml(row.table_name || '')} · ${escapeHtml(row.title || '')}</strong>
        <small>${escapeHtml([row.category, row.default_frequency].filter(Boolean).join(' · '))}</small>
      </button>`).join('') : '<span class="muted">—</span>';
      target.querySelectorAll('.bea-result').forEach(button => {
        button.addEventListener('click', () => loadBeaTable(
          button.dataset.table,
          button.dataset.frequency,
          button.dataset.line,
          button.dataset.title,
        ));
      });
      provenance(data);
    } catch (error) {
      target.textContent = error.message;
    }
  }

  function estatItemLookup() {
    const lookup = new Map();
    for (const dimension of currentEstatDimensions) {
      const items = new Map((dimension.items || []).map(item => [String(item.code), item.name]));
      lookup.set(dimension.id, items);
    }
    return lookup;
  }

  function estatDimensionSummary(item, lookup) {
    return Object.entries(item.dimensions || {}).map(([key, code]) => {
      const name = lookup.get(key)?.get(String(code));
      return name ? `${key}=${code} · ${name}` : `${key}=${code}`;
    }).join(' / ');
  }

  function renderEstatData(data, append = false) {
    const target = document.querySelector('#estat-data');
    const status = document.querySelector('#estat-data-status');
    const lookup = estatItemLookup();
    const rows = (data.values || []).map(item => `<tr>
      <td>${escapeHtml(estatDimensionSummary(item, lookup) || '—')}</td>
      <td class="numeric">${escapeHtml(item.value ?? '—')}</td>
      <td>${escapeHtml(item.unit || '—')}</td>
      <td>${escapeHtml(item.annotation || '—')}</td>
    </tr>`).join('');
    const table = rows
      ? `<div class="research-table-wrap"><table class="research-table"><thead><tr><th>Dimensions</th><th>Value</th><th>Unit</th><th>Annotation</th></tr></thead><tbody>${rows}</tbody></table></div>`
      : '<span class="muted">—</span>';
    if (append) {
      target.querySelector('.estat-next')?.remove();
      target.insertAdjacentHTML('beforeend', table);
    } else {
      target.innerHTML = table;
    }
    if (data.next_key) {
      target.insertAdjacentHTML(
        'beforeend',
        `<button class="ghost estat-next" type="button" data-next="${escapeHtml(String(data.next_key))}">Next</button>`,
      );
      target.querySelector('.estat-next')?.addEventListener('click', event => {
        loadEstatData(Number(event.currentTarget.dataset.next), true);
      });
    }
    status.textContent = t('macro.estat_facts', {
      shown: data.values?.length || 0,
      total: data.total_number ?? 0,
    });
    provenance(data);
  }

  function estatFilterParams(startPosition = null) {
    const params = new URLSearchParams({ lang: estatLang, limit: '500' });
    document.querySelectorAll('[data-estat-dimension]').forEach(input => {
      const value = input.value.trim();
      if (value) params.append('filter', `${input.dataset.estatDimension}=${value}`);
    });
    if (startPosition) params.set('start_position', String(startPosition));
    return params;
  }

  async function loadEstatData(startPosition = null, append = false) {
    if (!currentEstatId) return;
    const target = document.querySelector('#estat-data');
    const status = document.querySelector('#estat-data-status');
    if (!append) target.textContent = t('macro.loading');
    try {
      const params = estatFilterParams(startPosition);
      const data = await api(`/v1/macro/estat/${encodeURIComponent(currentEstatId)}/data?${params}`);
      renderEstatData(data, append);
    } catch (error) {
      if (!append) target.textContent = '—';
      status.textContent = error.message;
    }
  }

  function renderEstatMetadata(data) {
    currentEstatId = data.stats_data_id;
    currentEstatDimensions = data.dimensions || [];
    const target = document.querySelector('#estat-filters');
    const loadButton = document.querySelector('#estat-load-data');
    const title = data.table?.title || data.stats_data_id;
    const fields = currentEstatDimensions.map((dimension, index) => {
      const datalistId = `estat-dimension-${index}`;
      const items = dimension.items || [];
      const options = items.slice(0, 500).map(item => (
        `<option value="${escapeHtml(String(item.code))}">${escapeHtml(item.name || '')}</option>`
      )).join('');
      const examples = items.slice(0, 4).map(item => `${item.code}=${item.name}`).join(' · ');
      return `<div class="field">
        <label for="estat-filter-${index}">${escapeHtml(dimension.id)} · ${escapeHtml(dimension.name || '')}</label>
        <input id="estat-filter-${index}" data-estat-dimension="${escapeHtml(dimension.id)}" list="${datalistId}" autocomplete="off">
        <datalist id="${datalistId}">${options}</datalist>
        <small class="muted">${escapeHtml(String(items.length))} codes${examples ? ` · ${escapeHtml(examples)}` : ''}</small>
      </div>`;
    }).join('');
    target.innerHTML = `<strong>${escapeHtml(data.stats_data_id)} · ${escapeHtml(title)}</strong>${fields || '<span class="muted">—</span>'}`;
    loadButton.hidden = false;
    document.querySelector('#estat-data').innerHTML = '<span class="muted">—</span>';
    document.querySelector('#estat-data-status').textContent = '';
    provenance(data);
  }

  async function loadEstatMetadata(statsDataId) {
    const target = document.querySelector('#estat-filters');
    target.textContent = t('macro.loading');
    try {
      renderEstatMetadata(
        await api(`/v1/macro/estat/${encodeURIComponent(statsDataId)}/meta?lang=${estatLang}`),
      );
    } catch (error) {
      target.textContent = error.message;
      document.querySelector('#estat-load-data').hidden = true;
    }
  }

  async function searchEstat(event) {
    event.preventDefault();
    const query = document.querySelector('#estat-query')?.value.trim();
    if (!query) return;
    const target = document.querySelector('#estat-results');
    const status = document.querySelector('#estat-status');
    target.textContent = t('macro.loading');
    status.textContent = '';
    try {
      const data = await api(
        `/v1/macro/estat/tables?q=${encodeURIComponent(query)}&lang=${estatLang}&limit=50`,
      );
      const rows = data.tables || [];
      target.innerHTML = rows.length ? rows.map(row => `<button class="macro-result estat-result" type="button" data-id="${escapeHtml(row.stats_data_id || '')}">
        <strong>${escapeHtml(row.stats_data_id || '')} · ${escapeHtml(row.title || '')}</strong>
        <small>${escapeHtml([row.gov_org, row.stat_name, row.cycle, row.updated_at].filter(Boolean).join(' · '))}</small>
      </button>`).join('') : '<span class="muted">—</span>';
      target.querySelectorAll('.estat-result').forEach(button => {
        button.addEventListener('click', () => loadEstatMetadata(button.dataset.id));
      });
      status.textContent = t('macro.estat_count', { count: data.matched_count ?? rows.length });
      provenance(data);
    } catch (error) {
      target.textContent = error.message.includes('ESTAT_APP_ID') ? t('macro.estat_key') : error.message;
      status.textContent = '';
    }
  }

  async function loadFredSeries(id) {
    const target = document.querySelector('#macro-series');
    const status = document.querySelector('#macro-status');
    target.textContent = t('macro.loading');
    document.querySelector('#macro-series-id').textContent = id;
    try {
      const data = await api(`/v1/macro/fred/${encodeURIComponent(id)}?limit=5000`);
      renderSeries(data, data.observations || [], data.metadata);
      if (status) status.textContent = '';
    } catch (error) {
      target.textContent = error.message;
      if (status) {
        status.textContent = error.message.includes('FRED') ? t('macro.requires_key') : error.message;
      }
    }
  }

  async function searchFred(event) {
    event.preventDefault();
    const query = document.querySelector('#macro-query')?.value.trim();
    if (!query) return;
    const target = document.querySelector('#macro-results');
    const status = document.querySelector('#macro-status');
    target.textContent = t('macro.loading');
    try {
      const data = await api(`/v1/macro/fred/search?q=${encodeURIComponent(query)}&limit=50`);
      const rows = data.series || [];
      target.innerHTML = rows.length ? rows.map(row => `<button class="macro-result" type="button" data-id="${escapeHtml(row.id || '')}">
        <strong>${escapeHtml(row.id || '')} · ${escapeHtml(row.title || '')}</strong>
        <small>${escapeHtml([row.frequency, row.units, row.seasonal_adjustment].filter(Boolean).join(' · '))}</small>
        <small>${escapeHtml(row.notes || '')}</small>
      </button>`).join('') : '<span class="muted">—</span>';
      target.querySelectorAll('.macro-result').forEach(button => {
        button.addEventListener('click', () => loadFredSeries(button.dataset.id));
      });
      status.textContent = `${data.count ?? rows.length}`;
    } catch (error) {
      target.textContent = error.message;
      status.textContent = error.message.includes('FRED') ? t('macro.requires_key') : error.message;
    }
  }

  async function releases() {
    const target = document.querySelector('#macro-releases');
    if (!target) return;
    target.textContent = t('macro.loading');
    try {
      const data = await api('/v1/macro/fred/releases?limit=100');
      const rows = data.release_dates || [];
      target.innerHTML = rows.length ? `<div class="research-table-wrap"><table class="research-table"><thead><tr><th>Date</th><th>Release</th></tr></thead><tbody>${rows.map(row => `<tr><td>${escapeHtml(row.date || row.release_date || '—')}</td><td>${escapeHtml(row.name || '—')}</td></tr>`).join('')}</tbody></table></div>` : '<span class="muted">—</span>';
    } catch (error) {
      target.textContent = error.message.includes('FRED') ? t('macro.requires_key') : error.message;
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    loadBlsCatalog();
    loadBeaCatalog();
    document.querySelector('#estat-search-form')?.addEventListener('submit', searchEstat);
    document.querySelector('#estat-filter-form')?.addEventListener('submit', event => {
      event.preventDefault();
      loadEstatData();
    });
    document.querySelector('#macro-search-form')?.addEventListener('submit', searchFred);
    document.querySelector('#macro-releases-button')?.addEventListener('click', releases);
  });
})();
