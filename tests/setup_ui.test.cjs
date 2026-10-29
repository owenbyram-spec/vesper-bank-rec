'use strict';
// Execute the shipped browser code against a small DOM and synthetic local API.
// No browser packages, live providers, keys, or persisted financial data are used.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

const staticDir = path.join(__dirname, '..', 'vesper_bank_rec', 'static');
const html = fs.readFileSync(path.join(staticDir, 'index.html'), 'utf8');
const script = fs.readFileSync(path.join(staticDir, 'app.js'), 'utf8');

function attrs(source) {
  const result = {};
  for (const match of source.matchAll(/([\w-]+)(?:="([^"]*)")?/g)) result[match[1]] = match[2] ?? true;
  return result;
}

class Element {
  constructor(tag = 'div', attributes = {}) {
    this.tagName = tag.toUpperCase();
    Object.assign(this, attributes);
    this.value = attributes.value || '';
    this.checked = !!attributes.checked;
    this.hidden = !!attributes.hidden;
    this.disabled = !!attributes.disabled;
    this.open = !!attributes.open;
    this.textContent = '';
    this.children = [];
    this.events = {};
    this.dataset = {};
    this.classList = {add() {}, remove() {}, toggle() {}};
    this.defaults = {value: this.value, checked: this.checked};
  }
  append(...children) { for (const child of children) { child.parent = this; this.children.push(child); } }
  prepend(child) { child.parent = this; this.children.unshift(child); }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  setAttribute(name, value) { this[name] = value; }
  removeAttribute(name) { delete this[name]; }
  addEventListener(event, listener) { (this.events[event] ||= []).push(listener); }
  async fire(event) {
    for (const listener of this.events[event] || []) await listener({target: this, preventDefault() {}});
  }
  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }
  querySelectorAll(selector) {
    const descendants = this.children.flatMap(child => [child, ...child.querySelectorAll('*')]);
    if (selector === '*') return descendants;
    if (selector.startsWith('.')) return descendants.filter(child => String(child.className || child.class || '').split(' ').includes(selector.slice(1)));
    if (selector === 'button[type=submit]') return descendants.filter(child => child.tagName === 'BUTTON' && child.type === 'submit');
    return [];
  }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this); }
  showModal() { this.open = true; }
  close() { this.open = false; for (const listener of this.events.close || []) listener(); }
  focus() {}
  scrollIntoView() {}
  reset() { for (const control of this.elements || []) Object.assign(control, control.defaults); }
}

function makeDocument() {
  const nodes = new Map();
  for (const match of html.matchAll(/<(\w+)\b([^>]*\bid="[^"]+"[^>]*)>/g)) {
    const attributes = attrs(match[2]);
    nodes.set(attributes.id, new Element(match[1], attributes));
  }
  for (const match of html.matchAll(/<form\b([^>]*\bid="[^"]+"[^>]*)>([\s\S]*?)<\/form>/g)) {
    const form = nodes.get(attrs(match[1]).id);
    const controls = [];
    for (const control of match[2].matchAll(/<(input|textarea|select|button)\b([^>]*)>/g)) {
      const attributes = attrs(control[2]);
      const node = nodes.get(attributes.id) || new Element(control[1], attributes);
      if (attributes.name) controls[attributes.name] = node;
      controls.push(node);
      form.append(node);
    }
    form.elements = controls;
  }
  return {
    getElementById(id) { assert(nodes.has(id), `Shipped JS refers to missing HTML id ${id}`); return nodes.get(id); },
    createElement(tag) { return new Element(tag); },
    querySelector(selector) { return selector === 'dialog[open]' ? [...nodes.values()].find(node => node.tagName === 'DIALOG' && node.open) : null; },
    nodes,
  };
}

const tick = () => new Promise(resolve => setImmediate(resolve));
function deferred() { let resolve; const promise = new Promise(done => { resolve = done; }); return {promise, resolve}; }

async function setup({configured = false, failTest = false, workspaces, selected, testGate, demoGate, xero = {}, session = null, assistantReplies = []} = {}) {
  const document = makeDocument();
  const calls = [], events = [], popups = [], timers = new Map();
  let nextTimer = 0;
  const workspaceList = workspaces || [{id: 'workspace_first', name: 'First client', currency: 'USD', synthetic: true, version: 1, sessions: []}];
  let active = selected || workspaceList[0], activeSession = session;
  const sessions = new Map();
  if (activeSession) { sessions.set(active.id, activeSession); active.sessions = [activeSession]; }
  const xeros = new Map(), xeroResponses = [], pendingXeroValues = new Map();
  let xeroSyncFails = !!xero.failSync;
  if (xero.configured || xero.authorization) xeros.set(active.id, {configured: !!xero.configured, client_id: xero.client_id || "synthetic-xero-client", storage: "session-only", bound: !!xero.bound, company: xero.bound ? {id: "tenant_one", name: "Synthetic Xero Company", currency: xero.currency || "USD"} : null, authorization: {status: xero.authorization || (xero.configured ? "connected" : "idle"), message: ""}});
  const models = new Map();
  if (configured) models.set(active.id, {configured: true, consent: true, model: 'gpt-5.6-terra', storage: 'session-only'});
  const receipts = new Map();
  const settingsResponses = [];
  let testFails = failTest;
  const scope = () => active ? {id: active.id, synthetic: active.synthetic, version: active.version, session_id: activeSession?.id || null} : null;
  const state = () => ({workspace: active, workspaces: workspaceList, scope: scope(), session: activeSession,
    summary: activeSession ? {bank_control: {ready: false}, book_control: {ready: false}, matches: 0, blockers: ['Source needed'], ready_to_complete: false} : null});
  const xeroStatus = (workspaceId = active?.id) => {
    const record = xeros.get(workspaceId) || {configured: false, client_id: '', storage: null, bound: false, company: null, authorization: {status: 'idle', message: ''}};
    const {authorization, ...connection} = record;
    return {xero: structuredClone(connection), authorization: structuredClone(authorization), redirect_uri: 'http://localhost:8765/api/xero/callback', scopes: ['accounting.transactions.read', 'accounting.reports.read']};
  };
  const status = () => ({
    workspace_required: !active,
    scope: scope(),
    model: structuredClone(models.get(active?.id) || {configured: false, consent: false, model: 'gpt-5.6-terra', storage: null}),
    model_test: receipts.get(active?.id) || null,
    polling: {enabled: false},
    runtime: {instance_id: 'synthetic-instance', port: 8765, testing: true, source_dir: '/synthetic/source', data_dir: '/synthetic/data'},
  });
  async function fetch(url, options = {}) {
    const method = options.method || 'GET';
    const body = options.body ? JSON.parse(options.body) : undefined;
    calls.push({url, method, body, headers: options.headers});
    events.push('fetch:'+url);
    const requestWorkspace = options.headers?.['X-Vesper-Scope'] ? JSON.parse(options.headers['X-Vesper-Scope']).id : active?.id;
    let result;
    if (url === '/api/bootstrap') result = {csrf_token: 'synthetic-csrf', workspace: scope()};
    else if (url === '/api/state') result = state();
    else if (url === '/api/settings') {
      // Capture the server snapshot before a delayed network response, so a
      // later save/delete cannot rewrite what this older response contained.
      result = structuredClone(status());
      const pending = settingsResponses.shift();
      if (pending) {
        pending.started.resolve();
        await pending.gate.promise;
        if (pending.fail) return {ok: false, json: async () => ({detail: 'Synthetic stale status failure.'})};
      }
    }
    else if (url === '/api/settings/model' && method === 'POST') {
      assert.equal(body.consent, true, 'An explicit consent selection must reach save');
      models.set(active.id, {configured: true, consent: true, model: body.model, storage: body.persist ? 'os-credential-store' : 'session-only'});
      receipts.delete(active.id);
      result = {ok: true, saved_storage: models.get(active.id).storage, ...status()};
    } else if (url === '/api/settings/model/test') {
      if (testGate) await testGate.promise;
      if (testFails) {
        receipts.delete(active.id);
        return {ok: false, json: async () => ({detail: 'Synthetic provider unavailable. Saved connection retained.'})};
      }
      assert(models.has(active.id), 'A connection must be saved before its test');
      receipts.set(active.id, {at: '2026-10-28T20:00:00Z', workspace_id: active.id, instance_id: 'synthetic-instance'});
      result = {ok: true, message: 'Synthetic connection verified.', ...status()};
    } else if (url === '/api/settings/model' && method === 'DELETE') {
      models.delete(active.id); receipts.delete(active.id); result = {ok: true, ...status()};
    } else if (url === '/api/workspaces/select') {
      active = workspaceList.find(item => item.id === body.workspace_id);
      assert(active); activeSession = sessions.get(active.id) || null; result = {ok: true};
    } else if (url === '/api/demo') {
      if (demoGate) await demoGate.promise;
      active = {id: 'workspace_new_sample', name: 'New sample', synthetic: true, currency: 'USD', version: 1, sessions: []};
      workspaceList.push(active); result = {ok: true};
    } else if (url === '/api/settings/xero' && method === 'GET') {
      result = xeroStatus(requestWorkspace);
      const pending = xeroResponses.shift();
      if (pending) { pending.started.resolve(); await pending.gate.promise; if (pending.fail) return {ok: false, json: async () => ({detail: 'Synthetic old Xero status failure.'})}; }
    } else if (url === '/api/settings/xero/authorize') {
      assert(!('client_secret' in body) && !('refresh_token' in body));
      const previous = xeros.get(requestWorkspace) || {configured: false, client_id: '', storage: null, bound: false, company: null};
      pendingXeroValues.set(requestWorkspace, {client_id: body.client_id, storage: body.persist ? 'os-credential-store' : 'session-only'});
      xeros.set(requestWorkspace, {...previous, authorization: {status: 'pending', message: ''}});
      result = {...xeroStatus(requestWorkspace), authorization_url: xero.authorization_url || 'https://login.xero.com/identity/connect/authorize?client_id=synthetic-xero-client&state=synthetic-one-time-state', expires_at: '2050-01-01T00:00:00Z'};
      if (xero.authorizeGate) await xero.authorizeGate.promise;
    } else if (url === '/api/settings/xero/cancel') {
      const record = xeros.get(requestWorkspace);
      if (record) record.authorization = {status: 'idle', message: ''};
      result = {ok: true, ...xeroStatus(requestWorkspace)};
    } else if (url === '/api/settings/xero' && method === 'DELETE') {
      xeros.delete(requestWorkspace); result = {ok: true, ...xeroStatus(requestWorkspace)};
    } else if (url === '/api/xero/organisations') {
      assert(xeros.get(requestWorkspace)?.configured);
      result = {organisations: [{id: 'tenant_one', name: 'Synthetic Xero Company'}, {id: 'tenant_two', name: 'Other Synthetic Company'}]};
    } else if (url === '/api/xero/confirm') {
      assert(['tenant_one', 'tenant_two'].includes(body.tenant_id));
      const record = xeros.get(requestWorkspace);
      record.bound = true; record.company = {id: body.tenant_id, name: body.tenant_id === 'tenant_one' ? 'Synthetic Xero Company' : 'Other Synthetic Company', currency: xero.currency || 'USD'};
      result = {company: record.company, ...xeroStatus(requestWorkspace)};
    } else if (url === '/api/xero/accounts') {
      assert(xeros.get(requestWorkspace)?.bound);
      result = {accounts: [{id: 'xero_bank_usd', name: 'Operating bank', currency: 'USD', type: 'BANK'}, {id: 'xero_bank_eur', name: 'Foreign bank', currency: 'EUR', type: 'BANK'}]};
    } else if (url === '/api/xero/sync') {
      assert(xeros.get(requestWorkspace)?.bound);
      if (xero.syncGate) await xero.syncGate.promise;
      if (xeroSyncFails) return {ok: false, json: async () => ({detail: 'Synthetic unsupported movement; previous book evidence retained.'})};
      active.version++; activeSession.input_version++;
      result = {ok: true, result: {message: 'Controlled synthetic Xero ledger imported.', rows: 2}};
    } else if (url === '/api/matches/confirm-selected' || url === '/api/matches/confirm') {
      const pairs=url.endsWith('confirm-selected')?activeSession.drafts.filter(p=>body.proposal_ids.includes(p.id)):[body.proposal_id?activeSession.drafts.find(p=>p.id===body.proposal_id):body];
      for(const pair of pairs)activeSession.matches.push({id:'match_'+pair.bank_id,bank_id:pair.bank_id,ledger_id:pair.ledger_id,note:body.note});
      activeSession.drafts=activeSession.drafts.filter(p=>!pairs.some(pair=>pair.bank_id===p.bank_id||pair.ledger_id===p.ledger_id));
      active.version++;result={confirmed:true,count:pairs.length};
    } else if (url === '/api/assistant') {
      const reply=assistantReplies.shift()||{};
      if(reply.started)reply.started.resolve();
      if(reply.gate)await reply.gate.promise;
      if(reply.networkFailure)throw Error('Synthetic interrupted network');
      result=reply.result||{mode:'synthetic-rehearsal',message:'Synthetic evidence reviewed.',evidence:[],proposals:[]};
    } else if (url === '/api/sessions/new') {
      activeSession = xeroSession({account_id: body.account_id, account_label: body.account_label, period_start: body.period_start, period_end: body.period_end});
      sessions.set(active.id, activeSession); active.sessions = [activeSession]; active.version++; result = {ok: true};
    } else throw Error(`Unexpected synthetic API request ${method} ${url}`);
    return {ok: true, json: async () => structuredClone(result)};
  }
  class FormData {
    constructor(form) { this.form = form; }
    *[Symbol.iterator]() {
      for (const node of this.form.elements) {
        if (node.name && !node.disabled && (!['checkbox', 'radio'].includes(node.type) || node.checked)) yield [node.name, node.value];
      }
    }
  }
  const window = {
    prompt() { throw Error('Unexpected prompt'); },
    open(url, target) {
      events.push('popup:'+url);
      assert.equal(url, 'about:blank'); assert.equal(target, '_blank');
      if (xero.popupBlocked) return null;
      const popup = {closed: false, opener: {}, navigations: [], close() { this.closed = true; }};
      popup.location = {replace(value) { popup.navigations.push(value); }};
      popups.push(popup); return popup;
    },
  };
  vm.runInNewContext(script, {document, fetch, FormData, URL, URLSearchParams, AbortController, Date, console,
    setTimeout(fn, delay) { const id = ++nextTimer; timers.set(id, {fn, delay}); return id; },
    clearTimeout(id) { timers.delete(id); }, window}, {filename: 'shipped-app.js'});
  for (let count = 0; count < 10; count++) await tick();
  const node = id => document.getElementById(id);
  assert.equal(node('notice').className === 'error', false, node('notice').textContent);
  return {node, calls, status, models, receipts, document, events, popups, timers, xeros, xeroStatus, state,
    async openXero() { await this.openSettings(); node('xero-settings').open = true; await node('xero-settings').fire('toggle'); },
    completeXeroAuthorization(status = 'connected') { const record = xeros.get(active.id); record.configured = status === 'connected'; if (status === 'connected') Object.assign(record, pendingXeroValues.get(active.id), {bound: false, company: null}); record.authorization = {status, message: status === 'connected' ? '' : 'Synthetic Xero authorization '+status+'. Try again.'}; },
    setXeroSyncFailure(value) { xeroSyncFails = value; },
    holdNextXeroStatus({fail = false} = {}) { const gate = deferred(), started = deferred(); xeroResponses.push({gate, started, fail}); return {release: gate.resolve, started: started.promise}; },
    async runXeroPoll() { const item = [...timers].find(([,timer]) => timer.delay === 1500); assert(item, 'A pending Xero poll must be scheduled'); timers.delete(item[0]); await item[1].fn(); },
    setTestFailure(value) { testFails = value; },
    holdNextSettings({fail = false} = {}) {
      const gate = deferred(), started = deferred();
      settingsResponses.push({gate, started, fail});
      return {release: gate.resolve, started: started.promise};
    },
    async openSettings() { await node('settings-open').fire('click'); }};
}

function fillConnection(ui) {
  const form = ui.node('model-form');
  form.elements.api_key.value = 'synthetic-key-never-sent-to-provider';
  form.elements.consent.checked = true;
  form.elements.persist.checked = false;
  return form;
}

function connectionRequests(ui) {
  return ui.calls.filter(call => call.url.startsWith('/api/settings/model'));
}

test('Connect saves once, tests the saved bundle, and clears the password', async () => {
  const ui = await setup();
  await ui.openSettings();
  const form = fillConnection(ui);
  await form.fire('submit');
  const requests = connectionRequests(ui);
  assert.deepEqual(requests.map(call => [call.method, call.url]), [
    ['POST', '/api/settings/model'], ['POST', '/api/settings/model/test'],
  ]);
  assert.equal(requests[0].body.api_key, 'synthetic-key-never-sent-to-provider');
  assert.equal(requests[0].body.persist, false);
  assert.equal(requests[0].body.consent, true);
  assert.equal(requests[0].headers['X-Vesper-CSRF'], 'synthetic-csrf');
  assert.equal(JSON.parse(requests[0].headers['X-Vesper-Scope']).id, 'workspace_first');
  assert(!JSON.stringify(requests[1]).includes('synthetic-key-never-sent-to-provider'));
  assert.equal(form.elements.api_key.value, '');
  assert(ui.status().model_test);
  assert.match(ui.node('model-status').textContent, /connected|ready|verified/i);
  assert(!ui.node('connection-status').textContent.includes('synthetic-key-never-sent-to-provider'));
});

test('Failed verification retains the saved connection and retries without another save', async () => {
  const ui = await setup({failTest: true});
  await ui.openSettings();
  const form = fillConnection(ui);
  await form.fire('submit');
  assert(ui.status().model.configured);
  assert.equal(ui.status().model_test, null);
  assert.equal(form.elements.api_key.value, '');
  assert.match(ui.node('notice').textContent, /Synthetic provider unavailable/);
  assert.match(ui.node('model-connection-summary').textContent, /saved|unverified|check|retry/i);
  ui.setTestFailure(false);
  await form.fire('submit');
  assert.deepEqual(connectionRequests(ui).map(call => call.url), [
    '/api/settings/model', '/api/settings/model/test', '/api/settings/model/test',
  ]);
  assert(ui.status().model_test);
});

test('Repeated Connect submission while verification is pending cannot duplicate requests', async () => {
  const testGate = deferred();
  const ui = await setup({testGate});
  await ui.openSettings();
  const form = fillConnection(ui);
  const first = form.fire('submit');
  for (let count = 0; count < 10; count++) await tick();
  assert.equal(connectionRequests(ui).filter(call => call.url.endsWith('/test')).length, 1);
  const button = form.elements.find(control => control.tagName === 'BUTTON' && control.type === 'submit');
  assert(button.disabled, 'Connect must be disabled until verification finishes');
  await form.fire('submit');
  assert.equal(connectionRequests(ui).length, 2);
  testGate.resolve();
  await first;
  assert.equal(button.disabled, false);
});

test('Native dialog close clears an unsaved password', async () => {
  const ui = await setup();
  await ui.openSettings();
  const form = fillConnection(ui);
  ui.node('settings').close();
  assert.equal(form.elements.api_key.value, '');
});

test('Saved connection can be verified without reentering its key; changed settings require one', async () => {
  const ui = await setup({configured: true});
  await ui.openSettings();
  const form = ui.node('model-form');
  assert.equal(form.elements.api_key.required, false);
  await form.fire('submit');
  assert.deepEqual(connectionRequests(ui).map(call => call.url), ['/api/settings/model/test']);
  form.elements.model.value = 'another-compatible-model';
  await form.fire('submit');
  assert.equal(connectionRequests(ui).length, 1, 'An edited model cannot silently test the old saved model');
  assert.match(ui.node('notice').textContent, /API key.*save/i);
  assert.equal(ui.status().model.model, 'gpt-5.6-terra');
});

test('Verification success offers Finish; Finish closes setup and selects live assistance', async () => {
  const ui = await setup();
  await ui.openSettings();
  ui.node('rehearsal').checked = true;
  await fillConnection(ui).fire('submit');
  assert.equal(ui.node('setup-finish').hidden, false);
  await ui.node('settings-finish').fire('click');
  assert.equal(ui.node('settings').open, false);
  assert.equal(ui.node('rehearsal').checked, false);
  assert.equal(ui.node('model-form').elements.api_key.value, '');
});

test('Workspace changes clear unsaved secrets and replace the displayed connection state', async () => {
  const first = {id: 'workspace_first', name: 'First client', currency: 'USD', synthetic: true, version: 1, sessions: []};
  const second = {id: 'workspace_second', name: 'Second client', currency: 'USD', synthetic: false, version: 1, sessions: []};
  const ui = await setup({configured: true, workspaces: [first, second]});
  await ui.openSettings();
  await ui.node('model-form').fire('submit');
  assert.match(ui.node('model-status').textContent, /Connected/);
  fillConnection(ui);
  ui.node('workspace-select').value = second.id;
  await ui.node('workspace-select').fire('change');
  assert.equal(ui.node('model-form').elements.api_key.value, '');
  assert.equal(ui.node('model-form').elements.consent.checked, false);
  assert.equal(ui.node('setup-finish').hidden, true);
  assert.match(ui.node('model-status').textContent, /Second client/);
  assert.doesNotMatch(ui.node('model-status').textContent, /Connected/);
  assert.equal(ui.status().model.configured, false);
  assert.equal(ui.models.get(first.id).configured, true);
});

test('Disconnect is blocked during verification and clears only the selected saved connection afterward', async () => {
  const testGate = deferred();
  const ui = await setup({configured: true, testGate});
  await ui.openSettings();
  const pending = ui.node('model-form').fire('submit');
  for (let count = 0; count < 10; count++) await tick();
  assert.equal(ui.node('remove-model').disabled, true);
  await ui.node('remove-model').fire('click');
  assert.equal(connectionRequests(ui).filter(call => call.method === 'DELETE').length, 0);
  testGate.resolve();
  await pending;
  await ui.node('remove-model').fire('click');
  assert.equal(connectionRequests(ui).filter(call => call.method === 'DELETE').length, 1);
  assert.equal(ui.status().model.configured, false);
  assert.equal(ui.status().model_test, null);
  assert.equal(ui.node('setup-finish').hidden, true);
});

test('Missing live connection opens setup while preserving the unsent question', async () => {
  const ui = await setup();
  ui.node('chat-message').value = 'Explain the outstanding check.';
  ui.node('rehearsal').checked = false;
  await ui.node('chat-form').fire('submit');
  assert.equal(ui.node('settings').open, true);
  assert.equal(ui.node('chat-message').value, 'Explain the outstanding check.');
  assert.equal(ui.calls.some(call => call.url === '/api/assistant'), false);
});

test('Opening the current example resumes it and retains its connection instead of creating another', async () => {
  const ui = await setup({configured: true});
  const before = structuredClone(ui.status());
  await ui.node('demo-another').fire('click');
  assert.equal(ui.calls.some(call => call.url === '/api/demo'), false);
  assert.equal(ui.calls.some(call => call.url === '/api/workspaces/select'), false);
  assert.deepEqual(ui.status(), before);
  assert.equal(ui.node('rehearsal').checked, false, 'A connected saved example must keep live assistance');
});

test('A sole saved example is selected; multiple saved examples require explicit selection', async () => {
  const client = {id: 'workspace_client', name: 'Client', currency: 'USD', synthetic: false, version: 1, sessions: []};
  const sample = {id: 'workspace_sample', name: 'Saved example', currency: 'USD', synthetic: true, version: 1, sessions: []};
  const ui = await setup({workspaces: [client, sample], selected: client});
  await ui.node('demo-another').fire('click');
  assert.equal(ui.calls.some(call => call.url === '/api/demo'), false);
  assert.deepEqual(ui.calls.filter(call => call.url === '/api/workspaces/select').map(call => call.body.workspace_id), [sample.id]);
  assert.equal(ui.status().scope.id, sample.id);
  assert.equal(ui.node('rehearsal').checked, true);

  const another = {...sample, id: 'workspace_another'};
  const picker = await setup({workspaces: [client, sample, another], selected: client});
  await picker.node('demo-another').fire('click');
  assert.equal(picker.node('sample-picker').open, true);
  assert.equal(picker.calls.some(call => call.method === 'POST'), false);
  assert.deepEqual(picker.node('sample-select').children.map(option => option.value), [sample.id, another.id]);
  picker.node('sample-select').value = another.id;
  await picker.node('sample-form').fire('submit');
  assert.equal(picker.status().scope.id, another.id);
  assert.equal(picker.node('sample-picker').open, false);
  assert.equal(picker.calls.some(call => call.url === '/api/demo'), false);
});

test('A separate example is created only through the explicit fresh-example action', async () => {
  const ui = await setup({configured: true});
  await ui.node('fresh-example').fire('click');
  assert.equal(ui.calls.filter(call => call.url === '/api/demo').length, 1);
  assert.equal(ui.status().scope.id, 'workspace_new_sample');
  assert.equal(ui.status().model.configured, false);
  assert.equal(ui.models.get('workspace_first').configured, true);
});

test('Model choice, accounting setup, and diagnostics are collapsed initially', async () => {
  const ui = await setup();
  for (const id of ['model-advanced', 'qbo-settings', 'xero-settings', 'connection-diagnostics']) {
    assert.equal(ui.node(id).tagName, 'DETAILS');
    assert.equal(ui.node(id).open, false);
  }
});

for (const fail of [false, true]) {
  test(`Older settings ${fail ? 'failure' : 'success'} cannot overwrite a newer successful connection`, async () => {
    const ui = await setup();
    await ui.openSettings();
    const stale = ui.holdNextSettings({fail});
    const opening = ui.openSettings();
    await stale.started;
    await fillConnection(ui).fire('submit');
    const connected = ui.node('model-status').textContent;
    assert.match(connected, /Connected/);
    assert.equal(ui.node('setup-finish').hidden, false);
    stale.release();
    await opening;
    assert.equal(ui.node('model-status').textContent, connected);
    assert.equal(ui.node('model-connection-summary').dataset.state, 'connected');
    assert.equal(ui.node('setup-finish').hidden, false);
    assert.equal(ui.node('model-form').elements.consent.checked, true);
    assert.doesNotMatch(ui.node('notice').textContent, /stale status failure/);
  });

  test(`Older settings ${fail ? 'failure' : 'success'} cannot restore Connected after disconnect`, async () => {
    const ui = await setup({configured: true});
    await ui.openSettings();
    await ui.node('model-form').fire('submit');
    assert.match(ui.node('model-status').textContent, /Connected/);
    const stale = ui.holdNextSettings({fail});
    const opening = ui.openSettings();
    await stale.started;
    await ui.node('remove-model').fire('click');
    const disconnected = ui.node('model-status').textContent;
    assert.doesNotMatch(disconnected, /Connected/);
    stale.release();
    await opening;
    assert.equal(ui.node('model-status').textContent, disconnected);
    assert.equal(ui.node('model-connection-summary').dataset.state, 'disconnected');
    assert.equal(ui.node('setup-finish').hidden, true);
    assert.equal(ui.node('model-form').elements.consent.checked, false);
    assert.equal(ui.node('remove-model').hidden, true);
    assert.doesNotMatch(ui.node('notice').textContent, /stale status failure/);
  });
}

test('An A-to-B-to-A workspace round trip invalidates the old A status response', async () => {
  const first = {id: 'workspace_first', name: 'First client', currency: 'USD', synthetic: true, version: 1, sessions: []};
  const second = {id: 'workspace_second', name: 'Second client', currency: 'USD', synthetic: false, version: 1, sessions: []};
  const ui = await setup({workspaces: [first, second]});
  await ui.openSettings();
  const stale = ui.holdNextSettings();
  const opening = ui.openSettings();
  await stale.started;
  for (const workspace of [second, first]) {
    ui.node('workspace-select').value = workspace.id;
    await ui.node('workspace-select').fire('change');
  }
  await fillConnection(ui).fire('submit');
  const connected = ui.node('model-status').textContent;
  assert.match(connected, /Connected.*First client/);
  stale.release();
  await opening;
  assert.equal(ui.node('model-status').textContent, connected);
  assert.equal(ui.node('setup-finish').hidden, false);
});

test('Repeated example clicks before its first creation finishes create exactly one workspace', async () => {
  const client = {id: 'workspace_client', name: 'Client', currency: 'USD', synthetic: false, version: 1, sessions: []};
  const demoGate = deferred();
  const ui = await setup({workspaces: [client], demoGate});
  const first = ui.node('demo-another').fire('click');
  for (let count = 0; count < 10; count++) await tick();
  assert.equal(ui.calls.filter(call => call.url === '/api/demo').length, 1);
  for (const id of ['demo', 'demo-another', 'fresh-example', 'sample-resume', 'sample-select']) {
    assert.equal(ui.node(id).disabled, true, `Example control ${id} must be disabled while opening`);
  }
  await ui.node('demo-another').fire('click');
  await ui.node('fresh-example').fire('click');
  assert.equal(ui.calls.filter(call => call.url === '/api/demo').length, 1);
  demoGate.resolve();
  await first;
  assert.equal(ui.status().scope.id, 'workspace_new_sample');
  assert.equal(ui.calls.filter(call => call.url === '/api/demo').length, 1);
  assert.equal(ui.node('demo-another').disabled, false);
});

function xeroSession(values = {}) {
  return {id: 'session_xero', account_id: 'xero_bank_usd', account_label: 'Operating bank',
    period_start: '2026-09-01', period_end: '2026-09-30', currency: 'USD', status: 'draft', input_version: 1,
    matches: [], drafts: [], carry: [], classifications: {}, history: [], bank: null, ledger: null, anchor: null, ...values};
}
function xeroRequests(ui) { return ui.calls.filter(call => /\/xero(?:\/|$)/.test(call.url)); }
async function authorizeXero(ui) {
  ui.node('xero-form').elements.client_id.value = 'synthetic-xero-client';
  await ui.node('xero-form').fire('submit');
}
async function confirmXero(ui, id = 'tenant_one') {
  await ui.node('xero-load-organisations').fire('click');
  ui.node('xero-organisations').value = id;
  await ui.node('xero-organisations').fire('change');
  await ui.node('xero-confirm').fire('click');
}

test('Xero status is lazy and a synchronously opened popup precedes the scoped authorization POST', async () => {
  const gate = deferred();
  const ui = await setup({xero: {authorizeGate: gate}});
  assert.equal(xeroRequests(ui).length, 0, 'Collapsed Xero must not affect normal AI setup');
  await ui.openXero();
  assert.equal(ui.node('xero-redirect-uri').textContent, 'http://localhost:8765/api/xero/callback');
  const pending = authorizeXero(ui);
  assert.equal(ui.popups.length, 1, 'Popup must be created before awaiting authorization');
  const popup = ui.popups[0];
  assert.equal(popup.opener, null);
  assert.deepEqual(popup.navigations, []);
  assert(ui.events.indexOf('popup:about:blank') < ui.events.indexOf('fetch:/api/settings/xero/authorize'));
  await ui.node('xero-form').fire('submit');
  assert.equal(ui.calls.filter(call => call.url.endsWith('/xero/authorize')).length, 1);
  gate.resolve(); await pending;
  assert.equal(popup.navigations.length, 1);
  assert.equal(new URL(popup.navigations[0]).hostname, 'login.xero.com');
  const call = ui.calls.find(call => call.url.endsWith('/xero/authorize'));
  assert.deepEqual(call.body, {client_id: 'synthetic-xero-client', persist: false});
  assert.equal(call.headers['X-Vesper-CSRF'], 'synthetic-csrf');
  assert.equal(JSON.parse(call.headers['X-Vesper-Scope']).id, 'workspace_first');
  assert.match(ui.node('xero-status').textContent, /Waiting/);
  assert.equal(ui.node('xero-form').elements.client_id.value, 'synthetic-xero-client', 'Pending OAuth must not erase the public Client ID entered for retry');
  assert.equal(ui.node('xero-cancel').hidden, false);
  assert.equal(ui.node('xero-authorize').disabled, true);
  assert(ui.calls.every(call => !call.url.startsWith('https:')), 'Browser fetches must stay local');
  assert(!ui.node('connection-status').textContent.includes('synthetic-one-time-state'));
});

test('Blocked Xero popup offers an explicit trusted link and backend status, not window closure, confirms authorization', async () => {
  const ui = await setup({xero: {popupBlocked: true}});
  await ui.openXero(); await authorizeXero(ui);
  assert.equal(ui.popups.length, 0);
  assert.equal(ui.node('xero-popup-help').hidden, false);
  assert.equal(new URL(ui.node('xero-auth-link').href).hostname, 'login.xero.com');
  assert.equal(ui.node('xero-auth-link').rel, 'noopener noreferrer');
  ui.completeXeroAuthorization();
  await ui.runXeroPoll();
  assert.match(ui.node('xero-status').textContent, /explicitly confirm/);
  assert.equal(ui.node('xero-company-section').hidden, false);
  assert.equal(ui.node('xero-account-section').hidden, true);
  assert.equal(ui.node('xero-auth-link').href, '#');
  assert.equal([...ui.timers.values()].some(timer => timer.delay === 1500), false);
  assert.equal(ui.calls.some(call => call.url === '/api/xero/confirm' || call.url === '/api/xero/sync'), false);
});

test('Xero authorization URL rejects unsupported provider destinations without navigating or displaying them', async () => {
  const ui = await setup({xero: {authorization_url: 'https://evil.example/identity/connect/authorize?code=synthetic-private-code'}});
  await ui.openXero(); await authorizeXero(ui);
  assert.equal(ui.popups[0].closed, true);
  assert.deepEqual(ui.popups[0].navigations, []);
  assert.match(ui.node('notice').textContent, /supported Xero authorization address/);
  assert(!ui.node('notice').textContent.includes('evil.example'));
  assert(!ui.node('notice').textContent.includes('synthetic-private-code'));
  assert.equal(ui.node('xero-cancel').hidden, false, 'The rejected pending flow must still be cancellable');
});

test('A closed popup is not treated as success; explicit check, expiry, retry and cancel remain available', async () => {
  const ui = await setup();
  await ui.openXero(); await authorizeXero(ui);
  ui.popups[0].closed = true;
  await ui.node('xero-check').fire('click');
  assert.match(ui.node('xero-status').textContent, /Waiting/);
  ui.completeXeroAuthorization('expired');
  await ui.node('xero-check').fire('click');
  assert.match(ui.node('xero-status').textContent, /expired/);
  assert.equal(ui.node('xero-authorize').disabled, false);
  assert.equal(ui.node('xero-company-section').hidden, true);
  await authorizeXero(ui);
  assert.equal(ui.calls.filter(call => call.url.endsWith('/xero/authorize')).length, 2);
  await ui.node('xero-cancel').fire('click');
  assert.equal(ui.popups.at(-1).closed, true);
  assert.equal(ui.xeroStatus().authorization.status, 'idle');
  assert.equal([...ui.timers.values()].some(timer => timer.delay === 1500), false);
  assert.equal(ui.calls.some(call => call.url === '/api/xero/sync'), false);
});

test('Xero connection activity preserves an unsaved AI key and never calls the model setup API', async () => {
  const ui = await setup();
  await ui.openXero();
  const form = fillConnection(ui);
  await authorizeXero(ui);
  ui.completeXeroAuthorization('failed');
  await ui.node('xero-check').fire('click');
  assert.equal(form.elements.api_key.value, 'synthetic-key-never-sent-to-provider');
  assert.equal(form.elements.consent.checked, true);
  assert.equal(connectionRequests(ui).length, 0);
  assert.equal(JSON.stringify(xeroRequests(ui)).includes('synthetic-key-never-sent-to-provider'), false);
});

test('Xero organisation confirmation is explicit and account loading is gated by the confirmed organisation', async () => {
  const ui = await setup({xero: {configured: true}});
  await ui.openXero();
  await ui.node('xero-load-accounts').fire('click');
  assert.equal(ui.calls.some(call => call.url === '/api/xero/accounts'), false);
  await ui.node('xero-load-organisations').fire('click');
  assert.deepEqual(ui.node('xero-organisations').children.map(item => item.value), ['', 'tenant_one', 'tenant_two']);
  assert.equal(ui.node('xero-organisations').value, '');
  await ui.node('xero-confirm').fire('click');
  assert.equal(ui.calls.some(call => call.url === '/api/xero/confirm'), false);
  ui.node('xero-organisations').value = 'tenant_two';
  await ui.node('xero-organisations').fire('change');
  assert.equal(ui.node('xero-confirm').disabled, false);
  await ui.node('xero-confirm').fire('click');
  const confirmation = ui.calls.find(call => call.url === '/api/xero/confirm');
  assert.deepEqual(confirmation.body, {tenant_id: 'tenant_two'});
  assert.equal(ui.xeroStatus().xero.company.id, 'tenant_two');
  assert.equal(ui.node('xero-account-section').hidden, false);
  assert.equal(ui.calls.some(call => call.url === '/api/xero/sync'), false);
});

test('Use Xero account only prefills the main form and unsupported currency cannot be selected for import', async () => {
  const ui = await setup({xero: {configured: true, bound: true}});
  await ui.openXero(); await ui.node('xero-load-accounts').fire('click');
  ui.node('xero-accounts').value = 'xero_bank_eur';
  await ui.node('xero-accounts').fire('change');
  assert.equal(ui.node('xero-use-account').disabled, true);
  await ui.node('xero-use-account').fire('click');
  assert.match(ui.node('notice').textContent, /currency/);
  ui.node('xero-accounts').value = 'xero_bank_usd';
  await ui.node('xero-accounts').fire('change');
  await ui.node('xero-use-account').fire('click');
  assert.equal(ui.node('session-form').hidden, false);
  assert.equal(ui.node('session-form').elements.account_id.value, 'xero_bank_usd');
  assert.equal(ui.node('session-form').elements.account_label.value, 'Operating bank');
  assert.equal(ui.node('session-form').elements.period_start.value, '');
  assert.equal(ui.node('settings').open, false);
  assert.equal(ui.state().session, null);
  assert.equal(ui.calls.some(call => call.url === '/api/sessions/new' || call.url === '/api/xero/sync'), false);
});

test('Xero company base-currency mismatch refuses account reads before any provider import', async () => {
  const ui = await setup({xero: {configured: true, bound: true, currency: 'EUR'}});
  await ui.openXero();
  assert.match(ui.node('xero-status').textContent, /base currency/);
  assert.equal(ui.node('xero-load-accounts').disabled, true);
  await ui.node('xero-load-accounts').fire('click');
  assert.equal(ui.calls.some(call => call.url === '/api/xero/accounts' || call.url === '/api/xero/sync'), false);
});

test('Manual Xero import uses only selected editable period and suppresses duplicate submissions', async () => {
  const gate = deferred();
  const ui = await setup({session: xeroSession(), xero: {configured: true, bound: true, syncGate: gate}});
  await ui.openXero(); await ui.node('xero-load-accounts').fire('click');
  assert.equal(ui.node('xero-sync').disabled, false);
  const importing = ui.node('xero-sync').fire('click');
  assert.equal(ui.node('xero-sync').disabled, true);
  await ui.node('xero-sync').fire('click');
  await ui.node('xero-disconnect').fire('click');
  assert.equal(ui.calls.filter(call => call.url === '/api/xero/sync').length, 1);
  assert.equal(ui.calls.some(call => call.url === '/api/settings/xero' && call.method === 'DELETE'), false);
  const call = ui.calls.find(call => call.url === '/api/xero/sync');
  assert.deepEqual(call.body, {account_id: 'xero_bank_usd', start_date: '2026-09-01', end_date: '2026-09-30'});
  assert.equal(JSON.parse(call.headers['X-Vesper-Scope']).session_id, 'session_xero');
  gate.resolve(); await importing;
  assert.match(ui.node('notice').textContent, /Controlled synthetic Xero ledger imported/);
  assert.equal(ui.state().session.input_version, 2);
});

test('Failed Xero import retains financial state and completed periods never send an import', async () => {
  const ui = await setup({session: xeroSession(), xero: {configured: true, bound: true, failSync: true}});
  await ui.openXero(); await ui.node('xero-load-accounts').fire('click');
  const before = structuredClone(ui.state());
  await ui.node('xero-sync').fire('click');
  assert.match(ui.node('notice').textContent, /previous book evidence retained/);
  assert.deepEqual(ui.state(), before);
  const completed = await setup({session: xeroSession({status: 'completed'}), xero: {configured: true, bound: true}});
  await completed.openXero(); await completed.node('xero-load-accounts').fire('click');
  assert.equal(completed.node('xero-sync').disabled, true);
  await completed.node('xero-sync').fire('click');
  assert.equal(completed.calls.some(call => call.url === '/api/xero/sync'), false);
});

for (const fail of [false, true]) {
  test(`An older Xero status ${fail ? 'failure' : 'success'} cannot restore a connection after A-to-B-to-A and disconnect`, async () => {
    const first = {id: 'workspace_first', name: 'First client', currency: 'USD', synthetic: true, version: 1, sessions: []};
    const second = {id: 'workspace_second', name: 'Second client', currency: 'USD', synthetic: false, version: 1, sessions: []};
    const ui = await setup({workspaces: [first, second], xero: {configured: true, bound: true}});
    await ui.openXero();
    const stale = ui.holdNextXeroStatus({fail});
    const opening = ui.openSettings();
    await stale.started;
    for (const workspace of [second, first]) {
      ui.node('workspace-select').value = workspace.id;
      await ui.node('workspace-select').fire('change');
    }
    await ui.node('xero-disconnect').fire('click');
    const disconnected = ui.node('xero-status').textContent;
    stale.release(); await opening;
    assert.equal(ui.node('xero-status').textContent, disconnected);
    assert.equal(ui.node('xero-company-section').hidden, true);
    assert.equal(ui.xeroStatus().xero.configured, false);
    assert(!ui.node('notice').textContent.includes('old Xero status failure'));
  });
}

test('A delayed Xero authorization response cannot navigate a popup or populate another workspace', async () => {
  const gate = deferred();
  const first = {id: 'workspace_first', name: 'First client', currency: 'USD', synthetic: true, version: 1, sessions: []};
  const second = {id: 'workspace_second', name: 'Second client', currency: 'USD', synthetic: false, version: 1, sessions: []};
  const ui = await setup({workspaces: [first, second], xero: {authorizeGate: gate}});
  await ui.openXero(); const authorizing = authorizeXero(ui);
  ui.node('workspace-select').value = second.id;
  await ui.node('workspace-select').fire('change');
  gate.resolve(); await authorizing;
  assert.equal(ui.popups[0].closed, true);
  assert.deepEqual(ui.popups[0].navigations, []);
  assert.equal(ui.node('xero-form').elements.client_id.value, '');
  assert.equal(ui.node('xero-company-section').hidden, true);
  assert.equal(ui.node('xero-auth-link').href, '#');
});

test('Xero disconnect affects only its selected connection, preserves AI and cancels pending polling on close', async () => {
  const ui = await setup({configured: true, xero: {configured: true, bound: true}});
  await ui.openXero();
  ui.xeros.set('another_workspace', structuredClone(ui.xeros.get('workspace_first')));
  await ui.node('xero-disconnect').fire('click');
  assert.equal(ui.xeroStatus().xero.configured, false);
  assert.equal(ui.xeros.has('another_workspace'), true);
  assert.equal(ui.status().model.configured, true);
  await authorizeXero(ui);
  assert.equal([...ui.timers.values()].some(timer => timer.delay === 1500), true);
  ui.node('settings').close();
  assert.equal([...ui.timers.values()].some(timer => timer.delay === 1500), false);
});

function reviewSession(){
  const row=(id,amount,description)=>({id,date:'2026-09-02',amount,description,ordinal:1});
  return xeroSession({bank:{source_hash:'a'.repeat(64),rows:[row('bank_one','-75.00','First software'),row('bank_two','-75.00','Second software')]},ledger:{source_hash:'b'.repeat(64),rows:[row('book_one','-75.00','First software'),row('book_two','-75.00','Second software')]},drafts:[{id:'proposal_one',bank_id:'bank_one',ledger_id:'book_one',reason:'Exact group'},{id:'proposal_two',bank_id:'bank_two',ledger_id:'book_two',reason:'Exact group'}]});
}
test('Selected review starts empty and submits only explicitly selected proposal identities with note and scope',async()=>{
  const ui=await setup({session:reviewSession()});
  ui.node('rehearsal').checked=true;ui.node('chat-message').value='Explain current evidence';await ui.node('chat-form').fire('submit');
  assert.equal(ui.node('batch-confirm').disabled,true);
  const checks=ui.node('proposal-list').querySelectorAll('*').filter(n=>n.type==='checkbox');
  assert.equal(checks.length,2);assert(checks.every(n=>!n.checked));
  checks[1].checked=true;await checks[1].fire('change');
  ui.node('batch-note').value='Operator inspected the second duplicate occurrence.';
  await ui.node('batch-review').fire('submit');
  const sent=ui.calls.find(c=>c.url==='/api/matches/confirm-selected');
  assert.deepEqual(sent.body.proposal_ids,['proposal_two']);
  assert.match(sent.body.note,/second duplicate/);assert.equal(sent.body.scope.session_id,'session_xero');
  assert.equal(ui.state().session.matches[0].bank_id,'bank_two');
  assert(ui.node('chat-log').children.some(n=>n.textContent.includes('reconciliation changed after the response')));
  assert.equal(ui.node('batch-confirm').disabled,true);
});
test('Manual pairing and filtered exception views retain duplicate source identities',async()=>{
  const ui=await setup({session:reviewSession()});
  ui.node('exception-search').value='bank_two';await ui.node('exception-search').fire('input');
  assert.match(ui.node('exceptions-count').textContent,/^1 unmatched/);
  ui.node('manual-bank').value='bank_two';await ui.node('manual-bank').fire('change');
  assert.equal(ui.node('manual-ledger').children.length,3);
  ui.node('manual-ledger').value='book_two';ui.node('manual-note').value='Operator reviewed the second occurrence.';
  await ui.node('manual-match').fire('submit');
  const sent=ui.calls.find(c=>c.url==='/api/matches/confirm');
  assert.equal(sent.body.bank_id,'bank_two');assert.equal(sent.body.ledger_id,'book_two');
  assert.equal(sent.body.input_version,1);assert.equal(sent.body.scope.revision,1);
});
test('Uncertain assistant retry reuses its request receipt rather than silently spending twice',async()=>{
  const ui=await setup({assistantReplies:[{networkFailure:true},{}]});
  ui.node('rehearsal').checked=true;ui.node('chat-message').value='Explain my evidence';
  await ui.node('chat-form').fire('submit');
  assert.equal(ui.node('chat-message').value,'Explain my evidence');
  await ui.node('chat-form').fire('submit');
  const sent=ui.calls.filter(c=>c.url==='/api/assistant');
  assert.equal(sent.length,2);assert.equal(sent[0].body.request_id,sent[1].body.request_id);
  assert.match(sent[0].body.request_id,/^question_/);
});
test('A delayed assistant response cannot appear under a different workspace',async()=>{
  const gate=deferred(),started=deferred();
  const workspaces=[{id:'workspace_first',name:'First client',synthetic:true,currency:'USD',version:1,sessions:[]},{id:'workspace_second',name:'Second client',synthetic:true,currency:'USD',version:1,sessions:[]}];
  const ui=await setup({workspaces,assistantReplies:[{gate,started,result:{message:'OLD CLIENT RESPONSE',evidence:[],proposals:[]}}]});
  ui.node('rehearsal').checked=true;ui.node('chat-message').value='Explain old client';
  const pending=ui.node('chat-form').fire('submit');await started.promise;
  ui.node('workspace-select').value='workspace_second';await ui.node('workspace-select').fire('change');
  gate.resolve();await pending;
  assert(!ui.node('chat-log').children.some(n=>n.textContent.includes('OLD CLIENT RESPONSE')));
});

test('A delayed assistant response is discarded after leaving and returning to the same workspace',async()=>{
  const gate=deferred(),started=deferred();
  const workspaces=[{id:'workspace_first',name:'First client',synthetic:true,currency:'USD',version:1,sessions:[]},{id:'workspace_second',name:'Second client',synthetic:true,currency:'USD',version:1,sessions:[]}];
  const ui=await setup({workspaces,assistantReplies:[{gate,started,result:{message:'STALE RETURN RESPONSE',evidence:[{id:'old',label:'OLD EVIDENCE',data:{}}],proposals:[]}}]});
  ui.node('rehearsal').checked=true;ui.node('chat-message').value='Explain old work';
  const pending=ui.node('chat-form').fire('submit');await started.promise;
  for(const selected of ['workspace_second','workspace_first']){ui.node('workspace-select').value=selected;await ui.node('workspace-select').fire('change');}
  gate.resolve();await pending;
  assert(!ui.node('chat-log').children.some(n=>n.textContent.includes('STALE RETURN RESPONSE')));
  assert(!ui.node('chat-log').querySelectorAll('*').some(n=>n.textContent==='OLD EVIDENCE'));
});
