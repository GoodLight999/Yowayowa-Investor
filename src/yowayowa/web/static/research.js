(() => {
  const { api, escapeHtml, fmt, t, localeTag } = window.Yowayowa;
  const root = document.querySelector('#research-workspace');
  const symbol = root?.dataset.symbol;
  const cache = new Map();

  const label = (value) => String(value)
    .replaceAll('_', ' ')
    .replace(/([a-z])([A-Z])/g, '$1 $2');

  const display = (value) => {
    if (value === null || value === undefined || value === '') return '—';
    if (typeof value === 'number') {
      if (Math.abs(value) >= 1e9) return new Intl.NumberFormat(localeTag, { notation: 'compact', maximumFractionDigits: 2 }).format(value);
      return fmt.format(value);
    }
    if (typeof value === 'boolean') return value ? 'true' : 'false';
    return String(value);
  };

  function table(rows) {
    if (!rows.length) return `<span class="muted">${escapeHtml(t('research.none'))}</span>`;
    const keys = [...new Set(rows.slice(0, 40).flatMap(row => Object.keys(row || {})))].slice(0, 14);
    if (!keys.length) return `<pre class="research-json">${escapeHtml(JSON.stringify(rows, null, 2))}</pre>`;
    const head = keys.map(key => `<th>${escapeHtml(label(key))}</th>`).join('');
    const body = rows.map(row => `<tr>${keys.map(key => {
      const value = row?.[key];
      const rendered = typeof value === 'object' && value !== null ? JSON.stringify(value) : display(value);
      return `<td>${escapeHtml(rendered)}</td>`;
    }).join('')}</tr>`).join('');
    return `<div class="research-table-wrap"><table class="research-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  }

  function keyValues(object) {
    const rows = Object.entries(object || {}).map(([key, value]) => {
      let rendered = value;
      if (Array.isArray(value)) rendered = value.join(', ');
      else if (typeof value === 'object' && value !== null) rendered = JSON.stringify(value);
      return `<dt>${escapeHtml(label(key))}</dt><dd>${escapeHtml(display(rendered))}</dd>`;
    }).join('');
    return rows ? `<dl class="research-kv">${rows}</dl>` : `<span class="muted">${escapeHtml(t('research.none'))}</span>`;
  }

  function renderValue(name, value) {
    if (Array.isArray(value)) {
      if (value.every(item => item && typeof item === 'object' && !Array.isArray(item))) return table(value);
      return `<pre class="research-json">${escapeHtml(JSON.stringify(value, null, 2))}</pre>`;
    }
    if (value && typeof value === 'object') return keyValues(value);
    return `<dl class="research-kv"><dt>${escapeHtml(label(name))}</dt><dd>${escapeHtml(display(value))}</dd></dl>`;
  }

  function renderSection(section, value) {
    const target = document.querySelector('#research-content');
    if (!value || (Array.isArray(value) && !value.length) || (typeof value === 'object' && !Array.isArray(value) && !Object.keys(value).length)) {
      target.innerHTML = `<span class="muted">${escapeHtml(t('research.none'))}</span>`;
      return;
    }
    if (section === 'profile' || section === 'esg') {
      target.innerHTML = renderValue(section, value);
      return;
    }
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      target.innerHTML = `<div class="research-object">${Object.entries(value).map(([name, item]) => `
        <section><h3>${escapeHtml(label(name))}</h3>${renderValue(name, item)}</section>`).join('')}</div>`;
      return;
    }
    target.innerHTML = renderValue(section, value);
  }

  async function loadSection(section) {
    document.querySelector('#options-workspace').hidden = true;
    document.querySelector('#research-content').hidden = false;
    const status = document.querySelector('#research-status');
    status.textContent = t('research.loading');
    try {
      let data = cache.get(section);
      if (!data) {
        data = await api(`/v1/research/${encodeURIComponent(symbol)}?sections=${encodeURIComponent(section)}`);
        cache.set(section, data);
      }
      renderSection(section, data.sections?.[section]);
      const error = data.errors?.[section];
      status.textContent = error || '';
      const p = data.provenance || {};
      document.querySelector('#research-provenance').textContent = p.source
        ? `${p.source} · ${p.license_class || ''} · ${p.as_of || ''}` : '';
    } catch (error) {
      status.textContent = error.message;
      document.querySelector('#research-content').innerHTML = '';
    }
  }

  function optionTable(title, rows) {
    const keys = ['contractSymbol', 'strike', 'lastPrice', 'bid', 'ask', 'change', 'percentChange', 'volume', 'openInterest', 'impliedVolatility', 'inTheMoney'];
    const head = keys.map(key => `<th>${escapeHtml(label(key))}</th>`).join('');
    const body = (rows || []).map(row => `<tr>${keys.map(key => `<td>${escapeHtml(display(row[key]))}</td>`).join('')}</tr>`).join('');
    return `<section><h3>${escapeHtml(title)}</h3><div class="research-table-wrap"><table class="research-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div></section>`;
  }

  async function showOptions() {
    document.querySelector('#research-content').hidden = true;
    const workspace = document.querySelector('#options-workspace');
    workspace.hidden = false;
    const status = document.querySelector('#research-status');
    status.textContent = t('research.loading');
    try {
      const overview = await api(`/v1/options/${encodeURIComponent(symbol)}`);
      const select = document.querySelector('#option-expiration');
      select.innerHTML = (overview.expirations || []).map(exp => `<option value="${escapeHtml(exp)}">${escapeHtml(exp)}</option>`).join('');
      status.textContent = overview.expirations?.length ? `${overview.expirations.length}` : t('research.none');
      if (overview.expirations?.length) await loadOptions();
    } catch (error) {
      status.textContent = error.message;
    }
  }

  async function loadOptions() {
    const expiration = document.querySelector('#option-expiration').value;
    if (!expiration) return;
    const target = document.querySelector('#option-results');
    target.textContent = t('research.loading');
    try {
      const data = await api(`/v1/options/${encodeURIComponent(symbol)}?expiration=${encodeURIComponent(expiration)}`);
      target.innerHTML = `<div class="research-object">${optionTable(t('research.calls'), data.calls)}${optionTable(t('research.puts'), data.puts)}</div>`;
      const p = data.provenance || {};
      document.querySelector('#research-provenance').textContent = p.source ? `${p.source} · ${p.license_class || ''}` : '';
    } catch (error) {
      target.textContent = error.message;
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('#research-tabs button').forEach(button => {
      button.addEventListener('click', () => {
        document.querySelectorAll('#research-tabs button').forEach(item => item.classList.toggle('is-active', item === button));
        const section = button.dataset.section;
        if (section === 'options') showOptions(); else loadSection(section);
      });
    });
    document.querySelector('#option-load')?.addEventListener('click', loadOptions);
    if (symbol) loadSection('profile');
  });
})();
