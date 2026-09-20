(() => {
  const { api, escapeHtml, t } = window.Yowayowa;
  const META_KEY = 'yowayowa.ai.settings.v2';
  const KEY_KEY = 'yowayowa.ai.key.v2';
  const CODEX_CREDENTIAL_KEY = 'yowayowa.codex.credential.v1';
  const LOCAL_PROVIDER_IDS = new Set(['ollama', 'lmstudio', 'vllm', 'codex']);
  const messages = [];
  let serverReady = null;

  function settingsMeta() {
    try { return JSON.parse(localStorage.getItem(META_KEY) || '{}'); }
    catch (_) { return {}; }
  }

  function storedApiKey() {
    try { return sessionStorage.getItem(KEY_KEY) || ''; }
    catch (_) { return ''; }
  }

  function codexCredential() {
    try { return localStorage.getItem(CODEX_CREDENTIAL_KEY) || ''; }
    catch (_) { return ''; }
  }

  function saveCodexCredential(value) {
    try {
      if (value) localStorage.setItem(CODEX_CREDENTIAL_KEY, value);
      else localStorage.removeItem(CODEX_CREDENTIAL_KEY);
    } catch (_) {}
  }

  function providerPayload() {
    const meta = settingsMeta();
    const providerId = meta.providerId || 'server';
    if (providerId === 'server') return null;
    if (!meta.model) throw new Error(`${t('ai.model')}: required`);
    const apiKey = storedApiKey();
    if (!apiKey && !LOCAL_PROVIDER_IDS.has(providerId)) {
      throw new Error(`${t('ai.api_key')}: required`);
    }
    return {
      provider: meta.adapter === 'anthropic'
        ? 'anthropic'
        : (meta.adapter === 'codex_cli' ? 'codex_cli' : 'openai_compatible'),
      model: meta.model,
      api_key: apiKey || 'local',
      base_url: meta.adapter === 'codex_cli' ? null : (meta.baseUrl || null),
      credential: meta.adapter === 'codex_cli' ? (codexCredential() || null) : null,
    };
  }

  function providerSummary() {
    const meta = settingsMeta();
    const target = document.querySelector('#ai-current-provider');
    if (!target) return;
    if (!meta.providerId || meta.providerId === 'server') {
      target.textContent = window.YOWAYOWA_LOCALE === 'ja'
        ? '使用設定: サーバー設定'
        : 'Using: server configuration';
      return;
    }
    target.textContent = `${window.YOWAYOWA_LOCALE === 'ja' ? '使用設定' : 'Using'}: ${meta.providerId}${meta.model ? ` · ${meta.model}` : ''}`;
  }

  function context() {
    const params = new URLSearchParams(location.search);
    const result = { page: location.pathname };
    if (params.get('symbol')) result.symbol = params.get('symbol');
    if (params.get('symbols')) result.symbols = params.get('symbols').split(',').filter(Boolean);
    if (params.get('strategy')) result.strategy = params.get('strategy');
    if (params.get('region')) result.region = params.get('region');
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


  async function codexReady() {
    try {
      let data = await api('/v1/ai/codex/status');
      if (data?.mode === 'hosted_bridge') {
        const credential = codexCredential();
        if (!credential) return false;
        data = await api('/v1/ai/codex/session-status', {
          method: 'POST',
          body: JSON.stringify({ credential }),
        });
        if (data?.credential) saveCodexCredential(data.credential);
      }
      return Boolean(data?.authenticated);
    } catch (_) {
      return false;
    }
  }

  async function generatePromptPacket() {
    const panel = document.querySelector('#ai-prompt-packet-panel');
    const output = document.querySelector('#ai-prompt-packet');
    const meta = document.querySelector('#ai-prompt-packet-meta');
    const draft = document.querySelector('#ai-prompt')?.value.trim() || '';
    document.querySelector('#ai-status').textContent = window.YOWAYOWA_LOCALE === 'ja'
      ? '研究パケットを生成中…'
      : 'Building research packet…';
    try {
      const data = await api('/v1/ai/prompt-packet', {
        method: 'POST',
        body: JSON.stringify({
          messages,
          context: context(),
          user_prompt: draft || null,
        }),
      });
      output.value = data.prompt || '';
      panel.hidden = false;
      panel.open = true;
      meta.textContent = `${Number(data.characters || output.value.length).toLocaleString()} chars · ${(data.included_tools || []).join(', ')}`;
      try {
        await navigator.clipboard.writeText(output.value);
        document.querySelector('#ai-status').textContent = window.YOWAYOWA_LOCALE === 'ja'
          ? '外部AI用プロンプトを生成してコピーしました。'
          : 'External AI prompt generated and copied.';
      } catch (_) {
        document.querySelector('#ai-status').textContent = window.YOWAYOWA_LOCALE === 'ja'
          ? '外部AI用プロンプトを生成しました。'
          : 'External AI prompt generated.';
      }
    } catch (error) {
      document.querySelector('#ai-status').textContent = error.message;
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

  function refreshSendAvailability() {
    const send = document.querySelector('#ai-send');
    if (!send) return;
    const meta = settingsMeta();
    const providerId = meta.providerId || 'server';
    if (providerId !== 'server') {
      try {
        providerPayload();
        send.disabled = false;
      } catch (_) {
        send.disabled = true;
      }
      return;
    }
    send.disabled = !serverReady;
  }

  async function submit(event) {
    event.preventDefault();
    const prompt = document.querySelector('#ai-prompt');
    const text = prompt.value.trim();
    if (!text) return;
    let provider;
    try { provider = providerPayload(); } catch (error) {
      document.querySelector('#ai-status').innerHTML = `<a href="/settings">${escapeHtml(error.message)} · ${escapeHtml(window.YOWAYOWA_LOCALE === 'ja' ? '設定を開く →' : 'Open Settings →')}</a>`;
      return;
    }
    if (provider === null && !(await ensureServerReady())) {
      showServerSetupRequired();
      return;
    }
    if (provider?.provider === 'codex_cli' && !(await codexReady())) {
      document.querySelector('#ai-status').innerHTML = `<a href="/settings">${escapeHtml(window.YOWAYOWA_LOCALE === 'ja' ? 'CodexをChatGPTで接続してください →' : 'Connect Codex with ChatGPT →')}</a>`;
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
      if (data.provider_credential) saveCodexCredential(data.provider_credential);
      addMessage('assistant', data.answer || '—');
      renderTrace(data.tool_trace || []);
      renderProposals(data.proposed_operations || []);
      document.querySelector('#ai-status').textContent = `${data.provider} · ${data.model} · ${data.tool_trace?.length || 0} tools`;
    } catch (error) {
      addMessage('assistant', error.message);
      document.querySelector('#ai-status').textContent = error.message;
    } finally {
      refreshSendAvailability();
      prompt.focus();
    }
  }

  async function loadStatus() {
    providerSummary();
    try {
      const data = await api('/v1/ai/status');
      const configured = configuredServerProviders(data);
      serverReady = configured.length > 0;
      const meta = settingsMeta();
      const usingServer = !meta.providerId || meta.providerId === 'server';
      if (usingServer && !serverReady) {
        refreshSendAvailability();
        showServerSetupRequired();
        return;
      }
      const current = meta.providerId || 'server';
      document.querySelector('#ai-status').textContent = usingServer
        ? `${data.tools?.length || 0} tools · server: ${configured.join(', ') || 'none'}`
        : `${data.tools?.length || 0} tools · ${current === 'codex' ? 'ChatGPT subscription' : 'BYOK'}`;
      refreshSendAvailability();
    } catch (error) {
      document.querySelector('#ai-status').textContent = error.message;
      refreshSendAvailability();
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    document.querySelector('#ai-form')?.addEventListener('submit', submit);
    document.querySelector('#ai-prompt')?.addEventListener('keydown', event => {
      if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) document.querySelector('#ai-form').requestSubmit();
    });
    document.querySelectorAll('.ai-quick-prompt').forEach(button => button.addEventListener('click', () => {
      document.querySelector('#ai-prompt').value = button.textContent.trim();
      document.querySelector('#ai-prompt').focus();
    }));
    document.querySelector('#ai-export-prompt')?.addEventListener('click', () => generatePromptPacket());
    document.querySelector('#ai-copy-prompt-packet')?.addEventListener('click', async () => {
      const value = document.querySelector('#ai-prompt-packet')?.value || '';
      if (!value) return;
      try {
        await navigator.clipboard.writeText(value);
        document.querySelector('#ai-status').textContent = window.YOWAYOWA_LOCALE === 'ja'
          ? '研究パケットをコピーしました。'
          : 'Research packet copied.';
      } catch (error) {
        document.querySelector('#ai-status').textContent = error.message;
      }
    });
    document.querySelector('#ai-clear')?.addEventListener('click', () => {
      messages.length = 0;
      renderMessages();
      renderTrace([]);
      renderProposals([]);
    });
    const params = new URLSearchParams(location.search);
    const symbols = params.get('symbols');
    const symbol = params.get('symbol');
    const strategy = params.get('strategy');
    const region = params.get('region');
    if (strategy) {
      const selected = symbols ? ` 選択済み候補は ${symbols}。` : '';
      document.querySelector('#ai-prompt').value = `${strategy} を${region ? ` ${region.toUpperCase()} 市場で` : ''}実行して、解釈可能な研究優先度で候補を絞り込んで。${selected} 上位候補について、なぜ今調べる価値があるか、最初の棄却条件、追加で確認すべき一次情報・ニュース・イベントまで自律的に掘って。スコアを期待収益率として扱わず、根拠と推論を分けて。`;
    } else if (symbols) document.querySelector('#ai-prompt').value = `${symbols} を比較して、成長性・割安さ・需給・アナリスト予想・主要リスクを調べて。`;
    else if (symbol) document.querySelector('#ai-prompt').value = `${symbol} を財務・バリュエーション・アナリスト予想・保有状況・インサイダー・ニュース・今後のイベントまで横断分析して。`;
    document.querySelector('#ai-send').disabled = true;
    loadStatus();
  });
})();
