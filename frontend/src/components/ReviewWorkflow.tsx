import { useState, useEffect, useRef } from 'react';
import { api } from '../api';
import { sydneyTimestamp } from '../dates';
import type { ReviewIssue,WorkflowEvent } from '../types';
export const priorityLabels={urgent:'Urgent',attention:'Needs attention',routine:'Routine / not urgent',untriaged:'Untriaged'};
export function Priority({issue}:{issue:ReviewIssue}) {return <span className={`priority priority-${issue.priority??'untriaged'}`}>{priorityLabels[issue.priority??'untriaged']}</span>;}
export function WorkflowIndicators({issue}:{issue:ReviewIssue}){
 const w=issue.workflow;if(!w)return null;
 return <span className="workflow-indicators">
  <span>{w.reviewed?'Reviewed':'Not reviewed'}</span><span>{w.communications.some(c=>c.kind==='communication')?'Communication recorded':'Not notified'}</span>
  <span>{issue.contributions.some(c=>c.access==='clinical')?(w.releases.length?`Released (${new Set(w.releases.map(r=>r.source)).size} document${new Set(w.releases.map(r=>r.source)).size===1?'':'s'})`:'Not released'):'Own submissions · no release required'}</span>
  {w.actions.some(a=>!a.resolved)?<span>{w.actions.some(a=>!a.resolved&&a.kind==='appointment_request')?(w.actions.some(a=>!a.resolved&&a.booking)?'Appointment booked · discussion pending':'Appointment requested · action outstanding'):'Clinical action outstanding'}</span>:null}
  {w.communications.some(c=>c.method==='in_app'||c.kind==='appointment_request')?<span>{w.acknowledgements.length?'Demo acknowledgement recorded':'Sent · acknowledgement not recorded'}</span>:null}
 </span>;
}
const recipientsLabel=(values:string[])=>values.map(v=>v==='patient'?'Jane Doe':'Emily Doe (authorised demo carer)').join(' and ');
function eventDescription(a:WorkflowEvent){
 if(a.kind==='review')return `Reviewed ${a.sources?.length??0} source(s)`;
 if(a.kind==='priority')return `Priority: ${priorityLabels[a.priority as keyof typeof priorityLabels]} · ${a.basis}`;
 if(a.kind==='release')return `Released selected document to ${recipientsLabel(a.recipients??[])}`;
 if(a.kind==='action_created')return `Clinical action: ${a.description} · ${a.timeframe}`;
 if(a.kind==='action_resolved')return `Linked action resolved · ${a.basis}`;
 if(a.kind==='appointment_request')return `Appointment requested, not booked · ${a.timeframe} · ${recipientsLabel(a.recipients??[])}`;
 return `${a.method==='in_app'?'In-app message sent':a.method==='phone'?'Phone communication (clinician-recorded)':'In-person communication (clinician-recorded)'} · ${recipientsLabel(a.recipients??[])}${a.communication_at?' · '+sydneyTimestamp(a.communication_at):''}`;
}
export function ReviewWorkflow({issue,onChanged,initialMode=""}:{issue:ReviewIssue;onChanged:()=>Promise<void>;initialMode?:string}){
 const root=useRef<HTMLElement>(null);
 const w=issue.workflow;
 const [mode,setMode]=useState(initialMode);const [priority,setPriority]=useState(issue.workflow?.priority?(issue.priority??'untriaged'):'untriaged');const [basis,setBasis]=useState('');
 const [recipients,setRecipients]=useState<string[]>(['patient']);const [method,setMethod]=useState('phone');const [stamp,setStamp]=useState('');
 const [description,setDescription]=useState('');
 const [message,setMessage]=useState('');const [timeframe,setTimeframe]=useState('');const [source,setSource]=useState(issue.contributions.find(c=>c.access==='clinical')?.id??'');
 const [preview,setPreview]=useState<Record<string,unknown>|null>(null);const [busy,setBusy]=useState(false);const [error,setError]=useState('');const [status,setStatus]=useState('');
 useEffect(()=>{if(!mode)return;const frame=requestAnimationFrame(()=>{const target=preview?root.current?.querySelector<HTMLElement>('.workflow-preview'):root.current?.querySelector<HTMLElement>('.workflow-form input, .workflow-form textarea, .workflow-form select');target?.focus();});return()=>cancelAnimationFrame(frame);},[mode,Boolean(preview)]);
 if(!w)return null;
 const toggle=(next:string)=>{setMode(mode===next?'':next);setPriority(issue.workflow?.priority?(issue.priority??'untriaged'):'untriaged');setPreview(null);setMessage('');setError('');setStatus('');};
 async function save(body:Record<string,unknown>){
  if(busy)return;setBusy(true);setError('');
  try{const result=await api.reviewAction(issue.id,{...body,request_id:crypto.randomUUID()});await onChanged();setPreview(null);setMode('');setStatus(result.duplicate?'Already recorded; no duplicate created.':'Saved to the audit history.');}
  catch(e){setError((e as Error).message);}finally{setBusy(false);}
 }
 function prepare(e:React.FormEvent){e.preventDefault();
  if(!recipients.length){setError('Select at least one recipient.');return;}
  let communication_at:string|undefined;
  if(mode==='communication'&&method!=='in_app'){
   const value=new Date(stamp);if(!stamp||!/(Z|[+-]\d{2}:\d{2})$/.test(stamp)||Number.isNaN(value.getTime())){setError('Enter the actual communication date and time, including timezone.');return;}communication_at=value.toISOString();
  }
  setError('');setPreview({kind:mode,recipients:[...recipients],message,method,communication_at,timeframe,source,confirmed:true});
 }
 return <section ref={root} className="workflow-controls" aria-label="Clinician checklist">
  <h3 className="section-heading">Review checklist</h3>
  <div className="review-checklist">
   <label><input type="checkbox" checked={w.reviewed} disabled={busy||w.reviewed} onChange={e=>{if(e.target.checked)save({kind:'review',sources:issue.contributions.map(c=>c.id)});}}/>Reviewed</label>
   <label><input type="checkbox" checked={w.communications.some(c=>c.kind==='communication')} disabled={busy} onChange={()=>toggle('communication')}/>Patient/carer notified</label>
  </div>
  <div className="priority-select"><label htmlFor={'priority-'+issue.id}>Clinical priority</label><select id={'priority-'+issue.id} className="field" value={priority} onChange={e=>{setPriority(e.target.value as typeof priority);setMode('priority');setPreview(null);setStatus('');}}>{Object.entries(priorityLabels).map(([k,v])=><option key={k} value={k}>{k==='untriaged'?'Not yet assessed':v}</option>)}</select></div>
  {issue.workflow?.priority?<p className="metadata priority-assessment">{issue.workflow.priority.actor.name} · {sydneyTimestamp(issue.workflow.priority.at)}</p>:null}
  <div className="review-primary-actions">
   <button className="btn btn-primary" disabled={busy} onClick={()=>toggle('appointment_request')}>Notify patient/carer</button>
   <span className="metadata">Request an appointment to discuss this item</span>
   {issue.contributions.some(c=>c.access==='clinical')?<><button className="btn" disabled={busy||!issue.contributions.some(c=>c.access==='clinical'&&w.reviewed_sources.includes(c.id))} onClick={()=>toggle('release')}>Release to patient file</button>{!issue.contributions.some(c=>c.access==='clinical'&&w.reviewed_sources.includes(c.id))?<span className="metadata">Available after review</span>:null}</>:<span className="metadata">Own submissions remain accessible without release</span>}
  </div>
  {w.actions.some(a=>!a.resolved)?<p className="metadata">{w.actions.filter(a=>!a.resolved).length} outstanding action{w.actions.filter(a=>!a.resolved).length===1?'':'s'}</p>:null}
  {w.communications.some(c=>c.method==='in_app'||c.kind==='appointment_request')?<p className="metadata">{w.acknowledgements.length?'Demo acknowledgement recorded; understanding not verified.':'In-app request/message sent · acknowledgement not recorded.'}</p>:null}
  {w.releases.length?<p className="metadata">Released document(s): {new Set(w.releases.map(r=>r.source)).size}. Notification is recorded separately.</p>:null}
  {error?<p role="alert" className="login-error">{error}</p>:null}{status?<p role="status">{status}</p>:null}
  {mode==='action_created'?<form className="workflow-form" onSubmit={e=>{e.preventDefault();save({kind:'action_created',description,timeframe});}}>
   <label>Clinical action description<textarea className="field" required maxLength={1000} value={description} onChange={e=>setDescription(e.target.value)}/></label>
   <label>Requested timeframe<input className="field" required maxLength={200} value={timeframe} onChange={e=>setTimeframe(e.target.value)}/></label>
   <p className="metadata">Internal action only. This does not send a patient message or book an appointment.</p>
   <button className="btn" disabled={busy}>Save clinical action</button>
  </form>:null}
  {mode==='priority'?<form className="workflow-form" onSubmit={e=>{e.preventDefault();save({kind:'priority',priority,basis});}}>
   <label>Basis for assessment<textarea className="field" required maxLength={1000} value={basis} onChange={e=>setBasis(e.target.value)}/></label>
   <button className="btn" disabled={busy}>Save priority assessment</button>
  </form>:null}
  {['communication','appointment_request','release'].includes(mode)&&!preview?<form className="workflow-form" onSubmit={prepare}>
   <h3 className="section-heading">{mode==='release'?'Release one selected document':mode==='appointment_request'?'Appointment request':'Actual communication'}</h3>
   {mode==='release'?<><label>Document to share<select className="field" value={source} onChange={e=>setSource(e.target.value)}>{issue.contributions.filter(c=>c.access==='clinical').map(c=><option key={c.id} value={c.id}>{c.headline}</option>)}</select></label>
    {!w.reviewed_sources.includes(source)?<p className="metadata">Review the selected document before release.</p>:null}</>:null}
   <fieldset><legend>Recipients</legend>{['patient','carer'].map(r=><label className="check-label" key={r}><input type="checkbox" checked={recipients.includes(r)} onChange={e=>setRecipients(e.target.checked?[...recipients,r]:recipients.filter(x=>x!==r))}/>{r==='patient'?'Jane Doe':'Emily Doe · authorised demo carer'}</label>)}</fieldset>
   <p className="metadata">Recipients are recorded in one shared patient/carer demo view. Individual recipient access is simulated.</p>
   {mode==='communication'?<><label>Method<select className="field" value={method} onChange={e=>setMethod(e.target.value)}><option value="phone">Phone · clinician-recorded</option><option value="in_person">In person · clinician-recorded</option><option value="in_app">In-app message</option></select></label>
    {method!=='in_app'?<label>Actual communication time, including timezone<input className="field" required placeholder="2026-11-02T10:30:00+11:00" value={stamp} onChange={e=>setStamp(e.target.value)}/></label>:null}</>:null}
   {mode==='appointment_request'?<label>Requested timeframe or priority<input className="field" required maxLength={200} value={timeframe} onChange={e=>setTimeframe(e.target.value)}/></label>:null}
   <label>{mode==='communication'&&method!=='in_app'?'Communication record':'Clinician-approved patient-facing message'}<textarea className="field" required={mode!=='release'} maxLength={1000} value={message} onChange={e=>setMessage(e.target.value)}/></label>
   {mode==='appointment_request'?<p className="metadata">A request creates an outstanding action and an in-app notification. It does not book an appointment, record a discussion or release a document.</p>:null}
   <button className="btn" disabled={busy||(mode==='release'&&!w.reviewed_sources.includes(source))}>Preview {mode==='release'?'release':mode==='appointment_request'?'request':'communication'}</button>
  </form>:null}
  {preview?<div className="workflow-preview" tabIndex={-1} aria-label="Preview before confirmation">
   <h3 className="section-heading">Preview · {mode==='release'?'document release':mode==='appointment_request'?'appointment request':'communication'}</h3>
   <p><strong>To:</strong> {recipientsLabel(preview.recipients as string[])}</p>
   {mode==='release'?<><p>Only this document: <strong>{issue.contributions.find(c=>c.id===preview.source)?.headline}</strong></p>
    <a className="source-link" href={issue.contributions.find(c=>c.id===preview.source)?.source_url} target="_blank" rel="noreferrer">Preview exact original document ↗</a><p className="metadata">No other issue sources or internal notes will be shared. Notification is a separate action.</p></>:null}
   {mode==='appointment_request'?<p>Requested timeframe: {String(preview.timeframe)} · Appointment requested, not booked</p>:null}
   <blockquote className="original-contribution">{String(preview.message||'No accompanying message.')}</blockquote>
   {mode==='communication'&&method==='in_app'?<p className="metadata">Sending does not establish that the recipient read or understood this message.</p>:null}
   <div className="workflow-buttons"><button className="btn btn-primary" disabled={busy} onClick={()=>save(preview)}>Confirm {mode==='release'?'release':mode==='appointment_request'?'appointment request':method==='in_app'?'send message':'communication record'}</button><button className="btn" disabled={busy} onClick={()=>setPreview(null)}>Edit preview</button></div>
  </div>:null}
  {w.actions.some(a=>!a.resolved)?<details><summary>Outstanding linked actions ({w.actions.filter(a=>!a.resolved).length})</summary>{w.actions.filter(a=>!a.resolved).map(a=><form key={a.id} className="workflow-form" onSubmit={e=>{e.preventDefault();const form=new FormData(e.currentTarget);save({kind:'action_resolved',action:a.id,basis:form.get('basis')});}}><p>{a.kind==='appointment_request'?(a.booking?'Appointment booked · discussion pending':'Appointment requested'):a.description} · {a.timeframe} · {sydneyTimestamp(a.at)}</p>{a.booking?<p className="metadata">{a.booking.professional} · {a.booking.date} · {a.booking.time} · <a href={'/clinician/calendar#'+a.booking.id}>View calendar</a></p>:null}<label>Resolution record<input name="basis" className="field" required maxLength={1000}/></label><button className="btn" disabled={busy}>Record action resolution</button></form>)}</details>:null}
  <details className="audit-history"><summary>More actions & audit history ({w.history.length})</summary>
   <div className="workflow-buttons"><button className="btn" disabled={busy} onClick={()=>toggle('communication')}>Record prior communication</button><button className="btn" disabled={busy} onClick={()=>toggle('action_created')}>Add clinical action</button></div>
   <p className="metadata">Review, communication and release are independent. Opening a source does not mark it reviewed. Recording a notification does not release a document.</p>
   <ul>{w.history.map(a=><li key={a.id}><strong>{eventDescription(a)}</strong><p className="metadata">{a.actor.name} · {a.actor.role} · {sydneyTimestamp(a.at)}</p>{a.message?<p>{a.message}</p>:null}</li>)}</ul>
   {w.acknowledgements.map((a,i)=><p key={i} className="metadata">Demo acknowledgement: {a.recipient==='patient'?'Jane':'Emily'} · {sydneyTimestamp(a.at)}. This records acknowledgement, not understanding.</p>)}
  </details>
 </section>;
}
