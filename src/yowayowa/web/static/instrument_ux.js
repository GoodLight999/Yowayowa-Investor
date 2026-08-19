(() => {
  const { api, escapeHtml, localeTag, t } = window.Yowayowa;
  const host = document.querySelector('.instrument-header');
  const symbol = host?.dataset.symbol;
  if (!symbol) return;

  const percentKeys = new Set([
    'revenue_growth_yoy', 'operating_margin', 'net_margin', 'return_on_equity',
    'free_cash_flow_margin',
  ]);

  function syncIndicators() {
    const hidden = document.querySelector('#indicator-input');
    const checked = [...document.querySelectorAll('[data-indicator-token]:checked')]
      .map(input => input.dataset.indicatorToken);
    if (hidden) hidden.value = checked.join(',');
    const count = document.querySelector('#indicator-count');
    if (count) count.textContent = `${checked.length} ${t('instrument.indicator_selected', {}, 'selected')}`;
  }

  function bindIndicators() {
    document.querySelectorAll('[data-indicator-token]').forEach(input => {
      input.addEventListener('change', () => {
        syncIndicators();
        document.querySelector('#reload-chart')?.click();
      });
    });
    syncIndicators();
  }

  function latestAnnualPoints(series, limit = 5) {
    return (series?.points || [])
      .filter(point => String(point.fiscal_period || '').toUpperCase() === 'FY')
      .sort((a, b) => String(a.period_end).localeCompare(String(b.period_end)))
      .slice(-limit);
  }

  function compact(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return '—';
    return new Intl.NumberFormat(localeTag, { notation: 'compact', maximumFractionDigits: 2 }).format(number);
  }

  function renderFinancialGraph(data) {
    const target = document.querySelector('#financial-trend');
    if (!target) return;
    const keys = ['revenue', 'operating_income', 'net_income', 'operating_cash_flow'];
    const rows = keys.filter(key => data.metrics[key]).map(key => {
      const points = latestAnnualPoints(data.metrics[key], 5);
      if (!points.length) return '';
      const maxAbs = Math.max(...points.map(point => Math.abs(Number(point.value))), 1);
      const cells = points.map(point => {
        const height = Math.max(3, Math.round(Math.abs(Number(point.value)) / maxAbs * 82));
        const negative = Number(point.value) < 0;
        const year = String(point.period_end).slice(0, 4);
        return `<div class="finance-bar-cell" title="${escapeHtml(`${point.period_end} · ${compact(point.value)}`)}">
          <div class="finance-bar ${negative ? 'negative' : ''}" style="height:${height}px"></div>
          <small>${escapeHtml(year)}</small>
        </div>`;
      }).join('');
      return `<div class="finance-bar-row">
        <div class="finance-bar-label"><strong>${escapeHtml(t(`metric.${key}`, {}, data.metrics[key].label || key))}</strong><small class="term-note">${escapeHtml(compact(points.at(-1).value))}</small></div>
        <div class="finance-bar-series" style="--period-count:${points.length}">${cells}</div>
      </div>`;
    }).join('');
    target.innerHTML = rows || `<div class="list-state muted">${escapeHtml(t('research.none', {}, 'No financial history available.'))}</div>`;
  }

  function formatMetric(key, value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
    const number = Number(value);
    if (percentKeys.has(key)) {
      return new Intl.NumberFormat(localeTag, { style: 'percent', maximumFractionDigits: 1, signDisplay: key.includes('growth') ? 'always' : 'auto' }).format(number);
    }
    return new Intl.NumberFormat(localeTag, { maximumFractionDigits: 2 }).format(number);
  }

  function insight(tone, title, detail) {
    return `<div class="finance-insight ${tone}"><span class="finance-insight-dot"></span><div><strong>${escapeHtml(title)}</strong><small>${escapeHtml(detail)}</small></div></div>`;
  }

  function renderMachineRead(metrics) {
    const target = document.querySelector('#machine-financial-read');
    if (!target) return;
    const items = [];
    const growth = metrics.revenue_growth_yoy;
    if (Number.isFinite(Number(growth))) {
      const n = Number(growth);
      items.push(insight(
        n >= 0.10 ? 'positive' : n < 0 ? 'negative' : 'neutral',
        n >= 0.10 ? '売上高は二桁成長' : n < 0 ? '売上高は前年割れ' : '売上高は緩やかな変化',
        `売上高成長率 YoY = ${formatMetric('revenue_growth_yoy', n)}。YoY は前年同期比です。`,
      ));
    }
    const operating = metrics.operating_margin;
    if (Number.isFinite(Number(operating))) {
      const n = Number(operating);
      items.push(insight(
        n >= 0.15 ? 'positive' : n < 0 ? 'negative' : n < 0.05 ? 'caution' : 'neutral',
        n >= 0.15 ? '本業の利益率は高め' : n < 0 ? '本業は営業赤字' : '本業の利益率を確認',
        `営業利益率 (Operating margin) = ${formatMetric('operating_margin', n)}。業種によって適正水準は大きく異なります。`,
      ));
    }
    const fcf = metrics.free_cash_flow_margin;
    if (Number.isFinite(Number(fcf))) {
      const n = Number(fcf);
      items.push(insight(
        n >= 0.10 ? 'positive' : n < 0 ? 'negative' : 'neutral',
        n >= 0.10 ? 'FCF創出は強め' : n < 0 ? 'FCFはマイナス' : 'FCFはプラス',
        `FCFマージン = ${formatMetric('free_cash_flow_margin', n)}。FCFは営業CFから設備投資を差し引いた資金余力の目安です。`,
      ));
    }
    const current = metrics.current_ratio;
    if (Number.isFinite(Number(current))) {
      const n = Number(current);
      items.push(insight(
        n >= 1.5 ? 'positive' : n < 1 ? 'caution' : 'neutral',
        n >= 1.5 ? '短期流動性には余裕' : n < 1 ? '短期流動性は要確認' : '短期流動性は標準圏',
        `流動比率 (Current ratio) = ${formatMetric('current_ratio', n)}。1倍未満でも業種・運転資本構造によって意味は異なります。`,
      ));
    }
    const leverage = metrics.liabilities_to_equity;
    if (Number.isFinite(Number(leverage))) {
      const n = Number(leverage);
      items.push(insight(
        n > 3 ? 'caution' : n < 1 ? 'positive' : 'neutral',
        n > 3 ? '負債負担は大きめ' : n < 1 ? '負債は自己資本より小さい' : '負債水準を業種比較したい',
        `負債÷資本 = ${formatMetric('liabilities_to_equity', n)}倍。金融業などは単純比較に向きません。`,
      ));
    }
    target.innerHTML = items.length ? items.join('') : `<div class="list-state muted">${escapeHtml(t('research.none', {}, 'Not enough comparable financial ratios.'))}</div>`;
  }

  async function loadFinanceUX() {
    try {
      const [fundamentals, screen] = await Promise.all([
        api(`/v1/fundamentals/${encodeURIComponent(symbol)}`),
        api('/v1/screen', { method: 'POST', body: JSON.stringify({ symbols: [symbol], filters: [] }) }),
      ]);
      renderFinancialGraph(fundamentals);
      renderMachineRead(screen.rows?.[0]?.metrics || {});
      colorFinancialValues();
    } catch (error) {
      const graph = document.querySelector('#financial-trend');
      const read = document.querySelector('#machine-financial-read');
      if (graph) graph.textContent = error.message;
      if (read) read.textContent = error.message;
    }
  }

  function analystValue(value, fallback = '—') {
    if (value === null || value === undefined || value === '') return fallback;
    if (typeof value === 'number') return new Intl.NumberFormat(localeTag, { maximumFractionDigits: 2 }).format(value);
    return String(value);
  }

  function renderAnalyst(data) {
    const target = document.querySelector('#analyst-consensus');
    if (!target) return;
    const profile = data.sections?.profile || {};
    const analyst = data.sections?.analyst || {};
    const targets = analyst.price_targets || {};
    const low = profile.targetLowPrice ?? targets.low ?? targets.lowPrice;
    const mean = profile.targetMeanPrice ?? targets.mean ?? targets.meanPrice;
    const high = profile.targetHighPrice ?? targets.high ?? targets.highPrice;
    const recommendation = profile.recommendationKey || profile.recommendationMean || '—';
    const analystCount = profile.numberOfAnalystOpinions ?? targets.numberOfAnalystOpinions ?? '—';
    target.innerHTML = `<div class="analyst-grid">
      <div class="analyst-metric"><small>Target low</small><strong>${escapeHtml(analystValue(low))}</strong></div>
      <div class="analyst-metric"><small>Target mean</small><strong>${escapeHtml(analystValue(mean))}</strong></div>
      <div class="analyst-metric"><small>Target high</small><strong>${escapeHtml(analystValue(high))}</strong></div>
      <div class="analyst-metric"><small>Consensus · ${escapeHtml(analystValue(analystCount))} analysts</small><strong>${escapeHtml(analystValue(recommendation))}</strong></div>
    </div>
    <div class="analyst-source">${escapeHtml(data.provenance?.source || '—')} · ${escapeHtml(data.provenance?.retrieved_at ? new Date(data.provenance.retrieved_at).toLocaleString(localeTag) : '—')}</div>`;
  }

  async function loadAnalyst() {
    const target = document.querySelector('#analyst-consensus');
    if (!target) return;
    target.textContent = t('common.loading_ellipsis');
    try {
      renderAnalyst(await api(`/v1/research/${encodeURIComponent(symbol)}?sections=profile,analyst`));
    } catch (error) {
      target.textContent = error.message;
    }
  }

  function colorFinancialValues() {
    document.querySelectorAll('#fundamentals-table td:not(:first-child)').forEach(cell => {
      const normalized = cell.textContent.replaceAll(',', '').replace(/[A-Za-z¥$€£%×\s]/g, '');
      const number = Number(normalized);
      if (Number.isFinite(number) && number < 0) cell.classList.add('metric-negative');
    });
    document.querySelectorAll('#financial-health-metrics .metric-card strong').forEach(node => {
      const text = node.textContent.trim();
      if (text.startsWith('+')) node.classList.add('metric-positive');
      else if (text.startsWith('-')) node.classList.add('metric-negative');
    });
  }

  const observer = new MutationObserver(colorFinancialValues);
  const financialTable = document.querySelector('#fundamentals-table');
  if (financialTable) observer.observe(financialTable, { childList: true, subtree: true });

  bindIndicators();
  loadFinanceUX();
  loadAnalyst();
})();
