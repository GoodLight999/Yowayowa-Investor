const locale = window.YOWAYOWA_LOCALE || 'en';
const messages = window.YOWAYOWA_I18N || {};
const localeTag = locale === 'ja' ? 'ja-JP' : 'en-US';

const t = (key, values = {}, fallback = null) => {
  let text = messages[key] || fallback || key;
  for (const [name, value] of Object.entries(values)) {
    text = text.replaceAll(`{${name}}`, String(value));
  }
  return text;
};

const apiErrorMessages = locale === 'ja' ? {
  400: '入力内容を確認してください。',
  401: '認証が必要です。',
  403: 'この操作またはデータは現在の利用モードでは利用できません。',
  404: '対象が見つかりません。',
  409: '同じ名前または内容が既に存在します。',
  422: '入力条件を確認してください。',
  424: '必要な外部データ設定が完了していません。',
  429: '外部データの利用上限に達しました。しばらくしてから再試行してください。',
  502: '外部データの取得に失敗しました。',
  503: 'サービスを一時的に利用できません。',
} : {
  400: 'Check the submitted input.',
  401: 'Authentication is required.',
  403: 'This operation or data is unavailable in the current usage mode.',
  404: 'The requested item was not found.',
  409: 'The same name or item already exists.',
  422: 'Check the submitted conditions.',
  424: 'A required external-data configuration is missing.',
  429: 'An external-data rate limit was reached. Try again later.',
  502: 'External data could not be retrieved.',
  503: 'The service is temporarily unavailable.',
};

const api = async (path, options = {}) => {
  const response = await fetch(path, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
  });
  if (!response.ok) {
    let detail = null;
    try { detail = (await response.json()).detail || null; } catch (_) {}
    const generic = apiErrorMessages[response.status] || (locale === 'ja' ? '処理に失敗しました。' : 'The operation failed.');
    const message = locale === 'en' && typeof detail === 'string' ? detail : generic;
    throw new Error(message);
  }
  return response.json();
};

const fmt = new Intl.NumberFormat(localeTag, { maximumFractionDigits: 2 });
const percentFmt = new Intl.NumberFormat(localeTag, {
  style: 'percent',
  maximumFractionDigits: 2,
  signDisplay: 'always',
});
const escapeHtml = (value) => String(value)
  .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
  .replaceAll('"', '&quot;').replaceAll("'", '&#039;');

function setLocale(nextLocale) {
  if (!['ja', 'en'].includes(nextLocale) || nextLocale === locale) return;
  const url = new URL(window.location.href);
  url.searchParams.set('lang', nextLocale);
  window.location.assign(url.toString());
}

function installLocaleSwitcher() {
  document.querySelectorAll('[data-locale]').forEach(button => {
    button.addEventListener('click', () => setLocale(button.dataset.locale));
  });
}

async function updateHealth() {
  const label = document.querySelector('#api-status');
  const dot = document.querySelector('.status-dot');
  try {
    const health = await api('/v1/health');
    const mode = health.mode === 'personal' ? t('common.personal') : health.mode.toUpperCase();
    if (label) label.textContent = `${mode} · ${t('status.live')}`;
    dot?.classList.add('live');
  } catch (_) {
    if (label) label.textContent = t('status.api_offline');
  }
}

function renderSearch(rows) {
  const target = document.querySelector('#search-results');
  if (!target) return;
  if (!rows.length) {
    target.innerHTML = `<span class="muted">${escapeHtml(t('dashboard.no_matches'))}</span>`;
    return;
  }
  target.innerHTML = rows.map(row => `
    <a class="result-row" href="/instrument/${encodeURIComponent(row.symbol)}">
      <span><strong>${escapeHtml(row.symbol)}</strong><small>${escapeHtml(row.name)}</small></span>
      <span class="muted">${escapeHtml(row.cik || '')}</span>
    </a>`).join('');
}

async function mainWatchlist() {
  const lists = await api('/v1/watchlists');
  return lists.find(item => item.name === 'Main') || lists[0] || null;
}

function quoteChange(quote) {
  if (!quote || quote.previous_close === null || Number(quote.previous_close) === 0) return null;
  return Number(quote.price) / Number(quote.previous_close) - 1;
}

function renderWatchlist(main, quoteBatch = null) {
  const target = document.querySelector('#watchlist');
  if (!target) return;
  if (!main || !main.symbols.length) {
    target.innerHTML = `<span class="muted">${escapeHtml(t('dashboard.no_symbols'))}</span>`;
    return;
  }
  target.dataset.watchlistId = main.id;
  const quotes = quoteBatch?.quotes || {};
  target.innerHTML = main.symbols.map(symbol => {
    const quote = quotes[symbol];
    const change = quoteChange(quote);
    const changeClass = change === null ? '' : change > 0 ? 'market-positive' : change < 0 ? 'market-negative' : '';
    const price = quote ? fmt.format(Number(quote.price)) : '—';
    const day = change === null ? '—' : percentFmt.format(change);
    const title = quote ? new Date(quote.as_of).toLocaleString(localeTag) : t('dashboard.quote_unavailable');
    return `
      <div class="symbol-row" title="${escapeHtml(title)}">
        <a href="/instrument/${encodeURIComponent(symbol)}">${escapeHtml(symbol)}</a>
        <span class="watchlist-price">${escapeHtml(price)}</span>
        <span class="watchlist-change ${changeClass}">${escapeHtml(day)}</span>
        <button class="ghost watchlist-remove" type="button" data-symbol="${escapeHtml(symbol)}">${escapeHtml(t('common.remove'))}</button>
      </div>`;
  }).join('');
  target.querySelectorAll('.watchlist-remove').forEach(button => {
    button.addEventListener('click', async () => {
      await api(`/v1/watchlists/${main.id}/symbols/${encodeURIComponent(button.dataset.symbol)}`, { method: 'DELETE' });
      await loadWatchlist();
    });
  });
}

async function loadWatchlist() {
  const target = document.querySelector('#watchlist');
  if (!target) return;
  try {
    const main = await mainWatchlist();
    if (!main || !main.symbols.length) {
      renderWatchlist(main);
      return;
    }
    let quoteBatch = null;
    try {
      quoteBatch = await api(`/v1/markets/quotes?symbols=${encodeURIComponent(main.symbols.join(','))}`);
    } catch (_) {
      // Quote data is additive; watchlist CRUD must remain usable if the market provider is down.
    }
    renderWatchlist(main, quoteBatch);
  } catch (error) {
    target.textContent = error.message;
  }
}

function renderPlan(plan) {
  const target = document.querySelector('#operation-plan');
  if (!target) return;
  const ops = plan.operations.map(op => `<code>${escapeHtml(op.kind)}</code> ${escapeHtml(JSON.stringify(op.arguments))}`).join(' · ');
  target.innerHTML = `${escapeHtml(plan.summary)}${ops ? `<br>${ops}` : ''}`;
}

async function applyPlannedOperation(plan) {
  const compare = plan.operations.find(item => item.kind === 'compare.symbols');
  if (compare) {
    const symbols = compare.arguments.symbols || [];
    if (symbols.length < 2) throw new Error(t('dashboard.compare_requires_two'));
    location.assign(`/compare?symbols=${encodeURIComponent(symbols.join(','))}`);
    return true;
  }
  const op = plan.operations.find(item => item.kind === 'watchlist.add' || item.kind === 'watchlist.remove');
  if (!op) return false;
  const main = await mainWatchlist();
  if (!main) throw new Error(t('dashboard.main_watchlist_missing'));
  if (op.kind === 'watchlist.add') {
    await api(`/v1/watchlists/${main.id}/symbols`, { method: 'POST', body: JSON.stringify(op.arguments.symbols || []) });
  } else {
    for (const symbol of (op.arguments.symbols || [])) {
      await api(`/v1/watchlists/${main.id}/symbols/${encodeURIComponent(symbol)}`, { method: 'DELETE' });
    }
  }
  await loadWatchlist();
  return true;
}

function installGlobalCommandShortcut() {
  if (window.__YOWAYOWA_COMMAND_SHORTCUT_INSTALLED) return;
  window.__YOWAYOWA_COMMAND_SHORTCUT_INSTALLED = true;
  document.addEventListener('keydown', event => {
    const modifier = event.ctrlKey || event.metaKey;
    if (!modifier || !['k', 'p'].includes(event.key.toLowerCase())) return;
    const dialog = document.querySelector('#command-palette');
    const trigger = document.querySelector('#command-trigger');
    if (!dialog || !trigger) return;
    event.preventDefault();
    if (dialog.open) dialog.close();
    else trigger.click();
  });
}

function installCommandPalette() {
  const dialog = document.querySelector('#command-palette');
  const input = document.querySelector('#command-input');
  const results = document.querySelector('#command-results');
  const trigger = document.querySelector('#command-trigger');
  const close = document.querySelector('#command-close');
  if (!dialog || !input || !results || !trigger) return;

  const navigation = [
    { label: t('nav.overview'), detail: '/', href: '/', keywords: 'overview home dashboard', primary: true },
    { label: t('nav.discover'), detail: '/discover', href: '/discover', keywords: 'discover screen ideas', primary: true },
    { label: t('nav.markets'), detail: '/markets', href: '/markets', keywords: 'markets quotes', primary: true },
    { label: t('nav.macro'), detail: '/macro', href: '/macro', keywords: 'macro fred economics', primary: true },
    { label: t('nav.portfolio'), detail: '/portfolio', href: '/portfolio', keywords: 'portfolio holdings risk', primary: true },
    { label: t('nav.ai'), detail: '/ai', href: '/ai', keywords: 'ai research agent', primary: true },
    { label: t('nav.settings', {}, t('settings.title')), detail: '/settings', href: '/settings', keywords: 'settings provider model api key edinet', primary: true },
    { label: t('chart.nav'), detail: '/charts', href: '/charts', keywords: 'composer chart cross source ratio spread correlation' },
    { label: t('rates.nav'), detail: '/rates', href: '/rates', keywords: 'rates treasury yield curve bonds' },
    { label: t('institutional.nav'), detail: '/institutional', href: '/institutional', keywords: '13f institutional holdings sec managers 機関投資家 保有開示' },
    { label: t('nav.compare'), detail: '/compare', href: '/compare', keywords: 'compare comps 比較' },
    { label: t('nav.screener'), detail: '/screener', href: '/screener', keywords: 'screener filters スクリーナー' },
    { label: t('nav.news'), detail: '/news', href: '/news', keywords: 'news headlines ニュース' },
    { label: t('nav.calendar'), detail: '/calendar', href: '/calendar', keywords: 'calendar earnings dividend events 決算 イベント' },
    { label: t('nav.alerts'), detail: '/alerts', href: '/alerts', keywords: 'alerts notifications inbox アラート' },
    { label: t('nav.api'), detail: '/docs', href: '/docs', keywords: 'api docs openapi developer' },
  ];

  let items = [];
  let activeIndex = 0;
  let searchSequence = 0;
  let searchTimer = null;

  function filteredNavigation(query) {
    if (!query) return navigation.filter(item => item.primary);
    const needle = query.toLocaleLowerCase(localeTag);
    return navigation.filter(item =>
      `${item.label} ${item.keywords}`.toLocaleLowerCase(localeTag).includes(needle)
    );
  }

  function updateActive() {
    const buttons = [...results.querySelectorAll('.command-item')];
    if (!buttons.length) {
      activeIndex = 0;
      input.removeAttribute('aria-activedescendant');
      return;
    }
    activeIndex = Math.max(0, Math.min(activeIndex, buttons.length - 1));
    buttons.forEach((button, index) => {
      const active = index === activeIndex;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    input.setAttribute('aria-activedescendant', buttons[activeIndex].id);
    buttons[activeIndex].scrollIntoView({ block: 'nearest' });
  }

  function activate(index) {
    const item = items[index];
    if (!item) return;
    dialog.close();
    window.location.assign(item.href);
  }

  function renderGroups(instruments, navItems, { searching = false, error = null } = {}) {
    items = [];
    const sections = [];
    if (instruments.length) {
      const rows = instruments.map(item => {
        const index = items.length;
        items.push(item);
        return `<button id="command-option-${index}" class="command-item" type="button" role="option" data-index="${index}" aria-selected="false"><span><strong>${escapeHtml(item.label)}</strong><small>${escapeHtml(item.detail)}</small></span><span>${escapeHtml(item.meta)}</span></button>`;
      }).join('');
      sections.push(`<div class="command-group-label">${escapeHtml(t('command.instruments'))}</div>${rows}`);
    }
    if (navItems.length) {
      const rows = navItems.map(item => {
        const index = items.length;
        items.push(item);
        return `<button id="command-option-${index}" class="command-item" type="button" role="option" data-index="${index}" aria-selected="false"><span><strong>${escapeHtml(item.label)}</strong><small>${escapeHtml(item.detail)}</small></span><span>${escapeHtml(t('command.open'))}</span></button>`;
      }).join('');
      sections.push(`<div class="command-group-label">${escapeHtml(t('command.navigation'))}</div>${rows}`);
    }
    if (searching) sections.push(`<div class="command-empty">${escapeHtml(t('command.searching'))}</div>`);
    if (error) sections.push(`<div class="command-empty">${escapeHtml(error)}</div>`);
    if (!sections.length) sections.push(`<div class="command-empty">${escapeHtml(t('dashboard.no_matches'))}</div>`);
    results.innerHTML = sections.join('');
    activeIndex = 0;
    results.querySelectorAll('.command-item').forEach(button => {
      button.addEventListener('mousemove', () => {
        activeIndex = Number(button.dataset.index);
        updateActive();
      });
      button.addEventListener('click', () => activate(Number(button.dataset.index)));
    });
    updateActive();
  }

  function search(query) {
    const navItems = filteredNavigation(query);
    const sequence = ++searchSequence;
    if (searchTimer) window.clearTimeout(searchTimer);
    if (!query) {
      renderGroups([], navItems);
      return;
    }
    renderGroups([], navItems, { searching: true });
    searchTimer = window.setTimeout(async () => {
      try {
        const rows = await api(`/v1/instruments/search?q=${encodeURIComponent(query)}&limit=8`);
        if (sequence !== searchSequence || !dialog.open) return;
        const seen = new Set();
        const instruments = rows.filter(row => {
          const key = String(row.symbol).toUpperCase();
          if (seen.has(key)) return false;
          seen.add(key);
          return true;
        }).map(row => ({
          label: row.symbol,
          detail: row.name,
          meta: [row.exchange, row.currency, row.instrument_type].filter(Boolean).join(' · '),
          href: `/instrument/${encodeURIComponent(row.symbol)}`,
        }));
        renderGroups(instruments, navItems);
      } catch (error) {
        if (sequence !== searchSequence || !dialog.open) return;
        renderGroups([], navItems, { error: error.message });
      }
    }, 100);
  }

  function openPalette() {
    if (dialog.open) return;
    dialog.showModal();
    input.value = '';
    search('');
    window.requestAnimationFrame(() => input.focus());
  }

  trigger.addEventListener('click', openPalette);
  close?.addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => {
    searchSequence += 1;
    if (searchTimer) window.clearTimeout(searchTimer);
  });
  input.addEventListener('input', () => search(input.value.trim()));
  input.addEventListener('keydown', event => {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      activeIndex = Math.min(activeIndex + 1, Math.max(0, items.length - 1));
      updateActive();
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      activeIndex = Math.max(activeIndex - 1, 0);
      updateActive();
    } else if (event.key === 'Home') {
      event.preventDefault();
      activeIndex = 0;
      updateActive();
    } else if (event.key === 'End') {
      event.preventDefault();
      activeIndex = Math.max(0, items.length - 1);
      updateActive();
    } else if (event.key === 'Enter') {
      event.preventDefault();
      activate(activeIndex);
    }
  });
}

function installDashboard() {
  document.querySelector('#global-search')?.addEventListener('submit', async event => {
    event.preventDefault();
    const q = document.querySelector('#search-input')?.value?.trim();
    if (!q) return;
    const target = document.querySelector('#search-results');
    target.textContent = t('dashboard.searching');
    try { renderSearch(await api(`/v1/instruments/search?q=${encodeURIComponent(q)}`)); }
    catch (error) { target.textContent = error.message; }
  });

  document.querySelector('#watchlist-add-form')?.addEventListener('submit', async event => {
    event.preventDefault();
    const input = document.querySelector('#watchlist-symbol');
    const symbol = input?.value?.trim().toUpperCase();
    if (!symbol) return;
    const main = await mainWatchlist();
    if (!main) return;
    try {
      await api(`/v1/watchlists/${main.id}/symbols`, { method: 'POST', body: JSON.stringify([symbol]) });
      input.value = '';
      await loadWatchlist();
    } catch (error) {
      const target = document.querySelector('#watchlist');
      if (target) target.textContent = error.message;
    }
  });

  document.querySelector('#refresh-watchlist')?.addEventListener('click', loadWatchlist);
  document.querySelector('#operator-form')?.addEventListener('submit', async event => {
    event.preventDefault();
    const text = document.querySelector('#operator-input')?.value?.trim();
    if (!text) return;
    const target = document.querySelector('#operation-plan');
    target.textContent = t('dashboard.working');
    try {
      const plan = await api(`/v1/operations/plan?text=${encodeURIComponent(text)}`, { method: 'POST' });
      renderPlan(plan);
      if (plan.source === 'deterministic' && !plan.requires_confirmation) await applyPlannedOperation(plan);
    } catch (error) { target.textContent = error.message; }
  });
  loadWatchlist();
}

document.addEventListener('DOMContentLoaded', () => {
  installLocaleSwitcher();
  installGlobalCommandShortcut();
  installCommandPalette();
  updateHealth();
  installDashboard();
});
window.Yowayowa = { api, fmt, escapeHtml, locale, localeTag, t };
