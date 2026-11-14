(() => {
  const { api, escapeHtml, localeTag, t } = window.Yowayowa;
  const ja = window.YOWAYOWA_LOCALE === 'ja';

  function isoDate(date) {
    return date.toISOString().slice(0, 10);
  }

  function installDefaults() {
    const start = document.querySelector('#calendar-start');
    const end = document.querySelector('#calendar-end');
    if (!start || !end) return;
    const today = new Date();
    const fortnight = new Date(today);
    fortnight.setDate(fortnight.getDate() + 14);
    start.value = isoDate(today);
    end.value = isoDate(fortnight);
  }

  async function installScopeOptions() {
    const select = document.querySelector('#calendar-scope');
    if (!select) return;
    const [watchlists, portfolios] = await Promise.all([
      api('/v1/watchlists'),
      api('/v1/portfolios'),
    ]);
    watchlists.forEach(item => {
      const option = document.createElement('option');
      option.value = `watchlist:${item.id}`;
      option.textContent = t('calendar.scope.watchlist', { name: item.name });
      select.append(option);
    });
    portfolios.forEach(item => {
      const option = document.createElement('option');
      option.value = `portfolio:${item.id}`;
      option.textContent = t('calendar.scope.portfolio', { name: item.name });
      select.append(option);
    });
  }

  function isMarketScope() {
    return document.querySelector('#calendar-scope')?.value === 'market';
  }

  function applyScopeVisibility() {
    const market = isMarketScope();
    document.querySelector('#calendar-symbol-field').style.display = market ? 'grid' : 'none';
    document.querySelector('#calendar-market-types').style.display = market ? 'flex' : 'none';
    document.querySelector('#calendar-tracked-types').style.display = market ? 'none' : 'flex';
  }

  function eventTypeLabel(type) {
    return t(`calendar.${type}`, {}, type);
  }

  function eventTitle(event) {
    if (event.subtype) return t(`calendar.subtype.${event.subtype}`, {}, event.title);
    return event.title;
  }

  function eventWhen(event) {
    if (!event.starts_at) return '—';
    const start = new Date(event.starts_at).toLocaleString(localeTag);
    if (!event.ends_at) return start;
    const end = new Date(event.ends_at).toLocaleString(localeTag);
    return `${start} → ${end}`;
  }

  function eventClass(type) {
    const value = String(type || '').toLowerCase();
    if (value.includes('earning')) return 'event-earnings';
    if (value.includes('economic') || value.includes('macro')) return 'event-economic';
    if (value.includes('ipo')) return 'event-ipo';
    if (value.includes('split')) return 'event-split';
    if (value.includes('dividend')) return 'event-dividend';
    return '';
  }

  function isHighImpact(event) {
    if (eventClass(event.event_type) !== 'event-economic') return false;
    const text = `${event.title || ''} ${event.subtype || ''}`;
    return /(FOMC|CPI|consumer price|PCE|nonfarm|payroll|unemployment|GDP|policy rate|Fed\b|ECB\b|BOJ\b|日銀|政策金利|消費者物価|雇用統計|国内総生産)/i.test(text);
  }

  function render(data) {
    const target = document.querySelector('#calendar-results');
    if (Array.isArray(data.symbols) && data.symbols.length === 0) {
      target.innerHTML = `<span class="muted">${escapeHtml(t('calendar.tracked_empty'))}</span>`;
      return;
    }
    if (!data.events.length) {
      target.innerHTML = `<span class="muted">${escapeHtml(t('calendar.none'))}</span>`;
      return;
    }
    target.innerHTML = `<table><thead><tr><th>${escapeHtml(t('calendar.when'))}</th><th>${escapeHtml(t('calendar.type'))}</th><th>${escapeHtml(t('common.symbol'))}</th><th>${escapeHtml(t('calendar.event'))}</th></tr></thead><tbody>${data.events.map(event => {
      const klass = eventClass(event.event_type);
      const high = isHighImpact(event);
      const rowClass = [klass, high ? 'event-high' : ''].filter(Boolean).join(' ');
      const importance = high ? `<span class="event-importance">${ja ? '重要' : 'HIGH'}</span>` : '';
      return `<tr class="${rowClass}">
        <td>${escapeHtml(eventWhen(event))}</td>
        <td><span class="event-badge">${escapeHtml(eventTypeLabel(event.event_type))}</span>${importance}</td>
        <td>${event.symbol ? `<a href="/instrument/${encodeURIComponent(event.symbol)}"><strong>${escapeHtml(event.symbol)}</strong></a>` : '—'}</td>
        <td>${escapeHtml(eventTitle(event))}</td>
      </tr>`;
    }).join('')}</tbody></table>`;
  }

  function renderProvenance(data) {
    const p = data.provenance;
    const license = t(`license.${p.license_class}`, {}, p.license_class);
    document.querySelector('#calendar-as-of').textContent = `${data.start} → ${data.end}`;
    const unavailable = Array.isArray(data.unavailable_symbols) && data.unavailable_symbols.length
      ? `<div class="badge-fail">${escapeHtml(t('calendar.tracked_unavailable', { symbols: data.unavailable_symbols.join(', ') }))}</div>`
      : '';
    document.querySelector('#calendar-provenance').innerHTML = `
      <div><strong>${escapeHtml(p.source)}</strong></div>
      <div>${escapeHtml(t('common.provider'))} <code>${escapeHtml(p.provider)}</code> · ${escapeHtml(license)}</div>
      <div>${escapeHtml(t('common.retrieved'))} ${escapeHtml(new Date(p.retrieved_at).toLocaleString(localeTag))}</div>
      ${unavailable}`;
  }

  function trackedScopeParams(value) {
    if (value === 'all') return { scope: 'all' };
    const [scope, rawId] = value.split(':', 2);
    return { scope, scope_id: rawId };
  }

  async function loadCalendar() {
    const target = document.querySelector('#calendar-results');
    target.textContent = t('calendar.loading');
    const start = document.querySelector('#calendar-start').value;
    const end = document.querySelector('#calendar-end').value;
    const scopeValue = document.querySelector('#calendar-scope').value;
    let endpoint;
    let params;

    if (scopeValue === 'market') {
      const types = [...document.querySelectorAll('input[name="calendar-market-type"]:checked')]
        .map(input => input.value);
      params = new URLSearchParams({ start, end, types: types.join(',') });
      const symbol = document.querySelector('#calendar-symbol').value.trim().toUpperCase();
      if (symbol) params.set('symbol', symbol);
      endpoint = '/v1/calendar';
    } else {
      const types = [...document.querySelectorAll('input[name="calendar-tracked-type"]:checked')]
        .map(input => input.value);
      params = new URLSearchParams({ start, end, types: types.join(',') });
      const scope = trackedScopeParams(scopeValue);
      params.set('scope', scope.scope);
      if (scope.scope_id) params.set('scope_id', scope.scope_id);
      endpoint = '/v1/calendar/tracked';
    }

    try {
      const data = await api(`${endpoint}?${params.toString()}`);
      render(data);
      renderProvenance(data);
    } catch (error) {
      target.textContent = error.message;
    }
  }

  document.querySelector('#calendar-form')?.addEventListener('submit', event => {
    event.preventDefault();
    loadCalendar();
  });
  document.querySelector('#calendar-scope')?.addEventListener('change', applyScopeVisibility);

  installDefaults();
  applyScopeVisibility();
  loadCalendar();
  installScopeOptions().catch(() => undefined);
})();
