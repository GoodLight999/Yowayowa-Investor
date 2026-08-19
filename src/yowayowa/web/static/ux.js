(() => {
  if (window.__YOWAYOWA_UX_INSTALLED) return;
  window.__YOWAYOWA_UX_INSTALLED = true;

  const META_KEY = 'yowayowa.ai.settings.v2';
  const KEY_KEY = 'yowayowa.ai.key.v2';
  const LEGACY_KEY = 'yowayowa.ai.byok.session';
  let firstTurboLoad = true;
  let activeSection = null;
  const contextMessages = [];

  const ja = window.YOWAYOWA_LOCALE === 'ja';
  Object.assign(window.YOWAYOWA_I18N || {}, ja ? {
    'nav.calendar': '決算・経済イベント',
    'screener.evaluating': '財務データを評価中…',
    'compare.loading_sec': '財務データを取得中…',
    'instrument.valuation_basis': '最新年次財務',
  } : {
    'nav.calendar': 'Earnings & market events',
    'screener.evaluating': 'Evaluating financial statements…',
    'compare.loading_sec': 'Loading financial statements…',
    'instrument.valuation_basis': 'Latest annual financials',
  });

  function settingsMeta() {
    try { return JSON.parse(localStorage.getItem(META_KEY) || '{}'); }
    catch (_) { return {}; }
  }

  function contextualEnabled() {
    if (window.YOWAYOWA_MODE !== 'personal') return false;
    const meta = settingsMeta();
    return meta.contextualEnabled !== false;
  }

  function providerPayload() {
    const meta = settingsMeta();
    if (meta.providerId === 'server') return null;
    let apiKey = '';
    try { apiKey = sessionStorage.getItem(KEY_KEY) || ''; }
    catch (_) {}
    if (meta.providerId && meta.model && (apiKey || ['ollama', 'lmstudio', 'vllm'].includes(meta.providerId))) {
      return {
        provider: meta.adapter === 'anthropic' ? 'anthropic' : 'openai_compatible',
        model: meta.model,
        api_key: apiKey || 'local',
        base_url: meta.baseUrl || null,
      };
    }
    try {
      const legacy = JSON.parse(sessionStorage.getItem(LEGACY_KEY) || '{}');
      if (!legacy.provider || legacy.provider === 'server') return null;
      if (!legacy.model || !legacy.apiKey) return undefined;
      return {
        provider: legacy.provider === 'anthropic' ? 'anthropic' : 'openai_compatible',
        model: legacy.model,
        api_key: legacy.apiKey,
        base_url: legacy.baseUrl || null,
      };
    } catch (_) {
      return undefined;
    }
  }

  function pageSymbol() {
    const host = document.querySelector('[data-symbol]');
    if (host?.dataset.symbol) return host.dataset.symbol;
    const match = location.pathname.match(/^\/(?:instrument|research)\/([^/]+)/);
    if (match) return decodeURIComponent(match[1]);
    return new URLSearchParams(location.search).get('symbol') || null;
  }

  function ensureDrawer() { return document.querySelector('#context-ai-drawer'); }
  function closeDrawer() { ensureDrawer()?.classList.remove('is-open'); }

  function sectionContext(element) {
    const container = element.closest('.panel, .sheet, section, article, main') || element;
    const heading = container.querySelector('h1, h2, h3')?.textContent?.trim()
      || element.querySelector('h1, h2, h3')?.textContent?.trim()
      || document.title;
    const excerpt = (container.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 12000);
    return { heading, excerpt };
  }

  function openDrawer(element, preset = '') {
    const drawer = ensureDrawer();
    if (!drawer) return;
    activeSection = sectionContext(element);
    drawer.querySelector('#context-ai-section').textContent = activeSection.heading;
    const prompt = drawer.querySelector('#context-ai-prompt');
    if (preset) prompt.value = preset;
    drawer.querySelector('#context-ai-answer').textContent = '';
    drawer.querySelector('#context-ai-trace').textContent = '';
    drawer.classList.add('is-open');
    window.requestAnimationFrame(() => prompt.focus());
  }

  function injectAIButtons() {
    if (!contextualEnabled()) return;
    const { t } = window.Yowayowa || {};
    if (!t) return;
    document.querySelectorAll('.section-heading, .page-head, .page-heading').forEach(heading => {
      if (heading.closest('#context-ai-drawer') || heading.querySelector('.section-ai-button')) return;
      if (!heading.querySelector('h1, h2, h3')) return;
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'section-ai-button';
      button.textContent = t('ai.context.open', {}, 'Ask AI');
      button.addEventListener('click', event => {
        event.preventDefault();
        openDrawer(heading);
      });
      heading.append(button);
    });
    document.querySelectorAll('[data-ai-prompt]').forEach(button => {
      if (button.dataset.aiBound === 'true') return;
      button.dataset.aiBound = 'true';
      button.addEventListener('click', () => {
        const target = button.closest('.panel, .sheet, section') || button;
        openDrawer(target, button.dataset.aiPrompt || '');
      });
    });
  }

  function renderTrace(trace) {
    const target = document.querySelector('#context-ai-trace');
    if (!target) return;
    if (!trace?.length) { target.textContent = ''; return; }
    target.innerHTML = trace.map(item => {
      const args = JSON.stringify(item.arguments || {});
      return `<div><strong>${window.Yowayowa.escapeHtml(item.tool)}</strong> · ${window.Yowayowa.escapeHtml(args)}<br>→ ${window.Yowayowa.escapeHtml(item.result_preview || '')}</div>`;
    }).join('');
  }

  async function askContextAI(event) {
    event.preventDefault();
    const drawer = ensureDrawer();
    const prompt = drawer?.querySelector('#context-ai-prompt');
    const answer = drawer?.querySelector('#context-ai-answer');
    if (!drawer || !prompt || !answer) return;
    const text = prompt.value.trim();
    if (!text) return;
    const provider = providerPayload();
    const meta = settingsMeta();
    if (provider === undefined || (provider === null && meta.providerId && meta.providerId !== 'server')) {
      answer.innerHTML = `<a href="/settings">${window.Yowayowa.escapeHtml(window.Yowayowa.t('ai.configure', {}, 'Open AI settings'))}</a>`;
      return;
    }
    const evidenceInstruction = ja
      ? '数値・予想・現在情報は必ず利用可能なYowayowaツールで確認してください。出典から得た事実、あなたの推論、不確実性を明確に分け、使用したデータの基準時点(as-of)を示してください。根拠が取れないことは断定しないでください。'
      : 'Verify numerical, forecast and current claims with available Yowayowa tools. Clearly separate sourced facts, your inference and uncertainty, and state the data basis/as-of. Do not assert claims when evidence is unavailable.';
    contextMessages.push({ role: 'user', content: `${text}\n\n${evidenceInstruction}` });
    if (contextMessages.length > 12) contextMessages.splice(0, contextMessages.length - 12);
    answer.textContent = window.Yowayowa.t('ai.working', {}, 'Working…');
    drawer.querySelector('#context-ai-send').disabled = true;
    try {
      const data = await window.Yowayowa.api('/v1/ai/chat', {
        method: 'POST',
        body: JSON.stringify({
          messages: contextMessages,
          provider,
          context: {
            page: location.pathname,
            symbol: pageSymbol(),
            section_title: activeSection?.heading || null,
            section_excerpt: activeSection?.excerpt || null,
            evidence_required: true,
          },
          max_tool_rounds: 7,
          allow_mutations: false,
        }),
      });
      contextMessages.push({ role: 'assistant', content: data.answer || '—' });
      answer.textContent = data.answer || '—';
      renderTrace(data.tool_trace || []);
    } catch (error) {
      answer.textContent = error.message;
    } finally {
      drawer.querySelector('#context-ai-send').disabled = false;
    }
  }

  function bindDrawer() {
    const drawer = ensureDrawer();
    if (!drawer || drawer.dataset.bound === 'true') return;
    drawer.dataset.bound = 'true';
    drawer.querySelector('#context-ai-close')?.addEventListener('click', closeDrawer);
    drawer.querySelector('#context-ai-form')?.addEventListener('submit', askContextAI);
    drawer.querySelector('#context-ai-prompt')?.addEventListener('keydown', event => {
      if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) drawer.querySelector('#context-ai-form').requestSubmit();
    });
  }

  function bootUX() {
    bindDrawer();
    injectAIButtons();
  }

  document.addEventListener('DOMContentLoaded', bootUX);
  window.addEventListener('yowayowa:settings-changed', bootUX);
  document.addEventListener('turbo:load', () => {
    if (firstTurboLoad) {
      firstTurboLoad = false;
      return;
    }
    bootUX();
  });
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    const palette = document.querySelector('#command-palette');
    if (palette?.open) {
      event.preventDefault();
      palette.close();
      return;
    }
    closeDrawer();
  });
})();
