import {useEffect,useState,useRef} from 'react';
import {api} from '../api';
import {Link} from '../router';
import {sydneyDate,sydneyTimestamp} from '../dates';
import type {SharedCare as Data,AppointmentSlot} from '../types';
const names=(recipients:string[])=>recipients.map(r=>r==='patient'?'Jane':'Emily').join(' and ');
export function SharedCare({navigate}:{navigate:(to:string)=>void;compact?:boolean}){
 const [count,setCount]=useState(0);
 useEffect(()=>{let live=true;const poll=()=>api.sharedCare().then(d=>{if(live)setCount(d.messages.length);}).catch(()=>{});poll();const timer=setInterval(poll,15000);return()=>{live=false;clearInterval(timer);};},[]);
 return count?<section className="surface notification-home"><h2 className="section-heading">Notifications <span className="badge">{count}</span></h2><Link to="/notifications" navigate={navigate} className="source-link">View messages and appointment requests →</Link></section>:null;
}
export function Notifications({navigate}:{navigate:(to:string)=>void}){
 const [data,setData]=useState<Data|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const generation=useRef(0),mounted=useRef(false);
 const refresh=async()=>{const version=++generation.current;const result=await api.sharedCare();if(mounted.current&&version===generation.current){setData(result);setError('');}};
 useEffect(()=>{mounted.current=true;const poll=()=>refresh().catch(e=>{if(mounted.current)setError(e.message);});poll();const timer=setInterval(poll,15000);return()=>{mounted.current=false;generation.current++;clearInterval(timer);};},[]);
 async function acknowledge(message:string,recipient:string){if(busy)return;setBusy(true);setError('');try{await api.acknowledgeCare(message,recipient);await refresh();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 return <section className="notifications-page"><div className="portal-page-heading"><h1 className="page-title">Notifications</h1><button className="text-button" onClick={()=>refresh().catch(e=>setError(e.message))}>Refresh notifications</button></div><p className="metadata">Messages and appointment requests from your care team.</p>
  {error?<p role="alert">{error}</p>:null}{!data?<p role="status">Loading notifications…</p>:<>
   <p className="metadata portal-simulation">{data.simulation}</p>
   {!data.messages.length?<p className="surface">No notifications yet.</p>:data.messages.slice().reverse().map(m=><article className="surface notification-card" key={m.id}>
    <div className="notification-heading"><h2 className="section-heading">{m.kind==='appointment_request'?(m.booking?'Appointment booked':'Appointment requested'):'Message from your care team'}</h2><span className="badge">{m.status}</span></div>
    <p>{m.message}</p>{m.timeframe?<p className="metadata">Requested timeframe: {m.timeframe}</p>:null}
    <p className="metadata">{m.actor.name} · {sydneyTimestamp(m.at)} · To {names(m.recipients)}</p>
    {m.booking?<div className="booking-confirmed"><strong>{sydneyDate(m.booking.date)} · {m.booking.time} · {m.booking.professional}</strong>{m.booking.location?<p className="metadata">{m.booking.location}</p>:null}<Link to={'/calendar#'+m.booking.id} navigate={navigate} className="source-link">View appointment in calendar</Link><p className="metadata">Booking does not mean the report has been discussed.</p></div>:m.kind==='appointment_request'&&!m.action_resolved?<BookAppointment request={m} onBooked={refresh}/>:null}
    <details className="notification-acknowledgements"><summary>Acknowledgement</summary>{m.recipients.map(r=>m.acknowledgements.some(a=>a.recipient===r)?<p className="metadata" key={r}>Demo acknowledgement recorded for {r==='patient'?'Jane':'Emily'}; understanding is not verified.</p>:<button key={r} className="btn" disabled={busy} onClick={()=>acknowledge(m.id,r)}>Record demo acknowledgement as {r==='patient'?'Jane':'Emily'}</button>)}</details>
   </article>)}
  </>}
 </section>;
}
function BookAppointment({request,onBooked}:{request:Data['messages'][number];onBooked:()=>Promise<void>}){
 const [open,setOpen]=useState(false),[slots,setSlots]=useState<AppointmentSlot[]|null>(null),[selected,setSelected]=useState('');
 const [recipient,setRecipient]=useState(request.recipients.includes('patient')?'patient':request.recipients[0]??''),[preview,setPreview]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 async function load(){setOpen(true);setError('');setBusy(true);try{setSlots((await api.appointmentAvailability()).slots);}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 async function confirm(){if(busy)return;setBusy(true);setError('');try{await api.bookAppointment({request:request.id,slot:selected,recipient,confirmed:true,request_id:crypto.randomUUID()});await onBooked();}catch(e){setError((e as Error).message);setPreview(false);try{setSlots((await api.appointmentAvailability()).slots);}catch{/* Keep the original booking error visible. */}}finally{setBusy(false);}}
 const slot=slots?.find(s=>s.id===selected);
 return <section className="appointment-booking">
  {!open?<button className="btn btn-primary" onClick={load}>Choose an appointment with Dr Smith</button>:<>
   <h3 className="section-heading">Dr James Smith · available appointments</h3><p className="metadata">Fictional demonstration times · 30-minute appointments · Australia/Sydney. This is not connected to a real practice schedule. If these times do not meet the requested timeframe, contact your care team.</p>
   {error?<p role="alert">{error}</p>:null}
   {slots===null?<p role="status">Loading times…</p>:!slots.length?<p>No available demonstration times. Please contact your care team.</p>:preview&&slot?<div className="workflow-preview" aria-label="Appointment booking preview">
    <h4 className="section-heading">Confirm your appointment</h4><p><strong>{sydneyDate(slot.date)} · {slot.time} · {slot.professional}</strong></p>{slot.location?<p>{slot.location}</p>:null}<p>Booked by {recipient==='patient'?'Jane':'Emily'} in the shared demo workspace.</p>
    <p className="metadata">This adds the appointment to your calendar. It does not acknowledge the message, record a discussion or release a report.</p>
    <div className="workflow-buttons"><button className="btn btn-primary" disabled={busy} onClick={confirm}>Confirm booking</button><button className="btn" disabled={busy} onClick={()=>setPreview(false)}>Change time</button></div>
   </div>:<form onSubmit={e=>{e.preventDefault();setPreview(true);}} className="booking-form">
    <label>Booking as<select className="field" value={recipient} onChange={e=>setRecipient(e.target.value)}>{request.recipients.map(r=><option key={r} value={r}>{r==='patient'?'Jane Doe':'Emily Doe · authorised demo carer'}</option>)}</select></label>
    <fieldset className="appointment-slots"><legend>Choose a time</legend>{slots.map(s=><label className="appointment-slot" key={s.id}><input type="radio" name={'slot-'+request.id} required value={s.id} checked={selected===s.id} onChange={()=>setSelected(s.id)}/><span>{sydneyDate(s.date)} · {s.time}</span></label>)}</fieldset>
    <div className="workflow-buttons"><button className="btn" disabled={busy||!slot}>Preview appointment</button><button type="button" className="btn" onClick={()=>setOpen(false)}>Cancel</button></div>
   </form>}
  </>}
 </section>;
}
