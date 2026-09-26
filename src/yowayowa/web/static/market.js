(() => {
  const { api, escapeHtml, localeTag, t } = window.Yowayowa;
  let sectorData = null;
  let sectorHorizon = 'change_1m';

  function formatValue(value, unit) {
    if (!Number.isFinite(Number(value))) return '—';
    const n = Number(value);
    const digits = Math.abs(n) >= 1000 ? 1 : Math.abs(n) >= 100 ? 2 : 3;
    return `${new Intl.NumberFormat(localeTag, { maximumFractionDigits: digits }).format(n)} ${unit}`;
  }
  function formatChange(value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
    return new Intl.NumberFormat(localeTag, { style: 'percent', maximumFractionDigits: 2, signDisplay: 'always' }).format(Number(value));
  }
  function formatSectorChange(value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
    return new Intl.NumberFormat(localeTag, { style: 'percent', minimumFractionDigits: 2, maximumFractionDigits: 2, signDisplay: 'always' }).format(Number(value));
  }
  function changeClass(value) {
    if (value === null || value === undefined) return '';
    return Number(value) > 0 ? 'market-positive' : Number(value) < 0 ? 'market-negative' : '';
  }
  function sparkline(points) {
    if (!points?.length) return '';
    const values = points.map(([, value]) => Number(value)).filter(Number.isFinite);
    if (!values.length) return '';
    const width = 150, height = 44, padding = 3;
    const min = Math.min(...values), max = Math.max(...values), spread = max - min || 1;
    const coordinates = values.map((value, index) => {
      const x = padding + (index / Math.max(1, values.length - 1)) * (width - padding * 2);
      const y = height - padding - ((value - min) / spread) * (height - padding * 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' ');
    return `<svg class="market-sparkline" viewBox="0 0 ${width} ${height}" aria-hidden="true"><polyline points="${coordinates}"></polyline></svg>`;
  }
  function renderItem(item) {
    return `<article class="market-card">
      <div class="market-card-heading"><div><small>${escapeHtml(item.symbol)}</small><h3>${escapeHtml(item.label)}</h3></div><div class="market-sparkline-wrap">${sparkline(item.sparkline)}</div></div>
      <div class="market-value">${escapeHtml(formatValue(item.value, item.unit))}</div>
      <div class="market-change-grid">
        <span><small>1D</small><strong class="${changeClass(item.change_1d)}">${escapeHtml(formatChange(item.change_1d))}</strong></span>
        <span><small>1M</small><strong class="${changeClass(item.change_1m)}">${escapeHtml(formatChange(item.change_1m))}</strong></span>
        <span><small>3M</small><strong class="${changeClass(item.change_3m)}">${escapeHtml(formatChange(item.change_3m))}</strong></span>
        <span><small>1Y</small><strong class="${changeClass(item.change_1y)}">${escapeHtml(formatChange(item.change_1y))}</strong></span>
      </div></article>`;
  }
  function renderOverview(data) {
    const target = document.querySelector('#market-overview');
    const groups = new Map();
    for (const item of data.items) {
      if (!groups.has(item.category)) groups.set(item.category, []);
      groups.get(item.category).push(item);
    }
    target.innerHTML = [...groups.entries()].map(([category, items]) => `
      <section class="market-category">
        <div class="section-heading market-category-heading"><h2>${escapeHtml(t(`markets.category.${category}`, {}, category))}</h2><span class="pill">${items.length}</span></div>
        <div class="market-card-grid">${items.map(renderItem).join('')}</div>
      </section>`).join('');
    if (data.unavailable_symbols.length) {
      target.insertAdjacentHTML('beforeend', `<div class="badge-fail">${escapeHtml(t('common.unavailable'))}: ${escapeHtml(data.unavailable_symbols.join(', '))}</div>`);
    }
    const p = data.provenance;
    document.querySelector('#market-as-of').textContent = new Date(p.as_of || p.retrieved_at).toLocaleString(localeTag);
    const license = t(`license.${p.license_class}`, {}, p.license_class);
    document.querySelector('#market-provenance').innerHTML = `<div><strong>${escapeHtml(p.source)}</strong> · <code>${escapeHtml(p.provider)}</code> · ${escapeHtml(license)}</div><div>${escapeHtml(t('common.retrieved'))} ${escapeHtml(new Date(p.retrieved_at).toLocaleString(localeTag))}</div>`;
  }

  function renderSectors() {
    const target = document.querySelector('#sector-heatmap');
    if (!target || !sectorData) return;
    const items = [...sectorData.items].sort((left, right) => {
      const a = Number(left[sectorHorizon]);
      const b = Number(right[sectorHorizon]);
      if (!Number.isFinite(a)) return 1;
      if (!Number.isFinite(b)) return -1;
      return b - a;
    });
    const finite = items
      .map(item => Math.abs(Number(item[sectorHorizon])))
      .filter(Number.isFinite);
    const maxAbs = Math.max(...finite, 0.0001);
    target.innerHTML = items.map((item, index) => {
      const raw = Number(item[sectorHorizon]);
      const available = Number.isFinite(raw);
      const sign = !available ? 'flat' : raw > 0 ? 'positive' : raw < 0 ? 'negative' : 'flat';
      const strength = available ? Math.round(8 + Math.min(1, Math.abs(raw) / maxAbs) * 34) : 0;
      const label = t(`sector.${item.symbol}`, {}, item.label);
      return `<a class="sector-tile" href="/instrument/${encodeURIComponent(item.symbol)}" data-sign="${sign}" style="--sector-strength:${strength}%">
        <span><strong>${escapeHtml(label)}</strong><small>${escapeHtml(item.symbol)}</small></span>
        <span><span class="sector-return">${escapeHtml(formatSectorChange(available ? raw : null))}</span><span class="sector-rank">#${index + 1}</span></span>
      </a>`;
    }).join('');
    if (sectorData.unavailable_symbols.length) {
      target.insertAdjacentHTML(
        'beforeend',
        `<div class="list-state muted">${escapeHtml(t('sector.unavailable'))}: ${escapeHtml(sectorData.unavailable_symbols.join(', '))}</div>`,
      );
    }
    const p = sectorData.provenance;
    const license = t(`license.${p.license_class}`, {}, p.license_class);
    document.querySelector('#sector-source').textContent = `${p.source} · ${license} · ${t('common.as_of')} ${new Date(p.as_of || p.retrieved_at).toLocaleString(localeTag)}`;
  }

  async function loadSectors() {
    const target = document.querySelector('#sector-heatmap');
    if (!target) return;
    target.innerHTML = `<div class="list-state muted">${escapeHtml(t('sector.loading'))}</div>`;
    try {
      sectorData = await api('/v1/markets/sectors');
      renderSectors();
    } catch (error) {
      target.innerHTML = `<div class="list-state badge-fail">${escapeHtml(error.message)}</div>`;
    }
  }

  async function loadMarket() {
    const target = document.querySelector('#market-overview');
    target.innerHTML = `<div class="panel"><div class="list-state muted">${escapeHtml(t('markets.loading'))}</div></div>`;
    const [marketResult] = await Promise.allSettled([
      api('/v1/markets/overview'),
      loadSectors(),
    ]);
    if (marketResult.status === 'fulfilled') renderOverview(marketResult.value);
    else target.innerHTML = `<div class="panel"><div class="list-state badge-fail">${escapeHtml(marketResult.reason.message)}</div></div>`;
  }

  document.querySelectorAll('[data-sector-horizon]').forEach(button => {
    button.addEventListener('click', () => {
      sectorHorizon = button.dataset.sectorHorizon;
      document.querySelectorAll('[data-sector-horizon]').forEach(item => {
        item.classList.toggle('is-active', item === button);
      });
      renderSectors();
    });
  });
  document.querySelector('#refresh-market')?.addEventListener('click', loadMarket);
  loadMarket();
})();
