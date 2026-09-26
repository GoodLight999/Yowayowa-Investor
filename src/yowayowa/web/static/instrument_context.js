(() => {
  const { api, escapeHtml, localeTag, t } = window.Yowayowa;
  const header = document.querySelector('.instrument-header');
  const symbol = header?.dataset.symbol;
  if (!symbol) return;
  const ja = window.YOWAYOWA_LOCALE === 'ja';
  const COMPANY_KEY = 'yowayowa.company-context.v1';
  const jp = /^(\d{4,5})\.T$/i.exec(symbol);

  function remember() {
    const nameNode = document.querySelector('#instrument-name');
    const save = () => {
      if (!nameNode) return;
      const rendered = nameNode.textContent || '';
      if (/\s*·\s*CIK\s*$/.test(rendered)) {
        nameNode.textContent = rendered.replace(/\s*·\s*CIK\s*$/, '').trim();
        return;
      }
      const name = rendered.replace(/\s*·\s*CIK.*$/, '').trim();
      if (!name || name.includes('…')) return;
      try { localStorage.setItem(COMPANY_KEY, JSON.stringify({ symbol, name, exchange: '' })); } catch (_) {}
    };
    save();
    if (nameNode) new MutationObserver(save).observe(nameNode, { childList:true, subtree:true, characterData:true });
  }

  function eventClass(type) {
    const value = String(type || '').toLowerCase();
    if (value.includes('earning')) return 'event-earnings';
    if (value.includes('economic')) return 'event-economic';
    if (value.includes('split')) return 'event-split';
    if (value.includes('dividend')) return 'event-dividend';
    return '';
  }

  function item(title, meta, klass = '') {
    return `<div class="company-context-item ${klass}"><i></i><div><strong>${title}</strong><small>${escapeHtml(meta || '')}</small></div></div>`;
  }

  const chart = document.querySelector('.chart-panel');
  const financials = document.querySelector('#fundamentals-table')?.closest('.panel');
  if (chart) chart.id = 'company-chart';
  if (financials) financials.id = 'company-financials';
  const nav = document.createElement('nav');
  nav.className = 'instrument-section-nav';
  nav.innerHTML = `<a href="#company-chart">${ja ? '株価' : 'Price'}</a><a href="#company-financials">${ja ? '決算・財務' : 'Financials'}</a><a href="#company-news">${ja ? 'ニュース' : 'News'}</a><a href="#company-events">${ja ? 'イベント' : 'Events'}</a>${jp ? `<a href="#company-filings">${ja ? '開示書類' : 'Filings'}</a>` : ''}`;
  header.insertAdjacentElement('afterend', nav);

  const context = document.createElement('div');
  context.className = 'company-context-grid';
  context.innerHTML = `<section id="company-news" class="panel"><div class="section-heading"><h2>${ja ? 'この会社のニュース' : 'Company news'}</h2></div><div id="company-news-list" class="company-context-list"><div class="list-state muted">${t('common.loading_ellipsis')}</div></div></section><section id="company-events" class="panel"><div class="section-heading"><h2>${ja ? 'この会社の今後のイベント' : 'Upcoming company events'}</h2></div><div id="company-events-list" class="company-context-list"><div class="list-state muted">${t('common.loading_ellipsis')}</div></div></section>`;
  const analyst = document.querySelector('#analyst-consensus')?.closest('.panel');
  if (analyst) analyst.insertAdjacentElement('beforebegin', context); else document.querySelector('main.content')?.append(context);

  if (jp) {
    const filings = document.createElement('section');
    filings.id = 'company-filings'; filings.className = 'panel'; filings.style.marginTop = '12px';
    filings.innerHTML = `<div class="section-heading"><div><h2>${ja ? '決算・開示書類' : 'Financial filings'}</h2><small class="term-note">${ja ? '金融庁EDINETの公式提出書類を銘柄から自動照合します。EDINETで検索し直す必要はありません。' : 'Official FSA EDINET filings are matched automatically from the selected company.'}</small></div><a class="ghost button" href="/edinet?security_code=${encodeURIComponent(jp[1].slice(0,4))}">${ja ? '原典を詳しく見る' : 'Inspect source'}</a></div><div id="company-filings-list" class="company-context-list"><div class="list-state muted">${t('common.loading_ellipsis')}</div></div>`;
    context.insertAdjacentElement('afterend', filings);
  }

  remember();

  api(`/v1/news/${encodeURIComponent(symbol)}?limit=8`).then(data => {
    const target = document.querySelector('#company-news-list'); if (!target) return;
    target.innerHTML = (data.items || []).slice(0,8).map(row => item(row.url ? `<a href="${escapeHtml(row.url)}" rel="noreferrer">${escapeHtml(row.title)}</a>` : escapeHtml(row.title), [row.publisher, row.published_at ? new Date(row.published_at).toLocaleString(localeTag) : ''].filter(Boolean).join(' · '))).join('') || `<div class="list-state muted">${ja ? '直近のニュースはありません。' : 'No recent news.'}</div>`;
  }).catch(error => { const target = document.querySelector('#company-news-list'); if (target) target.textContent = error.message; });

  const start = new Date(); const end = new Date(start); end.setDate(end.getDate()+90);
  api(`/v1/calendar?start=${start.toISOString().slice(0,10)}&end=${end.toISOString().slice(0,10)}&types=earnings,split&symbol=${encodeURIComponent(symbol)}`).then(data => {
    const target = document.querySelector('#company-events-list'); if (!target) return;
    target.innerHTML = (data.events || []).slice(0,10).map(row => item(`<span class="event-badge">${escapeHtml(row.event_type || '')}</span> ${escapeHtml(row.title || '')}`, row.starts_at ? new Date(row.starts_at).toLocaleString(localeTag) : '', eventClass(row.event_type))).join('') || `<div class="list-state muted">${ja ? '90日以内の主要イベントはありません。' : 'No major events in the next 90 days.'}</div>`;
  }).catch(error => { const target = document.querySelector('#company-events-list'); if (target) target.textContent = error.message; });

  if (jp) {
    const since = new Date(start); since.setFullYear(since.getFullYear()-2);
    api(`/v1/filings/edinet/index/history?start_date=${since.toISOString().slice(0,10)}&end_date=${start.toISOString().slice(0,10)}&security_code=${encodeURIComponent(jp[1].slice(0,4))}&limit=20`).then(data => {
      const target = document.querySelector('#company-filings-list'); if (!target) return;
      const rows = data.filings || data.documents || [];
      target.innerHTML = `${data.coverage_complete === false ? `<div class="badge-fail">${ja ? 'この期間は一部未同期です。' : 'This period is partially synchronized.'}</div>` : ''}${rows.slice(0,8).map(row => item(escapeHtml(row.doc_description || row.doc_type_name || row.doc_id || ''), [row.submit_date_time || row.filing_date, row.doc_id].filter(Boolean).join(' · '))).join('') || `<div class="list-state muted">${ja ? '同期済み範囲に提出書類がありません。' : 'No filings in the synchronized range.'}<br><a href="/settings#data-sources">${ja ? 'EDINETを設定' : 'Configure EDINET'}</a></div>`}`;
    }).catch(error => { const target = document.querySelector('#company-filings-list'); if (target) target.innerHTML = `${escapeHtml(error.message)}<br><a href="/settings#data-sources">${ja ? 'EDINETを設定' : 'Configure EDINET'}</a>`; });
  }
})();
