'use strict';
const $=id=>document.getElementById(id);
let session,run=localStorage.getItem('folderundo-run'),scan,plan,result,status,mode='B0';
let busy=false,serverBusy=false,needsRefresh=true,pollExpired=false,eventAfter=0,eventRows=[],scanReceivedAt='';
let cancelPending=false,cancelRequested=false,admittingCopy=false,statusRequest=0,activeAction='';
const POLL_LIMIT_MS=190000;
function table(target,heads,rows,prefix=[]){const t=document.createElement('table'),h=document.createElement('tr');for(const s of heads){const c=document.createElement('th');c.scope='col';c.textContent=s;h.append(c)}t.append(h);for(const row of rows){const tr=document.createElement('tr');for(const value of row){const td=document.createElement('td'),code=document.createElement('code');code.textContent=value;td.append(code);tr.append(td)}t.append(tr)}$(target).replaceChildren(...prefix,t)}
function conflictNotes(value){
 if(!['CONFLICT','RECOVERY_REQUIRED'].includes(value.state))return [];
 const occupied=value.state==='CONFLICT'&&value.mode==='UNDO'&&(value.conflicts||[]).some(c=>c.reason==='CURRENT_PATH_DIFFERS'&&c.expected===null&&c.current);
 const explanation=document.createElement('p');explanation.className='conflict-explanation';
 explanation.textContent=occupied?'A file occupies a path the journal expected to be empty. Undo did not fully restore this copy. Leave existing files unchanged and download the evidence below before investigating.':'The files could not be confirmed against the recorded state. Leave existing files unchanged and download the evidence below before investigating.';
 const next=document.createElement('p');next.className='conflict-next-demo';next.textContent='For another demo, use "Preserve existing copies and create new". It starts a separate copy; the existing problem remains.';
 return [explanation,next];
}
function errorText(value){if(!value)return 'Request failed';if(typeof value==='string')return value;return [value.code,value.changed,value.preserved,value.next_action?'Next: '+value.next_action:null].filter(Boolean).join('\n')||'Request failed'}
async function api(path,body,timeoutMs=195000){
 if(body!==undefined&&(/^\/(?:reference-runs|runs)(?:\/preserve-and-create)?$/).test(path))body={...body,idempotency_key:crypto.randomUUID()};
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),timeoutMs);
 const opts=body===undefined?{cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf,'Idempotency-Key':body.idempotency_key||crypto.randomUUID()},body:JSON.stringify(body)};
 opts.signal=controller.signal;
 try{const r=await fetch(path,opts),v=await r.json();if(!r.ok){const e=new Error(errorText(v.error));e.code=v.error?.code;throw e}return v}
 catch(e){if(e.name==='AbortError')throw new Error(body===undefined?'The status request timed out. Existing copies are retained. Refresh journal to check again.':'The request timed out and may have been accepted. Do not repeat an operation based on this timeout. Refresh journal to inspect the retained records.');throw e}
 finally{clearTimeout(timer)}
}
function snapshotUsable(){return !!result&&status?.snapshot_current===true&&!status?.blocked&&!needsRefresh&&status?.state!=='CANCELLED'}
function stopFollowup(){return cancelRequested||cancelPending||status?.state==='CANCELLED'}
function forwardFailed(){return (status?.tasks||[]).filter(t=>t.kind==='FORWARD').slice(-1)[0]?.status==='FAILED'}
function exportUsable(){return !busy&&!serverBusy&&!cancelPending&&status?.export_ready===true&&(status.export_kind==='failure_receipt'||(snapshotUsable()&&!['AWAITING_APPROVAL','APPROVED','APPLYING','UNDO_REQUESTED','UNDOING'].includes(result.state)))}
function observedAt(){return status?.observed_at?String(status.observed_at):'an unavailable time'}
function guidance(){
 let text,target,label;
 if(cancelPending)text='Cancellation has been requested. Wait for its recorded outcome; requesting cancellation does not establish restoration or original-file preservation.';
 else if(busy)text=serverBusy?'The selected copy has queued or running work. Stored status is being observed; wait for the worker to finish.':'Working on the selected copy. Wait for the current request to finish.';
 else if(serverBusy&&!pollExpired)text='The selected copy has queued or running work. File operations and evidence download are unavailable until it finishes.';
 else if(!session)text='Open the private bootstrap URL printed by the running server to begin an owner session. Do not share that URL.';
 else if(!run){text='Choose a fixture, then create a synthetic copy. The four-document reference is a short mechanics walkthrough; it is not classification evidence.';target='create';label='Create synthetic copy'}
 else if(serverBusy&&pollExpired){text='Automatic observation has stopped. The worker may still have queued or running work; this does not cancel it. Refresh journal to check again. Existing copies are retained.';target='refresh';label='Refresh journal'}
 else if(status?.state==='CANCELLED'){text='Cancellation is recorded. This control outcome does not establish restoration or original-file preservation. Retain the evidence and use a separate copy for another demo.';target=exportUsable()?'export':'newcopy';label=exportUsable()?'Download failure evidence':'Preserve existing copies and create new'}
 else if((status?.blocked||!snapshotUsable())&&exportUsable()){text='A finalized failure or cancellation receipt is available. Download the recorded evidence before investigating. It does not establish original-file preservation or authorize recovery.';target='export';label='Download failure evidence'}
 else if(status?.blocked||!snapshotUsable()){text='The selected copy is retained, but its current status is unverified. Refresh the journal before choosing an operation. Refresh observes files and records; it does not reconcile or resume work.';target='refresh';label='Refresh journal'}
 else if(result.state==='CONFLICT'||result.state==='RECOVERY_REQUIRED'){text='Inspect the recorded problem and both paths before continuing. A conflict is not a successful restoration; retained evidence can be exported.';target='conflicts';label='Inspect conflicts and evidence'}
 else if(result.state==='UNDONE'){text='Undo is recorded. Read the current-file comparison below, then download the evidence. A later change does not rewrite the recorded history.';target=exportUsable()?'export':'refresh';label=exportUsable()?'Download recorded evidence':'Refresh journal'}
 else if(result.mode==='UNDO'||result.state==='PARTIALLY_APPLIED'){text='An incomplete Undo is recorded. Inspect the operations, then resume only that recorded direction; do not start a new forward move.';target='resume';label='Resume recorded direction'}
 else if(result.mode==='FORWARD'&&result.state==='APPLYING'&&result.approval){text='Incomplete forward work is recorded with an existing exact approval. Inspect the recorded paths and approval before explicitly resuming that direction.';target='resume';label='Resume recorded direction'}
 else if(result.state==='APPLIED'){text='Inspect the moved paths, then Undo the committed moves. The conflict simulation is an optional separate negative journey.';target='undo';label='Undo committed moves'}
 else if(result.state==='APPROVED'&&forwardFailed()){text='The original forward task failed, and its exact approval remains recorded. Inspect that approval, then explicitly Resume recorded direction to request a linked recovery task. Apply cannot restart the failed task.';target='resume';label='Resume recorded direction'}
 else if(result.state==='APPROVED'){text='This exact plan is approved. Apply is a separate action and will move the approved synthetic copies.';target='apply';label='Apply approved moves'}
 else if(result.state==='AWAITING_APPROVAL'){
  text=$('consent').checked?'Review consent is checked. Approve this exact plan; approval itself does not move files.':'Review every source, destination and source hash below, then check the consent box. No move is authorized yet.';
  target=$('consent').checked?'approve':'plan';label=$('consent').checked?'Approve exact plan':'Review the exact paths and hashes';
 }
 else if(!plan){text=scan?'The synthetic copy is scanned. Build the filing preview to inspect proposed paths before any approval.':'Scan the selected copy to inspect its files before building a preview.';target=scan?'preview':'scan';label=scan?'Build filing preview':'Scan files'}
 else{text='Inspect the journal and operation states before continuing. Refresh checks the current status without authorizing a move.';target='refresh';label='Refresh journal'}
 $('next-action').textContent=text;
 const link=$('next-link');link.hidden=!target;
 if(target){link.href='#'+target;link.textContent=label}else{link.removeAttribute('href');link.textContent=''}
}
function outcome(){
 $('mode-role').textContent=mode==='B1'?'Current copy: B1 uses NVIDIA Nemotron on Nebius to propose destinations. The model cannot move files; review and approve the exact plan. These demonstrations do not establish classification accuracy.':mode==='B0'?'Current copy: frozen B0 filing rules. No model calls; you approve the exact plan, and the executor performs approved moves.':'Current copy: reference cycle by file order. No AI or semantic classification; you approve the exact plan, and the executor performs approved moves.';
 $('recorded-summary').textContent='Recorded journal status: '+result.state+'. '+(result.state==='UNDONE'?'The journal records a completed Undo.':result.state==='CONFLICT'?'A conflict is recorded; complete restoration is not established.':'Read the operation records below for completed and pending work.')+' Last completed observation: '+observedAt()+'.';
 const operations=result.operations||[],effective=operations.filter(o=>o.direction==='UNDO'||!operations.some(r=>r.parent_forward_op_id===o.id));
 const states=[...new Set(['UNDONE','COMMITTED','PENDING','CONFLICT',...effective.map(o=>o.state)])];
 $('counts').textContent='Effective operation records (latest direction): '+states.map(k=>k+': '+effective.filter(o=>o.state===k).length).join(' · ')+'. These are journal records, not a separate file-integrity check.';
 const drift=result.current_drift,prefix='Last completed filesystem observation at '+observedAt()+': ';
 if(busy||serverBusy||!snapshotUsable()){$('current-summary').textContent='Current files: unverified. The displayed records are a retained observation, not a current filesystem check. Refresh journal after queued work finishes.'}
 else if(!drift||drift.error||typeof drift.manifest_sha!=='string'||!drift.manifest_sha){$('current-summary').textContent=prefix+'terminal comparison unavailable. Recorded status alone does not confirm current restoration.'}
 else if(drift.detected===true){$('current-summary').textContent=prefix+'drift was detected since the recorded terminal state. Exact restoration was not established; the recorded history is retained.'}
 else if(drift.detected===false&&drift.expected_sha===drift.manifest_sha){$('current-summary').textContent=prefix+'no drift from the recorded terminal manifest was detected.'+(result.state==='UNDONE'?' The observed manifest matched the journal’s recorded restored state.':' This comparison does not mean the moves have been undone.')+' Stored status does not check for later file changes.'}
 else{$('current-summary').textContent=prefix+'terminal comparison unverified. Refresh and inspect the evidence before drawing a conclusion.'}
}
function controls(){
 const pending=busy||serverBusy||cancelPending,locked=pending||!snapshotUsable();
 $('scan').disabled=locked||!run||status?.scan_ready!==true;
 $('refresh').disabled=busy||cancelPending||!run||(serverBusy&&!pollExpired)||status?.state==='CANCELLED';
 $('recover').disabled=locked||!plan;
 $('cancel').disabled=cancelPending||admittingCopy||!session||!run||(!status&&!serverBusy)||(['APPLIED','UNDONE','CONFLICT','CANCELLED','ERROR','ERROR_INTERRUPTED','REJECTED'].includes(status?.state)&&!(busy&&activeAction==='undo'&&status?.state==='APPLIED'));
 $('create').disabled=busy||cancelPending||!session||!!run;
 $('newcopy').disabled=busy||cancelPending||!session||!run;
 $('switch').disabled=busy||cancelPending||!session||!$('existing').value;
 $('preview').disabled=locked||!scan||!!plan;
 $('consent').disabled=locked||!plan||result?.state!=='AWAITING_APPROVAL';
 $('approve').disabled=locked||!plan||!$('consent').checked||result?.state!=='AWAITING_APPROVAL';
 $('apply').disabled=locked||forwardFailed()||!result?.approval||!['APPROVED','APPLYING'].includes(result?.state);
 const forwardResume=result?.mode==='FORWARD'&&result?.approval&&['APPROVED','APPLYING'].includes(result?.state);
 const reverseResume=(result?.mode==='UNDO'||result?.state==='PARTIALLY_APPLIED')&&!['APPLIED','UNDONE','CONFLICT'].includes(result?.state);
 $('resume').textContent='Resume recorded direction';
 $('resume').disabled=locked||!plan||!(forwardResume||reverseResume);
 $('undo').disabled=locked||!result?.after_sha||result?.mode==='UNDO'||!['APPLIED','CONFLICT'].includes(result?.state);
 $('conflict').disabled=locked||result?.state!=='APPLIED';
 $('workflow').setAttribute('aria-busy',String(busy||cancelPending||(serverBusy&&!pollExpired)));
 const link=$('export');link.hidden=!exportUsable();link.textContent=status?.export_kind==='failure_receipt'?'Download failure evidence':'Download recorded evidence ZIP';if(!link.hidden)link.href='/runs/'+run+'/export';else link.removeAttribute('href');
 if(result)outcome();else for(const id of ['mode-role','recorded-summary','current-summary'])$(id).replaceChildren();
 guidance();
}
function resetCopy(id){
 run=id;scan=plan=result=status=null;serverBusy=false;needsRefresh=true;pollExpired=false;eventAfter=0;eventRows=[];scanReceivedAt='';cancelRequested=false;statusRequest++;
 if(run)localStorage.setItem('folderundo-run',run);else localStorage.removeItem('folderundo-run');
 $('identity').textContent=run?'Selected copy: '+run+' · status unverified':'No copy selected · status unverified';
 $('consent').checked=false;
 for(const id of ['files','plan','operations','conflicts','counts','drift','events','policy-context','provider-receipt','mode-role','recorded-summary','current-summary'])$(id).replaceChildren();
 $('state').textContent=run?'Stored status has not been read. Existing copies are retained.':'Ready to create a synthetic copy.';
 controls();
}
function clearUnavailableObservation(){
 scan=plan=result=status=null;needsRefresh=true;pollExpired=true;eventAfter=0;eventRows=[];scanReceivedAt='';statusRequest++;
 $('identity').textContent=run?'Selected copy: '+run+' · status unverified':'No copy selected · status unverified';
 $('consent').checked=false;
 for(const id of ['files','plan','operations','conflicts','counts','drift','events','policy-context','provider-receipt','mode-role','recorded-summary','current-summary'])$(id).replaceChildren();
 $('state').textContent='Current state unavailable. Refresh to check the journal. Existing copies and their recorded evidence are retained; use Preserve existing copies and create new for another copy.';
}
function providerCopy(live){
 return live?{
  intro:'Try this synthetic copy lab: review destinations, approve an exact plan, then inspect the result and Undo. No personal folder is connected. B1 sends registered synthetic text to NVIDIA Nemotron on Nebius; B0 and reference modes make no model calls.',
  authority:'You approve the exact paths and hashes. The executor performs only approved moves. B1 model output proposes destinations and never authorizes movement. B2 remains locked.',
  notice:'B1 uses nvidia/Nemotron-3.5-Super-VL through the authorized Nebius broker. Only registered synthetic filenames and excerpts are sent. B0 and reference modes make no model calls. A failed proposal is retained and does not authorize a move.',
  badge:'LINUX · OWNER LOCAL SESSION · SYNTHETIC LAB · NVIDIA / NEBIUS LIVE BROKER'
 }:{
  intro:'Try this local synthetic copy lab: review destinations, approve an exact plan, then inspect the result and Undo. No personal folder is connected; the available B0 and reference modes make no model calls.',
  authority:'You approve the exact paths and hashes. The executor performs only approved moves. B1/B2 model modes remain locked while the provider is closed.',
  notice:'No content is sent remotely in the available B0 and reference modes. B1/B2 model modes are locked until provider access is configured.',
  badge:'LINUX · OWNER LOCAL SESSION · SYNTHETIC LAB · PROVIDER CLOSED'
 };
}
function brokerBudgetText(b){
 const missing=' Local broker budget snapshot unavailable.';
 if(!b||!['admission','reserved'].includes(b.stage)||
    !['calls','call_cap','committed_nusd','cap_nusd'].every(k=>Number.isSafeInteger(b[k])&&b[k]>=0)||
    b.cap_nusd===0||typeof b.alert_80!=='boolean')return missing;
 const alert=BigInt(b.calls)*5n>=BigInt(b.call_cap)*4n||BigInt(b.committed_nusd)*5n>=BigInt(b.cap_nusd)*4n;
 if(alert!==b.alert_80)return missing;
 const usd=n=>(n/1e9).toFixed(9);
 return ' Local broker snapshot ('+b.stage+'): '+(alert?'80% budget warning. ':'')+
  'Calls '+b.calls+'/'+b.call_cap+'; shared estimated commitment $'+usd(b.committed_nusd)+' / $'+usd(b.cap_nusd)+
  ' cap, including reservations at that point in time. This is not an account balance, invoice, funding confirmation or spending permission.';
}
function providerReceiptText(s){
 const evidence=s.context?.provider_evidence||s.context?.preview?.provider_evidence;
 const receipt=evidence?.response?.receipt;
 if(!receipt)return '';
 const input=receipt.usage?.prompt_tokens??'unknown',output=receipt.usage?.completion_tokens??'unknown';
 const latency=typeof receipt.latency_ms==='number'?receipt.latency_ms.toFixed(1)+' ms':'unknown';
 const validation=s.error?.code?' · App result: '+s.error.code:'';
 return 'Recorded NVIDIA / Nebius model call: '+(receipt.model_returned||receipt.model_requested||'unknown')+' · Request '+receipt.request_id+' · Usage '+input+' input / '+output+' output tokens · '+receipt.usage_status+' · Provider latency '+latency+validation+brokerBudgetText(receipt.budget_snapshot)+'. A model call does not authorize file movement. Apply and Undo make no model calls.';
}
function renderStatus(s){
 const oldPlanSha=plan?.plan_sha;
 status=s;result=s.result||null;mode=s.mode||mode;
 $('provider-receipt').textContent=providerReceiptText(s);
 serverBusy=s.busy===true||(s.state==='CREATED'&&!s.blocked);
 if(s.fixture_id){$('fixture').value=s.fixture_id;$('fixture').onchange()}
 $('identity').textContent='Registered copy: '+run+(s.workspace_id?' · Workspace: '+s.workspace_id:' · Workspace is not yet ready');
 $('policy-context').textContent=s.context?.creation?.classification?(mode==='B1'?'B1 model classification policy: ':'B0 classification policy: ')+s.context.creation.classification.policy_sha+' · Separate execution policy: '+s.context.creation.execution_policy_version:'Reference cycle · Execution policy: synthetic-v1';
 const observation=result?' Last completed observation: '+observedAt()+'.':' No completed observation is available.';
 $('state').textContent='Coordinator state: '+s.state+' · Stage: '+(s.stage||'unreported')+' · Provider '+(s.provider||'CLOSED')+'.'+(serverBusy?' Queued or running work; displayed records may be historical.':s.blocked?' Observation blocked. Refresh journal to inspect retained records.':s.snapshot_current===true?' Stored observation available.':' Stored observation is historical; refresh required.')+observation+' Reading stored status does not inspect files or reconcile work.';
 const events=new Map(eventRows.map(e=>[e.seq,e]));for(const e of s.events||[])events.set(e.seq,e);
 eventRows=[...events.values()].sort((a,b)=>a.seq-b.seq).slice(-200);
 if(Number.isSafeInteger(s.next_after))eventAfter=Math.max(eventAfter,s.next_after);
 $('events').textContent=eventRows.map(e=>`${e.seq} · ${e.kind}`).join('\n');
 plan=result?.plan?(typeof result.plan==='string'?JSON.parse(result.plan):result.plan):null;
 if(oldPlanSha!==plan?.plan_sha||result?.state!=='AWAITING_APPROVAL')$('consent').checked=false;
 if(result){
  table('operations',['Direction / state','From → To','Expected identity'],(result.operations||[]).map(o=>[o.direction+' / '+o.state,o.from_path+' → '+o.to_path,o.expected]));
  outcome();
  table('conflicts',['Conflict path','Reason','Expected / current'],(result.conflicts||[]).map(c=>[c.path,c.reason,JSON.stringify({expected:c.expected,current:c.current})]),conflictNotes(result));
  $('drift').textContent=result.current_drift?.detected?'Drift recorded in the completed observation at '+observedAt()+': '+JSON.stringify(result.current_drift)+'. The recorded terminal state describes history, not exact restoration of the current files.':'';
 }else for(const id of ['operations','conflicts','counts','drift','mode-role','recorded-summary','current-summary'])$(id).replaceChildren();
 if(plan){table('plan',['Source → Destination','Source SHA-256','Reason'],plan.operations.map(o=>[o.src+' → '+o.dst,o.expected.sha256,o.proposal.reason]));const p=document.createElement('p');p.textContent='Exact plan SHA-256: '+plan.plan_sha;$('plan').append(p)}else $('plan').replaceChildren();
 if(s.error)$('message').textContent=errorText(s.error);
 controls();
}
async function readStatus(timeoutMs=10000){const id=run,request=++statusRequest,s=await api('/runs/'+id+'?after='+eventAfter,undefined,timeoutMs);if(id===run&&request===statusRequest)renderStatus(s);return s}
async function waitForIdle(){
 const deadline=performance.now()+POLL_LIMIT_MS;
 pollExpired=false;
 try{for(;;){
  if(stopFollowup())return status;
  const remaining=deadline-performance.now();
  if(remaining<=0){pollExpired=true;needsRefresh=true;throw new Error('Stopped observing after 190 seconds. The worker has its own 180-second queue-included deadline; this page does not cancel it or assume success. Existing copies are retained. Refresh journal to inspect the recorded outcome.')}
  const s=await readStatus(Math.min(10000,remaining));
  if(stopFollowup())return status;
  if(!serverBusy){if(s.blocked||s.snapshot_current!==true||!s.result){needsRefresh=true;throw new Error(s.error?errorText(s.error):'No usable completed observation is available. Existing copies are retained. Refresh journal before choosing an operation.')}return s}
  await new Promise(resolve=>setTimeout(resolve,Math.min(750,Math.max(0,deadline-performance.now()))));
 }}catch(e){pollExpired=true;needsRefresh=true;throw e}
}
async function getScan(){
 if(stopFollowup())return;
 if(!status?.workspace_id||status?.scan_ready!==true||serverBusy||status?.blocked)throw new Error('The workspace is not ready for a file scan. Refresh journal after queued work finishes.');
 const value=await api('/runs/'+run+'/scan');if(stopFollowup())return;scan=value;scanReceivedAt=new Date().toISOString();
 const note=document.createElement('p');note.textContent='Last file scan received at '+scanReceivedAt+'. These rows describe that completed read; they do not monitor later changes.';
 table('files',['Path / eligibility','Bytes / source SHA-256'],scan.files.map(f=>[f.relative_path+' / '+f.status,f.size_bytes+' / '+f.sha256]),[note]);
 await readStatus();
}
async function refresh(){
 if(stopFollowup())return;
 needsRefresh=true;
 await api('/runs/'+run+'/refresh',{});
 if(stopFollowup())return;
 const s=await readStatus();
 if(serverBusy||s.blocked||s.snapshot_current!==true||!s.result)throw new Error(s.error?errorText(s.error):'Refresh did not establish an available completed observation. Existing copies are retained.');
 needsRefresh=false;pollExpired=false;outcome();controls();
}
async function refreshAndScan(){await refresh();if(!stopFollowup()&&status?.scan_ready===true)await getScan()}
async function observeOperation(){
 if(stopFollowup())return;
 await readStatus();
 if(serverBusy)await waitForIdle();
 if(stopFollowup())return;
 if(status?.snapshot_current===true&&!status?.blocked&&result){needsRefresh=false;outcome()}
 else needsRefresh=true;
 if(status?.error)$('message').textContent=errorText(status.error);
}
async function act(fn,action=''){
 if(busy||cancelPending)return;
 const previousFocus=document.activeElement;busy=true;activeAction=action;cancelRequested=false;pollExpired=false;controls();$('message').textContent='';
 try{await fn();if(serverBusy&&!stopFollowup())await waitForIdle()}
 catch(e){
  if(!cancelRequested){
  const observationStopped=pollExpired;needsRefresh=true;pollExpired=true;
  if(run&&session&&!observationStopped){try{await readStatus()}catch{clearUnavailableObservation()}}
  else clearUnavailableObservation();
  $('message').textContent=e.message;
  }
 }finally{
  busy=false;activeAction='';controls();
  if(previousFocus?.disabled&&!$('next-link').hidden)$('next-link').focus();
  else if(previousFocus?.isConnected&&!previousFocus.disabled&&previousFocus!==document.body&&document.activeElement===document.body)previousFocus.focus();
 }
}
function createCopy(preserve){return act(async()=>{
 let v;admittingCopy=true;controls();try{v=await api((($('fixture').value==='dev20'||$('fixture').value.startsWith('live-'))?'/runs':'/reference-runs')+(preserve?'/preserve-and-create':''),{fixture_id:$('fixture').value,mode:$('fixture').value.startsWith('live-')?'B1':$('fixture').value==='dev20'?'B0':'reference',config_sha:session.config_shas[$('fixture').value.startsWith('live-')?'B1':$('fixture').value==='dev20'?'B0':'reference']})}finally{admittingCopy=false}
 resetCopy(v.run_id);serverBusy=true;$('state').textContent='Copy creation accepted. Waiting for the queued worker; a registered run is not yet a completed workspace.';controls();
 await waitForIdle();
 if(stopFollowup())return;
 await getScan();
 await refresh();
 await loadSession();
})}
async function openCopy(id){
 resetCopy(id);await readStatus();
 if(serverBusy){await waitForIdle();await getScan();await refresh()}
 // An idle restored run remains a timestamped cached observation until Refresh.
}
$('next-link').onclick=event=>{const target=document.getElementById($('next-link').getAttribute('href')?.slice(1));if(target){event.preventDefault();target.focus();target.scrollIntoView({block:target.id==='conflicts'?'start':'center',behavior:'instant'})}};
$('create').onclick=()=>createCopy(false);$('newcopy').onclick=()=>createCopy(true);
$('switch').onclick=()=>act(()=>openCopy($('existing').value));
$('scan').onclick=()=>act(getScan);
$('refresh').onclick=()=>act(refreshAndScan);
$('cancel').onclick=async()=>{
 if($('cancel').disabled||cancelPending)return;
 const id=run,previousFocus=document.activeElement;cancelPending=true;cancelRequested=true;pollExpired=false;needsRefresh=true;statusRequest++;controls();$('message').textContent='';
 try{await api('/runs/'+id+'/cancel',{},195000);if(id===run){await readStatus();if(status?.snapshot_current===true&&!status?.blocked&&result&&status.state!=='CANCELLED')needsRefresh=false}}
 catch(e){if(id===run){try{await readStatus()}catch{}$('message').textContent=e.message}}
 finally{cancelPending=false;if(serverBusy)pollExpired=true;controls();if(previousFocus?.disabled&&!$('next-link').hidden)$('next-link').focus();else if(previousFocus?.isConnected&&!previousFocus.disabled&&document.activeElement===document.body)previousFocus.focus()}
};
$('preview').onclick=()=>act(async()=>{await api('/runs/'+run+'/preview',{manifest_sha:scan.manifest_sha,policy_version:scan.policy_version,mode});await observeOperation()});
$('consent').onchange=controls;
$('approve').onclick=()=>act(async()=>{const b={};for(const k of ['plan_sha','source_manifest_sha','policy_version','approval_nonce'])b[k]=plan[k];b.operation_ids=plan.operations.map(o=>o.op_id);await api('/runs/'+run+'/approve',b);await observeOperation()});
for(const [id,action] of [['resume','resume'],['apply','apply'],['undo','undo'],['recover','recover'],['conflict','conflict-fixture']])$(id).onclick=()=>act(async()=>{const b=id==='apply'?{approval_id:JSON.parse(result.approval).approval_id}:id==='undo'?{batch_id:plan.batch_id,expected_after_sha:result.after_sha}:{};let failure;try{await api('/runs/'+run+'/'+action,b)}catch(e){failure=e}await observeOperation();if(failure)throw failure},action);
$('fixture').onchange=()=>{$('mode-note').textContent=$('fixture').value.startsWith('live-')?'B1 sends the registered synthetic filename and excerpt to the NVIDIA model. Exactly one proposal call; no automatic retry. Approval and Undo remain local.':$('fixture').value==='dev20'?'B0 uses frozen public intent, purpose patterns, filename rules and an exact project dictionary. Review every proposed destination.':'Reference cycle assigns folders by sorted file order; it is not semantic classification.'};
async function loadSession(){session=await api('/session',undefined,10000);const copy=providerCopy(session.provider==='LIVE_BROKER');$('intro-copy').textContent=copy.intro;$('authority-copy').textContent=copy.authority;$('provider-notice').textContent=copy.notice;$('provider-badge').textContent=copy.badge;if(session.provider==='LIVE_BROKER'){for(const [id,label] of [['live-purchase','B1 · Completed synthetic purchase'],['live-injection','B1 · Consultation with adversarial instructions']]){if(!$('fixture').querySelector('option[value="'+id+'"]')){const o=document.createElement('option');o.value=id;o.textContent=label;$('fixture').append(o)}}}$('existing').replaceChildren();for(const r of session.registered_runs){const option=document.createElement('option');option.value=r.id;option.textContent=r.mode+' · '+r.fixture+' · '+r.id;option.selected=r.id===run;$('existing').append(option)}}
act(async()=>{
 const fragment=new URLSearchParams(location.hash.slice(1)),bootstrap=fragment.get('bootstrap');
 if(bootstrap){history.replaceState(null,'',location.pathname);const r=await fetch('/bootstrap',{method:'POST',headers:{'Content-Type':'application/json','X-Bootstrap-Token':bootstrap},body:'{}'});if(!r.ok)throw new Error('Owner bootstrap expired or invalid. Use the private URL file printed by this running server.')}
 try{await loadSession()}catch{throw new Error('Owner session required. Open the URL from the private bootstrap file printed by the server. Do not share that URL.')}
 $('intent').textContent=session.public_intent_policy;
 const known=session.registered_runs;
 if(!known.some(r=>r.id===run))run=known.length?known[0].id:null;
 if(run)await openCopy(run);else resetCopy(null);
});
