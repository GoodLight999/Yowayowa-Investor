(() => {
  const { api, apiStatusMessage, escapeHtml, localeTag } = window.Yowayowa;
  const ja = window.YOWAYOWA_LOCALE === 'ja';
  const text = ja ? {
    loading: '読込中',
    loadFailed: 'この情報を取得できませんでした。',
    noProposals: '提案記録はありません。',
    noProposalData: '監査記録が不完全なため、提案一覧を表示できません。',
    auditIntact: '監査記録: 整合性確認済み',
    auditBroken: '監査記録の整合性を確認できません。表示中の内容を信頼できる履歴として扱わないでください。',
    auditTruncated: '監査履歴の最新1,000件のみ表示しています。古い提案は一覧に含まれない場合があります。',
    evaluateMissing: '評価記録なし',
    evaluateAllowed: 'ゲート評価: 許可',
    evaluateBlocked: 'ゲート評価: 不許可',
    armed: 'armed',
    notArmed: 'armed なし',
    approved: '承認記録なし',
    noSubmit: '発注結果の記録なし',
    frozen: '発注停止ゲートで拒否',
    submitFailed: '発注失敗の記録あり',
    submitPending: '発注要求あり・応答記録なし',
    accepted: 'ブローカー受付記録あり',
    rejected: 'ブローカー拒否記録あり',
    submitUnknown: '受付結果を確認できない記録あり',
    auditState: '提案・評価・実行記録',
    emptyOrders: 'この読み取りでは注文行が返されませんでした。',
    unavailableOrders: '読み取りが完了していないため、注文の有無を判定できません。',
    emptyFills: 'この読み取りでは約定行が返されませんでした。フィード範囲は未確認です。',
    unavailableFills: '読み取りが完了していないため、約定の有無を判定できません。',
    fillsUnverified: '約定データの取得先・項目は実口座セッションで未検証です。実画面で確認されるまでは確定情報として扱わないでください。',
    ordersUnverified: '注文照会の取得先は実口座セッションで未検証です。注文状況はブローカーの画面でも確認してください。',
    notAvailable: '未取得',
    tableProposal: '提案',
    tableEvaluation: '評価記録',
    tableExecution: '発注結果記録',
    tableOrder: '注文',
    tableMatch: '照合',
    tableTime: '約定日時',
    tableFill: '約定',
    tablePrice: '価格',
    tableExecutionId: '約定ID',
    tableBrokerOrderId: 'ブローカー注文ID',
    gateReasons: '理由',
    source: '取得元',
    retrieved: '取得時刻',
    asOf: '基準時点',
    generated: '照会時刻',
    catalogVerified: '取得先検証済み',
    auditProblems: '確認項目',
    refreshed: '画面確認時刻',
    readState: '注文読み取り',
    fillsState: '約定読み取り',
    authState: '認証状態',
    yes: 'はい',
    no: 'いいえ',
  } : {
    loading: 'Loading',
    loadFailed: 'This information could not be retrieved.',
    noProposals: 'No proposal records.',
    noProposalData: 'Proposal records cannot be shown because the audit record is incomplete.',
    auditIntact: 'Audit record: integrity verified',
    auditBroken: 'Audit integrity could not be verified. Do not treat the displayed content as a reliable history.',
    auditTruncated: 'Showing only the latest 1,000 audit events. Older proposals may not appear in this list.',
    evaluateMissing: 'No evaluation recorded',
    evaluateAllowed: 'Gate evaluation: allowed',
    evaluateBlocked: 'Gate evaluation: blocked',
    armed: 'armed',
    notArmed: 'not armed',
    approved: 'No approval record',
    noSubmit: 'No submission result recorded',
    frozen: 'Rejected by frozen submission gate',
    submitFailed: 'Submission failure recorded',
    submitPending: 'Submission request recorded · no response recorded',
    accepted: 'Broker acknowledgement recorded',
    rejected: 'Broker rejection recorded',
    submitUnknown: 'Acceptance not confirmed',
    auditState: 'Proposal, evaluation & execution record',
    emptyOrders: 'No order rows were returned in this read.',
    unavailableOrders: 'The read is incomplete; order presence cannot be determined.',
    emptyFills: 'No fill rows were returned in this read. Feed coverage is unconfirmed.',
    unavailableFills: 'The read is incomplete; fill presence cannot be determined.',
    fillsUnverified: 'The fill data source and fields are unverified against a real broker session. Do not treat them as confirmed until checked in the broker UI.',
    ordersUnverified: 'The order inquiry sources are unverified against a real broker session. Confirm order status in the broker UI as well.',
    notAvailable: 'Not available',
    tableProposal: 'Proposal',
    tableEvaluation: 'Evaluation record',
    tableExecution: 'Submission result record',
    tableOrder: 'Order',
    tableMatch: 'Match',
    tableTime: 'Executed at',
    tableFill: 'Fill',
    tablePrice: 'Price',
    tableExecutionId: 'Execution ID',
    tableBrokerOrderId: 'Broker order ID',
    gateReasons: 'Reasons',
    source: 'Source',
    retrieved: 'Retrieved',
    asOf: 'As of',
    generated: 'Inquiry time',
    catalogVerified: 'Source verified',
    auditProblems: 'Integrity findings',
    refreshed: 'Page checked',
    readState: 'Order read',
    fillsState: 'Fill read',
    authState: 'Authentication',
    yes: 'Yes',
    no: 'No',
  };

  const missing = () => text.notAvailable;
  const value = raw => (raw === null || raw === undefined || raw === '' ? missing() : String(raw));
  const safe = raw => escapeHtml(value(raw));
  const statusText = raw => String(raw || 'unknown').replaceAll('_', ' ');
  let loadSequence = 0;

  function setMessage(target, message, className = 'broker-empty') {
    target.innerHTML = `<p class="${className}">${escapeHtml(message)}</p>`;
  }

  function evaluationLabel(entry) {
    if (!entry) return `<strong>${escapeHtml(text.evaluateMissing)}</strong><small>${escapeHtml(text.approved)}</small>`;
    const payload = entry.payload || {};
    const allowed = payload.allowed === true;
    const armed = payload.armed === true;
    const reasons = Array.isArray(payload.reasons) ? payload.reasons : [];
    const details = reasons.length
      ? `<small>${escapeHtml(text.gateReasons)}: ${reasons.map(escapeHtml).join(' · ')}</small>`
      : '';
    return `<strong>${escapeHtml(allowed ? text.evaluateAllowed : text.evaluateBlocked)}</strong><small>${escapeHtml(armed ? text.armed : text.notArmed)}</small>${details}<small>${escapeHtml(text.approved)}</small>`;
  }

  function submissionLabel(entry) {
    if (!entry) return `<strong>${escapeHtml(text.noSubmit)}</strong>`;
    if (entry.kind === 'response') {
      const payload = entry.payload || {};
      const accepted = payload.accepted === true;
      const status = payload.status;
      const label = accepted ? text.accepted : status === 'rejected' ? text.rejected : text.submitUnknown;
      return `<strong>${escapeHtml(label)}</strong><small>${escapeHtml(value(status))}</small>${payload.broker_order_id ? `<small>${escapeHtml(text.tableBrokerOrderId)}: ${safe(payload.broker_order_id)}</small>` : ''}`;
    }
    const stage = entry.payload?.stage;
    if (stage === 'submit-frozen') return `<strong>${escapeHtml(text.frozen)}</strong>`;
    if (stage === 'submit-failed') return `<strong>${escapeHtml(text.submitFailed)}</strong>`;
    if (entry.kind === 'request') return `<strong>${escapeHtml(text.submitPending)}</strong>`;
    return `<strong>${escapeHtml(text.noSubmit)}</strong>`;
  }

  function renderProposals(audit) {
    const host = document.querySelector('#proposal-table');
    const state = document.querySelector('#proposal-audit-state');
    const warning = document.querySelector('#proposal-audit-warning');
    if (!audit.intact) {
      warning.hidden = false;
      const problems = Array.isArray(audit.verify_problems) ? audit.verify_problems : [];
      warning.innerHTML = `<strong>${escapeHtml(text.auditBroken)}</strong>${problems.length ? `<ul>${problems.map(problem => `<li>${escapeHtml(problem)}</li>`).join('')}</ul>` : ''}`;
      state.textContent = ja ? '整合性未確認' : 'Integrity unverified';
      setMessage(host, text.noProposalData);
      return;
    }
    const entries = Array.isArray(audit.entries) ? audit.entries : [];
    const totalEntries = Number(audit.total_entries);
    const truncated = Number.isFinite(totalEntries) && totalEntries > entries.length;
    warning.hidden = !truncated;
    if (truncated) warning.textContent = text.auditTruncated;
    state.textContent = text.auditIntact;
    const proposals = entries.filter(entry => entry.kind === 'intent' && entry.payload?.proposal);
    if (!proposals.length) {
      setMessage(host, text.noProposals);
      return;
    }
    const rows = proposals.slice().reverse().map(entry => {
      const proposal = entry.payload.proposal;
      const clientId = entry.client_order_id;
      const ownEntries = entries.filter(item => item.client_order_id === clientId);
      const latestEvaluation = ownEntries.filter(item => item.kind === 'state' && item.payload?.stage === 'evaluate').at(-1);
      const latestSubmissionEvent = ownEntries.filter(item => (
        (item.kind === 'state' && ['submit-frozen', 'submit-failed'].includes(item.payload?.stage))
        || (item.kind === 'request' && item.payload?.stage === 'submit')
        || (item.kind === 'response' && item.payload?.stage === 'submit')
      )).at(-1);
      const referencePrice = proposal.limit_price ?? proposal.reference_price;
      const priceLabel = proposal.limit_price ? 'limit' : (proposal.reference_price ? 'reference' : '');
      const motivation = proposal.motivation ? `<small>${safe(proposal.motivation)}</small>` : '';
      const researchLink = proposal.source_research_link
        ? `<small>${ja ? '調査参照' : 'Research reference'}: ${safe(proposal.source_research_link)}</small>`
        : '';
      const provenance = proposal.provenance && typeof proposal.provenance === 'object'
        ? Object.entries(proposal.provenance).map(([key, item]) => `<small>${safe(key)}: ${safe(item)}</small>`).join('')
        : '';
      return `<tr>
        <td><strong>${safe(proposal.symbol)}</strong><small>${safe(clientId)} · ${safe(proposal.market)}</small><small>${safe(entry.ts)}</small>${motivation}${researchLink}${provenance}</td>
        <td>${safe(proposal.side)} ${safe(proposal.quantity)}<small>${safe(proposal.order_type)}${priceLabel ? ` · ${escapeHtml(priceLabel)} ${safe(referencePrice)}` : ''} ${safe(proposal.currency)}</small></td>
        <td>${evaluationLabel(latestEvaluation)}</td>
        <td>${submissionLabel(latestSubmissionEvent)}</td>
      </tr>`;
    }).join('');
    host.innerHTML = `<table class="broker-table"><thead><tr><th>${escapeHtml(text.tableProposal)}</th><th>${ja ? '売買・数量・価格' : 'Side · quantity · price'}</th><th>${escapeHtml(text.tableEvaluation)}</th><th>${escapeHtml(text.tableExecution)}</th></tr></thead><tbody>${rows}</tbody></table>`;
  }

  function renderReadSummary(report) {
    const openState = statusText(report.fetch_state_open?.state || report.fetch_state_open);
    const historyState = statusText(report.fetch_state_history?.state || report.fetch_state_history);
    const auth = statusText(report.auth_state?.state || report.auth_state);
    const complete = openState === 'ok' && historyState === 'ok' && auth === 'authenticated';
    return { complete, label: `${ja ? '未約定' : 'Open'}: ${openState} · ${ja ? '履歴' : 'History'}: ${historyState} · ${text.authState}: ${auth}` };
  }

  function renderOrders(report) {
    const host = document.querySelector('#orders-table');
    const meta = document.querySelector('#orders-fetch-state');
    const provenance = document.querySelector('#orders-provenance');
    const warning = document.querySelector('#orders-warning');
    const state = renderReadSummary(report);
    meta.textContent = state.label;
    meta.classList.toggle('is-warning', !state.complete);
    const catalogUnverified = report.catalog_verified_open === false || report.catalog_verified_history === false;
    warning.hidden = !catalogUnverified;
    if (catalogUnverified) warning.textContent = text.ordersUnverified;
    const items = Array.isArray(report.items) ? report.items : [];
    if (!items.length) {
      setMessage(host, state.complete ? text.emptyOrders : text.unavailableOrders);
    } else {
      const rows = items.map(item => {
        const order = item.order || {};
        const statusLabels = ja ? {
          preview: 'プレビュー', accepted: '受付済', pending: '受付確認中',
          partially_filled: '一部約定', filled: '約定済', cancelled: '取消済',
          inactive: '無効', rejected: '拒否', unknown: '不明',
        } : {
          preview: 'Preview', accepted: 'Accepted', pending: 'Pending',
          partially_filled: 'Partially filled', filled: 'Filled', cancelled: 'Cancelled',
          inactive: 'Inactive', rejected: 'Rejected', unknown: 'Unknown',
        };
        const orderStatus = statusLabels[order.status] || statusText(order.status);
        const orderSide = order.side === 'buy' ? (ja ? '買い' : 'Buy')
          : order.side === 'sell' ? (ja ? '売り' : 'Sell') : value(order.side);
        const match = item.match === 'audit_matched'
          ? (ja ? '提案と照合' : 'Audit matched')
          : item.match === 'audit_only'
            ? (ja ? '記録のみ・注文照会なし' : 'Audit only · not in inquiry')
            : (ja ? '監査記録なし' : 'No audit match');
        return `<tr>
          <td><strong>${safe(order.symbol)}</strong><small>${safe(report.market)} · ${escapeHtml(orderSide)}</small></td>
          <td>${safe(order.quantity)}</td>
          <td>${escapeHtml(orderStatus)}</td>
          <td>${safe(order.broker_order_id)}</td>
          <td>${escapeHtml(match)}${item.client_order_id ? `<small>${safe(item.client_order_id)}</small>` : ''}</td>
        </tr>`;
      }).join('');
      host.innerHTML = `<table class="broker-table"><thead><tr><th>${escapeHtml(text.tableOrder)}</th><th>${ja ? '注文数量' : 'Order quantity'}</th><th>${ja ? '状態' : 'Status'}</th><th>${escapeHtml(text.tableBrokerOrderId)}</th><th>${escapeHtml(text.tableMatch)}</th></tr></thead><tbody>${rows}</tbody></table>`;
    }
    const sources = Array.isArray(report.source_urls) ? report.source_urls : [];
    provenance.innerHTML = [
      [text.generated, report.generated_at],
      [ja ? '未約定・取得時刻' : 'Open orders · retrieved', report.retrieved_at_open],
      [ja ? '未約定・基準時点' : 'Open orders · as of', report.as_of_open],
      [ja ? '履歴・取得時刻' : 'Order history · retrieved', report.retrieved_at_history],
      [ja ? '履歴・基準時点' : 'Order history · as of', report.as_of_history],
      ...sources.map(url => [text.source, url]),
    ].filter(([, item]) => item !== null && item !== undefined && item !== '')
      .map(([label, item]) => `<span><strong>${escapeHtml(label)}:</strong> ${safe(item)}</span>`).join('');
  }

  function renderFills(outcome) {
    const host = document.querySelector('#fills-table');
    const meta = document.querySelector('#fills-fetch-state');
    const warning = document.querySelector('#fills-warning');
    const provenance = document.querySelector('#fills-provenance');
    const fetchState = statusText(outcome.fetch_state?.value || outcome.fetch_state);
    const auth = statusText(outcome.auth_state?.value || outcome.auth_state);
    const complete = fetchState === 'ok' && auth === 'authenticated';
    meta.textContent = `${text.fillsState}: ${fetchState} · ${text.authState}: ${auth}`;
    meta.classList.toggle('is-warning', !complete);
    warning.hidden = true;
    const verified = outcome.detail?.verified;
    if (verified === false) {
      warning.hidden = false;
      warning.textContent = text.fillsUnverified;
    }
    const executions = Array.isArray(outcome.executions) ? outcome.executions : [];
    if (!executions.length) {
      setMessage(host, complete ? text.emptyFills : text.unavailableFills);
    } else {
      const rows = executions.map(execution => {
        const executedAt = execution.executed_at
          ? safe(execution.executed_at)
          : execution.executed_at_raw
            ? `${safe(execution.executed_at_raw)}<small>${ja ? '原表記' : 'Raw source time'}</small>`
            : missing();
        return `<tr>
        <td>${executedAt}</td>
        <td><strong>${safe(execution.symbol)}</strong><small>${safe(execution.side)}</small></td>
        <td>${safe(execution.quantity)}</td>
        <td>${safe(execution.price)}</td>
        <td>${safe(execution.currency)}</td>
        <td>${safe(execution.execution_id)}</td>
        <td>${safe(execution.broker_order_id)}</td>
      </tr>`;
      }).join('');
      host.innerHTML = `<table class="broker-table"><thead><tr><th>${escapeHtml(text.tableTime)}</th><th>${escapeHtml(text.tableFill)}</th><th>${ja ? '数量' : 'Quantity'}</th><th>${escapeHtml(text.tablePrice)}</th><th>${ja ? '通貨' : 'Currency'}</th><th>${escapeHtml(text.tableExecutionId)}</th><th>${escapeHtml(text.tableBrokerOrderId)}</th></tr></thead><tbody>${rows}</tbody></table>`;
    }
    const snapshot = outcome.snapshot || {};
    provenance.innerHTML = [
      [text.source, outcome.source_url],
      [text.retrieved, outcome.retrieved_at],
      [text.asOf, outcome.as_of],
      [ja ? 'パーサー / スキーマ' : 'Parser / schema', [outcome.parser_version, outcome.schema_version].filter(Boolean).join(' / ')],
      ['Snapshot', snapshot.snapshot_id],
      ['SHA-256', snapshot.payload_sha256],
      [text.catalogVerified, verified === true ? text.yes : verified === false ? text.no : missing()],
    ].filter(([, item]) => item !== null && item !== undefined && item !== '')
      .map(([label, item]) => `<span><strong>${escapeHtml(label)}:</strong> ${safe(item)}</span>`).join('');
  }

  async function loadPage() {
    const sequence = ++loadSequence;
    const market = document.querySelector('#execution-market').value;
    document.querySelector('#execution-updated').textContent = text.loading;
    document.querySelector('#proposal-audit-state').textContent = text.loading;
    document.querySelector('#orders-fetch-state').textContent = text.loading;
    document.querySelector('#fills-fetch-state').textContent = text.loading;
    document.querySelector('#proposal-audit-warning').hidden = true;
    document.querySelector('#orders-warning').hidden = true;
    document.querySelector('#fills-warning').hidden = true;
    const urls = [
      api('/v1/broker-execution/audit?limit=1000'),
      api(`/v1/broker-execution/orders?market=${encodeURIComponent(market)}`),
      api(`/v1/broker-execution/executions?market=${encodeURIComponent(market)}`),
    ];
    const results = await Promise.allSettled(urls);
    if (sequence !== loadSequence) return;
    const targets = ['#proposal-table', '#orders-table', '#fills-table'];
    const stateTargets = ['#proposal-audit-state', '#orders-fetch-state', '#fills-fetch-state'];
    results.forEach((result, index) => {
      if (result.status === 'fulfilled') {
        if (index === 0) renderProposals(result.value);
        if (index === 1) renderOrders(result.value);
        if (index === 2) renderFills(result.value);
      } else {
        const statusMessage = result.reason?.status
          ? apiStatusMessage(result.reason.status)
          : null;
        setMessage(document.querySelector(targets[index]), statusMessage || text.loadFailed, 'broker-error');
        document.querySelector(stateTargets[index]).textContent = statusMessage || result.reason?.message || text.loadFailed;
        document.querySelector(stateTargets[index]).classList.add('is-warning');
      }
    });
    document.querySelector('#execution-updated').textContent = `${text.refreshed}: ${new Date().toISOString()}`;
  }

  document.querySelector('#execution-refresh')?.addEventListener('click', () => { void loadPage(); });
  document.querySelector('#execution-market')?.addEventListener('change', () => { void loadPage(); });
  void loadPage();
})();
