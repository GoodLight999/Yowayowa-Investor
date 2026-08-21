(() => {
  const { api, escapeHtml, t } = window.Yowayowa;
  const ja = window.YOWAYOWA_LOCALE === 'ja';
  const COMPANY_KEY = 'yowayowa.company-context.v1';

  function normalizeName(name) {
    return String(name || '').toLowerCase()
      .replace(/\b(incorporated|corporation|corp|company|co|limited|ltd|plc|holdings?|group)\b/g, '')
      .replace(/[.,()\-]/g, ' ').replace(/\s+/g, ' ').trim();
  }

  function score(row, query) {
    const symbol = String(row.symbol || '').toUpperCase();
    const exchange = String(row.exchange || '').toUpperCase();
    const rawQuery = String(query || '').trim();
    const needle = rawQuery.toUpperCase();
    const looksLikeTicker = /^[A-Z0-9.^=-]{1,12}(?:\.[A-Z0-9]{1,5})?$/.test(needle);
    let value = 0;
    if (looksLikeTicker && symbol === needle) value += 200;
    if (row.instrument_type === 'equity') value += 10;
    if (!symbol.includes('.')) value += 12;
    if (/^(NMS|NYQ|NYSE|NASDAQ|NGM|NCM)$/.test(exchange)) value += ja ? 20 : 55;
    if (/JPX|TSE|TYO/.test(exchange) || symbol.endsWith('.T')) value += ja ? 80 : 28;
    if (/OTC|PNK|GREY/.test(exchange)) value -= 45;
    if (/\.BK$/.test(symbol)) value -= 55;
    if (/\.(F|MU|BE|DU|HM)$/.test(symbol)) value -= 20;
    return value;
  }

  function groups(rows, query) {
    const grouped = new Map();
    rows.forEach(row => {
      const key = normalizeName(row.name) || String(row.symbol || '').toUpperCase();
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key).push(row);
    });
    return [...grouped.values()]
      .map(group => group.sort((a, b) => score(b, query) - score(a, query)))
      .sort((a, b) => score(b[0], query) - score(a[0], query));
  }

  function link(row, klass, preferred = false) {
    const meta = [row.exchange, row.currency, row.instrument_type].filter(Boolean).join(' · ');
    return `<a class="${klass}" href="/instrument/${encodeURIComponent(row.symbol)}" data-company-symbol="${escapeHtml(row.symbol)}" data-company-name="${escapeHtml(row.name || '')}" data-company-exchange="${escapeHtml(row.exchange || '')}"><span><strong>${escapeHtml(row.symbol)}</strong><small>${escapeHtml(row.name || '')}</small></span><span class="listing-market">${escapeHtml(meta || '—')}${preferred ? `<span class="listing-preferred">${ja ? '優先候補' : 'Preferred'}</span>` : ''}</span></a>`;
  }

  const form = document.querySelector('#global-search');
  if (!form) return;
  form.addEventListener('submit', async event => {
    event.preventDefault();
    event.stopImmediatePropagation();
    const query = document.querySelector('#search-input')?.value?.trim();
    const target = document.querySelector('#search-results');
    if (!query || !target) return;
    target.textContent = t('dashboard.searching');
    try {
      const rows = await api(`/v1/instruments/search?q=${encodeURIComponent(query)}&limit=30`);
      const grouped = groups(rows, query);
      target.innerHTML = grouped.length ? grouped.map(group => {
        const preferred = group[0];
        const alternatives = group.slice(1);
        return `<div class="company-search-group">${link(preferred, 'company-search-primary', true)}${alternatives.length ? `<details class="listing-alternatives"><summary>${ja ? '他の上場先' : 'Other listings'} ${alternatives.length}</summary>${alternatives.map(row => link(row, 'listing-alternative')).join('')}</details>` : ''}</div>`;
      }).join('') : `<span class="muted">${escapeHtml(t('dashboard.no_matches'))}</span>`;
      target.querySelectorAll('[data-company-symbol]').forEach(node => node.addEventListener('click', () => {
        try { localStorage.setItem(COMPANY_KEY, JSON.stringify({ symbol: node.dataset.companySymbol, name: node.dataset.companyName, exchange: node.dataset.companyExchange })); } catch (_) {}
      }));
    } catch (error) { target.textContent = error.message; }
  }, true);
})();
