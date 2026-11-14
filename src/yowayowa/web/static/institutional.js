(() => {
  const { api, escapeHtml, localeTag, t } = window.Yowayowa;
  const money = value => new Intl.NumberFormat(localeTag, { notation: 'compact', maximumFractionDigits: 2, style: 'currency', currency: 'USD' }).format(Number(value));
  const number = value => new Intl.NumberFormat(localeTag, { maximumFractionDigits: 2 }).format(Number(value));
  const percent = value => value === null ? '—' : new Intl.NumberFormat(localeTag, { style: 'percent', maximumFractionDigits: 2, signDisplay: 'always' }).format(Number(value));

  function renderMeta(data) {
    const latest = data.filings[0];
    const lag = latest ? Math.round((new Date(`${latest.filing_date}T00:00:00Z`) - new Date(`${latest.report_date}T00:00:00Z`)) / 86400000) : null;
    document.querySelector('#institutional-status').textContent = latest?.report_date || '—';
    document.querySelector('#institutional-meta').innerHTML = `
      <div><small>${escapeHtml(t('institutional.manager'))}</small><strong>${escapeHtml(data.manager_name)}</strong></div>
      <div><small>${escapeHtml(t('institutional.report_date'))}</small><strong>${escapeHtml(latest?.report_date || '—')}</strong></div>
      <div><small>${escapeHtml(t('institutional.filing_date'))}</small><strong>${escapeHtml(latest?.filing_date || '—')}</strong></div>
      <div><small>${escapeHtml(t('institutional.lag'))}</small><strong>${lag === null ? '—' : `${lag} ${escapeHtml(t('institutional.days'))}`}</strong></div>`;
  }

  function renderHoldings(data) {
    const target = document.querySelector('#institutional-holdings');
    const latest = data.filings[0];
    if (!latest?.holdings?.length) { target.textContent = '—'; return; }
    target.innerHTML = `<table><thead><tr><th>${escapeHtml(t('institutional.issuer'))}</th><th>${escapeHtml(t('institutional.class'))}</th><th>${escapeHtml(t('institutional.cusip'))}</th><th>${escapeHtml(t('institutional.value'))}</th><th>${escapeHtml(t('institutional.weight'))}</th><th>${escapeHtml(t('institutional.shares'))}</th></tr></thead><tbody>${latest.holdings.map(item => `
      <tr><td><strong>${escapeHtml(item.issuer)}</strong>${item.put_call ? `<small>${escapeHtml(item.put_call)}</small>` : ''}</td><td>${escapeHtml(item.title_of_class || '—')}</td><td><code>${escapeHtml(item.cusip)}</code></td><td>${escapeHtml(money(item.value_usd))}</td><td>${item.weight === null ? '—' : escapeHtml(percent(item.weight).replace('+',''))}</td><td>${escapeHtml(number(item.shares_or_principal))}</td></tr>`).join('')}</tbody></table>`;
  }

  function renderChanges(data) {
    const target = document.querySelector('#institutional-changes');
    if (!data.changes.length) { target.textContent = t('institutional.none'); return; }
    target.innerHTML = `<table><thead><tr><th>${escapeHtml(t('institutional.issuer'))}</th><th>${escapeHtml(t('institutional.status'))}</th><th>${escapeHtml(t('institutional.share_change'))}</th><th>${escapeHtml(t('institutional.value'))}</th></tr></thead><tbody>${data.changes.map(item => `
      <tr><td><strong>${escapeHtml(item.issuer)}</strong><small>${escapeHtml(item.cusip)}</small></td><td class="change-${escapeHtml(item.status)}">${escapeHtml(t(`institutional.${item.status}`, {}, item.status))}</td><td>${escapeHtml(number(item.share_change))} · ${escapeHtml(percent(item.share_change_fraction))}</td><td>${escapeHtml(money(item.current_value_usd))}</td></tr>`).join('')}</tbody></table>`;
  }

  function renderProvenance(data) {
    const latest = data.filings[0];
    if (!latest) { document.querySelector('#institutional-provenance').textContent = '—'; return; }
    const p = latest.provenance;
    document.querySelector('#institutional-provenance').innerHTML = `<div><strong>${escapeHtml(p.source)}</strong></div><div>${escapeHtml(t('common.provider'))} <code>${escapeHtml(p.provider)}</code> · ${escapeHtml(t(`license.${p.license_class}`, {}, p.license_class))}</div><div>${escapeHtml(t('common.as_of'))} ${escapeHtml(latest.report_date)} · ${escapeHtml(t('institutional.filing_date'))} ${escapeHtml(latest.filing_date)}</div>${p.source_url ? `<div><a href="${escapeHtml(p.source_url)}" rel="noreferrer">${escapeHtml(t('common.open_source'))}</a></div>` : ''}`;
  }

  async function load() {
    const cik = document.querySelector('#institutional-cik').value.trim();
    const quarters = document.querySelector('#institutional-quarters').value;
    if (!cik) return;
    document.querySelector('#institutional-status').textContent = t('institutional.loading');
    try {
      const data = await api(`/v1/institutional/13f/${encodeURIComponent(cik)}?quarters=${encodeURIComponent(quarters)}`);
      renderMeta(data); renderHoldings(data); renderChanges(data); renderProvenance(data);
    } catch (error) { document.querySelector('#institutional-status').textContent = error.message; }
  }

  document.querySelector('#institutional-form')?.addEventListener('submit', event => { event.preventDefault(); load(); });
  const initial = new URLSearchParams(location.search).get('cik');
  if (initial) { document.querySelector('#institutional-cik').value = initial; load(); }
})();
