(() => {
  if (window.__uiRecorderInstalled) return;
  window.__uiRecorderInstalled = true;
  const config = window.__recorderConfig;
  const mask = '[REDACTED]';
  const secret = /password|passwd|token|secret|session|api[-_]?key|credit[-_]?card|card[-_ ]?(number|no)|cvv|cvc|cc-/i;
  const selectors = ['input[type=password]', 'input[type=hidden]', '[autocomplete^="cc-"]', ...config.redact_selectors];
  const validSelectors = selectors.filter(s => {
    try { document.querySelector(s); return true; } catch { return false; }
  });
  const blockSelector = [...validSelectors, 'script', 'noscript', 'style', 'iframe[src^="data:"]'].join(',');
  const send = (type, payload) => {
    // A navigation can destroy the binding while an event is in flight.
    try { window.__recorderEmit({type, payload}).catch(() => {}); } catch {}
  };
  const sensitive = el => Boolean(el && (el.closest(validSelectors.join(',')) ||
    secret.test([el.id, el.getAttribute('name'), el.getAttribute('autocomplete'), el.getAttribute('aria-label'), el.getAttribute('placeholder'), ...(el.labels ? [...el.labels].map(l => l.textContent) : [])].join(' '))));
  const text = value => String(value || '').replace(/\s+/g, ' ').trim().slice(0, 500);
  function safeText(el) {
    if (!el || sensitive(el)) return mask;
    const clone = el.cloneNode(true);
    for (const node of clone.querySelectorAll('input, textarea, script, style, noscript, ' + validSelectors.join(','))) node.remove();
    for (const node of clone.querySelectorAll('[id], [name], [autocomplete], [aria-label]')) {
      if (sensitive(node)) node.replaceWith(mask);
    }
    return text(clone.textContent);
  }
  function css(el) {
    if (el.id) return '#' + CSS.escape(el.id);
    const parts = [];
    for (let node = el; node && node.nodeType === 1 && parts.length < 6; node = node.parentElement) {
      let part = node.localName;
      if (node.parentElement) {
        const siblings = [...node.parentElement.children].filter(s => s.localName === node.localName);
        if (siblings.length > 1) part += ':nth-of-type(' + (siblings.indexOf(node) + 1) + ')';
      }
      parts.unshift(part);
    }
    return parts.join(' > ');
  }
  function framePath() {
    const path = [];
    let win = window;
    try {
      while (win !== win.top) {
        const element = win.frameElement;
        path.unshift(element ? css(element) : '[cross-origin frame]');
        win = win.parent;
      }
    } catch { path.unshift('[cross-origin frame]'); }
    return path;
  }
  function role(el) {
    if (el.getAttribute('role')) return el.getAttribute('role');
    const tag = el.localName;
    if (tag === 'button') return 'button';
    if (tag === 'a' && el.hasAttribute('href')) return 'link';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'select') return el.multiple ? 'listbox' : 'combobox';
    if (tag === 'input') return ({checkbox:'checkbox', radio:'radio', range:'slider', number:'spinbutton', button:'button', submit:'button', reset:'button'})[el.type] || (el.type === 'password' || el.type === 'hidden' ? null : 'textbox');
    return ({img:'img', h1:'heading', h2:'heading', h3:'heading', option:'option'})[tag] || null;
  }
  function locator(el) {
    const label = el.labels ? [...el.labels].map(safeText).join(' ') : '';
    const labelled = (el.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean).map(id => safeText(document.getElementById(id))).join(' ');
    const name = labelled || el.getAttribute('aria-label') || label || el.getAttribute('alt') ||
      (['button','submit','reset'].includes(el.type) ? el.value : '') ||
      (['button','a','option'].includes(el.localName) || el.getAttribute('role') ? safeText(el) : '') || el.getAttribute('title') || '';
    const rect = el.getBoundingClientRect(), style = getComputedStyle(el);
    return {tag: el.localName, role: role(el), accessible_name: text(name), label: text(label),
      test_id: el.getAttribute('data-testid') || el.getAttribute('data-test-id') || el.getAttribute('data-test'),
      text: safeText(el), id: el.id || null, name: el.getAttribute('name'),
      frame_path: framePath(), visible: rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none',
      enabled: !el.matches(':disabled') && el.getAttribute('aria-disabled') !== 'true', css: css(el)};
  }
  for (const type of ['click', 'input', 'change', 'submit']) {
    document.addEventListener(type, event => {
      try {
        let el = event.composedPath().find(node => node instanceof Element);
        if (!el) return;
        if (type === 'click') el = el.closest('button, a, input, select, textarea, [role], [data-testid]') || el;
        const payload = {locator: locator(el), url: location.href, trusted: event.isTrusted};
        if (type === 'input' || type === 'change') {
          payload.value = sensitive(el) ? mask : (el.isContentEditable ? safeText(el) : String(el.value ?? '').slice(0, 2000));
          if (['checkbox', 'radio'].includes(el.type)) payload.checked = el.checked;
          if (el.localName === 'select') payload.selected = sensitive(el) ? mask : [...el.selectedOptions].map(o => ({value:o.value, label:text(o.textContent)}));
        }
        send('action.' + type, payload);
      } catch { send('sensor.error', {operation:'semantic action'}); }
    }, true);
  }
  // History API transitions do not always trigger a document navigation.
  for (const method of ['pushState', 'replaceState']) {
    const original = history[method];
    history[method] = function (...args) {
      const result = original.apply(this, args);
      send('location', {url: location.href, method});
      return result;
    };
  }
  addEventListener('popstate', () => send('location', {url:location.href, method:'popstate'}));
  addEventListener('hashchange', () => send('location', {url:location.href, method:'hashchange'}));
  function sanitizeDom(value) {
    if (!value || typeof value !== 'object') return value;
    if (Array.isArray(value)) return value.map(sanitizeDom);
    const out = {};
    for (const [key, val] of Object.entries(value)) {
      if (secret.test(key) || key === 'value' || key === 'textContent' && value.tagName === 'textarea') out[key] = mask;
      else if (key === 'attributes' && val && !Array.isArray(val)) {
        out[key] = {};
        for (const [attr, attrValue] of Object.entries(val)) {
          out[key][attr] = secret.test(attr) || attr === 'value' || /^on/i.test(attr) || ['srcdoc', 'nonce', 'integrity', 'style'].includes(attr) ? mask : sanitizeDom(attrValue);
        }
      } else out[key] = sanitizeDom(val);
    }
    return out;
  }
  try {
    window.rrwebRecord.record({
      emit(event) { send('dom', sanitizeDom(event)); },
      maskAllInputs: true,
      blockSelector,
      maskTextSelector: validSelectors.join(','),
      inlineStylesheet: false,
      recordCanvas: false,
      collectFonts: false,
      sampling: {mousemove:false, mouseInteraction:{MouseMove:false}, scroll:200, input:'last'},
    });
    window.__recorderSnapshot = () => window.rrwebRecord.record.takeFullSnapshot();
  } catch { send('sensor.error', {operation:'rrweb initialization'}); }
  if (validSelectors.length !== selectors.length) send('sensor.error', {operation:'invalid redaction selector skipped'});
  window.__recorderState = () => {
    const clone = document.body?.cloneNode(true);
    if (clone) {
      for (const node of clone.querySelectorAll('input, textarea, script, style, noscript, ' + validSelectors.join(','))) node.remove();
      for (const node of clone.querySelectorAll('[id], [name], [autocomplete], [aria-label]')) if (sensitive(node)) node.replaceWith(mask);
    }
    return {url:location.href, title:document.title,
      text:(clone?.textContent || '').replace(/\s+/g,' ').trim().slice(0,8000),
      controls:[...document.querySelectorAll('button, a[href], input, select, textarea, [role]')].slice(0,100).map(locator)};
  };
})();
