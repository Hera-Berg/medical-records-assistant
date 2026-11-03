import { useEffect, useState } from "react";
import { sydneyTimestamp } from "../dates";
import { api } from "../api";
export function OriginalObservation({ id,clinician=true }: { id: string;clinician?:boolean }) {
  const [note,setNote]=useState<{text:string; recorded_at:string; author:string; role:string; date_basis:string}|null>(null);
  const [error,setError]=useState<string|null>(null);
  useEffect(()=>{let live=true;api.originalObservation(id,clinician).then(d=>{if(live)setNote(d);}).catch((e:Error)=>{if(live)setError(e.message);});return()=>{live=false;};},[id,clinician]);
  if(error)return <p role="alert">{error}</p>;
  if(!note)return <p role="status">Loading original note…</p>;
  return <article className="surface"><h2 className="section-heading">Original typed observation</h2>
    <p className="metadata">{note.author} · {note.role} · {sydneyTimestamp(note.recorded_at)}</p>
    <p className="metadata">{note.date_basis}</p><blockquote className="original-observation">{note.text}</blockquote>
  </article>;
}
