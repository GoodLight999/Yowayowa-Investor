(() => {
  if (!window.__YOWAYOWA_FETCH_WRAPPED) {
    window.__YOWAYOWA_FETCH_WRAPPED = true;
    const rawFetch = window.fetch.bind(window);
    const credentials = [
      {
        prefix: '/v1/filings/edinet',
        storageKey: 'yowayowa.datasource.edinet.key.v1',
        header: 'X-Yowayowa-EDINET-Key',
      },
      {
        prefix: '/v1/macro/estat',
        storageKey: 'yowayowa.datasource.estat.key.v1',
        header: 'X-Yowayowa-Estat-Key',
      },
      {
        prefix: '/v1/macro/fred',
        storageKey: 'yowayowa.datasource.fred.key.v1',
        header: 'X-Yowayowa-FRED-Key',
      },
      {
        prefix: '/v1/macro/bea',
        storageKey: 'yowayowa.datasource.bea.key.v1',
        header: 'X-Yowayowa-BEA-Key',
      },
      {
        prefix: '/v1/macro/bls',
        storageKey: 'yowayowa.datasource.bls.key.v1',
        header: 'X-Yowayowa-BLS-Key',
      },
    ];

    window.fetch = (input, init = {}) => {
      const rawUrl = typeof input === 'string' ? input : input?.url;
      if (!rawUrl) return rawFetch(input, init);
      const url = new URL(rawUrl, window.location.origin);
      if (url.origin === window.location.origin) {
        const credential = credentials.find(item => url.pathname.startsWith(item.prefix));
        if (credential) {
          let key = '';
          try { key = sessionStorage.getItem(credential.storageKey) || ''; } catch (_) {}
          if (key) {
            const inherited = typeof input === 'string' ? undefined : input.headers;
            const headers = new Headers(init.headers || inherited);
            headers.set(credential.header, key);
            init = { ...init, headers };
          }
        }
      }
      return rawFetch(input, init);
    };
  }

  let firstTurboLoad = true;

  function installCurrentCompany() {
    if (window.YOWAYOWA_MODE !== 'personal') return;
    let company = null;
    try { company = JSON.parse(localStorage.getItem('yowayowa.company-context.v1') || 'null'); } catch (_) {}
    const host = document.querySelector('.masthead-actions');
    host?.querySelector('.current-company-link')?.remove();
    if (!company?.symbol || !host) return;
    const link = document.createElement('a');
    link.className = 'current-company-link';
    link.href = `/instrument/${encodeURIComponent(company.symbol)}`;
    const name = company.name || company.symbol;
    const escape = value => String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
    link.innerHTML = `<span>${window.YOWAYOWA_LOCALE === 'ja' ? '選択中' : 'Current'}</span><strong>${escape(name)}</strong>`;
    host.prepend(link);
  }

  function rebindCommonPageUI() {
    // app.js is intentionally evaluated once. Turbo replaces the body, so the
    // new controls need listeners, but document-level listeners must not be
    // registered again. Calling the individual installers avoids the previous
    // synthetic DOMContentLoaded event, which accumulated global shortcuts.
    window.installLocaleSwitcher?.();
    window.installCommandPalette?.();
    window.updateHealth?.();
    window.installDashboard?.();
  }

  document.addEventListener('DOMContentLoaded', installCurrentCompany);
  document.addEventListener('turbo:load', () => {
    installCurrentCompany();
    if (firstTurboLoad) {
      firstTurboLoad = false;
      return;
    }
    rebindCommonPageUI();
  });
})();
