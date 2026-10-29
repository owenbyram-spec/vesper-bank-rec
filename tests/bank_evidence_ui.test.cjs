'use strict';

const assert = require('node:assert/strict');
const {test} = require('node:test');
const {renderBankEvidence} = require('../vesper_bank_rec/static/bank-evidence.js');

// Minimal DOM deliberately rejects HTML sinks. These checks execute the shipped
// renderer rather than a second implementation of its presentation rules.
class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.attributes = {};
    this._text = '';
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  set innerHTML(_) { throw Error('HTML injection sink used'); }
  append(...nodes) { this.children.push(...nodes); }
  setAttribute(key, value) { this.attributes[key] = value; }
}

function render(data, label = 'Verified local evidence') {
  global.document = {createElement: tag => new Element(tag)};
  return renderBankEvidence({id: 'evidence_test', label, data});
}
function all(node, predicate) {
  return [node, ...node.children.flatMap(child => all(child, predicate))].filter(predicate);
}
const tags = (node, tag) => all(node, child => child.tagName === tag.toUpperCase());
const records = node => all(node, child => child.className === 'record');
const structured = node => node.children.filter(child => child.tagName !== 'DETAILS').map(child => child.textContent).join('\n');

test('untrusted labels, descriptions, reasons and source errors remain literal text', () => {
  const attack = '<img src=x onerror="global.compromised=true"><script>alert(1)</script>';
  const card = render({
    bank: [{id: 'bank_html', amount: '-75.00', date: '2026-09-15', description: attack}],
    bank_control: {ready: false, errors: [attack]},
    proposals: [{id: 'proposal_html', bank_id: 'bank_html', ledger_id: 'book_html', reason: attack}],
  }, attack);
  assert(card.textContent.includes(attack));
  assert.equal(tags(card, 'img').length, 0);
  assert.equal(tags(card, 'script').length, 0);
  assert.equal(card.attributes['aria-label'], 'Assistant tool evidence');
  assert.equal(global.compromised, undefined);
});

test('exact tie-out and signed cents are copied without numeric conversion', () => {
  const card = render({
    adjusted_bank: '9007199254740993.01', adjusted_books: '9007199254740993.00', difference: '0.01', currency: 'USD',
    bank: [{id: 'bank_cents', amount: '-0.01', date: '2026-09-30', description: 'One cent evidence'}],
  });
  const text = structured(card);
  assert(text.includes('Adjusted bank9007199254740993.01'));
  assert(text.includes('Adjusted books9007199254740993.00'));
  assert(text.includes('Difference0.01'));
  assert(text.includes('Signed amount: -0.01'));
  assert(!text.includes('9007199254740994'));
  assert(!text.includes('$'));
});

test('duplicate amounts retain distinct occurrence and pair identifiers', () => {
  const card = render({
    bank: [
      {id: 'bank_software_occurrence_1', source_id: 'source_1', amount: '-75.00', date: '2026-09-09', description: 'Software'},
      {id: 'bank_software_occurrence_2', source_id: 'source_2', amount: '-75.00', date: '2026-09-09', description: 'Software'},
    ],
    proposals: [
      {id: 'proposal_1', bank_id: 'bank_software_occurrence_1', ledger_id: 'book_occurrence_1'},
      {id: 'proposal_2', bank_id: 'bank_software_occurrence_2', ledger_id: 'book_occurrence_2'},
    ],
  });
  assert.equal(records(card).length, 2);
  for (const id of ['bank_software_occurrence_1', 'bank_software_occurrence_2', 'book_occurrence_1', 'book_occurrence_2', 'source_1', 'source_2']) {
    assert(structured(card).includes(id), id);
  }
  assert.equal(records(card).filter(row => row.textContent.includes('Signed amount: -75.00')).length, 2);
});

test('candidate and draft evidence explicitly requires review without approval controls', () => {
  const card = render({
    candidates: [{bank_id: 'bank_candidate', ledger_id: 'book_candidate', match_type: 'EXACT', score: 100, reason: 'Same signed amount'}],
    proposals: [{id: 'draft_only', bank_id: 'bank_draft', ledger_id: 'book_draft', status: 'awaiting_human_review'}],
    status: 'awaiting_human_review',
  });
  const text = structured(card);
  assert(text.includes('Candidates · human review required'));
  assert(text.includes('Draft proposals · human review required'));
  assert(text.includes('Draft proposal · not confirmed by the assistant'));
  assert(text.includes('Awaiting your review'));
  assert(text.includes('match type: EXACT'));
  assert.equal(tags(card, 'button').length, 0);
  assert.equal(tags(card, 'input').length, 0);
});

test('source check failure and unknown readiness remain distinct from passed controls', () => {
  const card = render({
    bank_control: {ready: false, errors: ['Closing balance does not agree with activity.']},
    book_control: {ready: 'true', errors: []},
    blockers: ['Review the opening anchor.'],
  });
  const text = structured(card);
  assert(text.includes('Bank source: Checks not passed'));
  assert(text.includes('Book source: Readiness unavailable'));
  assert(text.includes('Closing balance does not agree with activity.'));
  assert(text.includes('Review the opening anchor.'));
  assert(!text.includes('Checks passed'));
  const passed = render({bank: {ready: true, errors: []}, books: {ready: false, errors: ['Missing ledger']}});
  assert(structured(passed).includes('Bank source: Checks passed'));
  assert(structured(passed).includes('Book source: Checks not passed'));
});

test('paged records show supplied page metadata and local truncation honestly', () => {
  const rows = Array.from({length: 40}, (_, index) => ({id: 'record_' + index, amount: '-1.01', date: '2026-09-30', description: 'Returned evidence'}));
  const card = render({records: rows, pagination: {offset: 40, limit: 40, total: 137, has_more: true, next_offset: 80}});
  assert.equal(records(card).length, 25);
  const text = structured(card);
  assert(text.includes('Showing 25 of 40 returned records'));
  assert(text.includes('Page offset40'));
  assert(text.includes('Total records137'));
  assert(text.includes('More records availabletrue'));
  assert(text.includes('Next offset80'));
  assert(text.includes('Display limited.'));
  const raw = JSON.parse(tags(card, 'pre')[0].textContent);
  assert.equal(raw.records.length, 40);
  assert.equal(raw.records[39].id, 'record_39');
  assert.equal(raw.records[39].amount, '-1.01');
});

test('raw JSON audit is collapsed and preserves the supplied evidence', () => {
  const data = {evidence_id: 'evidence_exact', adjusted_bank: '6050.00', adjusted_books: '6050.00', difference: '0.00', extra: {original: 'unchanged'}};
  const card = render(data);
  const details = tags(card, 'details');
  assert.equal(details.length, 1);
  assert.equal(details[0].open, undefined);
  assert.equal(tags(details[0], 'summary')[0].textContent, 'Audit details (raw JSON)');
  assert.deepEqual(JSON.parse(tags(details[0], 'pre')[0].textContent), data);
});

test('oversized and malformed amounts are not rounded or shown as misleading prefixes', () => {
  const enormous = '1'.repeat(10000) + '.01';
  const card = render({adjusted_bank: enormous, adjusted_books: 6050.01, difference: null, bank: [{id: 'large_amount', amount: enormous}]});
  const text = structured(card);
  assert(text.includes('Amount too long to display; see audit details'));
  assert(text.includes('Exact amount unavailable'));
  assert(!text.includes('111111'));
  assert(!text.includes('6050.01'));
  assert(text.includes('Display limited.'));
});

test('unexpected shapes, excessive depth and cycles cannot cause unbounded rendering', () => {
  global.document = {createElement: tag => new Element(tag)};
  for (const item of [null, [], 'bad', {data: null}, {data: {bank: 'bad', proposals: {}, plan: {steps: null}}}]) {
    assert.doesNotThrow(() => renderBankEvidence(item));
  }
  const cycle = {records: [], note: 'x'.repeat(1000000)};
  cycle.self = cycle;
  const card = render(cycle);
  assert(card.textContent.length < 60000);
  assert(structured(card).includes('Display limited.'));
  assert.equal(tags(card, 'summary')[0].textContent, 'Audit details (bounded JSON view)');
});

test('checklist renders supplied statuses and evidence without fabricated completion', () => {
  const card = render({plan: {title: 'Review the remaining check', status: 'proposed', steps: [
    {title: 'Read source controls', status: 'review_required', evidence_id: 'controls_exact'},
    {description: 'Obtain posting evidence outside this tool'},
  ]}});
  const text = structured(card);
  assert(text.includes('Review checklist'));
  assert(text.includes('Checklist status: proposed'));
  assert(text.includes('Status: review required'));
  assert(text.includes('Evidence: controls_exact'));
  assert(text.includes('Obtain posting evidence outside this tool'));
  assert(!text.includes('completed'));
  assert.equal(tags(card, 'input').length, 0);
});

test('bank-tool item pages and manual checklist notes use their actual API shapes', () => {
  const bank = render({side: 'bank', items: [{id: 'paged_bank', amount: '-75.00', date: '2026-09-09', description: 'Software', description_truncated: true}], total: 41, returned: 1, has_more: true, next_cursor: 'opaque'});
  assert(structured(bank).includes('paged_bank'));
  assert(structured(bank).includes('More evidence is available.'));
  assert(structured(bank).includes('Some source text was excerpted'));
  const candidates = render({items: [{bank_id: 'candidate_bank', ledger_id: 'candidate_book', reason: 'Same exact signed amount'}], total: 1, has_more: false});
  assert(structured(candidates).includes('Candidate · not confirmed by the assistant'));
  assert(structured(candidates).includes('candidate_book'));
  const plan = render({plans: [{id: 'checklist_readonly', title: 'Inspect the remaining check', status: 'stale', execution: 'manual_review_only', stale_reason: 'Inputs changed; draft a new checklist.', steps: [{index: 0, action: 'review_source_controls', note: 'Read the independent statement controls.'}]}]});
  const text = structured(plan);
  assert(text.includes('Review source controls'));
  assert(text.includes('Read the independent statement controls.'));
  assert(text.includes('Manual review only · this checklist executes no actions.'));
  assert(text.includes('Inputs changed; draft a new checklist.'));
  assert(!text.includes('review_source_controls'));
  assert(!text.includes('manual_review_only'));
});
