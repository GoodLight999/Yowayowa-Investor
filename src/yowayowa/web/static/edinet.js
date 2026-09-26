(() => {
  const { api, escapeHtml, t } = window.Yowayowa;
  let currentDocId = null;

  const exactNumber = value => {
    const text = String(value ?? '');
    const match = text.match(/^(-?)(\d+)(\.\d+)?$/);
    if (!match) return text || '—';
    const grouped = match[2].replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    return `${match[1]}${grouped}${match[3] || ''}`;
  };

  const dateOnly = value => value ? String(value).slice(0, 10) : '—';

  const localDate = value => {
    const adjusted = new Date(value.getTime() - value.getTimezoneOffset() * 60000);
    return adjusted.toISOString().slice(0, 10);
  };

  const scopeText = item => [
    item.relative_year,
    item.consolidation,
    item.period_type,
    item.context_id,
  ].filter(Boolean).join(' · ');

  function renderProvenance(provenance, asOf = null) {
    const target = document.querySelector('#edinet-provenance');
    if (!target || !provenance) return;
    target.innerHTML = `
      <div><strong>${escapeHtml(provenance.source)}</strong></div>
      <div>${escapeHtml(t('common.provider'))} <code>${escapeHtml(provenance.provider)}</code> · ${escapeHtml(t(`license.${provenance.license_class}`, {}, provenance.license_class))}</div>
      ${asOf ? `<div>${escapeHtml(t('common.as_of'))} ${escapeHtml(asOf)}</div>` : ''}
      ${provenance.source_url ? `<div><a href="${escapeHtml(provenance.source_url)}" rel="noreferrer">${escapeHtml(t('common.open_source'))}</a></div>` : ''}`;
  }

  function documentTable(documents) {
    return `<table><thead><tr>
      <th>${escapeHtml(t('edinet.doc_id'))}</th>
      <th>${escapeHtml(t('edinet.filer'))}</th>
      <th>${escapeHtml(t('edinet.code'))}</th>
      <th>${escapeHtml(t('edinet.type'))}</th>
      <th>${escapeHtml(t('edinet.period'))}</th>
      <th>${escapeHtml(t('edinet.submitted'))}</th>
      <th>${escapeHtml(t('edinet.csv'))}</th>
      <th></th>
    </tr></thead><tbody>${documents.map(item => {
      const period = item.period_start || item.period_end
        ? `${dateOnly(item.period_start)} → ${dateOnly(item.period_end)}`
        : '—';
      return `<tr>
        <td><code>${escapeHtml(item.doc_id)}</code></td>
        <td><strong>${escapeHtml(item.filer_name)}</strong>${item.description ? `<small>${escapeHtml(item.description)}</small>` : ''}</td>
        <td>${escapeHtml(item.security_code || '—')}</td>
        <td>${escapeHtml(item.doc_type_code || '—')}</td>
        <td>${escapeHtml(period)}</td>
        <td>${escapeHtml(item.submitted_at || '—')}</td>
        <td>${item.csv_available ? '✓' : '—'}</td>
        <td><button class="ghost edinet-doc-button" type="button" data-doc-id="${escapeHtml(item.doc_id)}" ${item.csv_available ? '' : 'disabled'}>${escapeHtml(t('edinet.open'))}</button></td>
      </tr>`;
    }).join('')}</tbody></table>`;
  }

  function wireDocumentButtons(target) {
    target.querySelectorAll('.edinet-doc-button[data-doc-id]').forEach(button => {
      button.addEventListener('click', () => loadFinancials(button.dataset.docId));
    });
  }

  function renderDocuments(data) {
    const target = document.querySelector('#edinet-documents');
    const status = document.querySelector('#edinet-document-status');
    status.textContent = t('edinet.result_count', {
      matched: data.matched_count,
      total: data.total_count,
    });
    renderProvenance(data.provenance, data.filing_date);
    if (!data.documents.length) {
      target.textContent = t('edinet.no_filings');
      return;
    }
    target.innerHTML = documentTable(data.documents);
    wireDocumentButtons(target);
  }

  function renderHistory(data) {
    const target = document.querySelector('#edinet-history-documents');
    const status = document.querySelector('#edinet-history-status');
    const coverage = document.querySelector('#edinet-history-coverage');
    status.textContent = t('edinet.history_count', {
      matched: data.matched_count,
      indexed: data.indexed_days,
      expected: data.expected_days,
    });
    const coverageKey = data.coverage_complete
      ? 'edinet.history_complete'
      : 'edinet.history_partial';
    coverage.innerHTML = `
      <strong class="${data.coverage_complete ? '' : 'partial'}">${escapeHtml(t(coverageKey))}</strong>
      <span>${escapeHtml(t('edinet.index_range', {
        start: data.index_start || '—',
        end: data.index_end || '—',
      }))}</span>`;
    renderProvenance(data.provenance, data.index_end);
    if (!data.documents.length) {
      target.textContent = t('edinet.no_history');
      return;
    }
    target.innerHTML = documentTable(data.documents);
    wireDocumentButtons(target);
  }

  function renderFinancials(data) {
    const panel = document.querySelector('#edinet-financial-panel');
    const factsPanel = document.querySelector('#edinet-facts-panel');
    panel.hidden = false;
    factsPanel.hidden = false;
    document.querySelector('#edinet-financial-status').textContent = data.doc_id;
    document.querySelector('#edinet-meta').innerHTML = `
      <div><small>${escapeHtml(t('edinet.company'))}</small><strong title="${escapeHtml(data.company_name || '')}">${escapeHtml(data.company_name || '—')}</strong></div>
      <div><small>${escapeHtml(t('edinet.code'))}</small><strong>${escapeHtml(data.security_code || '—')}</strong></div>
      <div><small>${escapeHtml(t('edinet.period'))}</small><strong>${escapeHtml(`${dateOnly(data.period_start)} → ${dateOnly(data.period_end)}`)}</strong></div>
      <div><small>${escapeHtml(t('edinet.accounting'))}</small><strong>${escapeHtml(data.accounting_standard || '—')}</strong></div>
      <div><small>EDINET</small><strong>${escapeHtml(data.edinet_code || '—')}</strong></div>
      <div><small>${escapeHtml(t('edinet.type'))}</small><strong>${escapeHtml(data.document_type || '—')}</strong></div>
      <div><small>${escapeHtml(t('edinet.fact_count'))}</small><strong>${escapeHtml(data.fact_count)}</strong></div>
      <div><small>${escapeHtml(t('edinet.doc_id'))}</small><strong>${escapeHtml(data.doc_id)}</strong></div>`;

    const metricRows = Object.entries(data.metrics).map(([key, observations]) => {
      const item = observations[0];
      if (!item) return '';
      return `<tr>
        <td><strong>${escapeHtml(t(`edinet.metric.${key}`, {}, key))}</strong><small>${escapeHtml(t('edinet.contexts', { count: observations.length }))}</small></td>
        <td class="edinet-metric-value">${escapeHtml(exactNumber(item.value))}</td>
        <td>${escapeHtml(item.unit || '—')}</td>
        <td class="edinet-scope">${escapeHtml(scopeText(item) || '—')}</td>
        <td class="edinet-element" title="${escapeHtml(item.element_id)}">${escapeHtml(item.element_id)}</td>
      </tr>`;
    }).join('');
    const metrics = document.querySelector('#edinet-metrics');
    metrics.innerHTML = metricRows
      ? `<table><thead><tr><th>${escapeHtml(t('edinet.metric'))}</th><th>${escapeHtml(t('edinet.value'))}</th><th>${escapeHtml(t('edinet.unit'))}</th><th>${escapeHtml(t('edinet.scope'))}</th><th>${escapeHtml(t('edinet.element'))}</th></tr></thead><tbody>${metricRows}</tbody></table>`
      : escapeHtml(t('edinet.no_metrics'));

    const notes = [];
    if (data.unavailable_metrics?.length) {
      notes.push(`<div><strong>${escapeHtml(t('edinet.unavailable'))}:</strong> ${data.unavailable_metrics.map(key => escapeHtml(t(`edinet.metric.${key}`, {}, key))).join(' · ')}</div>`);
    }
    if (data.parse_warnings?.length) {
      notes.push(`<div><strong>${escapeHtml(t('edinet.parse_notes'))}:</strong> ${data.parse_warnings.map(escapeHtml).join(' · ')}</div>`);
    }
    document.querySelector('#edinet-notes').innerHTML = notes.join('');
    renderProvenance(data.provenance, data.period_end);
  }

  function renderFacts(data) {
    document.querySelector('#edinet-fact-status').textContent = t('edinet.fact_matches', {
      matched: data.matched_count,
      total: data.total_count,
    });
    const target = document.querySelector('#edinet-facts');
    if (!data.facts.length) {
      target.textContent = '—';
      return;
    }
    target.innerHTML = `<table><thead><tr>
      <th>${escapeHtml(t('edinet.element'))}</th>
      <th>${escapeHtml(t('edinet.metric'))}</th>
      <th>${escapeHtml(t('edinet.scope'))}</th>
      <th>${escapeHtml(t('edinet.unit'))}</th>
      <th>${escapeHtml(t('edinet.value'))}</th>
    </tr></thead><tbody>${data.facts.map(item => `<tr>
      <td class="edinet-element" title="${escapeHtml(item.element_id)}">${escapeHtml(item.element_id)}</td>
      <td>${escapeHtml(item.label)}</td>
      <td class="edinet-scope">${escapeHtml(scopeText(item) || '—')}</td>
      <td>${escapeHtml(item.unit || '—')}</td>
      <td class="edinet-metric-value">${escapeHtml(item.value.length > 180 ? `${item.value.slice(0, 177)}…` : item.value)}</td>
    </tr>`).join('')}</tbody></table>`;
  }

  async function loadHistory() {
    const securityCode = document.querySelector('#edinet-history-security-code').value.trim();
    const startDate = document.querySelector('#edinet-history-start').value;
    const endDate = document.querySelector('#edinet-history-end').value;
    const csvOnly = document.querySelector('#edinet-history-csv-only').checked;
    if (!securityCode || !startDate || !endDate) return;
    const params = new URLSearchParams({
      security_code: securityCode,
      start_date: startDate,
      end_date: endDate,
      csv_only: String(csvOnly),
      limit: '500',
    });
    document.querySelector('#edinet-history-status').textContent = t('edinet.history_loading');
    try {
      renderHistory(await api(`/v1/filings/edinet/index/history?${params}`));
    } catch (error) {
      document.querySelector('#edinet-history-status').textContent = error.message;
      document.querySelector('#edinet-history-coverage').textContent = '';
      document.querySelector('#edinet-history-documents').textContent = '—';
    }
  }

  async function loadDocuments() {
    const filingDate = document.querySelector('#edinet-date').value;
    const securityCode = document.querySelector('#edinet-security-code').value.trim();
    const csvOnly = document.querySelector('#edinet-csv-only').checked;
    if (!filingDate) return;
    const params = new URLSearchParams({
      filing_date: filingDate,
      csv_only: String(csvOnly),
      downloadable_only: 'true',
      limit: '500',
    });
    if (securityCode) params.set('security_code', securityCode);
    document.querySelector('#edinet-document-status').textContent = t('edinet.loading_filings');
    try {
      renderDocuments(await api(`/v1/filings/edinet/documents?${params}`));
    } catch (error) {
      document.querySelector('#edinet-document-status').textContent = error.message;
      document.querySelector('#edinet-documents').textContent = '—';
    }
  }

  async function loadFinancials(docId) {
    currentDocId = docId;
    document.querySelector('#edinet-financial-panel').hidden = false;
    document.querySelector('#edinet-financial-status').textContent = t('edinet.loading_financials');
    try {
      renderFinancials(await api(`/v1/filings/edinet/${encodeURIComponent(docId)}/financials`));
    } catch (error) {
      document.querySelector('#edinet-financial-status').textContent = error.message;
    }
  }

  async function loadFacts() {
    if (!currentDocId) return;
    const query = document.querySelector('#edinet-fact-query').value.trim();
    const params = new URLSearchParams({ limit: '200' });
    if (query) params.set('q', query);
    try {
      renderFacts(await api(`/v1/filings/edinet/${encodeURIComponent(currentDocId)}/facts?${params}`));
    } catch (error) {
      document.querySelector('#edinet-fact-status').textContent = error.message;
    }
  }

  document.querySelector('#edinet-history-form')?.addEventListener('submit', event => {
    event.preventDefault();
    loadHistory();
  });
  document.querySelector('#edinet-form')?.addEventListener('submit', event => {
    event.preventDefault();
    loadDocuments();
  });
  document.querySelector('#edinet-fact-form')?.addEventListener('submit', event => {
    event.preventDefault();
    loadFacts();
  });

  const now = new Date();
  const historyEnd = new Date(now);
  historyEnd.setDate(historyEnd.getDate() - 1);
  const historyStart = new Date(historyEnd);
  historyStart.setFullYear(historyStart.getFullYear() - 1);
  const dateInput = document.querySelector('#edinet-date');
  const historyStartInput = document.querySelector('#edinet-history-start');
  const historyEndInput = document.querySelector('#edinet-history-end');
  if (dateInput && !dateInput.value) dateInput.value = localDate(now);
  if (historyStartInput && !historyStartInput.value) historyStartInput.value = localDate(historyStart);
  if (historyEndInput && !historyEndInput.value) historyEndInput.value = localDate(historyEnd);

  const query = new URLSearchParams(location.search);
  const securityCode = query.get('security_code');
  if (query.get('date')) dateInput.value = query.get('date');
  if (query.get('start')) historyStartInput.value = query.get('start');
  if (query.get('end')) historyEndInput.value = query.get('end');
  if (securityCode) {
    document.querySelector('#edinet-security-code').value = securityCode;
    document.querySelector('#edinet-history-security-code').value = securityCode;
    loadHistory();
  }
})();
