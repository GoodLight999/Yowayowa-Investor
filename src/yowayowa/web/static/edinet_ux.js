(() => {
  const { api, escapeHtml } = window.Yowayowa;
  const form = document.querySelector('#edinet-company-search');
  const input = document.querySelector('#edinet-company-query');
  const results = document.querySelector('#edinet-company-results');
  if (!form || !input || !results) return;

  function choose(item) {
    const code = item.security_code || '';
    const normalized = code.length === 5 && code.endsWith('0') ? code.slice(0, 4) : code;
    const history = document.querySelector('#edinet-history-security-code');
    const direct = document.querySelector('#edinet-security-code');
    if (history) history.value = normalized;
    if (direct) direct.value = normalized;
    results.innerHTML = `<div class="result-row"><span><strong>${escapeHtml(normalized)}</strong><small>${escapeHtml(item.filer_name || '')}</small></span><span class="muted">selected</span></div>`;
    document.querySelector('#edinet-history-form')?.requestSubmit();
  }

  form.addEventListener('submit', async event => {
    event.preventDefault();
    const q = input.value.trim();
    if (!q) return;
    results.textContent = '検索中…';
    try {
      const data = await api(`/v1/filings/edinet/index/issuers?q=${encodeURIComponent(q)}&limit=20`);
      const rows = data.issuers || [];
      results.innerHTML = rows.length ? rows.map((item, index) => `<button class="macro-result edinet-company-result" type="button" data-index="${index}">
        <strong>${escapeHtml(item.security_code || '—')} · ${escapeHtml(item.filer_name || '—')}</strong>
        <small>EDINET ${escapeHtml(item.edinet_code || '—')} · latest ${escapeHtml(item.latest_filing_date || '—')}</small>
      </button>`).join('') : '<span class="muted">該当企業がありません。EDINET履歴インデックスの未同期期間では検索できない場合があります。</span>';
      results.querySelectorAll('.edinet-company-result').forEach(button => {
        button.addEventListener('click', () => choose(rows[Number(button.dataset.index)]));
      });
    } catch (error) {
      results.textContent = error.message;
    }
  });
})();
