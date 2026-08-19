(() => {
  const { api, escapeHtml, t } = window.Yowayowa;
  const STORAGE_KEY = 'yowayowa.ai.byok.session';
  const messages = [];
  let serverReady = null;
  const presets = {
    openai: 'https://api.openai.com/v1',
    openrouter: 'https://openrouter.ai/api/v1',
    gemini: 'https://generativelanguage.googleapis.com/v1beta/openai',
    anthropic: 'https://api.anthropic.com',
  };

  function currentConfig() {
    return {
      provider: document.querySelector('#ai-provider').value,
      model: document.querySelector('#ai-model').value.trim(),
      baseUrl: document.querySelector('#ai-base-url').value.trim(),
      apiKey: document.querySelector('#ai-api-key').value,
    };
  }

  function saveConfig() {
    try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify(currentConfig())); } catch (_) {}
  }

  function restoreConfig() {
    try {
      const raw = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || '{}');
      if (raw.provider) document.querySelector('#ai-provider').value = raw.provider;
      if (raw.model) document.querySelector('#ai-model').value = raw.model;
      if (raw.baseUrl) document.querySelector('#ai-base-url').value = raw.baseUrl;
      if (raw.apiKey) document.querySelector('#ai-api-key').value = raw.apiKey;
    } catch (_) {}
    updateProviderFields(false);
  }

  function updateProviderFields(overwriteUrl = true) {
    const provider = document.querySelector('#ai-provider').value;
    const model = document.querySelector('#ai-model');
    const base = document.querySelector('#ai-base-url');
    const key = document.querySelector('#ai-api-key');
    const server = provider === 'server';
    model.disabled = server;
    base.disabled = server;
    key.disabled = server;
    if (!server && overwriteUrl && presets[provider]) base.value = presets[provider];
    saveConfig();
  }

  function providerPayload() {
    const config = currentConfig();
    if (config.provider === 'server') return null;
    if (!config.model) throw new Error(`${t('ai.model')}: required`);
    if (!config.apiKey) throw new Error(`${t('ai.api_key')}: required`);
    return {
      provider: config.provider === 'anthropic' ? 'anthropic' : 'openai_compatible',
      model: config.model,
      api_key: config.apiKey,
      base_url: config.baseUrl || null,
    };
  }

  function context() {
    const params = new URLSearchParams(location.search);
    const result = { page: location.pathname };
    if (params.get('symbol')) result.symbol = params.get('symbol');
    if (params.get('symbols')) result.symbols = params.get('symbols').split(',').filter(Boolean);
    return result;
  }

  function addMessage(role, content) {
    messages.push({ role, content });
    if (messages.length > 24) messages.splice(0, messages.length - 24);
    renderMessages();
  }

  function renderMessages() {
    const target = document.querySelector('#ai-messages');
    target.innerHTML = messages.map(message => `<article class="ai-message ${escapeHtml(message.role)}">
      <span class="role">${escapeHtml(message.role)}</span><pre>${escapeHtml(message.content)}</pre>
    </article>`).join('');
    target.scrollTop = target.scrollHeight;
  }

  function renderTrace(trace) {
    const target = document.querySelector('#ai-trace');
    if (!trace?.length) {
      target.innerHTML = `<span class="muted">${escapeHtml(t('ai.no_tools'))}</span>`;
      return;
    }
    target.innerHTML = trace.map(item => `<div class="ai-trace">
      <strong>${escapeHtml(item.tool)}</strong>${item.mutating ? ' · proposal' : ''}
      <pre>${escapeHtml(JSON.stringify(item.arguments, null, 2))}\n→ ${escapeHtml(item.result_preview || '')}</pre>
    </div>`).join('');
  }

  function proposalLabel(operation) {
    const kind = operation.kind || '';
    const args = operation.arguments || {};
    if (kind === 'watchlist.add') return `Watchlist + ${args.symbols?.join(', ') || ''}`;
    if (kind === 'watchlist.remove') return `Watchlist − ${args.symbols?.join(', ') || ''}`;
    if (kind === 'compare.symbols') return `Compare ${args.symbols?.join(', ') || ''}`;
    if (kind === 'screen.set_filters') return `Screener · ${args.filters?.length || 0} filters`;
    if (kind === 'chart.set_indicators') return `Chart · ${args.indicators?.join(', ') || ''}`;
    return kind;
  }

  function renderProposals(operations) {
    const target = document.querySelector('#ai-proposals');
    if (!operations?.length) {
      target.innerHTML = `<span class="muted">${escapeHtml(t('ai.no_proposals'))}</span>`;
      return;
    }
    target.innerHTML = operations.map((operation, index) => `<div class="ai-proposal">
      <strong>${escapeHtml(proposalLabel(operation))}</strong>
      <button class="ghost ai-apply-proposal" type="button" data-index="${index}">${escapeHtml(t('ai.apply'))}</button>
    </div>`).join('');
    target.querySelectorAll('.ai-apply-proposal').forEach(button => {
      button.addEventListener('click', () => applyProposal(operations[Number(button.dataset.index)], button));
    });
  }

  async function mainWatchlist() {
    const lists = await api('/v1/watchlists');
    return lists.find(item => item.name === 'Main') || lists[0] || null;
  }

  async function applyProposal(operation, button) {
    const kind = operation.kind;
    const args = operation.arguments || {};
    button.disabled = true;
    try {
      if (kind === 'watchlist.add' || kind === 'watchlist.remove') {
        const main = await mainWatchlist();
        if (!main) throw new Error('Main watchlist not found');
        if (kind === 'watchlist.add') {
          await api(`/v1/watchlists/${main.id}/symbols`, {
            method: 'POST',
            body: JSON.stringify(args.symbols || []),
          });
        } else {
          for (const symbol of (args.symbols || [])) {
            await api(`/v1/watchlists/${main.id}/symbols/${encodeURIComponent(symbol)}`, { method: 'DELETE' });
          }
        }
        button.textContent = '✓';
        return;
      }
      if (kind === 'compare.symbols') {
        location.assign(`/compare?symbols=${encodeURIComponent((args.symbols || []).join(','))}`);
        return;
      }
      if (kind === 'screen.set_filters') {
        sessionStorage.setItem('yowayowa.screener.proposal', JSON.stringify(args));
        location.assign('/screener?proposal=1');
        return;
      }
      if (kind === 'chart.set_indicators') {
        const params = new URLSearchParams(location.search);
        const symbol = params.get('symbol') || context().symbol;
        if (symbol) location.assign(`/instrument/${encodeURIComponent(symbol)}?indicators=${encodeURIComponent((args.indicators || []).join(','))}`);
      }
    } catch (error) {
      button.disabled = false;
      button.textContent = error.message;
    }
  }

  function configuredServerProviders(data) {
    return Object.entries(data.configured || {}).filter(([, value]) => value).map(([name]) => name);
  }

  function showServerSetupRequired() {
    const target = document.querySelector('#ai-status');
    const label = window.YOWAYOWA_LOCALE === 'ja'
      ? 'AIプロバイダーが未設定です。設定を開く →'
      : 'No AI provider is configured. Open Settings →';
    target.innerHTML = `<a href="/settings">${escapeHtml(label)}</a>`;
  }

  async function ensureServerReady() {
    if (serverReady !== null) return serverReady;
    try {
      const data = await api('/v1/ai/status');
      serverReady = configuredServerProviders(data).length > 0;
    } catch (_) {
      return true;
    }
    return serverReady;
  }

  async function submit(event) {
    event.preventDefault();
    const prompt = document.querySelector('#ai-prompt');
    const text = prompt.value.trim();
    if (!text) return;
    let provider;
    try { provider = providerPayload(); } catch (error) {
      document.querySelector('#ai-status').textContent = error.message;
      return;
    }
    if (provider === null && !(await ensureServerReady())) {
      showServerSetupRequired();
      return;
    }
    addMessage('user', text);
    prompt.value = '';
    const send = document.querySelector('#ai-send');
    send.disabled = true;
    document.querySelector('#ai-status').textContent = t('ai.working');
    try {
      const data = await api('/v1/ai/chat', {
        method: 'POST',
        body: JSON.stringify({
          messages,
          provider,
          context: context(),
          max_tool_rounds: 7,
          allow_mutations: false,
        }),
      });
      addMessage('assistant', data.answer || '—');
      renderTrace(data.tool_trace || []);
      renderProposals(data.proposed_operations || []);
      document.querySelector('#ai-status').textContent = `${data.provider} · ${data.model} · ${data.tool_trace?.length || 0} tools`;
    } catch (error) {
      addMessage('assistant', error.message);
      document.querySelector('#ai-status').textContent = error.message;
    } finally {
      send.disabled = false;
      prompt.focus();
    }
  }

  async function loadStatus() {
    try {
      const data = await api('/v1/ai/status');
      const configured = configuredServerProviders(data);
      serverReady = configured.length > 0;
      const usingServer = currentConfig().provider === 'server';
      document.querySelector('#ai-send').disabled = usingServer && !serverReady;
      if (usingServer && !serverReady) {
        showServerSetupRequired();
        return;
      }
      document.querySelector('#ai-status').textContent = `${data.tools?.length || 0} tools · server: ${configured.join(', ') || 'none'}`;
    } catch (error) {
      document.querySelector('#ai-status').textContent = error.message;
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    restoreConfig();
    ['#ai-model', '#ai-base-url', '#ai-api-key'].forEach(selector => document.querySelector(selector)?.addEventListener('input', saveConfig));
    document.querySelector('#ai-provider')?.addEventListener('change', () => updateProviderFields(true));
    document.querySelector('#ai-form')?.addEventListener('submit', submit);
    document.querySelector('#ai-prompt')?.addEventListener('keydown', event => {
      if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) document.querySelector('#ai-form').requestSubmit();
    });
    document.querySelectorAll('.ai-quick-prompt').forEach(button => button.addEventListener('click', () => {
      document.querySelector('#ai-prompt').value = button.textContent.trim();
      document.querySelector('#ai-prompt').focus();
    }));
    document.querySelector('#ai-clear')?.addEventListener('click', () => {
      messages.length = 0;
      renderMessages();
      renderTrace([]);
      renderProposals([]);
    });
    const params = new URLSearchParams(location.search);
    const symbols = params.get('symbols');
    const symbol = params.get('symbol');
    if (symbols) document.querySelector('#ai-prompt').value = `${symbols} を比較して、成長性・割安さ・需給・アナリスト予想・主要リスクを調べて。`;
    else if (symbol) document.querySelector('#ai-prompt').value = `${symbol} を財務・バリュエーション・アナリスト予想・保有状況・インサイダー・ニュース・今後のイベントまで横断分析して。`;
    document.querySelector('#ai-send').disabled = true;
    loadStatus();
  });
})();
