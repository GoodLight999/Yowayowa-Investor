(() => {
  const examples = {
    prices: {
      sources: [
        { id: 'aapl', source: 'price', identifier: 'AAPL', label: 'AAPL price' },
        { id: 'msft', source: 'price', identifier: 'MSFT', label: 'MSFT price' },
      ], transforms: [],
    },
    financial: {
      sources: [
        { id: 'px', source: 'price', identifier: 'AAPL', label: 'AAPL price' },
        { id: 'rev', source: 'fundamental', identifier: 'AAPL', metric: 'revenue', label: 'AAPL revenue' },
      ], transforms: [],
    },
    correlation: {
      sources: [
        { id: 'aapl', source: 'price', identifier: 'AAPL', label: 'AAPL price' },
        { id: 'msft', source: 'price', identifier: 'MSFT', label: 'MSFT price' },
      ],
      transforms: [{ id: 'corr', kind: 'rolling_correlation', left: 'aapl', right: 'msft', window: 60, label: '60D correlation' }],
    },
  };

  function dispatchChange(node) {
    node.dispatchEvent(new Event('change', { bubbles: true }));
    node.dispatchEvent(new Event('input', { bubbles: true }));
  }

  function clearRows(selector) {
    document.querySelectorAll(`${selector} .composer-remove`).forEach(button => button.click());
  }

  function addSource(spec) {
    document.querySelector('#add-chart-source').click();
    const rows = document.querySelectorAll('#composer-sources .composer-row');
    const row = rows[rows.length - 1];
    row.querySelector('.composer-source-type').value = spec.source;
    dispatchChange(row.querySelector('.composer-source-type'));
    row.querySelector('.composer-source-id').value = spec.id;
    dispatchChange(row.querySelector('.composer-source-id'));
    row.querySelector('.composer-source-identifier').value = spec.identifier;
    row.querySelector('.composer-source-metric').value = spec.metric || '';
    row.querySelector('.composer-source-label').value = spec.label || '';
  }

  function addFormula(spec) {
    document.querySelector('#add-chart-formula').click();
    const rows = document.querySelectorAll('#composer-formulas .composer-row');
    const row = rows[rows.length - 1];
    row.querySelector('.composer-formula-kind').value = spec.kind;
    row.querySelector('.composer-formula-id').value = spec.id;
    row.querySelector('.composer-formula-left').value = spec.left;
    row.querySelector('.composer-formula-right').value = spec.right;
    row.querySelector('.composer-formula-window').value = String(spec.window || 60);
    row.querySelector('.composer-formula-label').value = spec.label || '';
  }

  function applyExample(name) {
    const example = examples[name];
    if (!example) return;
    clearRows('#composer-formulas');
    clearRows('#composer-sources');
    example.sources.forEach(addSource);
    example.transforms.forEach(addFormula);
    document.querySelector('#compose-chart').click();
  }

  document.querySelectorAll('[data-chart-example]').forEach(button => {
    button.addEventListener('click', () => applyExample(button.dataset.chartExample));
  });
})();
