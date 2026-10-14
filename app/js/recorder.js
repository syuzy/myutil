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
// IDs only exist in exported HTML; the actual website DOM is never marked.
const documentID = Math.random().toString(36).slice(2) + '-' + Date.now().toString(36);
const nodes = new WeakMap();
let nextID = 0, changeID = 0, rootNode = null, baselineRevision = 0, baselining = false;
function state(node) {
  if (!nodes.has(node)) nodes.set(node, {id:'n' + (++nextID), active:false, parent:null});
  return nodes.get(node);
}
function skipped(node) {
  return node.nodeType !== Node.TEXT_NODE && (!(node instanceof Element) || node.matches(ignore));
}
function tracked(node) {
  for (let current=node, depth=0; current && depth<80; current=state(current).parent, depth++) {
    if (!nodes.get(current)?.active) return false;
    if (current === rootNode) return true;
  }
  return false;
}
function minText(value, parent) {
  return parent?.closest('pre,code') ? value : String(value).slice(0,config.max_html_chars).replace(/\s+/g,' ');
}
function attribute(name, value) {
  if (value === null) return null;
  if (secret.test(name)) return MASK;
  if (name === 'href' || name === 'action') return url(value);
  if (name === 'style') {
    // Record visibility changes, not continuously changing animation geometry.
    return value.split(';').map(v => v.trim()).filter(v => /^(display|visibility|content-visibility)\s*:/i.test(v)).sort().join(';') || null;
  }
  return String(value).slice(0,500);
}
function serialize(root, limit=config.max_html_chars, overrides=null, nodeLimit=config.max_dom_nodes, shallow=false) {
  const chunks = [];
  let length=0, visited=0, truncated=false;
  const started=performance.now();
  function add(value) {
    const remaining=limit-length;
    chunks.push(value.slice(0,remaining)); length += Math.min(remaining,value.length);
    if (value.length > remaining) truncated=true;
  }
  function visit(node, depth) {
    if (truncated || skipped(node)) return;
    if (++visited > nodeLimit || length >= limit || depth > 80 || performance.now()-started > config.dom_budget_ms) {
      truncated=true; return;
    }
    const info=state(node);
    info.active=true; info.parent=node.parentNode;
    if (baselining) info.baseline_revision=baselineRevision;
    if (node.nodeType === Node.TEXT_NODE) {
      const value=overrides?.get(node)?.get('text') ?? node.textContent;
      info.text=minText(value,node.parentElement);
      // A tiny marker identifies a text node for later text/remove patches.
      add('<!--r:' + info.id + '-->' + esc(info.text.slice(0,limit))); return;
    }
    const tag=node.localName;
    add('<' + tag + ' data-recorder-node="' + info.id + '"');
    info.attributes={};
    if (redact && node.matches(redact)) { add('>' + MASK + '</' + tag + '>'); return; }
    for (const name of attrs) {
      if (name === 'value') continue;
      const override=overrides?.get(node);
      const value=attribute(name,override?.has(name) ? override.get(name) : node.getAttribute(name));
      if (value === null) continue;
      info.attributes[name]=value;
      add(' ' + name + '="' + esc(value) + '"');
    }
    if (node.matches('input,textarea,select')) {
      const value=sensitive(node) ? MASK : String(node.value).slice(0,2000);
      info.value=value;
      if (node.matches('input[type=checkbox],input[type=radio]')) info.checked=node.checked;
      add(' value="' + esc(value) + '"');
      if (node.checked) add(' checked');
    }
    if (tag === 'option' && node.selected) add(' selected');
    add('>');
    if (!shallow && !node.matches('textarea,input') && !(node.matches('[contenteditable]') && sensitive(node))) {
      for (let child=node.firstChild; child && !truncated; child=child.nextSibling) visit(child,depth+1);
    } else if (node.matches('[contenteditable]')) add(MASK);
    if (!['input','img','br','hr','area','source','wbr','embed'].includes(tag)) add('</' + tag + '>');
  }
  if (root) visit(root,0);
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
let inFlight=null, timer=null, changes=[], actions=[], droppedActions=0, droppedChanges=0;
let initial=null, queuedChars=0, lastURL=null;
function schedule() {
  if (timer === null && !inFlight) timer=setTimeout(() => { timer=null; void send(); },config.dom_flush_interval_ms);
}
function operation(op) {
  op.change_id=++changeID;
  op.observed_at=new Date().toISOString();
  for (const name of ['value','previous']) {
    if (typeof op[name] === 'string' && op[name].length > 4000) { op[name]=op[name].slice(0,4000); op.truncated=true; }
  }
  const previous=changes.at(-1);
  const key=op.op === 'attribute' ? op.name : '';
  if (['text','set_text','attribute','value'].includes(op.op) && previous?.op === op.op && previous.node_id === op.node_id && (previous.name || '') === key) {
    const history=[...(previous.intermediate || [])];
    const candidate={...previous,value:op.value,observed_at:op.observed_at,last_change_id:op.change_id,
      first_observed_at:previous.first_observed_at || previous.observed_at,intermediate:history};
    if (op.text_node_id !== undefined) candidate.text_node_id=op.text_node_id;
    if (op.checked !== undefined) candidate.checked=op.checked;
    if (op.truncated) candidate.truncated=true;
    if (history.length < config.max_transition_history) history.push({value:previous.value,observed_at:previous.observed_at});
    else candidate.coalesced=(candidate.coalesced || 0)+1;
    const oldSize=JSON.stringify(previous).length;
    let size=JSON.stringify(candidate).length;
    if (queuedChars-oldSize+size > config.max_html_chars*2 && history.length > (previous.intermediate || []).length) {
      history.pop(); candidate.coalesced=(candidate.coalesced || 0)+1; size=JSON.stringify(candidate).length;
    }
    if (queuedChars-oldSize+size > config.max_html_chars*2) { droppedChanges++; schedule(); return; }
    changes[changes.length-1]=candidate; queuedChars += size-oldSize;
    schedule(); return;
  }
  const size=JSON.stringify(op).length;
  if (changes.length >= config.max_pending_changes || queuedChars+size > config.max_html_chars*2) { droppedChanges++; schedule(); return; }
  changes.push(op); queuedChars += size; schedule();
}
function initialize(root) {
  rootNode=root;
  baselineRevision++; baselining=true;
  const baseline=serialize(root);
  baselining=false;
  initial={...baseline, root_id:state(root).id,baseline_revision:baselineRevision};
  lastURL=url(location.href);
  schedule();
}
function allowedTarget(node) {
  const el=node instanceof Element ? node : node.parentElement;
  return el && !el.closest(ignore) && !(redact && el.closest(redact)) && !(el.matches('input,textarea,select,[contenteditable]') && sensitive(el)) && (tracked(node) || rootNode?.contains(node));
}
function ensureTracked(node, overrides) {
  if (tracked(node)) return;
  const missing=[];
  for (let current=node; current && !tracked(current) && missing.length<80; current=current.parentNode) missing.push(current);
  for (const current of missing.reverse()) {
    const data=serialize(current,config.max_html_chars,overrides,1,true);
    operation({op:'context',node_id:state(current).id,parent_id:current.parentNode ? state(current.parentNode).id : null,
      html:data.html,partial:current.hasChildNodes(),truncated:data.truncated});
  }
}
function anchor(node, direction) {
  for (let n=node; n; n=n[direction]) if (!skipped(n)) return state(n).id;
  return null;
}
function process(records) {
  if (records.length > config.max_mutation_records) {
    droppedChanges += records.length-config.max_mutation_records;
    records=records.slice(0,config.max_mutation_records); schedule();
  }
  const root=document.querySelector(config.root_selector);
  if (!root) {
    if (rootNode) { operation({op:'remove',node_id:state(rootNode).id,parent_id:null}); state(rootNode).active=false; rootNode=null; }
    return;
  }
  const initialized=root !== rootNode;
  if (initialized) initialize(root);
  // MutationRecords keep old text/attribute values. Work backward to recover
  // each intermediate value instead of reading the final DOM for every record.
  const future=new WeakMap(), values=new Map();
  for (let i=records.length-1;i>=0;i--) {
    const record=records[i];
    if (!['attributes','characterData'].includes(record.type)) continue;
    const name=record.type === 'characterData' ? 'text' : record.attributeName;
    if (!future.has(record.target)) future.set(record.target,new Map());
    const map=future.get(record.target);
    values.set(record,map.has(name) ? map.get(name) : name === 'text' ? record.target.textContent : record.target.getAttribute(name));
    map.set(name,record.oldValue);
  }
  // The maps now describe pre-batch state; update them as records are applied.
  const started=performance.now();
  let inspected=0, addedChars=0, traversed=0;
  for (const record of records) {
    if (++inspected > config.max_mutation_records || performance.now()-started > config.dom_budget_ms) {
      droppedChanges += records.length-inspected+1; schedule(); break;
    }
    if (record.type === 'attributes' || record.type === 'characterData') {
      const name=record.type === 'characterData' ? 'text' : record.attributeName;
      future.get(record.target)?.set(name,values.get(record));
    }
    if (!allowedTarget(record.target)) continue;
    ensureTracked(record.target,future);
    const target=state(record.target);
    if (initialized && target.baseline_revision === baselineRevision && record.type !== 'childList') continue;
    if (record.type === 'attributes') {
      const name=record.attributeName;
      if (!attrs.has(name)) continue;
      if (name === 'value' && record.target.matches('input,textarea,select')) { valueChanged(record.target); continue; }
      const old=attribute(name,record.oldValue), value=attribute(name,values.get(record));
      if (old !== value) operation({op:'attribute',node_id:target.id,name,previous:old,value});
      target.attributes[name]=value;
      if (name === 'checked' && record.target.matches('input')) valueChanged(record.target);
      if (name === 'selected' && record.target.matches('option')) { const select=record.target.closest('select'); if (select) valueChanged(select); }
    } else if (record.type === 'characterData') {
      const old=minText(record.oldValue || '',record.target.parentElement), value=minText(values.get(record),record.target.parentElement);
      if (old !== value) operation({op:'text',node_id:target.id,parent_id:state(record.target.parentNode).id,previous:old,value});
      target.text=value;
    } else if (record.type === 'childList') {
      // textContent replaces all text children. Preserve its transitions while
      // avoiding a remove/add pair for every keystroke in a counter or ticker.
      const onlyText=!record.previousSibling && !record.nextSibling &&
        [...record.removedNodes,...record.addedNodes].every(n => n.nodeType === Node.TEXT_NODE);
      if (onlyText && (record.removedNodes.length || record.addedNodes.length)) {
        if (initialized && target.baseline_revision === baselineRevision) continue;
        // A serialized parent already includes parser-added text. Do not replay
        // that old append as set_text and accidentally erase its later controls.
        if (record.addedNodes.length && [...record.addedNodes].every(n => nodes.get(n)?.active) && [...record.removedNodes].every(n => !nodes.get(n)?.active)) continue;
        const old=[...record.removedNodes].map(n => minText(future.get(n)?.get('text') ?? n.textContent,record.target)).join('');
        const value=[...record.addedNodes].map(n => minText(future.get(n)?.get('text') ?? n.textContent,record.target)).join('');
        for (const n of record.removedNodes) state(n).active=false;
        for (const n of record.addedNodes) { const info=state(n); info.active=true; info.parent=record.target; }
        if (old !== value) operation({op:'set_text',node_id:target.id,text_node_id:record.addedNodes.length===1 ? state(record.addedNodes[0]).id : null,previous:old,value});
        continue;
      }
      let removedIndex=0;
      for (const node of record.removedNodes) {
        if (++removedIndex > config.max_mutation_records || performance.now()-started > config.dom_budget_ms) { droppedChanges += record.removedNodes.length-removedIndex+1; schedule(); break; }
        if (!skipped(node) && nodes.get(node)?.active) {
          operation({op:'remove',node_id:state(node).id,parent_id:target.id}); state(node).active=false;
        }
      }
      let previous=anchor(record.previousSibling,'previousSibling'), addedIndex=0;
      for (const node of record.addedNodes) {
        if (++addedIndex > config.max_mutation_records || traversed >= config.max_dom_nodes || performance.now()-started > config.dom_budget_ms) { droppedChanges += record.addedNodes.length-addedIndex+1; schedule(); break; }
        if (skipped(node) || nodes.get(node)?.active) continue;
        const remaining=config.max_html_chars-addedChars;
        if (remaining <= 0) { droppedChanges += record.addedNodes.length-addedIndex+1; schedule(); break; }
        const data=serialize(node,remaining,future,config.max_dom_nodes-traversed); addedChars += data.html.length; traversed += data.nodes;
        // Use record siblings: a transient node may already be detached now.
        state(node).parent=record.target;
        operation({op:'add',node_id:state(node).id,parent_id:target.id,
          previous_id:previous, next_id:anchor(record.nextSibling,'nextSibling'),
          html:data.html, truncated:data.truncated});
        previous=state(node).id;
      }
    }
  }
}
async function send() {
  while (inFlight) await inFlight;
  if (!initial && !changes.length && !droppedChanges) return;
  const baseline=initial; initial=null;
  const batch=changes.splice(0,config.max_changes_per_event);
  queuedChars=changes.reduce((sum,op) => sum+JSON.stringify(op).length,0);
  const payload={mode:baseline ? 'initial' : 'patch', document_id:documentID,
    url:url(location.href),title:document.title.slice(0,300),changes:batch,
    actions, dropped_actions:droppedActions,dropped_changes:droppedChanges,...(baseline || {})};
  actions=[]; droppedActions=0; droppedChanges=0;
  inFlight=window.__recorderEmit({type:'html',payload}).catch(() => {}).finally(() => { inFlight=null; if(changes.length || initial || droppedChanges) schedule(); });
  await inFlight;
}
function locationChanged() {
  const current=url(location.href);
  if (current !== lastURL) { operation({op:'location',previous:lastURL,value:current}); lastURL=current; }
}
function valueChanged(el) {
  if (redact && el.closest(redact)) return;
  const value=sensitive(el) ? MASK : String(el.value ?? shortText(el)).slice(0,2000);
  const info=state(el);
  const checkable=el.matches('input[type=checkbox],input[type=radio]');
  if (info.value !== value || (checkable && info.checked !== el.checked)) {
    operation({op:'value',node_id:info.id,previous:info.value ?? null,value,...(checkable ? {checked:el.checked,previous_checked:info.checked} : {})});
    info.value=value; if (checkable) info.checked=el.checked;
  }
}
const observer=new MutationObserver(process);
observer.observe(document,{subtree:true,childList:true,characterData:true,characterDataOldValue:true,
  attributes:config.watched_attributes.length>0,attributeOldValue:config.watched_attributes.length>0,
  ...(config.watched_attributes.length ? {attributeFilter:config.watched_attributes} : {})});
window.__recorderFlush=async () => {
  clearTimeout(timer); timer=null;
  process(observer.takeRecords()); locationChanged();
  while(initial || changes.length || droppedChanges || inFlight) await send();
};
{
  for (const type of ['click','input','change','submit']) document.addEventListener(type, event => {
    let el = event.composedPath().find(n => n instanceof Element);
    const root = document.querySelector(config.root_selector);
    if (!el || !root?.contains(el) || el.closest(ignore)) return;
    process(observer.takeRecords());
    if (!config.record_actions) { if (type === 'input' || type === 'change') valueChanged(el); return; }
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
    if (type === 'input' || type === 'change') valueChanged(el);
  }, true);
}
for (const method of ['pushState','replaceState']) {
  const original = history[method];
  history[method] = function(...args) { const result = original.apply(this,args); locationChanged(); return result; };
}
addEventListener('popstate',locationChanged);
addEventListener('hashchange',locationChanged);
addEventListener('pagehide', () => { process(observer.takeRecords()); void send(); });
if (document.readyState === 'loading') addEventListener('DOMContentLoaded',() => process(observer.takeRecords()),{once:true});
else process([]);
