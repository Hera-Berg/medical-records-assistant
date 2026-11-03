import { useEffect, useState, useRef } from 'react';
import { isDemoRecord } from "../record";
import { useComparison } from '../comparison';
import { sydneyDate as dateLabel, sydneyTimestamp, SYDNEY } from '../dates';
import { api } from '../api';
import { Link } from '../router';
import type { ClinicianDashboardData, ReviewIssue } from '../types';
import { ReviewWorkflow, Priority } from './ReviewWorkflow';
import { ReviewEvidence } from './ReviewEvidence';
import { reviewTitle, reviewKind, reviewSummary, isAbnormal } from '../reviewPresentation';

export function ClinicianHome({navigate,issueId}:{navigate:(to:string)=>void;issueId?:string}) {
 const [data,setData]=useState<ClinicianDashboardData|null>(null),[error,setError]=useState<string|null>(null);
 const [comparison,setComparison]=useState(''),[datesOpen,setDatesOpen]=useState(false);
 const {chosen,setChosen,expanded,setExpanded,scroll,setScroll,tab,setTab,selected,setSelected}=useComparison();
 const [mobile,setMobile]=useState(()=>matchMedia('(max-width: 1023px)').matches);
 const panel=useRef<HTMLElement>(null);
 useEffect(()=>{const query=matchMedia('(max-width: 1023px)');const update=()=>setMobile(query.matches);query.addEventListener('change',update);return()=>query.removeEventListener('change',update);},[]);
 useEffect(()=>{
  let live=true;setError(null);
  api.clinicianDashboard(chosen).then(d=>{if(live){setData(d);setComparison(d.comparison_date??'');}}).catch((e:Error)=>{if(live)setError(e.message);});
  return()=>{live=false;};
 },[chosen]);
 const filtered=data?.issues.filter(i=>!data.workflow_enabled || (tab==='reviewed'?i.workflow?.reviewed:tab==='action_required'?i.workflow?.actions.some(a=>!a.resolved):!i.workflow?.reviewed))??[];
 const routeId=issueId?decodeURIComponent(issueId):'';
 const selectedIssue=data?.issues.find(i=>i.id===(routeId||selected))??(!routeId?filtered[0]:undefined);
 useEffect(()=>{if(!routeId&&!selected&&filtered[0])setSelected(filtered[0].id);},[routeId,selected,filtered[0]?.id,setSelected]);
 useEffect(()=>{
  if(!mobile||!issueId||!data)return;
  panel.current?.focus();const previous=document.body.style.overflow;document.body.style.overflow='hidden';
  return()=>{document.body.style.overflow=previous;};
 },[mobile,issueId,Boolean(data)]);
 useEffect(()=>{if(data&&(!issueId||!mobile)){const frame=requestAnimationFrame(()=>window.scrollTo(0,scroll));return()=>cancelAnimationFrame(frame);}},[Boolean(data),issueId,mobile]);
 const reload=async()=>{setData(await api.clinicianDashboard(chosen));};
 if(error&&!data)return <p role="alert">Could not load the review dashboard: {error}</p>;
 if(!data)return <p role="status">Loading the review dashboard…</p>;
 const open=(issue:ReviewIssue)=>{setScroll(window.scrollY);setSelected(issue.id);navigate('/clinician/issue/'+encodeURIComponent(issue.id));};
 const close=()=>navigate('/clinician');
 const hour=Number(new Intl.DateTimeFormat('en-GB',{timeZone:SYDNEY,hour:'2-digit',hourCycle:'h23'}).format(new Date()));
 const greeting=hour<12?'Good morning':hour<18?'Good afternoon':'Good evening';
 const visible=expanded?filtered:filtered.slice(0,5);
 const endDate=data.today;
 const startDate=data.comparison_date;
 const periodLabel=startDate?`${new Date(startDate+'T00:00:00Z').toLocaleDateString('en-AU',{timeZone:SYDNEY,day:'numeric',month:'short',...(startDate.slice(0,4)!==endDate.slice(0,4)?{year:'numeric'}:{})})} – ${dateLabel(endDate)}`:'First review of this patient';
 return <div className="review-dashboard compact-review">
  <p className="clinician-greeting">{greeting}, {data.clinician.salutation}</p>
  <div className="review-heading">
   <h1 className="page-title">Changes since your last appointment</h1>
   <div className="review-period-controls"><span className="comparison-period">{periodLabel}</span><button className="text-button" aria-expanded={datesOpen} aria-controls="review-date-controls" onClick={()=>setDatesOpen(!datesOpen)}>Change dates</button></div>
  </div>
  {datesOpen?<form id="review-date-controls" onSubmit={e=>{e.preventDefault();setScroll(0);setSelected('');setChosen(comparison);if(issueId)close();}} className="comparison-form">
   <label>Compare from<input className="field" type="date" required max={data.today} value={comparison} onChange={e=>setComparison(e.target.value)}/></label>
   <button className="btn">Apply date</button>
   {data.last_consultation?<button type="button" className="btn" onClick={()=>{setScroll(0);setSelected('');setChosen('');setComparison(data.comparison_period.default_date??'');setError(null);if(issueId)close();}}>Use last consultation</button>:null}
   <span className="metadata">Australia/Sydney</span>
  </form>:null}
  {error?<p role="alert" className="login-error">{error}</p>:null}
  <div className="review-workbench">
   <section className="review-list-panel" aria-label="Changes to review">
    {data.workflow_enabled?<div className="review-tabs" role="tablist" aria-label="Review views">{([['needs_review','Needs review'],['action_required','Action required'],['reviewed','Reviewed']] as const).map(([value,label])=><button role="tab" id={'tab-'+value} aria-controls="review-issues" key={value} aria-selected={tab===value} className={tab===value?'active':''} onClick={()=>{setTab(value);setSelected('');setScroll(0);if(issueId)close();}}>{label} <span className="badge">{data.tab_counts?.[value]??0}</span></button>)}</div>:null}
    <div id="review-issues" role="tabpanel" aria-labelledby={data.workflow_enabled?'tab-'+tab:undefined}>
     {data.comparison_date?<>
      <p className="metadata issue-count">{filtered.length} issues · {filtered.reduce((n,i)=>n+i.source_count,0)} sources · Showing {visible.length}</p>
      {visible.length?<ul className="review-issue-list">{visible.map(issue=>{
       const abnormal=isAbnormal(issue),kind=reviewKind(issue),w=issue.workflow;
       return <li key={issue.id} data-summary={issue.id}>
        <button className={`issue-row ${selectedIssue?.id===issue.id?'is-selected':''} ${abnormal?'is-abnormal':''}`} aria-pressed={selectedIssue?.id===issue.id} aria-controls="review-detail" onClick={()=>open(issue)}>
         <span className={`issue-type ${abnormal?'abnormal-label':''}`}>{abnormal?'Abnormal':kind==='Specialist'?'Letter':kind==='Pathology'?'Result':kind}</span>
         <span className="issue-row-content"><strong>{reviewTitle(issue)}</strong><span className="metadata dashboard-line">{issue.section==='reported'?[...new Set(issue.contributions.map(c=>c.author))].join(' / '):kind} · {issue.label?.startsWith('Newly received')?'Received ':''}{dateLabel(issue.display_date)}{issue.source_count>1?` · ${issue.source_count} sources`:''}</span>
          {w&&(w.reviewed||w.communications.length||w.releases.length||w.actions.some(a=>!a.resolved))?<span className="metadata row-state">{[w.reviewed?'Reviewed':null,w.communications.some(c=>c.kind==='communication')?'Communication recorded':null,w.actions.some(a=>!a.resolved)?'Action outstanding':null,w.releases.length?'Released':null].filter(Boolean).join(' · ')}</span>:null}
          {w?.priority?<span className="row-priority"><Priority issue={issue}/></span>:null}
         </span><span className="issue-chevron" aria-hidden="true">›</span><span className="sr-only">View details</span>
        </button>
       </li>;
      })}</ul>:<p className="review-empty metadata">No items in this view.</p>}
      {filtered.length>5?<button className="text-button view-all-changes" onClick={()=>setExpanded(!expanded)}>{expanded?'Show first five changes':`View all ${filtered.length} changes`}</button>:null}
     </>:<p className="review-empty">Choose a comparison date before viewing changes.</p>}
    </div>
   </section>
   <aside id="review-detail" className={`review-detail-panel ${issueId?'is-open':''}`} ref={panel} tabIndex={-1} role={mobile&&issueId?'dialog':'region'} aria-modal={mobile&&issueId?true:undefined} aria-label="Issue details and actions" onKeyDown={e=>{
    if(e.key==='Escape'&&issueId){close();return;}
    if(e.key==='Tab'&&mobile&&issueId){const nodes=Array.from(panel.current?.querySelectorAll<HTMLElement>('a[href],button:not(:disabled),input:not(:disabled),select,textarea,summary')??[]).filter(n=>n.getClientRects().length);const first=nodes[0],last=nodes[nodes.length-1];if(first&&last){if(e.shiftKey&&(document.activeElement===first||document.activeElement===panel.current)){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}}}
   }}>
    {issueId?<Link to="/clinician" navigate={navigate} className="source-link review-detail-close">← Back to review</Link>:null}
    {selectedIssue?<>
     <p className="metadata review-mobile-identity">{data.patient.name} · DOB {dateLabel(data.patient.date_of_birth)}</p>
     <h2 className="page-title">{reviewTitle(selectedIssue)}</h2>
     {isAbnormal(selectedIssue)?<span className="abnormal-label issue-type detail-abnormal">Abnormal</span>:null}
     {reviewSummary(selectedIssue)?<p className="review-issue-summary">{reviewSummary(selectedIssue)}</p>:null}
     {selectedIssue.laboratory_flags?.map(flag=><p key={flag} className="laboratory-flag">{flag}</p>)}
     {selectedIssue.contributions[0]?<a href={selectedIssue.contributions[0].ordered_panel&&selectedIssue.contributions[0].artifact?'/api/clinician/artifact/'+selectedIssue.contributions[0].artifact:selectedIssue.contributions[0].source_url} target="_blank" rel="noreferrer" className="btn review-open-original">{selectedIssue.source_count===1?'Open original report':'Open first original source'} <span aria-hidden="true">↗</span></a>:null}
     <ReviewWorkflow key={selectedIssue.id} issue={selectedIssue} onChanged={reload}/>
     <ReviewEvidence issue={selectedIssue} navigate={navigate}/>
     <p className="metadata review-source-footer">Source: {[...new Set(selectedIssue.contributions.map(c=>c.author))].join(' / ')} · {dateLabel(selectedIssue.display_date)}{selectedIssue.source_count>1?` · ${selectedIssue.source_count} sources`:''}</p>
    </>:<p className="review-empty metadata">{routeId?'This issue is not available in the selected period.':'Select a change to inspect its evidence and actions.'}</p>}
   </aside>
  </div>
  <section className="review-next-appointment" aria-labelledby="upcoming-appointments">
   <h2 id="upcoming-appointments" className="section-heading">Next appointment</h2>
   {data.upcoming_appointments[0]?<span>{dateLabel(data.upcoming_appointments[0].date)}{data.upcoming_appointments[0].time?` · ${data.upcoming_appointments[0].time}`:''} · {data.upcoming_appointments[0].title} <span className="metadata">· {data.upcoming_appointments[0].professional??'Professional not recorded'}</span></span>:<span className="metadata">No upcoming appointments recorded.</span>}
   <Link to="/clinician/calendar" navigate={navigate} className="source-link">View calendar</Link>
   {data.upcoming_appointments.length>1?<details><summary className="metadata">More upcoming appointments ({data.upcoming_appointments.length-1})</summary><ul>{data.upcoming_appointments.slice(1).map(a=><li key={a.id}>{dateLabel(a.date)}{a.time?` · ${a.time}`:''} · {a.title} · {a.professional??'Professional not recorded'}</li>)}</ul></details>:null}
  </section>
  <footer className="review-notice"><span className="metadata">{isDemoRecord()?'':'Fictional demonstration · '}Prepared summaries require clinician review</span><details><summary>About this summary</summary><div className="metadata">
   <p>{data.comparison_period.boundary_note} Through {sydneyTimestamp(data.comparison_period.end_timestamp)} · Australia/Sydney.</p>
   <p>{data.last_consultation?`Your last completed consultation: ${dateLabel(data.last_consultation.date)} · ${data.last_consultation.title}`:'First review of this patient. Select a comparison date.'}</p>
   <p>{data.receipt_limitation} Historical documents newly received are not newly occurring clinical events.</p>
   <p>Explicit laboratory critical flags and clinician-assigned priority determine ordering, followed by recency. Abnormal laboratory flags are separate from clinical urgency. Grouped contributions retain individual perspectives.</p>
  </div></details></footer>
 </div>;
}
