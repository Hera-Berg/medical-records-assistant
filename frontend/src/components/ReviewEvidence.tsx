import {Link} from '../router';
import {sydneyDate as dateLabel,sydneyTimestamp} from '../dates';
import type {ReviewIssue} from '../types';
export function ReviewEvidence({issue,navigate}:{issue:ReviewIssue;navigate:(to:string)=>void}){
 return <details className="review-evidence" key={issue.id}>
  <summary>Evidence & source details <span className="metadata">({issue.source_count} {issue.source_count===1?'source':'sources'})</span></summary>
  {['walking','walking-support'].includes(issue.id)?<p className="metadata">Each contribution retains its reporter’s perspective. Jane’s reported requests are not independent verification of a shared cause.</p>:null}
  {[...issue.contributions,...(issue.context??[])].map(row=><section className="evidence-contribution" key={row.id}>
   <h3 className="section-heading">{row.author}{row.context?' · Older contextual source':''}</h3><p className="metadata">{row.role} · {dateLabel(row.date)}</p>
   <dl className="evidence-dates">
    <dt>Event / observation</dt><dd>{row.event_date?row.event_timestamp?sydneyTimestamp(row.event_timestamp):dateLabel(row.event_date):'Observation date not supplied'}</dd>
    {row.recorded_at?<><dt>Recorded</dt><dd>{sydneyTimestamp(row.recorded_at)}</dd></>:null}
    {row.document_date?<><dt>Document dated</dt><dd>{dateLabel(row.document_date)}</dd></>:null}
    <dt>Authored</dt><dd>{row.authored_at?sydneyTimestamp(row.authored_at):row.authored_date?dateLabel(row.authored_date):'Date not recorded'}</dd>
    <dt>Received</dt><dd>{row.received_at?sydneyTimestamp(row.received_at):row.received_date?dateLabel(row.received_date):'Date not recorded'}</dd>
    {row.uploaded_at?<><dt>Uploaded / imported</dt><dd>{sydneyTimestamp(row.uploaded_at)}</dd></>:null}
   </dl>
   {row.ordering?<p className="metadata">{row.ordering}</p>:null}
   {row.outside_period?<p className="metadata">Unresolved action predating the selected period · original date {dateLabel(row.date)}</p>:null}
   <h4 className="section-heading">Original source text</h4>
   {row.original_text?<blockquote className="original-contribution">{row.original_text}</blockquote>:<p className="metadata">Open the original document to read its contents.</p>}
   {row.results?.length?<div className="results-table-wrap"><table className="results-table"><thead><tr><th>Test</th><th>Result</th><th>Units</th><th>Laboratory range</th><th>Source flag / previous result</th></tr></thead><tbody>{row.results.map(r=><tr key={r.test}><th>{r.test}</th><td>{r.result}</td><td>{r.unit}</td><td>{r.reference}</td><td>{r.flag||'None'}</td></tr>)}</tbody></table></div>:null}
   <Link to={row.source_url} navigate={navigate} className="source-link">Open original source →</Link>
   {row.review_status?<p className="metadata">Review status: {row.review_status}</p>:null}
  </section>)}
 </details>;
}
