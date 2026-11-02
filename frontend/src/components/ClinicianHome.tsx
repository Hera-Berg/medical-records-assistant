import { useEffect, useState } from "react";
import { api } from "../api";
import { Link } from "../router";
import type { ClinicianDashboardData } from "../types";
const GROUPS = [["reported","Reported changes"],["documents","New clinical documents"],["medications","Medication changes"],["follow_up","Outstanding follow-up"]] as const;
const dateLabel = (date: string) => new Date(`${date}T12:00:00`).toLocaleDateString("en-AU",{day:"numeric",month:"short",year:"numeric"});
export function ClinicianHome({navigate}: {navigate:(to:string)=>void}) {
  const [data,setData]=useState<ClinicianDashboardData|null>(null);
  const [error,setError]=useState<string|null>(null);
  const [comparison,setComparison]=useState("");
  const [chosen,setChosen]=useState("");
  useEffect(()=>{
    let live=true; setData(null); setError(null);
    api.clinicianDashboard(chosen).then(d=>{if(live){setData(d);setComparison(d.comparison_date??"");}}).catch((e:Error)=>{if(live)setError(e.message);});
    return ()=>{live=false;};
  },[chosen]);
  if(error) return <p role="alert">Could not load the review dashboard: {error}</p>;
  if(!data) return <p role="status">Loading the review dashboard…</p>;
  const hour=new Date().getHours();
  const greeting=hour<12?"Good morning":hour<18?"Good afternoon":"Good evening";
  return <div className="review-dashboard">
    <p className="clinician-greeting">{greeting}, {data.clinician.salutation}</p>
    <section className="surface" aria-label="Patient and consultations">
      <h2 className="section-heading">{data.patient.name}</h2>
      <p className="metadata">Born {dateLabel(data.patient.date_of_birth)} · {data.patient.facility}</p>
      <dl className="data-list">
        <div className="data-row"><dt>Your last completed consultation</dt><dd>{data.last_consultation ? <>{dateLabel(data.last_consultation.date)} · {data.last_consultation.title}</>:"First review of this patient"}</dd></div>
        <div className="data-row"><dt>Upcoming appointment</dt><dd>{data.upcoming ? <>{dateLabel(data.upcoming.date)}{data.upcoming.time ? ` at ${data.upcoming.time}`:""} · {data.upcoming.professional??"Professional not recorded"}<span className="metadata dashboard-line">{data.upcoming.title}</span></>:"No upcoming appointment recorded"}</dd></div>
      </dl>
    </section>
    <div className="review-heading"><h2 className="section-heading">Changes since your last review</h2>
      <form onSubmit={e=>{e.preventDefault();setChosen(comparison);}} className="comparison-form">
        <label>Compare after<input className="field" type="date" required max={data.today} value={comparison} onChange={e=>setComparison(e.target.value)} /></label>
        <button className="btn">Apply date</button>
      </form>
    </div>
    {data.comparison_date ? <>
      <p className="metadata">After {dateLabel(data.comparison_date)} through {dateLabel(data.today)}. Dates refer to the source event, not when a summary was prepared.</p>
      <p className="metadata summary-provenance">{data.summary_kind}</p>
      {GROUPS.map(([key,title])=><section className="surface dashboard-section" key={key} aria-labelledby={`changes-${key}`}>
        <h3 className="section-heading" id={`changes-${key}`}>{title} <span className="badge">{data.groups[key].length}</span></h3>
        {data.groups[key].length ? <ul className="dashboard-rows">{data.groups[key].map(row=><li key={row.id}>
          <h4>{row.headline}</h4><p>{row.summary}</p>
          <p className="metadata">{row.author} · {row.role} · {dateLabel(row.date)}</p>
          <div className="source-row"><span className="metadata">{row.status}</span><Link to={row.source_url} navigate={navigate} className="source-link">{key==="follow_up" ? "View booking source":"View original source"}</Link></div>
        </li>)}</ul>:<p className="metadata">{key==="medications"?"No issued prescription or chart changes recorded in this period.":"No matching entries recorded in this period."}</p>}
      </section>)}
    </>:<p className="surface">First review of this patient. Choose a comparison date before viewing a date-based changes summary.</p>}
    <nav className="dashboard-secondary" aria-label="Full record"><Link to="/clinician/record" navigate={navigate}>Full clinical record and medication list</Link><Link to="/care-team" navigate={navigate}>Care team directory</Link></nav>
  </div>;
}
