(() => {
  const { api, escapeHtml, localeTag, t } = window.Yowayowa;
  let portfolios = [];

  const currentId = () => Number(document.querySelector('#portfolio-select')?.value || 0);
  const currentPortfolio = () => portfolios.find(item => item.id === currentId()) || null;

  function money(value, currency) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
    return new Intl.NumberFormat(localeTag, {
      style: 'currency',
      currency,
      notation: Math.abs(Number(value)) >= 1_000_000 ? 'compact' : 'standard',
      maximumFractionDigits: 2,
    }).format(Number(value));
  }

  function percent(value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
    return new Intl.NumberFormat(localeTag, {
      style: 'percent',
      maximumFractionDigits: 2,
      signDisplay: 'exceptZero',
    }).format(Number(value));
  }

  function decimal(value, digits = 3) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
    return new Intl.NumberFormat(localeTag, {
      maximumFractionDigits: digits,
      minimumFractionDigits: 0,
    }).format(Number(value));
  }

  function pnlClass(value) {
    if (value === null || value === undefined) return '';
    if (Number(value) > 0) return 'market-positive';
    if (Number(value) < 0) return 'market-negative';
    return '';
  }

  function renderPortfolioOptions(selectedId = 0) {
    const select = document.querySelector('#portfolio-select');
    if (!portfolios.length) {
      select.innerHTML = `<option value="">${escapeHtml(t('portfolio.no_portfolios'))}</option>`;
      select.disabled = true;
      return;
    }
    select.disabled = false;
    select.innerHTML = portfolios.map(item => `
      <option value="${item.id}" ${item.id === selectedId ? 'selected' : ''}>
        ${escapeHtml(item.name)} · ${escapeHtml(item.base_currency)}
      </option>`).join('');
  }

  function renderSummary(data) {
    const target = document.querySelector('#portfolio-summary');
    const items = [
      [t('portfolio.net_value'), money(data.net_market_value, data.base_currency)],
      [t('portfolio.gross_exposure'), money(data.gross_market_value, data.base_currency)],
      [t('portfolio.unrealized_pnl'), money(data.unrealized_pnl, data.base_currency), pnlClass(data.unrealized_pnl)],
      [t('portfolio.unrealized_return'), percent(data.unrealized_pnl_pct), pnlClass(data.unrealized_pnl)],
      [t('portfolio.day_pnl'), money(data.day_pnl, data.base_currency), pnlClass(data.day_pnl)],
      [t('portfolio.day_return'), percent(data.day_change_pct), pnlClass(data.day_pnl)],
      [t('portfolio.largest_weight'), percent(data.largest_position_weight)],
      [t('portfolio.concentration_hhi'), Number(data.concentration_hhi).toFixed(3)],
    ];
    target.innerHTML = items.map(([label, value, cls = '']) => `
      <div class="metric-card"><small>${escapeHtml(label)}</small><strong class="${cls}">${escapeHtml(value)}</strong></div>
    `).join('');
  }

  function renderPositions(data) {
    const target = document.querySelector('#portfolio-positions');
    if (!data.positions.length) {
      target.innerHTML = `<span class="muted">${escapeHtml(t('portfolio.no_priced_positions'))}</span>`;
      return;
    }
    target.innerHTML = `
      <table class="portfolio-table">
        <thead><tr><th>${escapeHtml(t('common.symbol'))}</th><th>${escapeHtml(t('portfolio.qty'))}</th><th>${escapeHtml(t('portfolio.price'))}</th><th>${escapeHtml(t('portfolio.value'))}</th><th>${escapeHtml(t('portfolio.weight'))}</th><th>${escapeHtml(t('portfolio.day'))}</th><th>${escapeHtml(t('portfolio.unrealized'))}</th><th></th></tr></thead>
        <tbody>${data.positions.map(item => `
          <tr>
            <td><a href="/instrument/${encodeURIComponent(item.symbol)}"><strong>${escapeHtml(item.symbol)}</strong></a><small class="position-currency">${escapeHtml(item.currency)}</small></td>
            <td>${escapeHtml(String(item.quantity))}</td>
            <td>${escapeHtml(money(item.price, item.currency))}</td>
            <td>${escapeHtml(money(item.market_value_base, data.base_currency))}</td>
            <td>${escapeHtml(percent(item.weight))}</td>
            <td class="${pnlClass(item.day_pnl_base)}">${escapeHtml(money(item.day_pnl_base, data.base_currency))}<small>${escapeHtml(percent(item.day_change_pct))}</small></td>
            <td class="${pnlClass(item.unrealized_pnl_base)}">${escapeHtml(money(item.unrealized_pnl_base, data.base_currency))}<small>${escapeHtml(percent(item.unrealized_pnl_pct))}</small></td>
            <td><button class="position-remove ghost" data-symbol="${escapeHtml(item.symbol)}" type="button">${escapeHtml(t('common.remove'))}</button></td>
          </tr>`).join('')}</tbody>
      </table>`;
    target.querySelectorAll('.position-remove').forEach(button => {
      button.addEventListener('click', () => removePosition(button.dataset.symbol));
    });
  }

  function renderExposure(data) {
    const target = document.querySelector('#currency-exposure');
    if (!data.currency_exposure.length) {
      target.textContent = t('portfolio.no_priced_currency');
      return;
    }
    target.innerHTML = data.currency_exposure.map(item => `
      <div class="exposure-row">
        <div class="exposure-label"><strong>${escapeHtml(item.currency)}</strong><span>${escapeHtml(money(item.market_value_base, data.base_currency))}</span><span>${escapeHtml(percent(item.weight))}</span></div>
        <div class="exposure-track"><span style="width:${Math.min(100, Math.max(0, Number(item.weight) * 100))}%"></span></div>
      </div>`).join('');
  }

  function renderHistory(rows) {
    const target = document.querySelector('#portfolio-history');
    if (!rows.length) {
      target.textContent = t('portfolio.history_empty');
      return;
    }
    const values = rows.map(row => Number(row.net_market_value));
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min || 1;
    const denominator = Math.max(1, rows.length - 1);
    const points = rows.map((row, index) => {
      const x = (index / denominator) * 100;
      const y = 86 - ((Number(row.net_market_value) - min) / span) * 78;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    }).join(' ');
    const currency = currentPortfolio()?.base_currency || 'USD';
    const first = rows[0];
    const last = rows[rows.length - 1];
    target.innerHTML = `
      <svg class="portfolio-history-svg" viewBox="0 0 100 92" preserveAspectRatio="none" role="img" aria-label="${escapeHtml(t('portfolio.history'))}"><polyline points="${points}"></polyline></svg>
      <div class="portfolio-history-meta"><span>${escapeHtml(new Date(first.captured_at).toLocaleString(localeTag))} · ${escapeHtml(money(first.net_market_value, currency))}</span><span>${escapeHtml(new Date(last.captured_at).toLocaleString(localeTag))} · ${escapeHtml(money(last.net_market_value, currency))}</span></div>`;
  }

  function renderProvenance(data) {
    const p = data.provenance;
    document.querySelector('#portfolio-as-of').textContent = `${t('common.as_of')} ${new Date(p.as_of || p.retrieved_at).toLocaleString(localeTag)}`;
    const license = t(`license.${p.license_class}`, {}, p.license_class);
    document.querySelector('#portfolio-provenance').innerHTML = `
      <div><strong>${escapeHtml(p.source)}</strong></div>
      <div>${escapeHtml(t('common.provider'))} <code>${escapeHtml(p.provider)}</code> · ${escapeHtml(license)}</div>
      <div>${escapeHtml(t('common.retrieved'))} ${escapeHtml(new Date(p.retrieved_at).toLocaleString(localeTag))}</div>
      ${data.unavailable_symbols.length ? `<div class="badge-fail">${escapeHtml(t('portfolio.unavailable_positions', { symbols: data.unavailable_symbols.join(', ') }))}</div>` : ''}`;
  }

  function resetRisk() {
    document.querySelector('#risk-message').textContent = t('risk.empty');
    document.querySelector('#risk-summary').innerHTML = '';
    document.querySelector('#risk-positions').innerHTML = '';
    document.querySelector('#risk-correlations').textContent = t('risk.no_pairs');
    document.querySelector('#risk-notes').innerHTML = '';
    const provenance = document.querySelector('#risk-provenance');
    if (provenance) provenance.innerHTML = '';
  }

  function renderRisk(data) {
    const summaryRows = [
      [t('risk.annual_return'), percent(data.annualized_return)],
      [t('risk.volatility'), percent(data.annualized_volatility)],
      [t('risk.sharpe'), decimal(data.sharpe_ratio)],
      [t('risk.max_drawdown'), percent(data.max_drawdown)],
      [t('risk.var95'), percent(data.value_at_risk_95)],
      [t('risk.es95'), percent(data.expected_shortfall_95)],
      [t('risk.beta'), decimal(data.beta)],
      [t('risk.benchmark_corr'), decimal(data.benchmark_correlation)],
      [t('risk.coverage'), percent(data.covered_gross_weight)],
      [t('risk.observations'), decimal(data.observations, 0)],
    ];
    document.querySelector('#risk-summary').innerHTML = `
      <table class="portfolio-table risk-summary-table">
        <tbody>${summaryRows.map(([label, value]) => `
          <tr><th>${escapeHtml(label)}</th><td>${escapeHtml(value)}</td></tr>`).join('')}</tbody>
      </table>`;

    const positions = document.querySelector('#risk-positions');
    if (!data.positions.length) {
      positions.textContent = t('risk.empty');
    } else {
      positions.innerHTML = `
        <table class="portfolio-table">
          <thead><tr><th>${escapeHtml(t('common.symbol'))}</th><th>${escapeHtml(t('risk.signed_weight'))}</th><th>${escapeHtml(t('risk.volatility'))}</th><th>${escapeHtml(t('risk.beta'))}</th><th>${escapeHtml(t('risk.correlation'))}</th><th>${escapeHtml(t('risk.contribution'))}</th><th>${escapeHtml(t('risk.observations'))}</th></tr></thead>
          <tbody>${data.positions.map(item => `
            <tr>
              <td><a href="/instrument/${encodeURIComponent(item.symbol)}"><strong>${escapeHtml(item.symbol)}</strong></a></td>
              <td>${escapeHtml(percent(item.signed_weight))}</td>
              <td>${escapeHtml(percent(item.volatility_annualized))}</td>
              <td>${escapeHtml(decimal(item.beta))}</td>
              <td>${escapeHtml(decimal(item.correlation_to_portfolio))}</td>
              <td>${escapeHtml(percent(item.variance_contribution))}</td>
              <td>${escapeHtml(decimal(item.observations, 0))}</td>
            </tr>`).join('')}</tbody>
        </table>`;
    }

    const correlations = document.querySelector('#risk-correlations');
    const pairs = [...data.correlations]
      .sort((left, right) => Math.abs(Number(right.correlation || 0)) - Math.abs(Number(left.correlation || 0)))
      .slice(0, 50);
    if (!pairs.length) {
      correlations.textContent = t('risk.no_pairs');
    } else {
      correlations.innerHTML = `
        <table class="portfolio-table">
          <thead><tr><th>${escapeHtml(t('risk.left'))}</th><th>${escapeHtml(t('risk.right'))}</th><th>${escapeHtml(t('risk.correlation'))}</th></tr></thead>
          <tbody>${pairs.map(item => `
            <tr><td>${escapeHtml(item.left)}</td><td>${escapeHtml(item.right)}</td><td>${escapeHtml(decimal(item.correlation))}</td></tr>`).join('')}</tbody>
        </table>`;
    }

    const methodNotes = [
      t('risk.note.weights'),
      t('risk.note.gross'),
      t('risk.note.tail'),
      t('risk.note.history'),
    ];
    if (Number(data.covered_gross_weight) < 1 - 1e-9) methodNotes.push(t('risk.note.partial'));
    document.querySelector('#risk-notes').innerHTML = methodNotes
      .map(note => `<li>${escapeHtml(note)}</li>`)
      .join('');
    const provenance = document.querySelector('#risk-provenance');
    if (provenance) {
      provenance.innerHTML = data.provenance.map(item => {
        const license = t(`license.${item.license_class}`, {}, item.license_class);
        return `<div><strong>${escapeHtml(item.source)}</strong> · <code>${escapeHtml(item.provider)}</code> · ${escapeHtml(license)} · ${escapeHtml(t('common.as_of'))} ${escapeHtml(new Date(item.as_of || item.retrieved_at).toLocaleString(localeTag))}</div>`;
      }).join('');
    }
    const message = document.querySelector('#risk-message');
    message.textContent = data.unavailable_symbols.length
      ? t('risk.unavailable', { symbols: data.unavailable_symbols.join(', ') })
      : `${data.benchmark} · ${data.period}`;
  }

  async function loadRisk() {
    const portfolioId = currentId();
    if (!portfolioId) return;
    const message = document.querySelector('#risk-message');
    const benchmark = document.querySelector('#risk-benchmark').value.trim().toUpperCase();
    const period = document.querySelector('#risk-period').value;
    const riskFreeRate = Number(document.querySelector('#risk-free-rate').value || 0);
    if (!benchmark || !Number.isFinite(riskFreeRate) || riskFreeRate < -1 || riskFreeRate > 1) {
      message.textContent = t('risk.empty');
      return;
    }
    message.textContent = t('risk.loading');
    const params = new URLSearchParams({
      benchmark,
      period,
      risk_free_rate: String(riskFreeRate),
    });
    try {
      const data = await api(`/v1/portfolios/${portfolioId}/risk?${params.toString()}`);
      renderRisk(data);
    } catch (error) {
      message.textContent = error.message;
    }
  }

  async function loadHistory(portfolioId = currentId()) {
    if (!portfolioId) return;
    const target = document.querySelector('#portfolio-history');
    try {
      const rows = await api(`/v1/portfolios/${portfolioId}/snapshots?limit=120`);
      renderHistory(rows);
    } catch (error) {
      target.textContent = error.message;
    }
  }

  async function loadPortfolios(preferredId = 0) {
    portfolios = await api('/v1/portfolios');
    const selected = portfolios.some(item => item.id === preferredId) ? preferredId : portfolios[0]?.id || 0;
    renderPortfolioOptions(selected);
    resetRisk();
    if (selected) await loadAnalytics(selected);
    else {
      document.querySelector('#portfolio-summary').innerHTML = `<div class="metric-card"><small>${escapeHtml(t('portfolio.title'))}</small><strong>${escapeHtml(t('portfolio.create_one'))}</strong></div>`;
      document.querySelector('#portfolio-positions').textContent = t('portfolio.create_to_begin');
      document.querySelector('#portfolio-history').textContent = t('portfolio.history_empty');
    }
  }

  async function loadAnalytics(portfolioId = currentId()) {
    if (!portfolioId) return;
    const message = document.querySelector('#portfolio-message');
    message.textContent = t('portfolio.valuing');
    try {
      const data = await api(`/v1/portfolios/${portfolioId}/analytics`);
      renderSummary(data);
      renderPositions(data);
      renderExposure(data);
      renderProvenance(data);
      await loadHistory(portfolioId);
      message.textContent = '';
    } catch (error) {
      message.textContent = error.message;
      document.querySelector('#portfolio-positions').textContent = error.message;
    }
  }

  async function removePosition(symbol) {
    const id = currentId();
    if (!id || !symbol) return;
    const message = document.querySelector('#portfolio-message');
    try {
      await api(`/v1/portfolios/${id}/positions/${encodeURIComponent(symbol)}`, { method: 'DELETE' });
      message.textContent = t('portfolio.removed', { symbol });
      resetRisk();
      await loadAnalytics(id);
    } catch (error) {
      message.textContent = error.message;
    }
  }

  function parsePortfolioCsv(text) {
    const lines = text.replace(/^\uFEFF/, '').split(/\r?\n/).map(line => line.trim()).filter(Boolean);
    if (lines.length < 2) throw new Error(t('portfolio.invalid_csv'));
    const headers = lines[0].split(',').map(value => value.trim().toLowerCase());
    const required = ['symbol', 'quantity', 'average_cost', 'currency'];
    if (!required.every(key => headers.includes(key))) throw new Error(t('portfolio.invalid_csv'));
    const index = Object.fromEntries(headers.map((header, offset) => [header, offset]));
    return lines.slice(1).map(line => {
      const values = line.split(',').map(value => value.trim());
      return {
        symbol: values[index.symbol]?.toUpperCase(),
        quantity: values[index.quantity],
        average_cost: values[index.average_cost] || null,
        currency: values[index.currency]?.toUpperCase(),
      };
    }).filter(item => item.symbol && item.quantity && item.currency);
  }

  async function importCsv() {
    const id = currentId();
    const file = document.querySelector('#portfolio-import-file')?.files?.[0];
    const message = document.querySelector('#portfolio-message');
    if (!id) {
      message.textContent = t('portfolio.create_first');
      return;
    }
    if (!file) {
      message.textContent = t('portfolio.invalid_csv');
      return;
    }
    try {
      const positions = parsePortfolioCsv(await file.text());
      if (!positions.length) throw new Error(t('portfolio.invalid_csv'));
      await api(`/v1/portfolios/${id}/positions/bulk`, {
        method: 'PUT',
        body: JSON.stringify({
          positions,
          replace: Boolean(document.querySelector('#portfolio-import-replace')?.checked),
        }),
      });
      document.querySelector('#portfolio-import-file').value = '';
      await loadPortfolios(id);
      message.textContent = t('portfolio.imported', { count: positions.length });
    } catch (error) {
      message.textContent = error.message;
    }
  }

  document.querySelector('#portfolio-select')?.addEventListener('change', () => {
    resetRisk();
    loadAnalytics();
  });
  document.querySelector('#refresh-portfolio')?.addEventListener('click', () => loadAnalytics());
  document.querySelector('#risk-run')?.addEventListener('click', loadRisk);
  document.querySelector('#portfolio-import-button')?.addEventListener('click', importCsv);
  document.querySelector('#portfolio-export-button')?.addEventListener('click', () => {
    const id = currentId();
    if (id) window.location.assign(`/v1/portfolios/${id}/export.csv`);
  });

  document.querySelector('#portfolio-create-form')?.addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const message = document.querySelector('#portfolio-message');
    try {
      const created = await api('/v1/portfolios', {
        method: 'POST',
        body: JSON.stringify({
          name: document.querySelector('#portfolio-name').value.trim(),
          base_currency: document.querySelector('#portfolio-base').value.trim().toUpperCase(),
        }),
      });
      form.reset();
      document.querySelector('#portfolio-base').value = 'USD';
      await loadPortfolios(created.id);
      message.textContent = t('portfolio.created', { name: created.name });
    } catch (error) {
      message.textContent = error.message;
    }
  });

  document.querySelector('#position-form')?.addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const id = currentId();
    const message = document.querySelector('#portfolio-message');
    if (!id) {
      message.textContent = t('portfolio.create_first');
      return;
    }
    const costRaw = document.querySelector('#position-cost').value.trim();
    try {
      await api(`/v1/portfolios/${id}/positions`, {
        method: 'PUT',
        body: JSON.stringify({
          symbol: document.querySelector('#position-symbol').value.trim().toUpperCase(),
          quantity: document.querySelector('#position-quantity').value,
          average_cost: costRaw || null,
          currency: document.querySelector('#position-currency').value.trim().toUpperCase(),
        }),
      });
      form.reset();
      document.querySelector('#position-currency').value = 'USD';
      resetRisk();
      await loadPortfolios(id);
      message.textContent = t('portfolio.position_saved');
    } catch (error) {
      message.textContent = error.message;
    }
  });

  loadPortfolios().catch(error => {
    document.querySelector('#portfolio-message').textContent = error.message;
  });
})();