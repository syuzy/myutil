// Runs inside an isolated closure created by browser.sensor_script.
if (window.__lightRecorder || (!config.capture_frames && window !== window.top)) return;
window.__lightRecorder = true;
const MASK = '[REDACTED]';
const secret = /password|passwd|token|secret|session|api[-_]?key|credit[-_]?card|card[-_ ]?(number|no)|cvv|cvc|cc-/i;
const omit = 'script,style,noscript,svg,canvas,template,link,meta,head';
const redact = config.redact_selectors.join(',');
const ignore = [omit, ...config.ignore_selectors].join(',');
try {
  document.querySelector(config.root_selector);
  if (redact) document.querySelector(redact);
  document.querySelector(ignore);
} catch {
  window.__recorderEmit({type:'warning', payload:{message:'Invalid CSS selector; HTML sensor disabled'}}).catch(() => {});
  return;
}
const attrs = new Set(['id','name','role','type','title','placeholder','for','alt','href','action',
  'aria-label','aria-labelledby','aria-describedby','aria-hidden','aria-expanded','aria-checked','aria-selected','aria-disabled',
  'data-testid','data-test-id','data-test','disabled','hidden','open','multiple','autocomplete', ...config.watched_attributes]);
const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const url = value => {
  try {
    const parsed = new URL(value, location.href);
    if (!['http:', 'https:', 'about:'].includes(parsed.protocol)) return '[URL OMITTED]';
    parsed.username = ''; parsed.password = '';
    for (const key of [...parsed.searchParams.keys()]) if (secret.test(key)) parsed.searchParams.set(key, MASK);
    if (secret.test(parsed.hash)) parsed.hash = MASK;
    return parsed.href;
  } catch { return '[URL OMITTED]'; }
};
const sensitive = el => Boolean((redact && el.closest(redact)) || el.matches('input[type=password], input[type=hidden]') ||
  secret.test([el.id, el.getAttribute('name'), el.getAttribute('autocomplete'), el.getAttribute('aria-label'),
    el.getAttribute('placeholder'), ...(el.labels ? [...el.labels].map(l => l.textContent.slice(0,200)) : [])].join(' ')));
// Bounded traversal; no DOM cloning, full-page querySelectorAll, layout reads,
// style computation, screenshots, or rrweb processing.
function serialize(root, limit = config.max_html_chars) {
  const chunks = [];
  let length = 0, visited = 0, truncated = false;
  const start = performance.now();
  function add(value) {
    const remaining = limit - length;
    chunks.push(value.slice(0, remaining));
    length += Math.min(remaining, value.length);
    if (value.length > remaining) truncated = true;
  }
  function visit(node, depth) {
    if (truncated) return;
    if (++visited > config.max_dom_nodes || length >= limit || depth > 80 || performance.now() - start > config.dom_budget_ms) {
      truncated = true; return;
    }
    if (node.nodeType === Node.TEXT_NODE) { add(esc(node.textContent.slice(0,limit))); return; }
    if (!(node instanceof Element) || node.matches(ignore)) return;
    const tag = node.localName;
    if (redact && node.matches(redact)) { add(`<${tag}>${MASK}</${tag}>`); return; }
    add('<' + tag);
    for (const name of attrs) {
      if (name === 'value' || !node.hasAttribute(name)) continue;
      let value = node.getAttribute(name);
      if (secret.test(name)) value = MASK;
      else if (name === 'href' || name === 'action') value = url(value);
      add(` ${name}="${esc(value.slice(0,500))}"`);
    }
    const control = node.matches('input,textarea,select');
    if (control) {
      add(` value="${esc(sensitive(node) ? MASK : String(node.value).slice(0,2000))}"`);
      if (node.checked) add(' checked');
    }
    if (node.localName === 'option' && node.selected) add(' selected');
    add('>');
    if (!node.matches('textarea,input') && !(node.matches('[contenteditable]') && sensitive(node))) {
      for (let child = node.firstChild; child && !truncated; child = child.nextSibling) visit(child, depth + 1);
    } else if (node.matches('[contenteditable]')) add(MASK);
    if (!['input','img','br','hr','area','source','wbr','embed'].includes(tag)) add(`</${tag}>`);
  }
  if (root) visit(root, 0);
  return {html:chunks.join(''), truncated, nodes:visited};
}
function shortText(el) {
  if (!el || sensitive(el)) return '';
  // A small DOM walk avoids reading an enormous container's textContent.
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      if (node instanceof Element && (node.matches(ignore) || (redact && node.matches(redact)))) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    }
  });
  let result = '', count = 0, node;
  while ((node = walker.nextNode()) && count++ < 60 && result.length < 200) {
    if (node.nodeType === Node.TEXT_NODE) result += node.textContent.slice(0,200);
  }
  return result.replace(/\s+/g,' ').trim().slice(0,200);
}
function cssPath(el) {
  if (el.id) return '#' + CSS.escape(el.id);
  if (el.getAttribute('name')) return el.localName + '[name="' + CSS.escape(el.getAttribute('name')) + '"]';
  const parts = [];
  for (let node=el; node && parts.length < 6; node=node.parentElement) {
    if (node.id) { parts.unshift('#' + CSS.escape(node.id)); break; }
    let index = 1;
    for (let sibling=node.previousElementSibling; sibling; sibling=sibling.previousElementSibling) {
      if (sibling.localName === node.localName) index++;
    }
    parts.unshift(node.localName + ':nth-of-type(' + index + ')');
  }
  return parts.join(' > ');
}
function locator(el) {
  if (redact && el.closest(redact)) return {tag:el.localName, redacted:true};
  const label = el.labels ? [...el.labels].slice(0,3).map(shortText).join(' ') : '';
  const role = el.getAttribute('role') || ({button:'button', a:'link', textarea:'textbox', select:'combobox'})[el.localName] ||
    (el.localName === 'input' ? ({checkbox:'checkbox',radio:'radio',submit:'button'})[el.type] || 'textbox' : null);
  const named = (el.getAttribute('aria-labelledby') || '').split(/\s+/).slice(0,3).map(id => shortText(document.getElementById(id))).join(' ').trim();
  return {tag:el.localName, role, name:named || el.getAttribute('aria-label') || label || shortText(el), label,
    test_id:el.getAttribute('data-testid') || el.getAttribute('data-test-id') || el.getAttribute('data-test'),
    css:cssPath(el)};
}
let dirty = false, dirtyAt = 0, timer = null, lastCapture = -Infinity, lastHTML = null, lastURL = null;
let inFlight = null, actions = [], droppedActions = 0, observedRoot = null;
function schedule() {
  if (!dirty || inFlight) return;
  clearTimeout(timer);
  const now = performance.now();
  const due = Math.max(lastCapture + config.min_snapshot_interval_ms, Math.min(now + config.debounce_ms, dirtyAt + config.max_wait_ms));
  timer = setTimeout(() => capture('change'), Math.max(0,due-now));
}
function changed() {
  if (!dirty) dirtyAt = performance.now();
  dirty = true; schedule();
}
async function capture(cause) {
  clearTimeout(timer);
  if (inFlight) { await inFlight; }
  const root = document.querySelector(config.root_selector);
  if (!root) return;
  dirty = false;
  lastCapture = performance.now();
  const state = serialize(root), currentURL = url(location.href);
  if (state.html === lastHTML && currentURL === lastURL) return;
  lastHTML = state.html; lastURL = currentURL;
  const batch = actions;
  actions = [];
  const payload = {...state, url:currentURL, title:document.title.slice(0,300), cause, actions:batch, dropped_actions:droppedActions};
  droppedActions = 0;
  inFlight = window.__recorderEmit({type:'html', payload}).catch(() => {}).finally(() => { inFlight = null; schedule(); });
  await inFlight;
}
window.__recorderFlush = () => capture('checkpoint');
const observer = new MutationObserver(records => {
  // Stop inspecting the batch at the first relevant change. Mutation records
  // are never serialized or sent across the browser/Python bridge.
  const root = document.querySelector(config.root_selector);
  if (!root) { observedRoot = null; return; }
  if (root !== observedRoot) { observedRoot = root; changed(); return; }
  let inspected = 0;
  for (const record of records) {
    if (++inspected > 200) { changed(); break; }
    const el = record.target instanceof Element ? record.target : record.target.parentElement;
    if (!el || !root.contains(el) || el.closest(ignore) || (redact && el.closest(redact))) continue;
    if (record.type === 'childList') {
      let relevant = false;
      for (const list of [record.addedNodes, record.removedNodes]) {
        for (const node of list) {
          if (++inspected > 200 || !(node instanceof Element && node.matches(ignore))) { relevant = true; break; }
        }
        if (relevant) break;
      }
      if (!relevant) continue;
    }
    changed(); break;
  }
});
observer.observe(document, {subtree:true, childList:true, characterData:true,
  attributes:config.watched_attributes.length > 0, ...(config.watched_attributes.length ? {attributeFilter:config.watched_attributes} : {})});
{
  for (const type of ['click','input','change','submit']) document.addEventListener(type, event => {
    let el = event.composedPath().find(n => n instanceof Element);
    const root = document.querySelector(config.root_selector);
    if (!el || !root?.contains(el) || el.closest(ignore)) return;
    if (!config.record_actions) { if (type === 'input' || type === 'change') changed(); return; }
    if (type === 'click') el = el.closest('button,a,input,select,textarea,[role],[data-testid]') || el;
    const item = {type, occurred_at:new Date().toISOString(), locator:locator(el)};
    if (type === 'input' || type === 'change') {
      item.value = sensitive(el) ? MASK : String(el.value ?? shortText(el)).slice(0,2000);
      if ('checked' in el) item.checked = el.checked;
    }
    const previous = actions.at(-1);
    if (type === 'input' && previous?.type === type && item.locator.css && previous.locator.css === item.locator.css) actions[actions.length-1] = item;
    else if (actions.length < 20) actions.push(item);
    else { actions.shift(); actions.push(item); droppedActions++; }
    // Input properties don't necessarily create MutationRecords.
    if (type === 'input' || type === 'change') changed();
  }, true);
}
for (const method of ['pushState','replaceState']) {
  const original = history[method];
  history[method] = function(...args) { const result = original.apply(this,args); changed(); return result; };
}
addEventListener('popstate',changed);
addEventListener('hashchange',changed);
addEventListener('pagehide', () => { if (dirty) void capture('navigation'); });
if (document.readyState === 'loading') addEventListener('DOMContentLoaded',changed,{once:true});
else changed();
