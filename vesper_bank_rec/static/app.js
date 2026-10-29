'use strict';
const $ = (id) => document.getElementById(id);
let pendingAssistantRequest = null, assistantSelectionGeneration = 0, lastAssistantResponseScope = null, assistantEvidenceStale = false;
let csrf = '', currentState = null, preview = null, company = null, controller = null, modelStatus = null, modelTesting = false, connectionGeneration = 0, exampleOpening = false;
function el(tag, text, className) { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (className) node.className = className; return node; }
function notice(message, error = false) { const activeDialog=document.querySelector('dialog[open]'); if(activeDialog){let feedback=activeDialog.querySelector('.dialog-feedback');if(!feedback){feedback=document.createElement('p');feedback.className='dialog-feedback';feedback.setAttribute('role','alert');activeDialog.prepend(feedback);}feedback.textContent=message;}  const node=$('notice'); node.textContent=message; node.className=error?'error':''; node.hidden=false; clearTimeout(notice.timer); notice.timer=setTimeout(()=>{node.hidden=true;}, error?14000:6000); }
function displayedScope(){const w=currentState?.workspace,s=currentState?.session;return w?{workspace_id:w.id,session_id:s?.id||null,input_version:s?.input_version||null,revision:w.version}:null;}
async function api(path, body, method) { const options={method:method || (body===undefined?'GET':'POST'), headers:currentState?.scope?{'X-Vesper-Scope':JSON.stringify(currentState.scope)}:{}}; if(options.method!=='GET'){options.headers['X-Vesper-CSRF']=csrf;if(currentState?.scope)options.headers['X-Vesper-Scope']=JSON.stringify(currentState.scope); options.headers['Content-Type']='application/json'; const finance=/^\/api\/(?:sessions\/|imports\/|anchor$|matches\/|classifications$|workspaces\/current$)/.test(path);const payload=body===undefined?{}:body;options.body=JSON.stringify(finance?{...payload,scope:displayedScope()}:payload);} const response=await fetch(path,options); let value; try{value=await response.json();}catch{throw Error('The local app returned an unreadable response. Your saved work is preserved.');} if(!response.ok) throw Error(value.error || value.detail || 'The request could not be completed.'); return value; }
function handle(fn){ return async(event)=>{ if(event)event.preventDefault(); try{await fn(event);}catch(error){notice(error.message,true);} }; }
function data(form){return Object.fromEntries(new FormData(form));}
async function refresh(){
  connectionGeneration++;
  const prior=currentState?.workspace?.id,priorSession=currentState?.session?.id,priorScope=JSON.stringify(currentState?.scope);
  xeroInvalidate();
  currentState=await api('/api/state');
  render();
  const changed=prior!==currentState.workspace?.id;
  if(prior&&(changed||priorSession!==currentState.session?.id)){
    assistantSelectionGeneration++;
    $('chat-log').replaceChildren(el('p','Ask about the selected client and period. Earlier responses belong to the previous selection.','chat-message assistant-message'));
    pendingAssistantRequest=null;lastAssistantResponseScope=null;assistantEvidenceStale=false;
  }
  if(priorScope!==JSON.stringify(currentState?.scope))xeroReset();
  if(lastAssistantResponseScope&&lastAssistantResponseScope!==JSON.stringify(currentState?.scope)&&!assistantEvidenceStale){
    chat('The reconciliation changed after the response above. Ask again for current evidence.');assistantEvidenceStale=true;
  }
  if(changed){
    $('model-form').reset();
    modelStatus=null;
    $('settings').querySelector('.dialog-feedback')?.remove();
  }
  try{await settingsStatus({updateForm:changed});}
  catch{showConnectionUnavailable();}
  if($('settings').open&&$('xero-settings').open){try{await xeroRefreshStatus({updateForm:changed});}catch{xeroError('The Xero connection could not be checked. Reopen this panel to try again.');}}
}
function empty(node,message){node.replaceChildren(el('div',message,'empty'));}
function option(value,text){const node=el('option',text);node.value=value;return node;}
function sourceCard(side, control, source){const card=el('div',undefined,'source-card'); card.append(el('h3',side==='bank'?'Independent bank statement':'Book ledger'));card.append(el('span',control.ready?'Controls passed':'Source needed',control.ready?'pass':'fail')); if(source){const dl=el('dl');for(const [key,value]of[['Opening',source.opening],['Signed activity',source.activity],['Closing',source.closing],['Records',source.rows.length]]){dl.append(el('dt',key),el('dd',String(value)));}card.append(dl,el('p',source.source,'fine'),el('code','SHA-256 '+source.source_hash.slice(0,20)+'…'));} else card.append(el('p','Import the exact account and period, with independent opening and closing balances.','fine'));return card;}
function record(row,side){const box=el('div',undefined,'record');box.append(el('span',side,'record-side'),el('strong',row.amount+' '+currentState.workspace.currency),el('p',row.description),el('small',row.date+(row.carried_from?' · carried from prior period':'')),el('small','Source row '+row.ordinal+' · '+row.id));return box;}
function rowById(id){const s=currentState.session;return [...(s.bank?.rows||[]),...(s.ledger?.rows||[]),...s.carry].find(row=>row.id===id);}
function render(){const state=currentState,w=state.workspace,s=state.session,r=state.summary; $('workspace-select').replaceChildren(option('','Choose a workspace'),...state.workspaces.map(item=>option(item.id,workspaceLabel(item,state.workspaces))));$('workspace-select').value=w?.id||'';$('demo-another').textContent=state.workspaces.some(item=>item.synthetic)?'Resume example':'Try the example';$('workspace-name').textContent=w?w.name:'Keep each client’s work separate';$('welcome').hidden=!!w;$('workflow').hidden=!w;$('delete-workspace').hidden=!w;$('synthetic-label').hidden=!w?.synthetic;$('rehearsal').disabled=!w?.synthetic;if(!w?.synthetic)$('rehearsal').checked=false;$('period-work').hidden=!s;$('assistant-scope').textContent=w?w.name+(s?' · '+s.account_label+' · '+s.period_start+' → '+s.period_end:' · Select a period'):'Select a client and period to begin.';if(!w)return; $('session-select').replaceChildren(option('','Select a saved reconciliation'),...w.sessions.map(item=>option(item.id,item.account_label+' · '+item.period_start+' to '+item.period_end+' · '+item.status)));$('session-select').value=s?.id||'';if(!s){$('session-form').hidden=false;return;} $('period-title').textContent=s.account_label+' · '+s.period_start+' — '+s.period_end;$('status').textContent=s.status.toUpperCase();$('adjusted-bank').textContent=r.bank_control.ready?r.adjusted_bank+' '+s.currency:'—';$('adjusted-books').textContent=r.book_control.ready?r.adjusted_books+' '+s.currency:'—';$('difference').textContent=r.bank_control.ready&&r.book_control.ready?r.difference+' '+s.currency:'—';$('difference-card').className=r.difference==='0.00'?'':'unbalanced';$('bank-components').textContent='Closing + deposits − withdrawals + corrections';$('book-components').textContent='Closing + evidenced pending adjustments';$('match-count').textContent=r.matches+' confirmed match'+(r.matches===1?'':'es')+' · input version '+s.input_version;
let next;if(s.status==='completed')next=[4,'Reconciliation complete','Export the selected session, or create the next contiguous period to carry outstanding items forward.'];else if(!r.bank_control.ready||!r.book_control.ready)next=[1,'Check the independent sources','Import a bank statement and book ledger with their own opening and closing controls.'];else if(!s.anchor)next=[1,'Review the opening anchor','Confirm the prior reconciled opening and explain any opening outstanding items.'];else if(s.drafts.length)next=[2,'Review suggested matches','Confirm the exact pairs you have reviewed; every decision remains yours.'];else if(r.blockers.length)next=[3,'Resolve the remaining exceptions',r.blockers[0]];else next=[4,'Ready for your acceptance','The adjusted balances agree. Review the evidence and explicitly accept the reconciliation.'];$('next-number').textContent=next[0];$('next-title').textContent=next[1];$('next-description').textContent=next[2];$('source-cards').replaceChildren(sourceCard('bank',r.bank_control,s.bank),sourceCard('ledger',r.book_control,s.ledger));$('anchor-title').textContent=s.anchor?'Opening anchor · '+s.anchor.kind.replaceAll('_',' '):'Review the opening reconciliation anchor';$('anchor-current').replaceChildren();if(s.anchor)$('anchor-current').append(el('p',s.anchor.evidence,'fine'),el('p','As of '+s.anchor.as_of+' · bank '+s.anchor.bank_opening+' / books '+s.anchor.book_opening,'fine'));$('anchor-form').hidden=s.anchor?.kind==='prior_completed';const start=new Date(s.period_start+'T12:00:00Z');start.setUTCDate(start.getUTCDate()-1);$('anchor-form').elements.as_of.value=start.toISOString().slice(0,10);
renderMatchReview(s);
renderChecklists();
renderExceptions(s);
const blockers=$('blockers');blockers.replaceChildren();if(r.blockers.length){const list=el('ul',undefined,'blocker-list');r.blockers.forEach(message=>list.append(el('li',typeof message==='string'?message:message.message)));blockers.append(list);}else blockers.append(el('div',s.status==='completed'?'Accepted and completed. The historical evidence and outstanding schedule are saved.':'All deterministic controls pass and the adjusted balances agree exactly. Explicit human acceptance is still required.','ready'));$('complete-button').textContent=s.status==='completed'?'Reopen reconciliation':'Accept & complete reconciliation';$('complete-button').disabled=s.status!=='completed'&&!r.ready_to_complete;$('finish-title').textContent=s.status==='completed'?'A completed, evidenced reconciliation.':'An exact tie-out, with evidence.';for(const [id,format]of[['csv-export','csv'],['xlsx-export','xlsx'],['print-export','html'],['backup-export','json']])$(id).href='/api/export/'+format+'?'+new URLSearchParams({session_id:s.id,workspace_id:w.id,input_version:s.input_version,revision:w.version}).toString();$('export-context').textContent='Exact export: '+w.name+' / '+s.account_label+' / '+s.id+' / input version '+s.input_version+'. JSON backup includes this workspace’s saved periods; no connection credentials.';$('history').replaceChildren(...s.history.slice().reverse().map(entry=>{const node=el('div',entry.action.replaceAll('_',' '),'history-entry');node.append(el('small',entry.at+' · input version '+entry.input_version));return node;}));$('propose').disabled=s.status==='completed';$('import-form').querySelector('button[type=submit]').disabled=s.status==='completed';$('anchor-form').querySelector('button[type=submit]').disabled=s.status==='completed';}
function renderChecklists(){
  const plans=currentState.assistant_checklists||[];
  $('assistant-plans-box').hidden=!plans.length;
  $('assistant-plans').replaceChildren();
  for(const plan of plans){
    if(typeof window.renderBankEvidence==='function')$('assistant-plans').append(window.renderBankEvidence({id:plan.id,label:'Saved review checklist',data:{plan}}));
  }
}
$('assistant-plan-example').addEventListener('click',()=>{
  $('chat-message').value='Read the selected reconciliation evidence and draft a short review checklist for the remaining work. Save it for my review; execute no actions.';
  $('chat-message').focus();
});
$('assistant-match-example').addEventListener('click',()=>{
  $('chat-message').value='Inspect the selected sources and propose one page of up to forty deterministic matches for my review. Stop after that page and explain any remaining exceptions.';
  $('chat-message').focus();
});
function matchesExceptionFilter(row){
  const side=$('exception-side').value||'all',query=$('exception-search').value.trim().toLocaleLowerCase();
  return (side==='all'||row.side===side)&&(!query||[row.id,row.date,row.amount,row.description].some(value=>String(value).toLocaleLowerCase().includes(query)));
}
function renderExceptions(s){
const usedB=new Set(s.matches.map(x=>x.bank_id)),usedL=new Set(s.matches.map(x=>x.ledger_id));const unmatched=[...(s.bank?.rows||[]).filter(x=>!usedB.has(x.id)).map(x=>({...x,side:'bank'})),...[...(s.ledger?.rows||[]),...s.carry].filter(x=>!usedL.has(x.id)).map(x=>({...x,side:'ledger'}))].filter(matchesExceptionFilter);const exceptions=$('exceptions');exceptions.replaceChildren();$('exceptions-count').textContent=unmatched.length+' unmatched records shown · '+(s.bank?.rows?.length||0)+' imported bank rows / '+((s.ledger?.rows?.length||0)+s.carry.length)+' book and opening rows.';if(!unmatched.length)empty(exceptions,'No unmatched records fit this view.');unmatched.forEach(row=>{const box=el('div',undefined,'exception');box.append(record(row,row.side==='bank'?'Bank exception':'Book / opening outstanding'));const why=currentState.match_diagnostics?.rows?.find(item=>item.bank_id===row.id);if(why)box.append(el('p',why.message,'fine'));const investigate=el('button','Investigate this item','quiet');investigate.type='button';investigate.addEventListener('click',()=>{$('chat-message').value='Investigate unmatched '+row.side+' record '+row.id+'. Read its evidence and explain what needs review. Draft any suggested actions for my review.';$('chat-message').focus();});box.append(investigate);const classification=s.classifications[row.id];if(classification)box.append(el('p','Reviewed: '+classification.kind.replaceAll('_',' ')+' · '+classification.evidence,'fine'));if(row.origin_side==='opening'||row.carried_from)box.append(el('p','Retained as outstanding from the evidenced opening / prior completed period until it clears.','fine'));const controls=el('form',undefined,'inline');const kindLabel=el('label','Treatment');const kind=el('select');const choices=row.side==='bank'?[['bank_fee','Bank fee · pending book posting'],['interest','Interest · pending book posting'],['returned_payment','Returned payment · pending book posting'],['book_adjustment','Other book adjustment · pending posting'],['bank_correction','Evidenced bank error / correction']]:[['outstanding',row.amount.startsWith('-')?'Outstanding withdrawal':'Deposit in transit']];choices.forEach(([value,text])=>kind.append(option(value,text)));if(classification)kind.value=classification.kind;kindLabel.append(kind);const noteLabel=el('label','Evidence / review note');const note=el('input');note.required=true;note.value=classification?.evidence||'';note.placeholder='Identify supporting evidence';noteLabel.append(note);const button=el('button','Save treatment','secondary');button.type='submit';button.disabled=s.status==='completed';controls.append(kindLabel,noteLabel,button);controls.addEventListener('submit',handle(async()=>{await api('/api/classifications',{row_id:row.id,kind:kind.value,evidence:note.value});await refresh();}));box.append(controls);exceptions.append(box);});
}
$('exception-search').addEventListener('input',()=>{if(currentState?.session)renderExceptions(currentState.session);});
$('exception-side').addEventListener('change',()=>{if(currentState?.session)renderExceptions(currentState.session);});
const selectedProposalIds = new Set();
function rowChoice(row){return row.date+' · '+row.amount+' · '+row.description.slice(0,80)+' · row '+row.ordinal+' · '+row.id;}
function updateBatchSelection(){
  const count=selectedProposalIds.size;
  $('batch-selection').textContent=count+' reviewed pair'+(count===1?'':'s')+' selected (maximum 40).';
  $('batch-confirm').disabled=!count||currentState?.session?.status==='completed';
}
function renderManualLedger(){
  const s=currentState.session,bank=rowById($('manual-bank').value);
  const used=new Set(s.matches.map(item=>item.ledger_id));
  $('manual-ledger').replaceChildren(option('','Choose the corresponding book record'),
    ...[...(s.ledger?.rows||[]),...s.carry].filter(row=>!used.has(row.id)&&bank&&row.amount===bank.amount).map(row=>option(row.id,rowChoice(row))));
}
function renderMatchReview(s){
  selectedProposalIds.clear();
  updateBatchSelection();
  $('batch-review').hidden=!s.drafts.length||s.status==='completed';
  const proposals=$('proposal-list');proposals.replaceChildren();
  if(!s.drafts.length)empty(proposals,'No pending suggestions. Generate candidates, or ask the assistant to investigate.');
  s.drafts.forEach(proposal=>{
    const b=rowById(proposal.bank_id),l=rowById(proposal.ledger_id);if(!b||!l)return;
    const box=el('div',undefined,'proposal');
    const selection=el('label',undefined,'check');
    const checkbox=el('input');checkbox.type='checkbox';checkbox.disabled=s.status==='completed';
    checkbox.setAttribute('aria-label','Select reviewed pair: '+b.id+' and '+l.id);
    checkbox.addEventListener('change',()=>{
      if(checkbox.checked&&selectedProposalIds.size>=40){checkbox.checked=false;notice('Review at most forty pairs in one batch.');return;}
      if(checkbox.checked&&s.drafts.some(other=>selectedProposalIds.has(other.id)&&(other.bank_id===proposal.bank_id||other.ledger_id===proposal.ledger_id))){checkbox.checked=false;notice('That bank or book record is already selected. Choose one proposal for each transaction.');return;}
      if(checkbox.checked)selectedProposalIds.add(proposal.id);else selectedProposalIds.delete(proposal.id);
      updateBatchSelection();
    });
    selection.append(checkbox,el('span','Include this pair in selected review'));
    const pair=el('div',undefined,'pair');pair.append(record(b,'Bank statement'),el('span','↔','arrow'),record(l,'Books'));
    const foot=el('div',undefined,'proposal-foot');foot.append(el('p',proposal.reason));
    const review=el('form',undefined,'inline');const noteLabel=el('label','Review evidence / who performed it','grow');
    const note=el('textarea');note.required=true;note.maxLength=2000;note.rows=2;note.placeholder='Identify review evidence and the reviewer; describe any assistant demonstration as such.';note.disabled=s.status==='completed';noteLabel.append(note);
    const button=el('button','Confirm reviewed match','secondary');button.type='submit';button.disabled=s.status==='completed';
    review.addEventListener('submit',handle(async()=>{await api('/api/matches/confirm',{proposal_id:proposal.id,note:note.value});await refresh();}));
    review.append(noteLabel,button);box.append(selection,pair,foot,review);proposals.append(box);
  });
  $('confirmed-title').textContent='Confirmed matches · '+s.matches.length;
  const confirmed=$('confirmed-list');confirmed.replaceChildren();
  s.matches.forEach(match=>{
    const box=el('div',undefined,'proposal');const pair=el('div',undefined,'pair');
    pair.append(record(rowById(match.bank_id),'Bank'),el('span','↔','arrow'),record(rowById(match.ledger_id),'Books'));
    const button=el('button','Undo this confirmation','quiet');button.disabled=s.status==='completed';
    button.addEventListener('click',handle(async()=>{await api('/api/matches/unconfirm',{match_id:match.id});await refresh();}));
    box.append(pair,el('p','Review note: '+match.note,'fine'),button);confirmed.append(box);
  });
  const used=new Set(s.matches.map(item=>item.bank_id));
  $('manual-bank').replaceChildren(option('','Choose an unmatched bank record'),
    ...(s.bank?.rows||[]).filter(row=>!used.has(row.id)).map(row=>option(row.id,rowChoice(row))));
  $('manual-bank').disabled=$('manual-ledger').disabled=$('manual-confirm').disabled=s.status==='completed';
  $('manual-note').value='';$('batch-note').value='';renderManualLedger();
  const diagnostic=currentState.match_diagnostics;
  $('match-diagnostics').replaceChildren();
  if(diagnostic){
    $('match-diagnostics').append(el('p',diagnostic.message||'Review suggestions against both independent sources.'));
    for(const [reason,count] of Object.entries(diagnostic.reason_counts||{}))if(count)$('match-diagnostics').append(el('p',count+' · '+reason.replaceAll('_',' ')));
  }
}
$('manual-bank').addEventListener('change',renderManualLedger);
$('manual-match').addEventListener('submit',handle(async()=>{
  await api('/api/matches/confirm',{bank_id:$('manual-bank').value,ledger_id:$('manual-ledger').value,input_version:currentState.session.input_version,note:$('manual-note').value});
  await refresh();notice('Reviewed manual pair confirmed.');
}));
$('batch-review').addEventListener('submit',handle(async()=>{
  const result=await api('/api/matches/confirm-selected',{proposal_ids:[...selectedProposalIds],note:$('batch-note').value});
  await refresh();notice(result.count+' reviewed pairs confirmed.');
}));
$('workspace-select').addEventListener('change',handle(async()=>{if(!$('workspace-select').value)return;await api('/api/workspaces/select',{workspace_id:$('workspace-select').value});preview=null;$('import-preview').hidden=true;await refresh();}));$('new-workspace-toggle').addEventListener('click',()=>{$('workspace-form').hidden=!$('workspace-form').hidden;});$('workspace-form').addEventListener('submit',handle(async()=>{await api('/api/workspaces/new',data($('workspace-form')));$('workspace-form').reset();$('workspace-form').hidden=true;await refresh();}));async function exampleAction(action){
  if(exampleOpening)return;
  exampleOpening=true;
  const controls=['demo','demo-another','fresh-example','sample-resume','sample-select'].map($);
  controls.forEach(control=>{control.disabled=true;});
  try{await action();}
  finally{exampleOpening=false;controls.forEach(control=>{control.disabled=false;});}
}
async function createExample(){
  await api('/api/demo',{});
  $('rehearsal').checked=true;
  await refresh();
  notice('A separate example is ready. Your other saved work is still available in Selected client.');
}
function workspaceLabel(item,workspaces){
  const names=workspaces.filter(other=>other.name===item.name);
  return item.name+(names.length>1?' · saved '+(names.findIndex(other=>other.id===item.id)+1):'');
}
async function resumeExample(workspaceId){
  if(currentState?.workspace?.id!==workspaceId)await api('/api/workspaces/select',{workspace_id:workspaceId});
  preview=null;
  $('import-preview').hidden=true;
  await refresh();
  if(!modelStatus?.configured)$('rehearsal').checked=true;
  notice('Your saved example is open. Continue where you left off.');
}
async function openExample(){
  const examples=currentState.workspaces.filter(item=>item.synthetic);
  if(currentState.workspace?.synthetic)return resumeExample(currentState.workspace.id);
  if(examples.length===1)return resumeExample(examples[0].id);
  if(!examples.length)return createExample();
  $('sample-select').replaceChildren(...examples.map(item=>option(item.id,workspaceLabel(item,examples))));
  $('sample-picker').showModal();
}
$('demo').addEventListener('click',handle(()=>exampleAction(openExample)));
$('demo-another').addEventListener('click',handle(()=>exampleAction(openExample)));
$('fresh-example').addEventListener('click',handle(()=>exampleAction(createExample)));
$('sample-form').addEventListener('submit',handle(()=>exampleAction(async()=>{
  await resumeExample($('sample-select').value);
  $('sample-picker').close();
})));
$('sample-close').addEventListener('click',()=>$('sample-picker').close());
$('new-session-toggle').addEventListener('click',()=>{$('session-form').hidden=!$('session-form').hidden;});$('session-form').addEventListener('submit',handle(async()=>{await api('/api/sessions/new',data($('session-form')));$('session-form').hidden=true;await refresh();}));$('session-select').addEventListener('change',handle(async()=>{if(!$('session-select').value)return;await api('/api/sessions/select',{session_id:$('session-select').value});preview=null;$('import-preview').hidden=true;await refresh();}));
$('import-form').addEventListener('submit',handle(async()=>{const file=$('csv-file').files[0];if(!file)throw Error('Choose a CSV file.');if(file.size>2000000)throw Error('CSV is limited to 2 MB.');const values=data($('import-form'));const mapping=Object.fromEntries(['date','description','amount','account','currency'].map(key=>[key,values['map_'+key]]));preview=await api('/api/imports/preview',{side:values.side,csv:await file.text(),opening:values.opening,closing:values.closing,source:values.source,mapping});const box=$('import-preview');box.hidden=false;box.replaceChildren(el('strong',preview.ready?'Source controls passed':'Source controls failed'),el('p',preview.total_rows+' source rows · '+preview.valid_rows+' valid rows · activity '+preview.activity+' · opening '+preview.opening+' → closing '+preview.closing));if(preview.errors.length){const list=el('ul',undefined,'blocker-list');preview.errors.forEach(error=>list.append(el('li',(error.ordinal?'Row '+error.ordinal+': ':'')+error.message)));box.append(list);}else{const button=el('button','Accept & import this source','primary');button.addEventListener('click',handle(async()=>{await api('/api/imports/commit',{preview_id:preview.id});box.hidden=true;preview=null;await refresh();notice('Source imported. Previous match reviews were reset to preserve the evidence boundary.');}));box.append(button);}box.append(el('p','Source SHA-256: '+preview.source_hash,'fine'));}));$('anchor-form').addEventListener('submit',handle(async()=>{const values=data($('anchor-form'));let outstanding;try{outstanding=JSON.parse(values.outstanding);}catch{throw Error('Opening outstanding items must be a JSON list.');}await api('/api/anchor',{kind:values.kind,as_of:values.as_of,evidence:values.evidence,outstanding});await refresh();notice('Opening evidence accepted.');}));$('propose').addEventListener('click',handle(async()=>{await api('/api/matches/propose',{});await refresh();}));$('complete-form').addEventListener('submit',handle(async()=>{const action=currentState.session.status==='completed'?'reopen':'complete';await api('/api/sessions/'+action,{note:$('acceptance-note').value});$('acceptance-note').value='';await refresh();notice(action==='complete'?'Reconciliation accepted and completed.':'Reconciliation reopened; prior evidence remains in history.');}));$('restore-file').addEventListener('change',handle(async()=>{const file=$('restore-file').files[0];if(!file)return;if(file.size>15000000)throw Error('Backup exceeds 15 MB.');let payload;try{payload=JSON.parse(await file.text());}catch{throw Error('This file is not valid JSON.');}await api('/api/restore',payload);$('restore-file').value='';await refresh();notice('Backup restored into a separate workspace.');}));$('delete-workspace').addEventListener('click',handle(async()=>{const w=currentState.workspace;const typed=window.prompt('Type the exact selected workspace name to delete its local financial data. Export a backup first if needed.\n'+w.name);if(typed===null)return;await api('/api/workspaces/current',{confirm_name:typed},'DELETE');await refresh();notice('Selected workspace deleted locally.');}));
function chat(text,kind='assistant'){const node=el('p',text,'chat-message '+kind+'-message');$('chat-log').append(node);$('chat-log').scrollTop=$('chat-log').scrollHeight;return node;}
$('chat-form').addEventListener('submit',handle(async()=>{
  if(!$('rehearsal').checked&&!(modelStatus?.configured&&modelStatus?.consent)){await openSettings();notice('Connect your assistant here. Your question is still in the chat.');return;}
  const message=$('chat-message').value,rehearsal=$('rehearsal').checked,capturedScope=JSON.stringify(currentState?.scope||{}),capturedGeneration=assistantSelectionGeneration;
  if(!pendingAssistantRequest||pendingAssistantRequest.message!==message||pendingAssistantRequest.rehearsal!==rehearsal||pendingAssistantRequest.scope!==capturedScope){
    pendingAssistantRequest={id:'question_'+Date.now().toString(36)+'_'+Math.random().toString(36).slice(2),message,rehearsal,scope:capturedScope};
  }
  const requestId=pendingAssistantRequest.id;
  chat(message,'user');$('chat-message').value='';$('chat-send').disabled=true;$('chat-cancel').hidden=false;
  controller=new AbortController();const waiting=chat('Reading the selected period’s local evidence…');
  try{
    const response=await fetch('/api/assistant',{method:'POST',headers:{'Content-Type':'application/json','X-Vesper-CSRF':csrf,'X-Vesper-Scope':capturedScope},body:JSON.stringify({message,rehearsal,request_id:requestId}),signal:controller.signal});
    const result=await response.json();
    pendingAssistantRequest=null;
    if(capturedGeneration!==assistantSelectionGeneration||capturedScope!==JSON.stringify(currentState?.scope||{}))return;
    if(!response.ok)throw Error(result.error||result.detail||'Assistant unavailable. Your reconciliation is saved.');
    lastAssistantResponseScope=capturedScope;assistantEvidenceStale=false;
    waiting.textContent=(result.mode==='synthetic-rehearsal'?'SYNTHETIC REHEARSAL\n':'')+result.message+(result.replayed?'\nSaved response · no new model call.':'');
    for(const item of result.evidence||[]){
      if(typeof window.renderBankEvidence==='function')$('chat-log').append(window.renderBankEvidence(item));
      else{const details=el('details');details.append(el('summary',item.label||item.id),el('pre',JSON.stringify(item.data,null,2),'chat-evidence'));$('chat-log').append(details);}
    }
    if(result.proposals?.length)chat('Draft match proposals are ready in Match Review. Review and confirm the pairs there.');
    await refresh();
  }catch(error){
    if(capturedGeneration===assistantSelectionGeneration&&capturedScope===JSON.stringify(currentState?.scope||{})){
      waiting.textContent=error.name==='AbortError'?'Request cancelled. Review any saved drafts before asking a new question.':error.message;
      if(pendingAssistantRequest?.id===requestId)$('chat-message').value=message;
    }
    throw error;
  }finally{$('chat-send').disabled=false;$('chat-cancel').hidden=true;controller=null;}
}));
$('chat-cancel').addEventListener('click',handle(async()=>{await api('/api/assistant/cancel',{});controller?.abort();}));
function showConnectionUnavailable(){
  $('model-status').textContent='Assistant connection could not be checked. Open settings to try again.';
  $('model-connection-summary').textContent='We could not check your connection. Try opening these settings again. Your reconciliation is saved.';
  $('connection-status').textContent='Connection status is currently unavailable.';
}
function connectionControls(){
  const available=!!currentState?.workspace;
  const form=$('model-form');
  for(const control of form.elements)control.disabled=!available||modelTesting;
  form.elements.api_key.required=!modelStatus?.configured;
  $('remove-model').hidden=!modelStatus?.configured;
  $('settings-finish').disabled=modelTesting||!available;
  $('assistant-connect').disabled=!available;
}
function paintConnection(result,{updateForm=false}={}){
  connectionGeneration++;
  const form=$('model-form'),model=result.model||{};
  modelStatus=model;
  if(updateForm){
    if(model.model)form.elements.model.value=model.model;
    form.elements.consent.checked=!!model.consent;
    form.elements.persist.checked=model.storage==='os-credential-store';
  }
  $('connection-status').textContent=JSON.stringify(result,null,2);
  $('polling').checked=!!result.polling?.enabled;
  const name=currentState?.workspace?.name;
  $('setup-workspace').textContent=name?'For '+name:'Choose a client workspace or open the example first.';
  const saved=!!(model.configured&&model.consent),connected=saved&&!!result.model_test;
  const message=!name?'Choose a workspace to connect its assistant.':connected?'Connected — ready to ask about '+name+'.':saved?'Key saved — check the connection to finish setup for '+name+'.':'Connect AI to ask questions about '+name+'.';
  $('model-status').textContent=message;
  $('model-connection-summary').textContent=message;
  $('model-connection-summary').dataset.state=connected?'connected':saved?'saved':'disconnected';
  $('connect-model').textContent=connected?'Check connection again':saved?'Try connection again':'Connect assistant';
  $('assistant-connect').textContent=saved?'Connection settings':'Connect AI';
  $('setup-finish').hidden=!connected;
  const runtime=result.runtime;
  $('settings-context').textContent=runtime?'Port '+runtime.port+' · workspace '+(result.scope?.id||'none')+' · '+(runtime.testing?'verification mode':'normal mode')+'. Session-only keys last until this app process stops.':'';
  connectionControls();
}
async function settingsStatus(options={}){
  const workspaceId=currentState?.workspace?.id||null,ticket=++connectionGeneration;
  const current=()=>ticket===connectionGeneration&&(currentState?.workspace?.id||null)===workspaceId;
  try{
    const result=await api('/api/settings');
    if(!current())return null;
    paintConnection(result,options);
    return result;
  }catch(error){
    if(!current())return null;
    throw error;
  }
}
async function openSettings(){
  $('settings').querySelector('.dialog-feedback')?.remove();
  $('model-form').elements.api_key.value='';
  $('setup-finish').hidden=true;
  if(!$('settings').open)$('settings').showModal();
  await settingsStatus({updateForm:true});
  if($('xero-settings').open){try{await xeroRefreshStatus({updateForm:true});}catch{xeroError('The Xero connection could not be checked. Reopen this panel to try again.');}}
}
function closeSettings(){
  $('model-form').elements.api_key.value='';
  $('settings').close();
}
$('settings-open').addEventListener('click',handle(openSettings));
$('assistant-connect').addEventListener('click',handle(openSettings));
$('settings-close').addEventListener('click',closeSettings);
$('settings').addEventListener('close',()=>{$('model-form').elements.api_key.value='';});
$('settings-finish').addEventListener('click',()=>{
  $('rehearsal').checked=false;
  closeSettings();
  $('chat-message').focus();
});
$('assistant-example').addEventListener('click',()=>{
  $('chat-message').value='Explain why this reconciliation balances and what remains outstanding. Use the saved reconciliation summary and unmatched records.';
  $('chat-message').focus();
});
$('model-form').addEventListener('submit',handle(async()=>{
  if(modelTesting)return;
  const form=$('model-form'),values=data(form),scope=JSON.stringify(currentState?.scope);
  const persist=form.elements.persist.checked,consent=form.elements.consent.checked;
  const sameWorkspace=()=>{
    if(scope!==JSON.stringify(currentState?.scope))throw Error('The selected work changed during setup. Open settings for the client you want and try again.');
  };
  modelTesting=true;
  connectionGeneration++;
  connectionControls();
  $('setup-finish').hidden=true;
  try{
    sameWorkspace();
    const changed=modelStatus&&(values.model!==modelStatus.model||persist!==(modelStatus.storage==='os-credential-store')||consent!==!!modelStatus.consent);
    if(values.api_key){
      $('model-connection-summary').textContent='Saving your key securely…';
      const saved=await api('/api/settings/model',{api_key:values.api_key,model:values.model,persist,consent});
      form.elements.api_key.value='';
      sameWorkspace();
      if(!saved.model?.configured||!saved.model?.consent)throw Error('Your connection was not saved. Please try again.');
      paintConnection(saved);
    }else if(!modelStatus?.configured||changed){
      throw Error('Paste your API key to save these settings, then choose Connect assistant.');
    }
    sameWorkspace();
    $('model-connection-summary').textContent='Key saved. Checking the connection with OpenAI…';
    const tested=await api('/api/settings/model/test',{});
    sameWorkspace();
    paintConnection(tested,{updateForm:true});
    if(!tested.model_test)throw Error('The connection check did not finish. Your key is saved; try the connection again.');
    $('rehearsal').checked=false;
    notice('Connected. Choose Finish, then ask your assistant a question.');
  }catch(error){
    if(scope===JSON.stringify(currentState?.scope)){
      try{
        const status=await settingsStatus();
        const prefix=status?.model?.configured?'Key saved, but the connection needs attention. ':'';
        $('model-connection-summary').textContent=prefix+error.message;
        $('model-connection-summary').dataset.state='error';
        $('setup-finish').hidden=true;
      }catch{showConnectionUnavailable();}
    }
    throw error;
  }finally{
    modelTesting=false;
    connectionControls();
  }
}));
$('remove-model').addEventListener('click',handle(async()=>{
  if(modelTesting)return;
  const scope=JSON.stringify(currentState?.scope);
  modelTesting=true;
  connectionGeneration++;
  connectionControls();
  try{
    await api('/api/settings/model',{},'DELETE');
    if(scope!==JSON.stringify(currentState?.scope))return;
    $('model-form').reset();
    await settingsStatus({updateForm:true});
    notice('Assistant disconnected for this client. Your saved financial work is unchanged.');
  }finally{modelTesting=false;connectionControls();}
}));
// Xero uses its own scoped state. Provider tokens and authorization codes never enter this UI.
let xeroStatus=null,xeroBusy=false,xeroChecking=false,xeroEpoch=0,xeroStatusTicket=0;
let xeroOrganisations=[],xeroAccounts=[],xeroPopup=null,xeroPollTimer=null,xeroPollAttempts=0;
function xeroStopPolling(){clearTimeout(xeroPollTimer);xeroPollTimer=null;}
function xeroClosePopup(){try{if(xeroPopup&&!xeroPopup.closed)xeroPopup.close();}catch{}xeroPopup=null;}
function xeroInvalidate(){xeroEpoch++;xeroStatusTicket++;xeroBusy=false;xeroChecking=false;xeroStopPolling();}
function xeroReset(){
  xeroClosePopup();xeroStatus=null;xeroOrganisations=[];xeroAccounts=[];xeroPollAttempts=0;
  $('xero-form').reset();
  $('xero-organisations').replaceChildren(option('','Choose an organisation'));
  $('xero-accounts').replaceChildren(option('','Choose a bank account'));
  $('xero-auth-link').href='#';$('xero-popup-help').hidden=true;
  $('xero-status').textContent='Open this panel to check the Xero connection.';
  $('xero-company').textContent='';$('xero-company-section').hidden=true;$('xero-account-section').hidden=true;
  xeroControls();
}
function xeroCapture(){return{epoch:xeroEpoch,scope:JSON.stringify(currentState?.scope),workspace:currentState?.workspace?.id};}
function xeroCurrent(captured){return captured.epoch===xeroEpoch&&captured.scope===JSON.stringify(currentState?.scope);}
function xeroPending(){return xeroStatus?.authorization?.status==='pending';}
function xeroCurrencyError(account){
  const expected=currentState?.workspace?.currency,base=xeroStatus?.xero?.company?.currency;
  if(base&&expected!==base)return 'This Xero organisation uses '+base+' as its base currency. Select a workspace in that currency; this connector supports base-currency bank accounts only.';
  if(account&&(!account.currency||account.currency!==expected||base&&account.currency!==base))return 'This account currency does not match the workspace and Xero base currency. Choose a supported base-currency account.';
  return '';
}
function xeroImportAccount(){return xeroAccounts.find(item=>item.id===currentState?.session?.account_id);}
function xeroControls(){
  const available=!!currentState?.workspace,busy=xeroBusy||xeroChecking,pending=xeroPending();
  const connected=!!xeroStatus?.xero?.configured,bound=!!xeroStatus?.xero?.bound;
  for(const control of $('xero-form').elements)control.disabled=!available||busy||pending;
  $('xero-authorize').textContent=pending?'Authorization in progress':connected?'Authorize Xero again':'Authorize with Xero';
  $('xero-check').hidden=!pending;$('xero-check').disabled=!available||busy;
  $('xero-cancel').hidden=!pending;$('xero-cancel').disabled=!available||busy;
  $('xero-disconnect').hidden=!connected&&!pending;$('xero-disconnect').disabled=!available||busy;
  $('xero-load-organisations').disabled=!connected||busy||pending;
  $('xero-organisations').disabled=!connected||busy||pending;
  $('xero-confirm').disabled=!connected||busy||pending||!xeroOrganisations.some(item=>item.id===$('xero-organisations').value);
  $('xero-load-accounts').disabled=!bound||busy||pending||!!xeroCurrencyError();
  $('xero-accounts').disabled=!bound||busy||pending;
  const chosen=xeroAccounts.find(item=>item.id===$('xero-accounts').value);
  $('xero-use-account').disabled=!bound||busy||pending||!chosen||!!xeroCurrencyError(chosen);
  const session=currentState?.session,account=xeroImportAccount();
  $('xero-sync').disabled=!bound||busy||pending||!session||session.status==='completed'||!account||!!xeroCurrencyError(account);
  $('xero-import-context').textContent=!session?'Choose an account and period in the main workspace first.':session.status==='completed'?'This saved period is completed. Create or select an editable period before importing.':!account?'Show Xero bank accounts and select a reconciliation with an exact matching account ID.':'Selected: '+session.account_label+' · '+session.account_id+' · '+session.period_start+' → '+session.period_end+' · '+session.currency+'.';
}
function paintXero(result,{updateForm=false}={}){
  const source=result.xero||{},organisation=source.company;
  // Deliberate public-field projection: never render an authorization URL or raw response.
  xeroStatus={xero:{configured:!!source.configured,client_id:source.client_id||'',storage:source.storage||null,bound:!!source.bound,company:organisation?{id:organisation.id,name:organisation.name,currency:organisation.currency}:null},authorization:{status:result.authorization?.status||'idle',message:result.authorization?.message||''},redirect_uri:result.redirect_uri||'',scopes:Array.isArray(result.scopes)?result.scopes:[]};
  const status=xeroStatus.xero,auth=xeroStatus.authorization,name=currentState?.workspace?.name;
  if(updateForm&&auth.status!=='pending'){$('xero-form').elements.client_id.value=status.client_id;$('xero-form').elements.persist.checked=status.storage==='os-credential-store';}
  $('xero-workspace').textContent=name?'Xero connection for '+name+'.':'Select a client workspace first.';
  $('xero-redirect-uri').textContent=xeroStatus.redirect_uri;$('xero-scopes').textContent=xeroStatus.scopes.join(', ');
  let message=auth.status==='pending'?'Waiting for approval on Xero. Return here after authorizing.':status.bound?'Xero organisation confirmed: '+(status.company?.name||'selected organisation')+'. Choose its bank account and period.':status.configured?'Xero authorized. Choose and explicitly confirm the intended organisation.':'Xero is not connected for this workspace.';
  if(['failed','expired'].includes(auth.status))message=auth.message||'Authorization did not finish. Authorize with Xero again.';
  const currencyError=status.bound?xeroCurrencyError():'';
  $('xero-status').textContent=currencyError||message;
  $('xero-status').dataset.state=currencyError||['failed','expired'].includes(auth.status)?'error':status.bound?'connected':status.configured?'saved':'disconnected';
  $('xero-company-section').hidden=!status.configured||auth.status==='pending';
  $('xero-account-section').hidden=!status.bound||auth.status==='pending';
  $('xero-company').textContent=status.bound?'Confirmed organisation: '+status.company.name+' · '+status.company.id+' · '+(status.company.currency||'currency not available'):'Select the intended organisation; none is confirmed yet.';
  if(auth.status!=='pending'){$('xero-auth-link').href='#';$('xero-popup-help').hidden=true;xeroStopPolling();}
  xeroControls();
}
function xeroError(message){$('xero-status').textContent=message;$('xero-status').dataset.state='error';}
function xeroSchedulePoll(){
  xeroStopPolling();
  if(!xeroPending()||!$('settings').open||!$('xero-settings').open||xeroBusy||xeroChecking||xeroPollAttempts>=120)return;
  const captured=xeroCapture();
  xeroPollTimer=setTimeout(async()=>{
    xeroPollTimer=null;
    if(!xeroCurrent(captured)||!xeroPending()||!$('settings').open||!$('xero-settings').open)return;
    xeroPollAttempts++;
    try{await xeroRefreshStatus();}
    catch{if(xeroCurrent(captured))xeroError('Authorization status could not be checked. Choose Check authorization to try again.');}
  },1500);
}
async function xeroRefreshStatus({updateForm=false}={}){
  if(!currentState?.workspace||xeroBusy)return null;
  const captured=xeroCapture(),ticket=++xeroStatusTicket;
  const current=()=>xeroCurrent(captured)&&ticket===xeroStatusTicket;
  xeroChecking=true;xeroControls();
  try{
    const result=await api('/api/settings/xero');
    if(!current())return null;
    paintXero(result,{updateForm});
    return xeroStatus;
  }catch(error){if(current())throw error;return null;}
  finally{if(current()){xeroChecking=false;xeroControls();xeroSchedulePoll();}}
}
async function xeroAction(action){
  if(xeroBusy||xeroChecking||!currentState?.workspace)return;
  xeroInvalidate();xeroBusy=true;
  const captured=xeroCapture();xeroControls();
  try{await action(captured);}
  catch(error){if(xeroCurrent(captured)){xeroError(error.message);notice(error.message,true);}}
  finally{if(xeroCurrent(captured)){xeroBusy=false;xeroControls();xeroSchedulePoll();}}
}
function xeroAuthorizationURL(value){
  try{
    const url=new URL(value);
    if(url.protocol==='https:'&&url.hostname==='login.xero.com'&&!url.port&&!url.username&&!url.password&&url.pathname==='/identity/connect/authorize'&&!url.hash)return url.href;
  }catch{}
  throw Error('The local app did not return a supported Xero authorization address. Cancel and try again.');
}
$('xero-settings').addEventListener('toggle',handle(async()=>{
  if(!$('xero-settings').open){xeroStopPolling();return;}
  if(!$('settings').open)return;
  try{await xeroRefreshStatus({updateForm:true});}
  catch{ xeroError('The Xero connection could not be checked. Close and reopen this panel to try again.'); }
}));
$('settings').addEventListener('close',()=>{xeroInvalidate();});
$('xero-form').addEventListener('submit',handle(()=>{
  const form=$('xero-form'),values=data(form);
  if(xeroPending())return;
  return xeroAction(async captured=>{
    if(typeof values.client_id!=='string'||!values.client_id.trim())throw Error('Enter your Xero app Client ID first.');
    // Open during the user's gesture, before awaiting the local server.
    let popup=null;
    try{popup=window.open('about:blank','_blank');if(popup)popup.opener=null;}catch{}
    xeroPopup=popup;xeroPollAttempts=0;
    try{
      const result=await api('/api/settings/xero/authorize',{client_id:values.client_id.trim(),persist:form.elements.persist.checked});
      if(!xeroCurrent(captured)){try{popup?.close();}catch{}return;}
      paintXero(result);
      const authorizationURL=xeroAuthorizationURL(result.authorization_url);
      xeroOrganisations=[];xeroAccounts=[];
      $('xero-organisations').replaceChildren(option('','Choose an organisation'));
      $('xero-accounts').replaceChildren(option('','Choose a bank account'));
      $('xero-auth-link').href=authorizationURL;$('xero-popup-help').hidden=false;
      try{if(popup&&!popup.closed)popup.location.replace(authorizationURL);}catch{try{popup?.close();}catch{}xeroPopup=null;}
    }catch(error){try{popup?.close();}catch{}if(xeroPopup===popup)xeroPopup=null;throw error;}
  });
}));
$('xero-check').addEventListener('click',handle(async()=>{
  xeroPollAttempts=0;
  try{await xeroRefreshStatus();}catch(error){xeroError(error.message);throw error;}
}));
$('xero-cancel').addEventListener('click',handle(()=>xeroAction(async captured=>{
  const result=await api('/api/settings/xero/cancel',{});
  if(!xeroCurrent(captured))return;
  xeroClosePopup();paintXero(result);
  notice('Pending Xero authorization cancelled. Saved financial work is unchanged.');
})));
$('xero-load-organisations').addEventListener('click',handle(()=>xeroAction(async captured=>{
  if(!xeroStatus?.xero?.configured||xeroPending())throw Error('Finish Xero authorization before choosing an organisation.');
  const result=await api('/api/xero/organisations');
  if(!xeroCurrent(captured))return;
  xeroOrganisations=Array.isArray(result.organisations)?result.organisations:[];
  $('xero-organisations').replaceChildren(option('','Choose an organisation'),...xeroOrganisations.map(item=>option(item.id,item.name+' · '+item.id)));
  if(!xeroOrganisations.length)xeroError('No authorised organisations were returned. Check the organisations approved on Xero.');
})));
$('xero-organisations').addEventListener('change',xeroControls);
$('xero-confirm').addEventListener('click',handle(()=>xeroAction(async captured=>{
  const tenant=$('xero-organisations').value;
  if(!xeroStatus?.xero?.configured||!xeroOrganisations.some(item=>item.id===tenant))throw Error('Select an authorised organisation before confirming it.');
  const result=await api('/api/xero/confirm',{tenant_id:tenant});
  if(!xeroCurrent(captured))return;
  xeroAccounts=[];$('xero-accounts').replaceChildren(option('','Choose a bank account'));
  paintXero(result,{updateForm:true});
  notice('Xero organisation explicitly confirmed for this workspace. Choose its bank account next.');
})));
$('xero-load-accounts').addEventListener('click',handle(()=>xeroAction(async captured=>{
  if(!xeroStatus?.xero?.bound)throw Error('Confirm the Xero organisation before loading bank accounts.');
  const currencyError=xeroCurrencyError();if(currencyError)throw Error(currencyError);
  const result=await api('/api/xero/accounts');
  if(!xeroCurrent(captured))return;
  xeroAccounts=Array.isArray(result.accounts)?result.accounts:[];
  $('xero-accounts').replaceChildren(option('','Choose a bank account'),...xeroAccounts.map(item=>option(item.id,item.name+' · '+item.currency+' · '+item.id)));
  if(!xeroAccounts.length)xeroError('No supported Xero bank accounts were returned for this organisation.');
})));
$('xero-accounts').addEventListener('change',xeroControls);
$('xero-use-account').addEventListener('click',handle(()=>{
  if(xeroBusy||xeroChecking)return;
  const account=xeroAccounts.find(item=>item.id===$('xero-accounts').value);
  if(!xeroStatus?.xero?.bound||!account)throw Error('Choose a bank account from the confirmed Xero organisation.');
  const currencyError=xeroCurrencyError(account);if(currencyError)throw Error(currencyError);
  const form=$('session-form'),session=currentState?.session;
  form.elements.account_id.value=account.id;form.elements.account_label.value=account.name;
  form.elements.period_start.value=session?.period_start||'';form.elements.period_end.value=session?.period_end||'';
  form.hidden=false;closeSettings();form.scrollIntoView({behavior:'smooth',block:'center'});form.elements.period_start.focus();
  notice('Account details are ready. Review the dates and choose Create reconciliation. No source data was imported.');
}));
$('xero-sync').addEventListener('click',handle(()=>xeroAction(async captured=>{
  const session=currentState?.session,account=xeroImportAccount();
  if(!xeroStatus?.xero?.bound)throw Error('Confirm the Xero organisation first.');
  if(!session||session.status==='completed')throw Error('Select an editable account and period before importing.');
  if(!account)throw Error('Show Xero bank accounts and select the reconciliation with its exact account ID.');
  const currencyError=xeroCurrencyError(account);if(currencyError)throw Error(currencyError);
  const result=await api('/api/xero/sync',{account_id:session.account_id,start_date:session.period_start,end_date:session.period_end});
  if(!xeroCurrent(captured))return;
  await refresh();
  if(currentState?.workspace?.id===captured.workspace)notice(result.result?.message||'Controlled Xero book ledger imported. Review its source controls and independent bank statement.');
})));
$('xero-disconnect').addEventListener('click',handle(()=>xeroAction(async captured=>{
  const result=await api('/api/settings/xero',{},'DELETE');
  if(!xeroCurrent(captured))return;
  xeroClosePopup();xeroOrganisations=[];xeroAccounts=[];
  $('xero-organisations').replaceChildren(option('','Choose an organisation'));
  $('xero-accounts').replaceChildren(option('','Choose a bank account'));
  paintXero(result,{updateForm:true});
  notice('Xero disconnected locally for this workspace. Imported evidence is preserved.');
})));

$('qbo-form').addEventListener('submit',handle(async()=>{const f=$('qbo-form'),v=data(f);await api('/api/settings/qbo',{...v,persist:f.elements.persist.checked});for(const name of['client_secret','refresh_token'])f.elements[name].value='';company=null;$('confirm-company').hidden=true;await settingsStatus();notice('Credentials saved. Test and confirm the company before syncing.');}));$('test-qbo').addEventListener('click',handle(async()=>{const result=await api('/api/settings/qbo/test',{});company=result.company;$('qbo-company').textContent='Company returned by Intuit: '+(company?.name||company?.CompanyName||'')+' · ID '+(company?.id||'');$('confirm-company').hidden=!result.requires_confirmation;await settingsStatus();}));$('confirm-company').addEventListener('click',handle(async()=>{if(!company?.id)throw Error('Test the connection first.');await api('/api/settings/qbo/test',{confirm_company_id:company.id});$('confirm-company').hidden=true;await settingsStatus();notice('Returned QuickBooks company explicitly bound to this workspace.');}));$('disconnect-qbo').addEventListener('click',handle(async()=>{await api('/api/settings/qbo',{},'DELETE');company=null;$('qbo-company').textContent='Disconnected. Local imported evidence is preserved.';$('confirm-company').hidden=true;await settingsStatus();}));$('qbo-accounts').addEventListener('click',handle(async()=>{const result=await api('/api/qbo/accounts');const accounts=result.accounts||result;const list=$('qbo-account-list');list.replaceChildren();for(const account of accounts)list.append(el('div',(account.name||account.Name||'')+' · account ID '+(account.id||account.Id||'')+' · '+(account.currency||''),'account-item'));}));function syncParams(){const s=currentState.session;if(!s)throw Error('Select an account and period first.');return{account_id:s.account_id,start_date:s.period_start,end_date:s.period_end};}$('qbo-sync').addEventListener('click',handle(async()=>{const result=await api('/api/qbo/sync',syncParams());await refresh();await settingsStatus();notice(result.result?.message||result.message||'Controlled ledger refresh completed.');}));$('polling').addEventListener('change',handle(async()=>{await api('/api/qbo/polling',{enabled:$('polling').checked,interval_seconds:300,params:syncParams()});await settingsStatus();}));
$('quit-app').addEventListener('click',handle(async()=>{
  if(modelTesting||controller)throw Error('Finish or cancel the current assistant request before quitting.');
  $('quit-app').disabled=true;
  try{
    await api('/api/app/quit',{});
    $('model-form').elements.api_key.value='';
    if($('settings').open)$('settings').close();
    for(const control of document.querySelectorAll('button,input,select,textarea'))control.disabled=true;
    notice('Bank Rec is closed. Your saved work is safe. Open the app again to continue.');
    clearTimeout(notice.timer);
    $('model-status').textContent='App closed. Open Vesper Bank Rec again to continue.';
  }catch(error){$('quit-app').disabled=false;throw error;}
}));
(async()=>{try{const bootstrap=await api('/api/bootstrap');csrf=bootstrap.csrf_token;$('quit-app').hidden=!bootstrap.desktop_mode;await refresh();}catch(error){notice(error.message,true);}})();
