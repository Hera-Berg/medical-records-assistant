import {useEffect,useState} from 'react';
import {api} from '../api';
import {sydneyTimestamp,sydneyDate} from '../dates';
import type {SubmittedNote} from '../types';
export function SubmittedUpdates({version,onChanged}:{version:number;onChanged:()=>void}){
 const [notes,setNotes]=useState<SubmittedNote[]|null>(null),[error,setError]=useState(''),[editing,setEditing]=useState<string|null>(null),[draft,setDraft]=useState(''),[busy,setBusy]=useState(false);
 useEffect(()=>{let live=true;api.submittedNotes().then(d=>{if(live)setNotes(d.notes);}).catch(e=>{if(live)setError(e.message);});return()=>{live=false;};},[version]);
 async function save(note:SubmittedNote){if(busy)return;setBusy(true);setError('');try{await api.correctSubmittedNote(note.id,draft,note.revision);setNotes((await api.submittedNotes()).notes);setEditing(null);onChanged();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 return <section className="submitted-updates"><h2 className="section-heading">Your submitted notes</h2><p className="metadata">Shared patient/carer entries. Add a correction without changing the original. Individual authorship is not authenticated in this demo.</p>{error?<p role="alert">{error}</p>:null}
  {notes===null?(error?null:<p role="status">Loading your submitted notes…</p>):!notes.length?<p className="metadata">Notes you record here will appear below.</p>:notes.map(note=><article className="surface submitted-note" key={note.id}><p className="metadata">{note.author} · {note.event_date?'Event '+sydneyDate(note.event_date)+' · ':''}Recorded {sydneyTimestamp(note.recorded_at)}{note.corrected_at?' · Corrected '+sydneyTimestamp(note.corrected_at):''}</p>
   {editing===note.id?<form onSubmit={e=>{e.preventDefault();save(note);}}><label htmlFor={'correction-'+note.id}>Corrected note</label><textarea id={'correction-'+note.id} className="field" required maxLength={10000} value={draft} onChange={e=>setDraft(e.target.value)}/><div className="workflow-buttons"><button className="btn" disabled={busy}>Save correction</button><button className="btn" type="button" disabled={busy} onClick={()=>setEditing(null)}>Cancel</button></div></form>:<><p className="submitted-note-text">{note.text}</p><button className="text-button" onClick={()=>{setEditing(note.id);setDraft(note.text);}}>Correct this note</button></>}
   {note.corrected_at?<details><summary className="metadata">Original note retained</summary><blockquote className="original-contribution">{note.original_text}</blockquote></details>:null}
  </article>)}
 </section>;
}
