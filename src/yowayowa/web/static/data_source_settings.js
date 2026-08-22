(() => {
  const ja = window.YOWAYOWA_LOCALE === 'ja';
  const { api, escapeHtml } = window.Yowayowa;
  const sources = [
    {
      id: 'estat',
      label: 'e-Stat / Japan official statistics',
      title: ja ? '日本の政府統計（e-Stat）' : 'Japan government statistics (e-Stat)',
      description: ja
        ? '消費者物価・雇用・賃金などの政府統計に使います。アプリケーションIDはこのブラウザタブだけに保持します。'
        : 'Used for official Japanese statistics such as prices, employment and wages. The application ID stays only in this browser tab.',
      storageKey: 'yowayowa.datasource.estat.key.v1',
      placeholder: 'e-Stat application ID',
      docs: 'https://www.e-stat.go.jp/mypage/user/register',
      docsLabel: ja ? 'e-Statで利用登録 ↗' : 'Register for e-Stat API ↗',
    },
    {
      id: 'fred',
      label: 'FRED / personal macro',
      title: 'FRED',
      description: ja
        ? 'FREDの系列検索・時系列取得に使います。系列ごとの権利条件は元データ提供者に従います。APIキーはこのブラウザタブだけに保持します。'
        : 'Used for FRED series search and time series. Underlying series rights still follow their original providers. The API key stays only in this browser tab.',
      storageKey: 'yowayowa.datasource.fred.key.v1',
      placeholder: 'FRED API key',
      docs: 'https://fred.stlouisfed.org/docs/api/api_key.html',
      docsLabel: ja ? 'FRED APIキーを取得 ↗' : 'Get a FRED API key ↗',
    },
    {
      id: 'bea',
      label: 'BEA / US official macro',
      title: 'BEA / U.S. Bureau of Economic Analysis',
      description: ja
        ? '米国GDP・個人消費などBEAの公式統計に使います。UserIDはこのブラウザタブだけに保持します。'
        : 'Used for official BEA statistics such as U.S. GDP and personal consumption. The UserID stays only in this browser tab.',
      storageKey: 'yowayowa.datasource.bea.key.v1',
      placeholder: 'BEA UserID',
      docs: 'https://apps.bea.gov/api/signup/',
      docsLabel: ja ? 'BEA APIに登録 ↗' : 'Register for the BEA API ↗',
    },
    {
      id: 'bls',
      label: 'BLS / US labor statistics',
      title: 'BLS / U.S. Bureau of Labor Statistics',
      description: ja
        ? 'BLS Public Data API v2の拡張利用枠に使います。登録キーがなくてもv1相当の公開利用が可能な範囲はそのまま使えます。キーはこのブラウザタブだけに保持します。'
        : 'Used for the expanded BLS Public Data API v2 limits. Unregistered public access remains available where the provider supports it. The key stays only in this browser tab.',
      storageKey: 'yowayowa.datasource.bls.key.v1',
      placeholder: 'BLS registration key',
      docs: 'https://data.bls.gov/registrationEngine/',
      docsLabel: ja ? 'BLS APIに登録 ↗' : 'Register for the BLS API ↗',
    },
  ];

  function readKey(source) {
    try { return sessionStorage.getItem(source.storageKey) || ''; } catch (_) { return ''; }
  }

  function writeKey(source, value) {
    try {
      if (value) sessionStorage.setItem(source.storageKey, value);
      else sessionStorage.removeItem(source.storageKey);
    } catch (_) {}
  }

  function browserReady(source) {
    return Boolean(readKey(source));
  }

  async function serverStatus() {
    try { return await api('/v1/settings/status'); } catch (_) { return {}; }
  }

  function readinessText(serverReady, tabReady) {
    if (serverReady) return ja ? 'サーバーで利用可能' : 'Ready · server';
    if (tabReady) return ja ? 'このタブで利用可能' : 'Ready · browser tab';
    return ja ? '未設定' : 'Not configured';
  }

  function updateStatusCards(status) {
    const target = document.querySelector('#settings-data-sources');
    if (!target) return;
    for (const source of sources) {
      let card = target.querySelector(`[data-source="${source.id}"]`);
      if (!card && source.id === 'bls') {
        card = document.createElement('div');
        card.className = 'capability';
        card.dataset.source = source.id;
        target.append(card);
      }
      if (!card) continue;
      const ready = Boolean(status[source.id]) || browserReady(source);
      card.innerHTML = `<strong>${escapeHtml(source.label)}</strong><small class="ux-note ${ready ? 'badge-ok' : 'muted'}">${escapeHtml(readinessText(Boolean(status[source.id]), browserReady(source)))}</small>`;
    }
  }

  function panelMarkup(source) {
    const saved = readKey(source);
    return `<details class="datasource-key-panel datasource-key-disclosure" data-browser-source="${escapeHtml(source.id)}">
      <summary><span><strong>${escapeHtml(source.title)}</strong><small class="ux-note" data-role="status">${escapeHtml(readinessText(false, Boolean(saved)))}</small></span></summary>
      <div class="datasource-key-body">
        <p>${escapeHtml(source.description)} <a href="${escapeHtml(source.docs)}" target="_blank" rel="noreferrer">${escapeHtml(source.docsLabel)}</a></p>
        <div class="datasource-key-row">
          <input type="password" autocomplete="off" value="${escapeHtml(saved)}" placeholder="${escapeHtml(source.placeholder)}" aria-label="${escapeHtml(source.placeholder)}">
          <button class="primary" type="button" data-action="save">${ja ? '保存' : 'Save'}</button>
          <button class="ghost" type="button" data-action="clear">${ja ? '消去' : 'Clear'}</button>
        </div>
      </div>
    </details>`;
  }

  async function install() {
    const host = document.querySelector('#data-sources');
    const cards = document.querySelector('#settings-data-sources');
    if (!host || !cards) return;
    document.querySelector('[data-browser-source-group]')?.remove();

    const group = document.createElement('div');
    group.dataset.browserSourceGroup = 'true';
    group.className = 'datasource-browser-keys';
    group.innerHTML = sources.map(panelMarkup).join('');
    host.append(group);

    let status = await serverStatus();
    updateStatusCards(status);
    const observer = new MutationObserver(() => updateStatusCards(status));
    observer.observe(cards, { childList: true });

    group.addEventListener('click', event => {
      const button = event.target.closest('button[data-action]');
      if (!button) return;
      const panel = button.closest('[data-browser-source]');
      const source = sources.find(item => item.id === panel?.dataset.browserSource);
      if (!panel || !source) return;
      const input = panel.querySelector('input');
      const value = button.dataset.action === 'save' ? input.value.trim() : '';
      if (!value) input.value = '';
      writeKey(source, value);
      panel.querySelector('[data-role="status"]').textContent = readinessText(
        Boolean(status[source.id]),
        Boolean(value),
      );
      updateStatusCards(status);
      window.dispatchEvent(new CustomEvent('yowayowa:data-source-settings-changed', {
        detail: { source: source.id, configured: Boolean(value) },
      }));
    });

    window.addEventListener('yowayowa:data-source-settings-refresh', async () => {
      status = await serverStatus();
      updateStatusCards(status);
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install, { once: true });
  else install();
})();
