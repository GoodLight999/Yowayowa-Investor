(() => {
  const { api, escapeHtml, localeTag, t } = window.Yowayowa;

  async function installScopeOptions() {
    const select = document.querySelector('#news-scope');
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

  function isSearchScope() {
    return document.querySelector('#news-scope')?.value === 'search';
  }

  function applyScopeVisibility() {
    const query = document.querySelector('#news-query');
    const submit = document.querySelector('#news-form button[type="submit"]');
    const search = isSearchScope();
    query.style.display = search ? 'block' : 'none';
    query.required = search;
    if (submit) submit.textContent = t(search ? 'common.search' : 'common.run');
  }

  function savedScopeParams(value) {
    if (value === 'all') return { scope: 'all' };
    const [scope, rawId] = value.split(':', 2);
    return { scope, scope_id: rawId };
  }

  function relatedSymbols(item) {
    if (Array.isArray(item.symbols)) return item.symbols;
    return item.symbol ? [item.symbol] : [];
  }

  function render(data) {
    const target = document.querySelector('#news-results');
    if (Array.isArray(data.symbols) && data.symbols.length === 0) {
      target.innerHTML = `<span class="muted">${escapeHtml(t('calendar.tracked_empty'))}</span>`;
      renderProvenance(data);
      return;
    }
    if (!data.items.length) {
      target.innerHTML = `<span class="muted">${escapeHtml(t('news.none'))}</span>`;
      renderProvenance(data);
      return;
    }
    target.innerHTML = data.items.map(item => {
      const symbols = relatedSymbols(item);
      const symbolLinks = symbols.map(symbol =>
        `<a href="/instrument/${encodeURIComponent(symbol)}">${escapeHtml(symbol)}</a>`
      ).join(', ');
      const published = item.published_at
        ? escapeHtml(new Date(item.published_at).toLocaleString(localeTag))
        : '';
      const meta = [symbolLinks, item.publisher ? escapeHtml(item.publisher) : '', published]
        .filter(Boolean)
        .join(' · ');
      return `
        <article class="result-row news-row">
          <span>
            <strong>${item.url ? `<a href="${escapeHtml(item.url)}" rel="noreferrer">${escapeHtml(item.title)}</a>` : escapeHtml(item.title)}</strong>
            ${meta ? `<small>${meta}</small>` : ''}
            ${item.summary ? `<small>${escapeHtml(item.summary)}</small>` : ''}
          </span>
        </article>`;
    }).join('');
    renderProvenance(data);
  }

  function renderProvenance(data) {
    const p = data.provenance;
    const license = t(`license.${p.license_class}`, {}, p.license_class);
    document.querySelector('#news-as-of').textContent = `${t('common.as_of')} ${new Date(p.as_of || p.retrieved_at).toLocaleString(localeTag)}`;
    const unavailable = Array.isArray(data.unavailable_symbols) && data.unavailable_symbols.length
      ? `<div class="badge-fail">${escapeHtml(t('common.unavailable'))}: ${escapeHtml(data.unavailable_symbols.join(', '))}</div>`
      : '';
    document.querySelector('#news-provenance').innerHTML = `
      <div><strong>${escapeHtml(p.source)}</strong></div>
      <div>${escapeHtml(t('common.provider'))} <code>${escapeHtml(p.provider)}</code> · ${escapeHtml(license)}</div>
      <div>${escapeHtml(t('common.retrieved'))} ${escapeHtml(new Date(p.retrieved_at).toLocaleString(localeTag))}</div>
      ${unavailable}`;
  }

  async function loadNews() {
    const target = document.querySelector('#news-results');
    const scopeValue = document.querySelector('#news-scope').value;
    target.textContent = t('news.loading');
    try {
      if (scopeValue === 'search') {
        const query = document.querySelector('#news-query').value.trim();
        if (!query) {
          target.textContent = t('news.prompt');
          return;
        }
        render(await api(`/v1/news/${encodeURIComponent(query)}?limit=20`));
        return;
      }
      const params = new URLSearchParams({ limit: '60', per_symbol_limit: '6' });
      const scope = savedScopeParams(scopeValue);
      params.set('scope', scope.scope);
      if (scope.scope_id) params.set('scope_id', scope.scope_id);
      render(await api(`/v1/news/saved?${params.toString()}`));
    } catch (error) {
      target.textContent = error.message;
    }
  }

  document.querySelector('#news-form')?.addEventListener('submit', event => {
    event.preventDefault();
    loadNews();
  });
  document.querySelector('#news-scope')?.addEventListener('change', applyScopeVisibility);

  applyScopeVisibility();
  installScopeOptions().catch(() => undefined);
})();
