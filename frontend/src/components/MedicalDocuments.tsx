import {useEffect,useState} from 'react';
import {api} from '../api';
import {Link} from '../router';
import {ReviewWorkflow} from './ReviewWorkflow';
import {sydneyDate} from '../dates';
import type {PortalDocument} from '../types';
export function MedicalDocuments({clinician,navigate,version}:{clinician:boolean;navigate:(to:string)=>void;version:number}){
 const [rows,setRows]=useState<PortalDocument[]|null>(null),[error,setError]=useState(''),[expanded,setExpanded]=useState(false);
 useEffect(()=>{let live=true;api.portalDocuments(clinician).then(d=>{if(live)setRows(d.documents);}).catch(e=>{if(live)setError(e.message);});return()=>{live=false;};},[clinician,version]);
 const [selected,setSelected]=useState<string|null>(null);
 const reload=async()=>{setRows((await api.portalDocuments(clinician)).documents);};
 const current=rows?.find(d=>d.id===selected);
 const visible=rows?(expanded?rows:rows.slice(0,10)):[];
 return <section className="medical-documents"><div className="portal-page-heading"><div><h1 className="page-title">Documents</h1><p className="metadata">Medical reports, letters and uploaded documents in one place.</p></div><Link to="/add" navigate={navigate} className="btn">Upload document</Link></div>
  {current?.workflow_issue?<section className="document-release" aria-label="Document review and release"><div className="portal-page-heading"><h2 className="section-heading">{current.title}</h2><button className="btn" onClick={()=>setSelected(null)}>Close</button></div><a className="source-link" href={current.url??undefined} target="_blank" rel="noreferrer">Open original document ↗</a><ReviewWorkflow key={current.id} issue={current.workflow_issue} onChanged={reload} initialMode={current.workflow_issue.workflow?.reviewed?'release':''}/></section>:null}
  {error?<p role="alert">{error}</p>:!rows?<p role="status">Loading documents…</p>:<>
   <p className="metadata document-count">Showing {visible.length} of {rows.length} documents</p>
   {!rows.length?<p className="surface">No documents have been added.</p>:<ul className="medical-document-list">{visible.map(d=><li key={d.id}>
    <div><h2 className="section-heading">{d.accessible&&d.url?<Link to={d.url} navigate={navigate}>{d.title}</Link>:d.title}</h2><p className="metadata">{d.author} · {d.date?sydneyDate(d.date):'Document date not supplied'}{d.received_date?' · Received '+sydneyDate(d.received_date):''}</p>{d.status==='Released earlier edition'?<p className="metadata">Earlier released edition</p>:null}{d.release_message?<p className="metadata">{d.release_message}</p>:null}</div>
    <div className="document-actions">{clinician&&d.workflow_issue?<button className="btn" onClick={()=>setSelected(d.id)}>{d.released?'Released · manage':'Release to patient file'}</button>:null}{d.accessible&&d.url?<Link to={d.url} navigate={navigate} className="source-link">Open <span aria-hidden="true">↗</span></Link>:<span className="badge">Awaiting clinician release</span>}</div>
   </li>)}</ul>}
   {rows.length>10?<button className="text-button" onClick={()=>setExpanded(!expanded)}>{expanded?'Show first 10 documents':`View all documents (${rows.length})`}</button>:null}
   {!clinician&&rows.some(d=>!d.accessible)?<p className="metadata">Unreleased clinician documents are shown by name only. Their contents become available after clinician release.</p>:null}
  </>}
 </section>;
}
