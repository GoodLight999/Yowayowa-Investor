(() => {
  const { api, escapeHtml, localeTag, t } = window.Yowayowa;
  const sources = document.querySelector('#composer-sources');
  const formulas = document.querySelector('#composer-formulas');
  const errors = document.querySelector('#composer-errors');
  const provenance = document.querySelector('#composer-provenance');
  const status = document.querySelector('#composer-status');
  const chartHost = document.querySelector('#composer-chart');
  const presetSelect = document.querySelector('#chart-preset-select');
  const presetName = document.querySelector('#chart-preset-name');
  const presetStatus = document.querySelector('#chart-preset-status');
  let chart = null;
  let sourceCounter = 1;
  let formulaCounter = 1;
  let presets = [];

  function field(label, control) {
    return `<label><span>${escapeHtml(label)}</span>${control}</label>`;
  }

  function sourceRow(initial = {}) {
    const row = document.createElement('div');
    row.className = 'composer-row';
    row.dataset.period = initial.period || '5y';
    const id = initial.id || `s${sourceCounter++}`;
    row.innerHTML = `
      ${field(t('chart.source_type'), `<select class="composer-source-type"><option value="price">${escapeHtml(t('chart.price'))}</option><option value="fundamental">${escapeHtml(t('chart.fundamental'))}</option><option value="fred">${escapeHtml(t('chart.fred'))}</option></select>`)}
      ${field(t('chart.id'), `<input class="composer-source-id" value="${escapeHtml(id)}" maxlength="32">`)}
      ${field(t('chart.symbol_series'), `<input class="composer-source-identifier" maxlength="64">`)}
      ${field(t('chart.metric'), '<input class="composer-source-metric" maxlength="64">')}
      ${field(t('chart.label'), '<input class="composer-source-label" maxlength="120">')}
      <button class="ghost composer-remove" type="button">${escapeHtml(t('common.remove'))}</button>`;
    const type = row.querySelector('.composer-source-type');
    const identifier = row.querySelector('.composer-source-identifier');
    const metric = row.querySelector('.composer-source-metric');
    const label = row.querySelector('.composer-source-label');
    type.value = initial.source || 'price';
    identifier.value = initial.series_id || initial.symbol || initial.identifier || '';
    metric.value = initial.metric || '';
    label.value = initial.label || '';

    function sync() {
      const kind = type.value;
      metric.disabled = kind !== 'fundamental';
      metric.placeholder = kind === 'fundamental' ? t('chart.metric_hint') : '—';
      identifier.placeholder = kind === 'fred' ? t('chart.fred_hint') : t('chart.price_hint');
      updateFormulaSources();
    }
    type.addEventListener('change', sync);
    row.querySelector('.composer-source-id').addEventListener('input', updateFormulaSources);
    row.querySelector('.composer-remove').addEventListener('click', () => {
      row.remove();
      updateFormulaSources();
    });
    sync();
    return row;
  }

  function formulaRow(initial = {}) {
    const row = document.createElement('div');
    row.className = 'composer-row formula';
    const id = initial.id || `f${formulaCounter++}`;
    row.innerHTML = `
      ${field(t('chart.kind'), `<select class="composer-formula-kind"><option value="ratio">${escapeHtml(t('chart.ratio'))}</option><option value="spread">${escapeHtml(t('chart.spread'))}</option><option value="rolling_correlation">${escapeHtml(t('chart.correlation'))}</option></select>`)}
      ${field(t('chart.id'), `<input class="composer-formula-id" value="${escapeHtml(id)}" maxlength="32">`)}
      ${field(t('chart.left'), '<select class="composer-formula-left"></select>')}
      ${field(t('chart.right'), '<select class="composer-formula-right"></select>')}
      ${field(t('chart.window'), '<input class="composer-formula-window" type="number" min="2" max="500" value="60">')}
      ${field(t('chart.label'), '<input class="composer-formula-label" maxlength="120">')}
      <button class="ghost composer-remove" type="button">${escapeHtml(t('common.remove'))}</button>`;
    row.querySelector('.composer-formula-kind').value = initial.kind || 'ratio';
    row.querySelector('.composer-formula-window').value = String(initial.window || 60);
    row.querySelector('.composer-formula-label').value = initial.label || '';
    row.querySelector('.composer-remove').addEventListener('click', () => row.remove());
    formulas.append(row);
    updateFormulaSources();
    if (initial.left) row.querySelector('.composer-formula-left').value = initial.left;
    if (initial.right) row.querySelector('.composer-formula-right').value = initial.right;
    return row;
  }

  function sourceIds() {
    return [...sources.querySelectorAll('.composer-source-id')]
      .map(input => input.value.trim())
      .filter(Boolean);
  }

  function updateFormulaSources() {
    const ids = sourceIds();
    formulas.querySelectorAll('.composer-formula-left, .composer-formula-right').forEach(select => {
      const previous = select.value;
      select.innerHTML = ids.map(id => `<option value="${escapeHtml(id)}">${escapeHtml(id)}</option>`).join('');
      if (ids.includes(previous)) select.value = previous;
    });
  }

  function buildPayload() {
    const sourceSpecs = [...sources.querySelectorAll('.composer-row')].map(row => {
      const source = row.querySelector('.composer-source-type').value;
      const id = row.querySelector('.composer-source-id').value.trim();
      const identifier = row.querySelector('.composer-source-identifier').value.trim();
      const metric = row.querySelector('.composer-source-metric').value.trim();
      const label = row.querySelector('.composer-source-label').value.trim();
      const item = { id, source, period: row.dataset.period || '5y' };
      if (label) item.label = label;
      if (source === 'fred') item.series_id = identifier;
      else item.symbol = identifier;
      if (source === 'fundamental') item.metric = metric;
      return item;
    });
    if (!sourceSpecs.length) throw new Error(t('chart.empty'));

    const transforms = [...formulas.querySelectorAll('.composer-row')].map(row => {
      const item = {
        id: row.querySelector('.composer-formula-id').value.trim(),
        kind: row.querySelector('.composer-formula-kind').value,
        left: row.querySelector('.composer-formula-left').value,
        right: row.querySelector('.composer-formula-right').value,
        window: Number(row.querySelector('.composer-formula-window').value),
      };
      const label = row.querySelector('.composer-formula-label').value.trim();
      if (label) item.label = label;
      return item;
    });
    return { sources: sourceSpecs, transforms };
  }

  function applyPayload(payload) {
    sources.replaceChildren();
    formulas.replaceChildren();
    sourceCounter = 1;
    formulaCounter = 1;
    for (const source of (payload.sources || [])) sources.append(sourceRow(source));
    updateFormulaSources();
    for (const transform of (payload.transforms || [])) formulaRow(transform);
  }

  function renderPresets(selectedId = null) {
    const previous = selectedId === null ? presetSelect.value : String(selectedId);
    presetSelect.innerHTML = '<option value="">—</option>' + presets.map(item =>
      `<option value="${item.id}">${escapeHtml(item.name)}</option>`
    ).join('');
    if (presets.some(item => String(item.id) === previous)) presetSelect.value = previous;
  }

  async function loadPresetCatalog(selectedId = null) {
    presets = await api('/v1/research-presets?kind=chart');
    renderPresets(selectedId);
  }

  async function savePreset() {
    const name = presetName.value.trim();
    if (!name) {
      presetStatus.textContent = t('discover.preset');
      return;
    }
    const saved = await api('/v1/research-presets', {
      method: 'POST',
      body: JSON.stringify({ name, kind: 'chart', payload: buildPayload() }),
    });
    presetName.value = '';
    presetStatus.textContent = saved.name;
    await loadPresetCatalog(saved.id);
  }

  async function applySelectedPreset() {
    const selected = presets.find(item => String(item.id) === presetSelect.value);
    if (!selected) return;
    applyPayload(selected.payload);
    presetStatus.textContent = selected.name;
    await compose();
  }

  async function deleteSelectedPreset() {
    const selected = presets.find(item => String(item.id) === presetSelect.value);
    if (!selected) return;
    await api(`/v1/research-presets/${selected.id}`, { method: 'DELETE' });
    presetStatus.textContent = selected.name;
    await loadPresetCatalog();
  }

  function renderErrors(data) {
    const entries = Object.entries(data.errors || {});
    if (!entries.length) {
      errors.textContent = t('chart.no_errors');
      return;
    }
    errors.innerHTML = entries.map(([id, message]) =>
      `<div class="composer-error"><strong>${escapeHtml(id)}</strong> · ${escapeHtml(message)}</div>`
    ).join('');
  }

  function renderProvenance(data) {
    if (!data.series.length) {
      provenance.textContent = '—';
      return;
    }
    provenance.innerHTML = data.series.map(series => {
      const sourceRows = series.provenance.map(item => {
        const asOf = item.as_of || item.retrieved_at;
        return `${item.source} · ${item.provider} · ${new Date(asOf).toLocaleString(localeTag)}`;
      });
      return `<div class="composer-provenance-row"><strong>${escapeHtml(series.id)} · ${escapeHtml(series.label)}</strong><small>${escapeHtml(sourceRows.join(' | ') || 'derived / no source metadata')}</small></div>`;
    }).join('');
  }

  function renderChart(data) {
    if (chart) chart.remove();
    chartHost.replaceChildren();
    const visible = data.series.filter(series => series.points?.length);
    chartHost.style.height = `${Math.max(420, 230 + visible.length * 118)}px`;
    chart = LightweightCharts.createChart(chartHost, {
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
      timeScale: { borderColor: '#d1ccc2', minBarSpacing: 0.05 },
    });
    const palette = ['#2456d8', '#087a54', '#bc3145', '#7254a7', '#9a6b24', '#13736d', '#59636f', '#9a4567'];
    visible.forEach((item, index) => {
      const series = chart.addSeries(LightweightCharts.LineSeries, {
        color: palette[index % palette.length],
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: true,
        title: `${item.id} · ${item.label}`,
      }, index);
      series.setData(item.points.map(point => ({ time: point.date, value: point.value })));
      if (item.unit === 'correlation') {
        for (const price of [-1, 0, 1]) {
          series.createPriceLine({
            price,
            color: 'rgba(108,105,98,.45)',
            lineWidth: 1,
            lineStyle: LightweightCharts.LineStyle.Dashed,
            axisLabelVisible: true,
          });
        }
      }
      chart.panes()[index]?.setHeight(index === 0 ? 180 : 115);
    });
    chart.timeScale().fitContent();
  }

  async function compose() {
    status.textContent = t('chart.loading');
    try {
      const data = await api('/v1/charts/compose', {
        method: 'POST',
        body: JSON.stringify(buildPayload()),
      });
      renderChart(data);
      renderErrors(data);
      renderProvenance(data);
      status.textContent = new Date(data.composed_at).toLocaleTimeString(localeTag);
    } catch (error) {
      status.textContent = error.message;
    }
  }

  document.querySelector('#add-chart-source')?.addEventListener('click', () => {
    sources.append(sourceRow());
    updateFormulaSources();
  });
  document.querySelector('#add-chart-formula')?.addEventListener('click', () => formulaRow());
  document.querySelector('#compose-chart')?.addEventListener('click', compose);
  document.querySelector('#chart-preset-save')?.addEventListener('click', () => {
    savePreset().catch(error => { presetStatus.textContent = error.message; });
  });
  document.querySelector('#chart-preset-load')?.addEventListener('click', () => {
    applySelectedPreset().catch(error => { presetStatus.textContent = error.message; });
  });
  document.querySelector('#chart-preset-delete')?.addEventListener('click', () => {
    deleteSelectedPreset().catch(error => { presetStatus.textContent = error.message; });
  });

  sources.append(sourceRow({ id: 'px', source: 'price', identifier: 'AAPL', label: 'AAPL price' }));
  sources.append(sourceRow({ id: 'rev', source: 'fundamental', identifier: 'AAPL', metric: 'revenue', label: 'AAPL revenue' }));
  updateFormulaSources();
  loadPresetCatalog().catch(error => { presetStatus.textContent = error.message; });
})();
