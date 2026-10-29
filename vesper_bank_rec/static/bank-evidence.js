(function (root) {
  'use strict';

  // Financial values are supplied by verified server tools. This view only
  // copies strings; it never parses, rounds, totals or approves them.
  const LIMIT = {text: 2000, rows: 25, lists: 20, id: 512, amount: 256, raw: 48000};
  const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const own = (value, key) => object(value) && Object.prototype.hasOwnProperty.call(value, key);
  const scalar = value => typeof value === 'string' || typeof value === 'boolean' ||
    (typeof value === 'number' && Number.isFinite(value));
  const LABELS = {
    awaiting_human_review: 'Awaiting your review', manual_review_only: 'Manual review only',
    human_review_only: 'Human review required', stale: 'Evidence changed — review again',
    review_source_controls: 'Review source controls', inspect_tieout: 'Inspect the tie-out',
    inspect_unmatched_bank: 'Inspect unmatched bank records', inspect_unmatched_book: 'Inspect unmatched book records',
    review_match_proposals: 'Review match proposals', review_pending_adjustments: 'Review pending adjustments',
    review_completion_requirements: 'Review completion requirements', pending_external_posting: 'Awaiting external posting',
  };

  function renderBankEvidence(item) {
    const doc = root.document;
    const card = doc.createElement('section');
    card.className = 'bank-evidence chat-evidence';
    card.setAttribute('aria-label', 'Assistant tool evidence');
    let shortened = false;
    let displayedRows = 0;
    function element(tag, value, className) {
      const node = doc.createElement(tag);
      if (className) node.className = className;
      if (value !== undefined) node.textContent = value;
      return node;
    }
    function text(value, max = LIMIT.text) {
      if (!scalar(value)) return '';
      const result = String(value);
      if (result.length <= max) return result;
      shortened = true;
      return result.slice(0, max) + '… [display shortened]';
    }
    function exact(value, kind) {
      const max = kind === 'amount' ? LIMIT.amount : LIMIT.id;
      if (typeof value !== 'string' || value.length === 0) return kind === 'amount' ? 'Exact amount unavailable' : '';
      if (value.length <= max) return value;
      shortened = true;
      // A prefix could be mistaken for a different amount or transaction ID.
      return kind === 'amount' ? 'Amount too long to display; see audit details' : 'Identifier too long to display; see audit details';
    }
    function human(value) { return typeof value === 'string' ? LABELS[value] || text(value).replaceAll('_', ' ') : text(value); }
    function pair(parent, label, value) {
      parent.append(element('dt', label), element('dd', value));
    }
    function list(parent, title, values) {
      if (!Array.isArray(values) || !values.length) return;
      parent.append(element('strong', title));
      const ul = element('ul');
      for (const value of values.slice(0, LIMIT.lists)) {
        const content = scalar(value) ? value : object(value) ? value.message || value.detail || value.reason : '';
        if (scalar(content)) ul.append(element('li', text(content)));
      }
      parent.append(ul);
      if (values.length > LIMIT.lists) shortened = true;
    }
    const data = object(item) && object(item.data) ? item.data : {};
    card.append(element('strong', text(object(item) && (item.label || item.id) || 'Tool evidence', 200)));
    const evidenceId = data.evidence_id || (object(item) && item.id);
    if (typeof evidenceId === 'string') card.append(element('p', 'Evidence: ' + exact(evidenceId, 'id'), 'fine'));

    const tieOut = ['adjusted_bank', 'adjusted_books', 'difference'];
    if (tieOut.some(key => own(data, key))) {
      const dl = element('dl');
      for (const [key, label] of [['adjusted_bank', 'Adjusted bank'], ['adjusted_books', 'Adjusted books'], ['difference', 'Difference']]) {
        if (own(data, key)) pair(dl, label, exact(data[key], 'amount'));
      }
      if (typeof data.currency === 'string') pair(dl, 'Currency', text(data.currency, 20));
      card.append(dl);
    }

    for (const [label, control] of [
      ['Bank source', data.bank_control || (!Array.isArray(data.bank) && data.bank)],
      ['Book source', data.book_control || (!Array.isArray(data.books) && data.books)],
    ]) {
      if (!object(control) || !own(control, 'ready')) continue;
      const section = element('div', undefined, 'bank-evidence-control');
      const state = control.ready === true ? 'Checks passed' : control.ready === false ? 'Checks not passed' : 'Readiness unavailable';
      section.append(element('p', label + ': ' + state, control.ready === true ? 'pass' : 'fail'));
      list(section, 'Source issues', control.errors);
      card.append(section);
    }
    list(card, 'Completion blockers', data.blockers);

    function rows(title, values, total) {
      if (!Array.isArray(values)) return;
      card.append(element('p', title + (Number.isSafeInteger(total) && total >= 0 ? ' · total ' + total : '')));
      if (!values.length) card.append(element('p', 'No records returned on this page.', 'fine'));
      const available = Math.max(0, 75 - displayedRows);
      const count = Math.min(values.length, LIMIT.rows, available);
      for (const row of values.slice(0, count)) {
        if (!object(row)) continue;
        displayedRows++;
        const record = element('div', undefined, 'record');
        if (own(row, 'amount')) record.append(element('strong', 'Signed amount: ' + exact(row.amount, 'amount')));
        if (own(row, 'date')) record.append(element('p', text(row.date, 80)));
        if (own(row, 'description')) record.append(element('p', text(row.description)));
        const identifiers = element('dl');
        for (const [key, label] of [['id', 'Record ID'], ['source_id', 'Source ID'], ['bank_id', 'Bank ID'], ['ledger_id', 'Book ID'], ['bank_item_id', 'Bank ID'], ['book_txn_id', 'Book ID'], ['carried_from', 'Carried from']]) {
          if (typeof row[key] === 'string') pair(identifiers, label, exact(row[key], 'id'));
        }
        record.append(identifiers);
        for (const key of ['status', 'why_not_matched', 'why_detail', 'classification', 'evidence']) {
          if (scalar(row[key])) record.append(element('p', key.replaceAll('_', ' ') + ': ' + (key === 'status' ? human(row[key]) : text(row[key])), 'fine'));
        }
        if (row.description_truncated === true || row.external_id_truncated === true || row.evidence_truncated === true) {
          record.append(element('p', 'Some source text was excerpted by the tool; use the saved source for full review.', 'fine'));
        }
        card.append(record);
      }
      if (values.length > count) {
        shortened = true;
        card.append(element('p', 'Showing ' + count + ' of ' + values.length + ' returned records. This view is limited; see audit details.', 'fine'));
      }
    }
    for (const [key, label, totalKey] of [
      ['bank', 'Bank records', 'total_bank'], ['books', 'Book records', 'total_books'],
      ['records', 'Records', 'total'], ['outstanding', 'Outstanding records', null],
      ['pending_adjustments', 'Adjustments awaiting external posting', null], ['corrections', 'Bank corrections', null],
    ]) rows(label, data[key], totalKey && data[totalKey]);

    const pagedCandidates = Array.isArray(data.items) && data.items.some(row => object(row) && own(row, 'bank_id') && own(row, 'ledger_id'));
    if (Array.isArray(data.items) && !pagedCandidates) rows(data.side === 'bank' ? 'Bank records' : data.side === 'book' ? 'Book records' : 'Records', data.items, data.total);

    for (const [key, label, values] of [['candidates', 'Candidate', data.candidates], ['proposals', 'Draft proposal', data.proposals], ['items', 'Candidate', pagedCandidates ? data.items : null]]) {
      if (!Array.isArray(values) || !values.length) continue;
      card.append(element('p', key === 'proposals' ? 'Draft proposals · human review required' : 'Candidates · human review required'));
      for (const proposal of values.slice(0, LIMIT.rows)) {
        if (!object(proposal)) continue;
        const section = element('div', undefined, 'proposal');
        section.append(element('strong', label + ' · not confirmed by the assistant'));
        const dl = element('dl');
        for (const [field, title] of [['id', 'Proposal ID'], ['bank_id', 'Bank ID'], ['ledger_id', 'Book ID'], ['bank_item_id', 'Bank ID'], ['book_txn_id', 'Book ID']]) {
          if (typeof proposal[field] === 'string') pair(dl, title, exact(proposal[field], 'id'));
        }
        if (own(proposal, 'amount')) pair(dl, 'Signed amount', exact(proposal.amount, 'amount'));
        section.append(dl);
        for (const field of ['status', 'match_type', 'score', 'confidence', 'reason']) {
          if (scalar(proposal[field])) section.append(element('p', field.replaceAll('_', ' ') + ': ' + (field === 'status' ? human(proposal[field]) : text(proposal[field])), 'fine'));
        }
        card.append(section);
      }
      if (values.length > LIMIT.rows) shortened = true;
    }
    if (scalar(data.status)) card.append(element('p', 'Tool status: ' + human(data.status), 'fine'));

    const page = object(data.pagination) ? data.pagination : data;
    const pagination = element('dl');
    for (const [key, label] of [['offset', 'Page offset'], ['limit', 'Page limit'], ['total', 'Total records'], ['page_number', 'Suggestion page'], ['planned_pages', 'Planned pages'], ['has_more', 'More records available'], ['next_offset', 'Next offset']]) {
      if (scalar(page[key])) pair(pagination, label, text(page[key], 100));
    }
    if (pagination.children.length) card.append(pagination);
    if (typeof data.next_cursor === 'string' && data.has_more === true) card.append(element('p', 'More evidence is available. Ask to continue this page; continuation is never automatic.', 'fine'));

    const plans = object(data.plan) ? [data.plan] : Array.isArray(data.plans) ? data.plans : [];
    for (const plan of plans.slice(0, LIMIT.lists)) {
      if (!object(plan)) continue;
      card.append(element('strong', 'Review checklist'));
      if (scalar(plan.title)) card.append(element('p', text(plan.title)));
      if (scalar(plan.goal)) card.append(element('p', text(plan.goal)));
      if (scalar(plan.status)) card.append(element('p', 'Checklist status: ' + human(plan.status), 'fine'));
      if (scalar(plan.execution)) card.append(element('p', human(plan.execution) + ' · this checklist executes no actions.', 'fine'));
      if (scalar(plan.stale_reason)) card.append(element('p', text(plan.stale_reason), 'fine'));
      if (scalar(plan.id)) card.append(element('p', 'Checklist ID: ' + exact(plan.id, 'id'), 'fine'));
      const steps = Array.isArray(plan.steps) ? plan.steps : Array.isArray(plan.checklist) ? plan.checklist : [];
      const ol = element('ol');
      for (const step of steps.slice(0, LIMIT.lists)) {
        const li = element('li');
        if (scalar(step)) li.append(element('span', text(step)));
        else if (object(step)) {
          for (const key of ['title', 'action', 'description', 'note', 'status', 'evidence_id']) {
            if (scalar(step[key])) li.append(element('p', (key === 'status' ? 'Status: ' : key === 'evidence_id' ? 'Evidence: ' : '') + (key === 'action' || key === 'status' ? human(step[key]) : text(step[key]))));
          }
        }
        ol.append(li);
      }
      card.append(ol);
      if (steps.length > LIMIT.lists) shortened = true;
    }
    if (plans.length > LIMIT.lists) shortened = true;

    // Build a bounded plain snapshot instead of invoking arbitrary toJSON hooks
    // or serializing an unexpectedly deep/large object before applying limits.
    let budget = LIMIT.raw;
    let nodes = 1800;
    let rawShortened = false;
    const seen = new WeakSet();
    function snapshot(value, depth) {
      if (--nodes < 0 || budget <= 0 || depth > 7) { rawShortened = true; return '[audit display limit]'; }
      if (typeof value === 'string') {
        if (value.length <= budget) { budget -= value.length; return value; }
        rawShortened = true;
        budget = 0;
        return '[value exceeds audit display limit]';
      }
      if (scalar(value) || value === null) { budget -= 24; return value; }
      if (!value || typeof value !== 'object') return '[unsupported value]';
      if (seen.has(value)) { rawShortened = true; return '[circular reference]'; }
      seen.add(value);
      const result = Array.isArray(value) ? [] : Object.create(null);
      const keys = Array.isArray(value) ? Array.from({length: Math.min(value.length, 100)}, (_, index) => index) : Object.keys(value).slice(0, 80);
      if ((Array.isArray(value) ? value.length : Object.keys(value).length) > keys.length) rawShortened = true;
      for (const key of keys) {
        if (budget <= 0 || nodes <= 0) { rawShortened = true; break; }
        if (String(key).length > 200) { rawShortened = true; continue; }
        budget -= String(key).length;
        result[key] = snapshot(value[key], depth + 1);
      }
      seen.delete(value);
      return result;
    }
    let raw;
    try { raw = JSON.stringify(snapshot(object(item) ? item.data : null, 0), null, 2); }
    catch (_) { raw = 'Audit details could not be displayed.'; rawShortened = true; }
    if (raw && raw.length > LIMIT.raw) {
      // Never present a sliced JSON document as if it were complete JSON.
      raw = 'Audit details exceed the display limit. Use the workspace export for the complete evidence.';
      rawShortened = true;
    }
    if (shortened || rawShortened) card.append(element('p', 'Display limited. Some evidence is shortened or omitted; the saved workspace and exports remain the source for full review.', 'fine'));
    const audit = element('details');
    audit.append(element('summary', rawShortened ? 'Audit details (bounded JSON view)' : 'Audit details (raw JSON)'), element('pre', raw || 'null', 'chat-evidence'));
    card.append(audit);
    return card;
  }

  root.renderBankEvidence = renderBankEvidence;
  if (typeof module !== 'undefined' && module.exports) module.exports = {renderBankEvidence};
})(typeof globalThis !== 'undefined' ? globalThis : window);
