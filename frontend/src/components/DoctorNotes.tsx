/** Progress notes are authorised by the clinician session, with encryption managed by the server. */
import {useEffect,useRef,useState} from 'react';
import {api} from '../api';
import {sydneyDate,sydneyTimestamp,sydneyToday} from '../dates';
import type {ProgressNotes} from '../types';
export function DoctorNotes({version}:{version:number}){
 const [data,setData]=useState<ProgressNotes|null>(null),[error,setError]=useState(''),[text,setText]=useState(''),[written,setWritten]=useState(sydneyToday);
 const [busy,setBusy]=useState(false),[saved,setSaved]=useState(false),[passphrase,setPassphrase]=useState('');
 const token=useRef(crypto.randomUUID());
 useEffect(()=>{let live=true;api.progressNotes().then(d=>{if(live){setData(d);setError('');}}).catch(e=>{if(live)setError(e.message);});return()=>{live=false;};},[version]);
 async function save(e:React.FormEvent){e.preventDefault();if(busy)return;setBusy(true);setError('');setSaved(false);try{setData(await api.saveProgressNote({text:text.trim(),written,request_id:token.current}));setText('');token.current=crypto.randomUUID();setSaved(true);}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 async function importOlder(e:React.FormEvent){e.preventDefault();if(busy)return;setBusy(true);setError('');try{setData(await api.importLegacyProgressNotes(passphrase));setPassphrase('');}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 return <section className="progress-notes"><h1 className="page-title">Progress notes</h1><p className="metadata">Available to signed-in clinicians only. Excluded from patient documents and summaries.</p>
  {error?<p role="alert">{error}</p>:null}{!data?<p role="status">Loading progress notes…</p>:<>
   <details className="surface progress-note-compose"><summary className="section-heading">Add a progress note</summary><form onSubmit={save}>
    <p className="metadata">Your signed-in clinician name and role will be recorded automatically.</p>
    <label>Note date<input className="field" type="date" required max={sydneyToday()} value={written} onChange={e=>{setWritten(e.target.value);setSaved(false);}}/></label>
    <label htmlFor="progress-note-text">Note</label><textarea id="progress-note-text" className="field" required maxLength={16000} rows={7} value={text} onChange={e=>{setText(e.target.value);setSaved(false);}}/>
    <button className="btn btn-primary" disabled={busy||!text.trim()}>{busy?'Saving…':'Save progress note'}</button>
    <p className="metadata">Saved notes are retained. Add a further note to record a correction.</p>
   </form></details>
   {saved?<p role="status">Progress note saved.</p>:null}
   <div className="progress-note-list">{!data.notes.length?<p className="metadata">No progress notes recorded yet.</p>:data.notes.map(note=><details className="progress-note" key={note.id}>
    <summary><strong>{note.author}</strong><span className="metadata">{note.written?' · '+sydneyDate(note.written):' · Note date not recorded'}</span></summary>
    <p className="metadata">{note.role}</p><p className="progress-note-text">{note.text}</p><p className="metadata">Saved {sydneyTimestamp(note.original_saved_at??note.saved_at)}</p>
   </details>)}</div>
   {data.legacy_locked_count>0?<details className="legacy-note-import"><summary>Import older encrypted notes ({data.legacy_locked_count})</summary><p className="metadata">These older notes still use their original encryption key. Import them once using the original passphrase; subsequent access uses clinician sign-in. Their original encrypted records are retained.</p><form onSubmit={importOlder}><label>Original encryption passphrase<input className="field" type="password" autoComplete="off" value={passphrase} onChange={e=>setPassphrase(e.target.value)}/></label><button className="btn" disabled={busy||!passphrase}>Import older notes</button></form></details>:null}
  </>}
 </section>;
}
