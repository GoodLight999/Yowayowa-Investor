(() => {
  const { api, escapeHtml, t } = window.Yowayowa;
  const META_KEY = 'yowayowa.ai.settings.v2';
  const KEY_KEY = 'yowayowa.ai.key.v2';
  const PKCE_KEY = 'yowayowa.openrouter.pkce.v1';
  const ENABLED_KEY = 'yowayowa.ai.enabled-providers.v1';
  const EDINET_KEY = 'yowayowa.datasource.edinet.key.v1';
  const ja = window.YOWAYOWA_LOCALE === 'ja';
  let providers = [];
  let enabledProviders = [];
  let dataSourceStatus = {};

  const providerSelect = document.querySelector('#settings-provider');
  const modelInput = document.querySelector('#settings-model');
  const baseInput = document.querySelector('#settings-base-url');
  const keyInput = document.querySelector('#settings-api-key');
  const contextToggle = document.querySelector('#context-ai-enabled');
  const status = document.querySelector('#settings-status');

  function readJson(storage, key, fallback) {
    try { return JSON.parse(storage.getItem(key) || JSON.stringify(fallback)); }
    catch (_) { return fallback; }
  }

  function readMeta() { return readJson(localStorage, META_KEY, {}); }
  function readKey() { try { return sessionStorage.getItem(KEY_KEY) || ''; } catch (_) { return ''; } }
  function readEdinetKey() { try { return sessionStorage.getItem(EDINET_KEY) || ''; } catch (_) { return ''; } }
  function selectedProvider() { return providers.find(item => item.id === providerSelect.value) || null; }
  function unlistedAllowed() { return dataSourceStatus.allow_unlisted_ai_endpoints !== false; }
  function providerAllowed(item) { return unlistedAllowed() || item.category === 'cloud'; }

  function effectiveApiKey(provider) {
    const entered = keyInput.value || readKey();
    if (entered) return entered;
    return provider?.auth_modes?.includes('none') ? 'local' : '';
  }

  function enabledIds() {
    const saved = readJson(localStorage, ENABLED_KEY, null);
    const meta = readMeta();
    let ids = Array.isArray(saved)
      ? saved.filter(id => providers.some(p => p.id === id && providerAllowed(p)))
      : ['openrouter'].filter(id => providers.some(p => p.id === id && providerAllowed(p)));
    if (
      meta.providerId
      && meta.providerId !== 'server'
      && providers.some(p => p.id === meta.providerId && providerAllowed(p))
      && !ids.includes(meta.providerId)
    ) ids.push(meta.providerId);
    return ids;
  }

  function saveEnabled() {
    try { localStorage.setItem(ENABLED_KEY, JSON.stringify(enabledProviders)); } catch (_) {}
  }

  function save() {
    const provider = selectedProvider();
    const providerId = providerSelect.value || 'server';
    const meta = {
      providerId,
      adapter: provider?.adapter || 'openai_compatible',
      model: modelInput.value.trim(),
      baseUrl: baseInput.value.trim(),
      contextualEnabled: contextToggle.checked,
    };
    try {
      localStorage.setItem(META_KEY, JSON.stringify(meta));
      if (keyInput.value) sessionStorage.setItem(KEY_KEY, keyInput.value);
      else sessionStorage.removeItem(KEY_KEY);
    } catch (_) {}
    status.textContent = t('settings.saved', {}, 'Saved.');
    window.dispatchEvent(new CustomEvent('yowayowa:settings-changed'));
  }

  function rebuildProviderSelect(preferred = null) {
    const selected = preferred || providerSelect.value || readMeta().providerId || 'server';
    providerSelect.innerHTML = '<option value="server">Server configuration</option>' + providers
      .filter(item => providerAllowed(item) && enabledProviders.includes(item.id))
      .map(item => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.label)}</option>`)
      .join('');
    providerSelect.value = [...providerSelect.options].some(option => option.value === selected) ? selected : 'server';
    syncProvider(false);
  }

  function restore() {
    const meta = readMeta();
    rebuildProviderSelect(meta.providerId);
    if (meta.model) modelInput.value = meta.model;
    if (meta.baseUrl) baseInput.value = meta.baseUrl;
    if (typeof meta.contextualEnabled === 'boolean') contextToggle.checked = meta.contextualEnabled;
    const key = readKey(); if (key && key !== 'local') keyInput.value = key;
    syncProvider(false);
  }

  function syncProvider(overwrite = true) {
    const provider = selectedProvider();
    const server = providerSelect.value === 'server';
    modelInput.disabled = server; baseInput.disabled = server; keyInput.disabled = server;
    document.querySelector('#fetch-provider-models').disabled = server;
    if (provider && overwrite) baseInput.value = provider.base_url;
    const oauth = document.querySelector('#openrouter-oauth');
    oauth.hidden = providerSelect.value !== 'openrouter';
    keyInput.placeholder = provider?.auth_modes?.includes('none') ? 'not required' : '';
  }

  function authLabel(item) {
    const modes = new Set(item.auth_modes || []);
    const labels = [];
    if (modes.has('oauth_pkce')) labels.push('OAuth');
    if (modes.has('api_key')) labels.push(ja ? 'APIキー' : 'API key');
    if (modes.has('none')) labels.push(ja ? '認証不要' : 'No auth');
    return labels.join(' / ');
  }

  function renderProviderCatalog() {
    const target = document.querySelector('#provider-catalog');
    const active = providers.filter(item => providerAllowed(item) && enabledProviders.includes(item.id));
    target.innerHTML = active.length ? active.map(item => `<div class="provider-row">
      <div><strong>${escapeHtml(item.label)}</strong><small>${escapeHtml(item.category)}</small></div>
      <div><small>${escapeHtml(item.note || '')}</small>${item.docs_url ? `<small><a href="${escapeHtml(item.docs_url)}" target="_blank" rel="noreferrer">docs ↗</a></small>` : ''}</div>
      <span class="provider-auth">${escapeHtml(authLabel(item))}</span>
    </div>`).join('') : `<div class="list-state muted">${ja ? '利用するプロバイダを有効にしてください。' : 'Enable a provider to show it here.'}</div>`;
  }

  function renderProviderManager() {
    const catalog = document.querySelector('#provider-catalog');
    document.querySelector('.provider-manager')?.remove();
    const manager = document.createElement('details');
    manager.className = 'provider-manager';
    manager.innerHTML = `<summary><span>${ja ? '利用するプロバイダを管理' : 'Manage enabled providers'}</span><span class="pill">${enabledProviders.length}/${providers.length}</span></summary><div class="provider-toggle-grid">${providers.map(item => {
      const allowed = providerAllowed(item);
      const suffix = allowed ? '' : (ja ? ' · セルフホスト専用' : ' · self-host only');
      return `<label class="provider-toggle ${allowed ? '' : 'muted'}"><input type="checkbox" value="${escapeHtml(item.id)}" ${enabledProviders.includes(item.id) ? 'checked' : ''} ${allowed ? '' : 'disabled'}><span>${escapeHtml(item.label + suffix)}</span></label>`;
    }).join('')}</div>`;
    catalog.insertAdjacentElement('beforebegin', manager);
    manager.addEventListener('change', () => {
      enabledProviders = [...manager.querySelectorAll('input:checked:not(:disabled)')].map(input => input.value);
      saveEnabled(); rebuildProviderSelect(); renderProviderCatalog();
      manager.querySelector('.pill').textContent = `${enabledProviders.length}/${providers.length}`;
    });
  }

  function applyEndpointPolicy() {
    const permitted = enabledProviders.filter(id => providers.some(item => item.id === id && providerAllowed(item)));
    if (permitted.length !== enabledProviders.length) {
      enabledProviders = permitted;
      saveEnabled();
    }
    const meta = readMeta();
    if (meta.providerId && meta.providerId !== 'server') {
      const selected = providers.find(item => item.id === meta.providerId);
      if (selected && !providerAllowed(selected)) {
        try {
          localStorage.setItem(META_KEY, JSON.stringify({ ...meta, providerId: 'server', model: '', baseUrl: '' }));
          sessionStorage.removeItem(KEY_KEY);
        } catch (_) {}
      }
    }
    rebuildProviderSelect();
    renderProviderCatalog();
    renderProviderManager();
  }

  async function loadProviders() {
    providers = await api('/v1/ai/providers');
    enabledProviders = enabledIds();
    renderProviderCatalog(); renderProviderManager(); restore();
  }

  function installModelFilter(target) {
    document.querySelector('.model-filter-bar')?.remove();
    const bar = document.createElement('div'); bar.className = 'model-filter-bar';
    bar.innerHTML = `<input type="search" autocomplete="off" placeholder="${ja ? 'モデル名で絞り込み' : 'Filter models'}"><span class="model-filter-count">—</span>`;
    target.insertAdjacentElement('beforebegin', bar);
    const apply = () => {
      const buttons = [...target.querySelectorAll('.model-result')];
      const q = bar.querySelector('input').value.trim().toLowerCase();
      let shown = 0;
      buttons.forEach(button => {
        const hit = !q || button.textContent.toLowerCase().includes(q);
        button.hidden = !hit;
        if (hit) shown += 1;
      });
      bar.querySelector('.model-filter-count').textContent = `${shown}/${buttons.length}`;
    };
    bar.querySelector('input').addEventListener('input', apply); apply();
  }

  async function fetchModels() {
    const provider = selectedProvider();
    const target = document.querySelector('#model-results');
    if (!provider) return;
    const apiKey = effectiveApiKey(provider);
    if (!apiKey) { status.textContent = t('ai.api_key', {}, 'API key') + ': required'; return; }
    if (!modelInput.value.trim()) modelInput.value = 'model-discovery-placeholder';
    status.textContent = t('common.loading_ellipsis');
    try {
      const data = await api('/v1/ai/models', {
        method: 'POST',
        body: JSON.stringify({
          provider: {
            provider: provider.adapter,
            model: modelInput.value.trim(),
            api_key: apiKey,
            base_url: baseInput.value.trim() || provider.base_url,
          },
          limit: 1000,
        }),
      });
      target.hidden = false;
      target.innerHTML = data.models.length
        ? data.models.map(model => `<button class="model-result" type="button" data-model="${escapeHtml(model)}">${escapeHtml(model)}</button>`).join('')
        : '<div class="list-state muted">No models returned.</div>';
      target.querySelectorAll('.model-result').forEach(button => button.addEventListener('click', () => {
        modelInput.value = button.dataset.model;
        save();
      }));
      installModelFilter(target);
      status.textContent = `${data.models.length} models`;
    } catch (error) { status.textContent = error.message; }
  }

  function bytesToBase64Url(bytes) {
    let binary = '';
    for (const byte of bytes) binary += String.fromCharCode(byte);
    return btoa(binary).replaceAll('+','-').replaceAll('/','_').replaceAll('=','');
  }

  async function sha256Base64Url(text) {
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
    return bytesToBase64Url(new Uint8Array(digest));
  }

  async function startOpenRouterOAuth() {
    const bytes = new Uint8Array(64);
    crypto.getRandomValues(bytes);
    const verifier = bytesToBase64Url(bytes);
    const challenge = await sha256Base64Url(verifier);
    const callback = new URL('/settings', location.origin);
    const lang = new URLSearchParams(location.search).get('lang');
    if (lang) callback.searchParams.set('lang',lang);
    try { sessionStorage.setItem(PKCE_KEY, JSON.stringify({ verifier, callback: callback.toString() })); } catch (_) {}
    const auth = new URL('https://openrouter.ai/auth');
    auth.searchParams.set('callback_url',callback.toString());
    auth.searchParams.set('code_challenge',challenge);
    auth.searchParams.set('code_challenge_method','S256');
    location.assign(auth.toString());
  }

  async function finishOpenRouterOAuth() {
    const code = new URLSearchParams(location.search).get('code');
    if (!code) return false;
    let pkce = {};
    try { pkce = JSON.parse(sessionStorage.getItem(PKCE_KEY) || '{}'); } catch (_) {}
    if (!pkce.verifier) { status.textContent = 'OpenRouter OAuth verifier is missing.'; return true; }
    status.textContent = 'OpenRouter OAuth…';
    try {
      const response = await fetch('https://openrouter.ai/api/v1/auth/keys', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({
          code,
          code_verifier: pkce.verifier,
          code_challenge_method: 'S256',
        }),
      });
      if (!response.ok) throw new Error(`OpenRouter OAuth HTTP ${response.status}`);
      const data = await response.json();
      if (!data.key) throw new Error('OpenRouter did not return an API key.');
      sessionStorage.setItem(KEY_KEY, data.key);
      keyInput.value = data.key;
      if (!enabledProviders.includes('openrouter')) {
        enabledProviders.push('openrouter');
        saveEnabled();
        renderProviderManager();
        renderProviderCatalog();
      }
      rebuildProviderSelect('openrouter');
      syncProvider(true);
      save();
      sessionStorage.removeItem(PKCE_KEY);
      const clean = new URL(location.href);
      clean.searchParams.delete('code');
      history.replaceState(null,'',clean.toString());
      status.textContent = 'OpenRouter connected.';
    } catch(error) { status.textContent = error.message; }
    return true;
  }

  function renderDataStatus() {
    const target = document.querySelector('#settings-data-sources');
    if (!target) return;
    const labels = {
      sec: 'SEC / US filings',
      yahoo_personal: 'Yahoo / personal market data',
      edinet: 'EDINET / Japan official filings',
      estat: 'e-Stat / Japan official statistics',
      fred: 'FRED / personal macro',
      bea: 'BEA / US official macro',
    };
    const browserEdinet = Boolean(readEdinetKey());
    target.innerHTML = Object.entries(labels).map(([key,label]) => {
      const serverReady = Boolean(dataSourceStatus[key]);
      const ready = serverReady || (key === 'edinet' && browserEdinet);
      let state = ready ? (ja ? '利用可能' : 'Ready') : (ja ? '未設定' : 'Not configured');
      if (key === 'edinet' && serverReady) state = ja ? 'サーバーで利用可能' : 'Ready · server';
      else if (key === 'edinet' && browserEdinet) state = ja ? 'このタブで利用可能' : 'Ready · browser tab';
      return `<div class="capability" data-source="${escapeHtml(key)}"><strong>${escapeHtml(label)}</strong><span class="${ready ? 'badge-ok' : 'muted'}">${escapeHtml(state)}</span></div>`;
    }).join('');
  }

  function installEdinetSettings() {
    const target = document.querySelector('#settings-data-sources');
    if (!target) return;
    const saved = readEdinetKey();
    const panel = document.createElement('div');
    panel.className = 'datasource-key-panel';
    panel.innerHTML = `<h3>${ja ? '日本企業の公式開示（EDINET）' : 'Japan official filings (EDINET)'}</h3><p>${ja ? '日本株の銘柄ページから自動利用します。キーはこのブラウザタブだけに保持します。' : 'Used automatically from Japanese company pages. The key stays only in this browser tab.'} <a href="https://api.edinet-fsa.go.jp/api/auth/index.aspx?mode=1" target="_blank" rel="noreferrer">${ja ? 'EDINETでAPIキーを発行 ↗' : 'Get an EDINET API key ↗'}</a></p><div class="datasource-key-row"><input id="edinet-browser-key" type="password" autocomplete="off" value="${escapeHtml(saved)}" placeholder="EDINET API key"><button id="save-edinet-browser-key" class="primary" type="button">${ja ? '保存' : 'Save'}</button><button id="clear-edinet-browser-key" class="ghost" type="button">${ja ? '消去' : 'Clear'}</button></div><div id="edinet-browser-key-status" class="ux-note">${saved ? (ja ? 'このタブで利用可能' : 'Ready in this tab') : (ja ? '未設定' : 'Not configured')}</div>`;
    target.insertAdjacentElement('afterend',panel);
    const state = value => {
      panel.querySelector('#edinet-browser-key-status').textContent = value ? (ja ? 'このタブで利用可能' : 'Ready in this tab') : (ja ? '未設定' : 'Not configured');
      renderDataStatus();
    };
    panel.querySelector('#save-edinet-browser-key').addEventListener('click', () => {
      const value = panel.querySelector('#edinet-browser-key').value.trim();
      try {
        if (value) sessionStorage.setItem(EDINET_KEY,value);
        else sessionStorage.removeItem(EDINET_KEY);
      } catch (_) {}
      state(value);
    });
    panel.querySelector('#clear-edinet-browser-key').addEventListener('click', () => {
      panel.querySelector('#edinet-browser-key').value = '';
      try { sessionStorage.removeItem(EDINET_KEY); } catch (_) {}
      state('');
    });
    const advanced = document.createElement('details');
    advanced.className = 'advanced-tools';
    advanced.innerHTML = `<summary>${ja ? '詳細データ・開発者向け' : 'Advanced data & developer tools'}</summary><div class="advanced-tools-links"><a class="ghost button" href="/edinet">${ja ? 'EDINET原典' : 'EDINET source'}</a><a class="ghost button" href="/institutional">${ja ? '米国機関投資家の保有開示' : 'US institutional holdings'}</a><a class="ghost button" href="/licenses">${ja ? 'データライセンス' : 'Data licenses'}</a><a class="ghost button" href="/docs" data-turbo="false">API</a></div>`;
    panel.insertAdjacentElement('afterend',advanced);
  }

  async function loadDataStatus() {
    const target = document.querySelector('#settings-data-sources');
    try {
      dataSourceStatus = await api('/v1/settings/status');
      renderDataStatus();
      installEdinetSettings();
      applyEndpointPolicy();
    } catch(error) { target.textContent = error.message; }
  }

  providerSelect?.addEventListener('change', () => syncProvider(true));
  document.querySelector('#ai-settings-form')?.addEventListener('submit', event => {
    event.preventDefault();
    save();
  });
  document.querySelector('#clear-ai-api-key')?.addEventListener('click', () => {
    keyInput.value = '';
    save();
    status.textContent = ja ? 'APIキーを消去しました。' : 'API key cleared.';
  });
  document.querySelector('#fetch-provider-models')?.addEventListener('click',fetchModels);
  document.querySelector('#openrouter-oauth')?.addEventListener('click', () => startOpenRouterOAuth().catch(error => {
    status.textContent = error.message;
  }));

  Promise.all([loadProviders(),loadDataStatus()])
    .then(() => finishOpenRouterOAuth())
    .catch(error => { status.textContent = error.message; });
})();
