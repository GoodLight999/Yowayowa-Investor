(() => {
  const { api, escapeHtml, localeTag, t } = window.Yowayowa;

  const number = value => new Intl.NumberFormat(localeTag, { maximumFractionDigits: 6 }).format(Number(value));
  const scopeLabels = new Map([['all', t('calendar.scope.saved')]]);

  function statusLabel(item) {
    if (item.triggered_at) return t('alerts.triggered');
    return item.enabled ? t('alerts.active') : t('alerts.inactive');
  }

  function statusClass(item) {
    return item.triggered_at ? 'market-positive' : item.enabled ? '' : 'muted';
  }

  function condition(item) {
    return item.operator === 'above' ? `≥ ${number(item.target)}` : `≤ ${number(item.target)}`;
  }

  async function deleteEmpty(path) {
    const response = await fetch(path, { method: 'DELETE' });
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  }

  function render(items) {
    const target = document.querySelector('#alerts-list');
    if (!items.length) {
      target.innerHTML = `<span class="muted">${escapeHtml(t('alerts.none'))}</span>`;
      return;
    }
    target.innerHTML = `<table><thead><tr><th>${escapeHtml(t('common.symbol'))}</th><th>${escapeHtml(t('alerts.condition'))}</th><th>${escapeHtml(t('alerts.last_price'))}</th><th>${escapeHtml(t('alerts.status'))}</th><th>${escapeHtml(t('alerts.checked'))}</th><th></th></tr></thead><tbody>${items.map(item => `
      <tr>
        <td><a href="/instrument/${encodeURIComponent(item.symbol)}"><strong>${escapeHtml(item.symbol)}</strong></a></td>
        <td>${escapeHtml(condition(item))}</td>
        <td>${item.last_price === null ? '—' : escapeHtml(number(item.last_price))}</td>
        <td class="${statusClass(item)}">${escapeHtml(statusLabel(item))}</td>
        <td>${item.last_checked_at ? escapeHtml(new Date(item.last_checked_at).toLocaleString(localeTag)) : '—'}</td>
        <td><button class="ghost alert-delete" type="button" data-id="${item.id}">${escapeHtml(t('common.remove'))}</button></td>
      </tr>`).join('')}</tbody></table>`;
    target.querySelectorAll('.alert-delete').forEach(button => {
      button.addEventListener('click', async () => {
        try {
          await deleteEmpty(`/v1/alerts/${encodeURIComponent(button.dataset.id)}`);
          await loadAlerts();
        } catch (error) {
          document.querySelector('#alert-message').textContent = error.message;
        }
      });
    });
  }

  async function loadAlerts() {
    render(await api('/v1/alerts'));
  }

  function renderProvenance(data) {
    const p = data.provenance;
    const license = t(`license.${p.license_class}`, {}, p.license_class);
    document.querySelector('#alerts-as-of').textContent = `${t('common.as_of')} ${new Date(data.evaluated_at).toLocaleString(localeTag)}`;
    document.querySelector('#alerts-provenance').innerHTML = `
      <div><strong>${escapeHtml(p.source)}</strong></div>
      <div>${escapeHtml(t('common.provider'))} <code>${escapeHtml(p.provider)}</code> · ${escapeHtml(license)}</div>
      <div>${escapeHtml(t('common.retrieved'))} ${escapeHtml(new Date(p.retrieved_at).toLocaleString(localeTag))}</div>`;
  }

  async function installEventScopes() {
    const select = document.querySelector('#event-scope');
    if (!select) return;
    const [watchlists, portfolios] = await Promise.all([
      api('/v1/watchlists'),
      api('/v1/portfolios'),
    ]);
    watchlists.forEach(item => {
      const value = `watchlist:${item.id}`;
      const label = t('calendar.scope.watchlist', { name: item.name });
      scopeLabels.set(value, label);
      select.append(new Option(label, value));
    });
    portfolios.forEach(item => {
      const value = `portfolio:${item.id}`;
      const label = t('calendar.scope.portfolio', { name: item.name });
      scopeLabels.set(value, label);
      select.append(new Option(label, value));
    });
  }

  function scopeValue(item) {
    return item.scope === 'all' ? 'all' : `${item.scope}:${item.scope_id}`;
  }

  function scopePayload(value) {
    if (value === 'all') return { scope: 'all' };
    const [scope, id] = value.split(':', 2);
    return { scope, scope_id: Number(id) };
  }

  function renderSubscriptions(items) {
    const target = document.querySelector('#event-subscriptions');
    if (!items.length) {
      target.innerHTML = `<span class="muted">${escapeHtml(t('events.none_subscriptions'))}</span>`;
      return;
    }
    target.innerHTML = `<table><thead><tr><th>${escapeHtml(t('events.scope'))}</th><th>${escapeHtml(t('events.types'))}</th><th>${escapeHtml(t('events.lead_days'))}</th><th>${escapeHtml(t('alerts.checked'))}</th><th></th></tr></thead><tbody>${items.map(item => `
      <tr>
        <td>${escapeHtml(scopeLabels.get(scopeValue(item)) || scopeValue(item))}</td>
        <td>${escapeHtml(item.event_types.map(type => t(`calendar.${type}`, {}, type)).join(', '))}</td>
        <td>${escapeHtml(String(item.lead_days))}</td>
        <td>${item.last_checked_at ? escapeHtml(new Date(item.last_checked_at).toLocaleString(localeTag)) : '—'}</td>
        <td><button class="ghost event-subscription-delete" type="button" data-id="${item.id}">${escapeHtml(t('common.remove'))}</button></td>
      </tr>`).join('')}</tbody></table>`;
    target.querySelectorAll('.event-subscription-delete').forEach(button => {
      button.addEventListener('click', async () => {
        try {
          await deleteEmpty(`/v1/event-subscriptions/${encodeURIComponent(button.dataset.id)}`);
          await Promise.all([loadSubscriptions(), loadInbox()]);
        } catch (error) {
          document.querySelector('#event-message').textContent = error.message;
        }
      });
    });
  }

  async function loadSubscriptions() {
    renderSubscriptions(await api('/v1/event-subscriptions'));
  }

  function renderInbox(items) {
    const target = document.querySelector('#event-inbox');
    document.querySelector('#event-inbox-count').textContent = String(items.length);
    if (!items.length) {
      target.innerHTML = `<span class="muted">${escapeHtml(t('events.none_inbox'))}</span>`;
      return;
    }
    target.innerHTML = `<table><thead><tr><th>${escapeHtml(t('calendar.when'))}</th><th>${escapeHtml(t('calendar.type'))}</th><th>${escapeHtml(t('common.symbol'))}</th><th>${escapeHtml(t('calendar.event'))}</th><th></th></tr></thead><tbody>${items.map(item => `
      <tr>
        <td>${escapeHtml(new Date(item.starts_at).toLocaleString(localeTag))}</td>
        <td>${escapeHtml(t(`calendar.${item.event_type}`, {}, item.event_type))}</td>
        <td><a href="/instrument/${encodeURIComponent(item.symbol)}"><strong>${escapeHtml(item.symbol)}</strong></a></td>
        <td>${escapeHtml(item.title)}</td>
        <td><button class="ghost event-inbox-ack" type="button" data-id="${item.id}">${escapeHtml(t('events.ack'))}</button></td>
      </tr>`).join('')}</tbody></table>`;
    target.querySelectorAll('.event-inbox-ack').forEach(button => {
      button.addEventListener('click', async () => {
        try {
          await api(`/v1/event-inbox/${encodeURIComponent(button.dataset.id)}/ack`, { method: 'POST' });
          await loadInbox();
        } catch (error) {
          document.querySelector('#event-message').textContent = error.message;
        }
      });
    });
  }

  async function loadInbox() {
    renderInbox(await api('/v1/event-inbox'));
  }

  async function evaluate() {
    const priceMessage = document.querySelector('#alert-message');
    const eventMessage = document.querySelector('#event-message');
    priceMessage.textContent = t('alerts.evaluating');
    eventMessage.textContent = t('alerts.evaluating');
    const [priceResult, eventResult] = await Promise.allSettled([
      api('/v1/alerts/evaluate', { method: 'POST' }),
      api('/v1/event-subscriptions/evaluate', { method: 'POST' }),
    ]);
    if (priceResult.status === 'fulfilled') {
      render(priceResult.value.alerts);
      renderProvenance(priceResult.value);
      priceMessage.textContent = '';
    } else {
      priceMessage.textContent = priceResult.reason.message;
    }
    if (eventResult.status === 'fulfilled') {
      renderSubscriptions(eventResult.value.subscriptions);
      renderInbox(eventResult.value.inbox);
      renderProvenance(eventResult.value);
      eventMessage.textContent = eventResult.value.unavailable_symbols.length
        ? `${t('common.unavailable')}: ${eventResult.value.unavailable_symbols.join(', ')}`
        : '';
    } else {
      eventMessage.textContent = eventResult.reason.message;
    }
  }

  document.querySelector('#alert-form')?.addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const message = document.querySelector('#alert-message');
    try {
      await api('/v1/alerts', {
        method: 'POST',
        body: JSON.stringify({
          symbol: document.querySelector('#alert-symbol').value.trim().toUpperCase(),
          operator: document.querySelector('#alert-operator').value,
          target: document.querySelector('#alert-target').value,
        }),
      });
      form.reset();
      document.querySelector('#alert-operator').value = 'above';
      await loadAlerts();
      message.textContent = t('alerts.saved');
    } catch (error) {
      message.textContent = error.message;
    }
  });

  document.querySelector('#event-subscription-form')?.addEventListener('submit', async event => {
    event.preventDefault();
    const message = document.querySelector('#event-message');
    const eventTypes = [...document.querySelectorAll('#event-types input:checked')]
      .map(input => input.value);
    if (!eventTypes.length) {
      message.textContent = t('events.types');
      return;
    }
    try {
      await api('/v1/event-subscriptions', {
        method: 'POST',
        body: JSON.stringify({
          ...scopePayload(document.querySelector('#event-scope').value),
          event_types: eventTypes,
          lead_days: Number(document.querySelector('#event-lead-days').value),
        }),
      });
      await loadSubscriptions();
      message.textContent = t('events.saved');
    } catch (error) {
      message.textContent = error.message;
    }
  });

  document.querySelector('#evaluate-alerts')?.addEventListener('click', evaluate);
  installEventScopes()
    .then(loadSubscriptions)
    .catch(error => {
      document.querySelector('#event-message').textContent = error.message;
    });
  loadAlerts().catch(error => {
    document.querySelector('#alert-message').textContent = error.message;
  });
  loadInbox().catch(error => {
    document.querySelector('#event-message').textContent = error.message;
  });
})();
