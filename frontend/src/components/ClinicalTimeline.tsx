import {useEffect,useState} from 'react';
import {api} from '../api';
import {Link} from '../router';
import {sydneyDate,sydneyTimestamp} from '../dates';
export interface ClinicalTimelineEvent {
 id:string;category:string;event_type:string;date:string|null;month:string|null;date_basis:string;
 document_date:string|null;received_date:string|null;author:string;role:string;organisation:string|null;
 title:string;excerpt:string|null;excerpt_truncated:boolean;source_url:string;original_url:string;link_label:string;
}
export interface ClinicalTimelineData {events:ClinicalTimelineEvent[];categories:string[];today:string;default_from:string;timezone:string;limitations:string;}
export function ClinicalTimeline({navigate,version,clinician=true}:{navigate:(to:string)=>void;version:number;clinician?:boolean}){
 const [data,setData]=useState<ClinicalTimelineData|null>(null),[error,setError]=useState('');
 const [category,setCategory]=useState('All'),[range,setRange]=useState('30'),[limit,setLimit]=useState(10);
 useEffect(()=>{let live=true;api.clinicalTimeline(clinician).then(d=>{if(live){setData(d);setError('');}}).catch(e=>{if(live)setError(e.message);});return()=>{live=false;};},[version,clinician]);
 if(error)return <p role="alert">Could not load the timeline: {error}</p>;
 if(!data)return <p role="status">Loading the timeline…</p>;
 const start=new Date(data.today+'T12:00:00Z');start.setUTCDate(start.getUTCDate()-Number(range)+1);
 const from=range==='all'?'':start.toISOString().slice(0,10);
 const all=data.events.filter(e=>category==='All'||e.category===category);
 const filtered=all.filter(e=>!from||(e.date!==null&&e.date.slice(0,10)>=from&&e.date.slice(0,10)<=data.today));
 const visible=filtered.slice(0,limit),months=[...new Set(visible.map(e=>e.month))];
 const more=filtered.length>limit,older=range!=='all'&&all.some(e=>!e.date||e.date.slice(0,10)<from);
 return <section className="clinical-timeline" aria-label={clinician?"Clinical timeline":"Patient timeline"}>
  <div className="timeline-toolbar">
   <div className="timeline-filters" role="group" aria-label="Event type">{['All',...data.categories].map(c=><button key={c} className={'btn '+(category===c?'btn-primary':'')} aria-pressed={category===c} onClick={()=>{setCategory(c);setLimit(10);}}>{c}</button>)}</div>
   <label className="timeline-range"><span className="sr-only">Date range</span><select className="field" aria-label="Date range" value={range} onChange={e=>{setRange(e.target.value);setLimit(10);}}><option value="30">Last 30 days</option><option value="90">Last 90 days</option><option value="365">Last year</option><option value="all">All history</option></select></label>
  </div>
  <p className="metadata timeline-count">Showing {visible.length} of {filtered.length} events · {range==='all'?'All history':sydneyDate(from)+' – '+sydneyDate(data.today)} · Australia/Sydney</p>
  {!visible.length?<p className="metadata">No events match this period and event type.</p>:months.map(month=><section className="timeline-month" key={month??'undated'}>
   <h2 className="section-heading">{month?new Intl.DateTimeFormat('en-AU',{month:'long',year:'numeric',timeZone:'Australia/Sydney'}).format(new Date(month+'-01T12:00:00Z')):'Date not recorded'}</h2>
   <ol>{visible.filter(e=>e.month===month).map(e=><li key={e.id} className="clinical-timeline-row">
    <div className="timeline-event-date"><time dateTime={e.date??undefined}>{e.date?(e.date.includes('T')?sydneyTimestamp(e.date):sydneyDate(e.date)):'Date not recorded'}</time><span className="metadata">{e.date_basis}</span></div>
    <div className="timeline-event-content"><span className="timeline-event-type">{e.event_type}</span><h3>{e.category==='Test results'?e.title:<>{e.author} <span>· {e.role}</span></>}</h3>
     {e.category==='Test results'?<p className="metadata">{e.author} · {e.role}</p>:null}
     {e.organisation&&e.organisation!==e.author?<p className="metadata">{e.organisation}</p>:null}
     {e.excerpt?<p className="timeline-excerpt">{e.excerpt}{e.excerpt_truncated?'…':''}</p>:e.category!=='Test results'?<p className="timeline-document-title">{e.title}</p>:null}
     {e.category==='Letters'&&e.received_date&&e.document_date&&e.document_date!==e.received_date?<p className="metadata">Document dated {sydneyDate(e.document_date)} · received {sydneyDate(e.received_date)}</p>:null}
     {e.category==='Test results'?<p className="metadata">{e.document_date?'Report dated '+sydneyDate(e.document_date):'Report date not recorded'} · {e.received_date?'Received '+sydneyDate(e.received_date):'Receipt date not recorded'}</p>:null}
     {e.original_url.startsWith('/api/')?<a className="source-link timeline-source" href={e.original_url} target="_blank" rel="noreferrer">{e.link_label} ↗</a>:<Link className="source-link timeline-source" to={e.source_url} navigate={navigate}>{e.link_label} →</Link>}
    </div>
   </li>)}</ol>
  </section>)}
  {more||older?<div className="timeline-load"><button className="btn" onClick={()=>{if(!more)setRange('all');setLimit(limit+10);}}>Load earlier events</button>{!more&&older?<p className="metadata">Includes history before the selected period.</p>:null}</div>:null}
  <details className="timeline-about"><summary className="metadata">About dates and sources</summary><p className="metadata">One entry per original document or note. Source dates are shown as recorded; upload dates do not establish when a condition began. {data.limitations}</p></details>
 </section>;
}
