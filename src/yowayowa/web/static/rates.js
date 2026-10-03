(() => {
  const { api, escapeHtml, localeTag, t } = window.Yowayowa;
  const yearInput = document.querySelector('#rate-year');
  let curveChart = null;
  let spreadChart = null;

  function percentPoint(value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
    return `${new Intl.NumberFormat(localeTag, {
      maximumFractionDigits: 2,
      signDisplay: 'always',
    }).format(Number(value))} pp`;
  }

  function yieldValue(value) {
    return `${new Intl.NumberFormat(localeTag, { maximumFractionDigits: 3 }).format(Number(value))}%`;
  }

  function renderTable(latest) {
    document.querySelector('#yield-table').innerHTML = `<table><thead><tr><th>${escapeHtml(t('rates.maturity'))}</th><th>${escapeHtml(t('rates.yield'))}</th></tr></thead><tbody>${latest.points.map(point => `
      <tr><td>${escapeHtml(point.maturity)}</td><td>${escapeHtml(yieldValue(point.yield_percent))}</td></tr>`).join('')}</tbody></table>`;
  }

  function renderCurve(latest) {
    const host = document.querySelector('#yield-curve-chart');
    if (curveChart) curveChart.remove();
    curveChart = LightweightCharts.createChart(host, {
      autoSize: true,
      layout: { background: { type: 'solid', color: 'transparent' }, textColor: '#6c6962', attributionLogo: true },
      grid: { vertLines: { color: 'rgba(23,23,22,.055)' }, horzLines: { color: 'rgba(23,23,22,.055)' } },
      rightPriceScale: { borderColor: '#d1ccc2' },
      timeScale: { visible: false },
      crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    });
    const line = curveChart.addSeries(LightweightCharts.LineSeries, {
      color: '#2456d8', lineWidth: 2, priceLineVisible: false, lastValueVisible: false,
    });
    line.setData(latest.points.map((point, index) => ({ time: index + 1, value: point.yield_percent })));
    curveChart.timeScale().fitContent();
  }

  function renderSpreadHistory(history) {
    const host = document.querySelector('#yield-spread-chart');
    if (spreadChart) spreadChart.remove();
    spreadChart = LightweightCharts.createChart(host, {
      autoSize: true,
      layout: { background: { type: 'solid', color: 'transparent' }, textColor: '#6c6962', attributionLogo: true },
      grid: { vertLines: { color: 'rgba(23,23,22,.055)' }, horzLines: { color: 'rgba(23,23,22,.055)' } },
      rightPriceScale: { borderColor: '#d1ccc2' },
      timeScale: { borderColor: '#d1ccc2', minBarSpacing: 0.05 },
    });
    const twoYear = spreadChart.addSeries(LightweightCharts.LineSeries, {
      color: '#2456d8', lineWidth: 2, priceLineVisible: false, title: t('rates.spread_10y_2y'),
    });
    const threeMonth = spreadChart.addSeries(LightweightCharts.LineSeries, {
      color: '#7254a7', lineWidth: 2, priceLineVisible: false, title: t('rates.spread_10y_3m'),
    });
    twoYear.setData(history.filter(item => item.spread_10y_2y !== null).map(item => ({ time: item.date, value: item.spread_10y_2y })));
    threeMonth.setData(history.filter(item => item.spread_10y_3m !== null).map(item => ({ time: item.date, value: item.spread_10y_3m })));
    twoYear.createPriceLine({
      price: 0,
      color: 'rgba(108,105,98,.55)',
      lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: true,
      title: '0',
    });
    spreadChart.timeScale().fitContent();
  }

  function render(data) {
    const latest = data.latest;
    document.querySelector('#yield-date').textContent = latest.date;
    document.querySelector('#spread-10y-2y').textContent = percentPoint(latest.spread_10y_2y);
    document.querySelector('#spread-10y-3m').textContent = percentPoint(latest.spread_10y_3m);
    renderTable(latest);
    renderCurve(latest);
    renderSpreadHistory(data.history);
    const p = data.provenance;
    const license = t(`license.${p.license_class}`, {}, p.license_class);
    document.querySelector('#rate-provenance').innerHTML = `<div><strong>${escapeHtml(p.source)}</strong></div><div>${escapeHtml(t('common.provider'))} <code>${escapeHtml(p.provider)}</code> · ${escapeHtml(license)}</div><div>${escapeHtml(t('common.retrieved'))} ${escapeHtml(new Date(p.retrieved_at).toLocaleString(localeTag))}</div>${p.source_url ? `<div><a href="${escapeHtml(p.source_url)}" rel="noreferrer">${escapeHtml(t('common.open_source'))}</a></div>` : ''}`;
  }

  async function load() {
    const year = Number(yearInput.value);
    document.querySelector('#yield-table').textContent = t('rates.loading');
    try {
      render(await api(`/v1/rates/treasury/curve?year=${encodeURIComponent(year)}`));
    } catch (error) {
      document.querySelector('#yield-table').textContent = error.message;
    }
  }

  yearInput.value = String(new Date().getFullYear());
  document.querySelector('#reload-rates')?.addEventListener('click', load);
  load();
})();
