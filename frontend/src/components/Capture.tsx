/**
 * One action, classified later.
 *
 * Drop anywhere on the window, paste anywhere, pick a file, type a note, or say
 * it out loud. There is no question about what kind of document it is — that is
 * the model's job, and asking the user at capture time is asking them to do it
 * in a corridor.
 *
 * The upload is visible and non-blocking. It never gates the interface, and it
 * never waits for the inference box: the bytes are safe as soon as the server
 * answers, whether or not anything is awake to read them.
 *
 * **Everything goes through the IndexedDB outbox**, not just recordings. A
 * recording is the reason the outbox exists — it is the one capture that exists
 * nowhere else, so a reload mid-upload can lose it outright — but running
 * dropped files through the same queue means one path to reason about and one
 * place where "sent" is decided.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api";
import * as outbox from "../outbox";
import type { Outgoing } from "../outbox";
import { Link } from "../router";
import type { ArtifactMeta, CaptureResult } from "../types";
import { Recorder } from "./Recorder";
import { sydneyToday } from "../dates";

/** How often a recording waiting to be typed up is asked about. */
const TRANSCRIPT_POLL_MS = 2000;

/** How long that goes on before the screen stops asking and says so. */
const TRANSCRIPT_GIVE_UP_MS = 5 * 60 * 1000;

export type Source = Outgoing["source"];

export function useCapture(onCaptured: () => void) {
  const [pending, setPending] = useState<Outgoing[]>([]);
  const [recent, setRecent] = useState<CaptureResult[]>([]);
  const [note, setNote] = useState<string | null>(null);
  const [durable, setDurable] = useState(true);
  const inFlight = useRef(false);
  const metadata = useRef<Record<string,string>>({});
  const [tick, setTick] = useState(0);

  const reload = useCallback(async () => {
    setPending(await outbox.all());
    setDurable(outbox.isDurable());
  }, []);

  const enqueue = useCallback(
    async (files: File[], source: Source, capturedTs: string | null = null) => {
      if (files.length === 0) return;
      for (const file of files) {
        await outbox.add(file, file.name || "capture", source, capturedTs, metadata.current);
      }
      await reload();
      setTick((n) => n + 1);
    },
    [reload],
  );

  const enqueueBlob = useCallback(
    async (blob: Blob, filename: string, capturedTs: string) => {
      await outbox.add(blob, filename, "recorder", capturedTs, metadata.current);
      await reload();
      setTick((n) => n + 1);
    },
    [reload],
  );

  // Drain on load, whenever something is added, and when the network returns.
  useEffect(() => {
    reload();
    const onOnline = () => setTick((n) => n + 1);
    window.addEventListener("online", onOnline);
    return () => window.removeEventListener("online", onOnline);
  }, [reload]);

  useEffect(() => {
    if (inFlight.current) return;
    const next = pending.find((item) => item.error === null);
    if (!next) return;

    inFlight.current = true;
    let cancelled = false;

    const file = new File([next.blob], next.filename, {
      type: next.blob.type || "application/octet-stream",
    });

    api
      .capture([file], next.source, next.capturedTs, undefined, next.metadata)
      .then(async (response) => {
        if (cancelled) return;
        await outbox.remove(next.id);
        setRecent((seen) => [...response.results, ...seen].slice(0, 8));
        setNote(response.note);
        await reload();
        onCaptured();
        setTick((n) => n + 1);
      })
      .catch(async (exc: Error) => {
        if (cancelled) return;
        const terminal = exc instanceof ApiError && exc.status >= 400 && exc.status < 500;
        await outbox.update({
          ...next,
          attempts: next.attempts + 1,
          // A 4xx will not become a 2xx by being sent again. Anything else is
          // the server being unavailable, which is temporary and worth
          // retrying — and the bytes are on disk in this browser meanwhile.
          error: terminal || next.attempts >= 4 ? exc.message : null,
        });
        await reload();
        if (!terminal && next.attempts < 4) {
          window.setTimeout(() => setTick((n) => n + 1), 1500);
        }
      })
      .finally(() => {
        inFlight.current = false;
      });

    return () => {
      cancelled = true;
    };
  }, [pending, tick, onCaptured, reload]);

  const dismiss = useCallback(
    async (id: number) => {
      await outbox.remove(id);
      await reload();
    },
    [reload],
  );

  const retry = useCallback(
    async (item: Outgoing) => {
      await outbox.update({ ...item, error: null, attempts: 0 });
      await reload();
      setTick((n) => n + 1);
    },
    [reload],
  );

  return {
    setMetadata: (value: Record<string,string>) => { metadata.current = value; },
    enqueue,
    enqueueBlob,
    pending,
    recent,
    note,
    durable,
    dismiss,
    retry,
    uploading: pending.filter((item) => item.error === null).length,
  };
}

/**
 * Drag-and-drop and paste, attached to the whole window.
 *
 * Anywhere on the page, not a target the user has to aim at. The overlay only
 * appears while a file is actually over the window — it is a state change, not
 * an animation, and it disappears the instant the drag ends.
 */
export function useWindowCapture(enqueue: (files: File[], source: Source) => void, enabled = true) {
  const [dragging, setDragging] = useState(false);
  const depth = useRef(0);

  useEffect(() => {
    // Off where a screen takes drops itself (Ask follows what it is handed).
    if (!enabled) {
      setDragging(false);
      return;
    }
    const onDragEnter = (event: DragEvent) => {
      if (!event.dataTransfer?.types.includes("Files")) return;
      depth.current += 1;
      setDragging(true);
    };
    const onDragOver = (event: DragEvent) => {
      if (event.dataTransfer?.types.includes("Files")) event.preventDefault();
    };
    const onDragLeave = () => {
      depth.current = Math.max(0, depth.current - 1);
      if (depth.current === 0) setDragging(false);
    };
    const onDrop = (event: DragEvent) => {
      if (!event.dataTransfer?.files.length) return;
      event.preventDefault();
      depth.current = 0;
      setDragging(false);
      enqueue(Array.from(event.dataTransfer.files), "drop");
    };
    const onPaste = (event: ClipboardEvent) => {
      const target = event.target as HTMLElement | null;
      // A paste into a text field is a paste into a text field.
      if (target && ["INPUT", "TEXTAREA"].includes(target.tagName)) return;
      const files = Array.from(event.clipboardData?.files ?? []);
      if (files.length === 0) return;
      event.preventDefault();
      enqueue(files, "paste");
    };

    window.addEventListener("dragenter", onDragEnter);
    window.addEventListener("dragover", onDragOver);
    window.addEventListener("dragleave", onDragLeave);
    window.addEventListener("drop", onDrop);
    window.addEventListener("paste", onPaste);
    return () => {
      window.removeEventListener("dragenter", onDragEnter);
      window.removeEventListener("dragover", onDragOver);
      window.removeEventListener("dragleave", onDragLeave);
      window.removeEventListener("drop", onDrop);
      window.removeEventListener("paste", onPaste);
    };
  }, [enqueue, enabled]);

  return dragging;
}

export function DropOverlay() {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[color:var(--color-paper)]/92">
      <p className="rounded-xl border-2 border-dashed border-[color:var(--color-accent)] px-8 py-6 text-lg font-semibold text-[color:var(--color-accent)]">
        Drop it anywhere. It is stored first and read later.
      </p>
    </div>
  );
}

export function CapturePanel({
  capture,
  onCaptured,
  navigate,
  mode,
}: {
  capture: ReturnType<typeof useCapture>;
  onCaptured: () => void;
  navigate: (to: string) => void;
  /** Opened from the Add sheet: start on the recorder, or in the note field. */
  mode?: "voice" | "note";
}) {
  const [text, setText] = useState("");
  const [author,setAuthor]=useState("");
  const [role,setRole]=useState("");
  const [date,setDate]=useState("");
  const [voice,setVoice]=useState(mode === "voice");
  const noteField = useRef<HTMLTextAreaElement>(null);
  useEffect(()=>{ if (mode === "note") noteField.current?.focus(); setVoice(mode === "voice"); },[mode]);
  const [saving,setSaving]=useState(false);
  const [saved,setSaved]=useState(false);
  useEffect(()=>{capture.setMetadata({author,role,observation_date:date});return()=>capture.setMetadata({});},[author,role,date]);
  const [noteError, setNoteError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  // Recordings from this session, so their transcripts can be shown as they
  // arrive. `audio/` only: a photograph has nothing to be typed up.
  const recordings = capture.recent.filter(
    (result) => result.short && (result.mime ?? "").startsWith("audio/"),
  );

  const submitNote = async () => {
    if (!text.trim() || saving) return;
    setSaving(true);setSaved(false);
    try {
      await api.note(text,{author,role,observation_date:date});
      setSaved(true);
      setText("");
      setNoteError(null);
      onCaptured();
    } catch (exc) {
      setNoteError((exc as Error).message);
    } finally {setSaving(false);}
  };

  const reporter = (
    <div className="update-reporter">
      <label>Who is adding this update?<input className="field" value={author} maxLength={120} onChange={e=>setAuthor(e.target.value)} placeholder="Your name, e.g. Emily Doe"/></label>
      <label>Relationship or role<input className="field" value={role} maxLength={120} onChange={e=>setRole(e.target.value)} placeholder="e.g. Daughter, patient, registered nurse"/></label>
    </div>
  );

  // Opened from the Add sheet: the one thing that was chosen comes first, and
  // who is adding it is optional and folded beneath.
  if (mode) {
    return (
      <section className="update-form update-focused no-print">
        {mode === "voice" ? (
          <div className="update-recorder"><Recorder onRecorded={capture.enqueueBlob} pending={capture.pending}/>{recordings.map(result=><Transcribing key={result.short} short={result.short!} navigate={navigate}/>)}</div>
        ) : (
          <>
            <label className="update-text">What would you like to note?<textarea ref={noteField} className="field" rows={6} value={text} onChange={e=>{setText(e.target.value);setSaved(false);}} placeholder="For example: I have felt dizzy in the mornings since Friday."/></label>
            <label className="update-date">When did this happen? (optional)<input className="field" type="date" max={sydneyToday()} value={date} onChange={e=>setDate(e.target.value)}/></label>
          </>
        )}
        <details className="pt-details">
          <summary>Adding this for someone else? (optional)</summary>
          {reporter}
        </details>
        {mode === "note" ? (
          <>
            <button type="button" className="pt-btn pt-wide" disabled={!text.trim()||saving} onClick={submitNote}>{saving?'Saving…':'Save the note'}</button>
            {!text.trim() && !saving ? <p className="metadata">Type something first.</p> : null}
            {noteError?<p role="alert" className="login-error">{noteError}</p>:null}
            {saved?<p role="status" className="pt-ok">Saved to your record. It is on History now.</p>:null}
          </>
        ) : null}
        <CaptureStatus capture={capture} />
      </section>
    );
  }

  return (
    <section className="update-form no-print">
      <p className="update-intro">Add a document or tell the care team what has changed.</p>
      {reporter}
      <div className="update-methods">
        <div><input ref={fileInput} type="file" multiple accept="image/*,application/pdf,audio/*,video/*,text/plain" className="hidden" onChange={event=>{capture.enqueue(Array.from(event.target.files??[]),"upload");event.target.value="";}}/>
        <button type="button" className="btn" onClick={()=>fileInput.current?.click()}>Upload a document</button><p className="metadata">PDF or photo · you can also drop a file here</p></div>
        <button type="button" className="btn" aria-expanded={voice} onClick={()=>setVoice(!voice)}>Record a voice note</button>
      </div>
      {voice?<div className="update-recorder"><Recorder onRecorded={capture.enqueueBlob} pending={capture.pending}/>{recordings.map(result=><Transcribing key={result.short} short={result.short!} navigate={navigate}/>)}</div>:null}
      <label className="update-text">Or write it down<textarea ref={noteField} aria-label="Your update" className="field" rows={6} value={text} onChange={e=>{setText(e.target.value);setSaved(false);}} placeholder="For example: Jane has felt nauseous since Friday and has been eating less than usual."/></label>
      <label className="update-date">When did this happen? (optional)<input className="field" type="date" max={sydneyToday()} value={date} onChange={e=>setDate(e.target.value)}/></label>
      <button type="button" className="btn btn-primary" disabled={!text.trim()||saving} onClick={submitNote}>{saving?'Saving…':'Save update'}</button>
      {noteError?<p role="alert" className="login-error">{noteError}</p>:null}
      {saved?<p role="status">Update saved and available in the clinician review.</p>:null}
      <p className="metadata update-notice">Saved updates and uploads go to the clinician review. Updates are not monitored continuously.</p>
      <CaptureStatus capture={capture} />
    </section>
  );
}

/**
 * What became of a recording that was just sent, and what happens to it next.
 *
 * The second half is the point. The first version of this went quiet the moment
 * a transcript arrived — the words appeared and nothing said what would become
 * of them, which reads as either "done" or "broken" depending on the reader,
 * and is neither. Nothing in this build reads a transcript for medications, and
 * a record that does not say so has quietly let someone believe they just told
 * it about a dose change.
 *
 * The sentence comes from the server, so it is the same sentence the timeline
 * row and the artefact page show and — minus the live reason — the same one
 * written into `wiki/`.
 */
function Transcribing({
  short,
  navigate,
}: {
  short: string;
  navigate: (to: string) => void;
}) {
  const [meta, setMeta] = useState<ArtifactMeta | null>(null);
  const [gaveUp, setGaveUp] = useState(false);

  useEffect(() => {
    let live = true;
    const began = Date.now();
    const poll = () => {
      api
        .artifactMeta(short)
        .then((result) => {
          if (!live) return;
          setMeta(result);
          // Stop asking once the answer cannot change without something else
          // happening: a transcript arrived, or the job is terminal.
          const settled =
            result.transcript !== null ||
            (result.job !== null &&
              ["unreadable", "needs-attention", "blocked-auth"].includes(
                result.job.state,
              ));
          if (settled) window.clearInterval(timer);
          else if (Date.now() - began > TRANSCRIPT_GIVE_UP_MS) {
            setGaveUp(true);
            window.clearInterval(timer);
          }
        })
        .catch(() => undefined);
    };
    const timer = window.setInterval(poll, TRANSCRIPT_POLL_MS);
    poll();
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [short]);

  if (!meta) return null;

  const transcript = meta.transcript;

  return (
    <div className="mt-6 border-t border-[color:var(--color-rule)] pt-4">
      <h2 className="text-lg font-semibold">What you just said</h2>

      {transcript ? (
        <>
          <p className="mt-1">
            {transcript.text || "There was no speech in that recording."}
          </p>
          {transcript.dropped > 0 ? (
            <p className="mt-1 text-[color:var(--color-muted)]">
              {transcript.dropped} part{transcript.dropped === 1 ? "" : "s"} of it{" "}
              {transcript.dropped === 1 ? "was" : "were"} left out because{" "}
              {transcript.dropped === 1 ? "it was" : "they were"} not speech.
            </p>
          ) : null}
        </>
      ) : null}

      {/* Always. Whether the words are here yet or not, the record says what
          happens to them — the state this component exists to stop being
          silent about. */}
      <p className="mt-1">{gaveUp ? STILL_GOING : meta.reading.text}</p>

      {meta.reading.deferred ? (
        <p className="mt-1 text-[color:var(--color-muted)]">
          Your medication list has not changed. The recording and these words are both
          kept, and this page will read them for medications when that is built —
          you will not need to say it again.{" "}
          <Link to={`/artifact/${short}`} navigate={navigate}>
            See this recording on its own page
          </Link>
          .
        </p>
      ) : null}
    </div>
  );
}

const STILL_GOING =
  "Still being written down. It is safe in your folder; this page has stopped " +
  "checking, and the timeline will show it when it is done.";

export function CaptureStatus({ capture }: { capture: ReturnType<typeof useCapture> }) {
  if (capture.pending.length === 0 && capture.recent.length === 0) return null;

  return (
    <div className="mt-8 border-t border-[color:var(--color-rule)] pt-6">
      <h2 className="text-lg font-semibold">Just added</h2>
      {capture.note ? (
        <p className="mt-1 text-[color:var(--color-muted)]">{capture.note}</p>
      ) : null}
      {!capture.durable ? (
        <p className="mt-1 text-[color:var(--color-warn)]">
          This browser is not letting the page save things for later, so anything
          waiting here would be lost if you closed the tab. Keep it open until the
          list below is empty.
        </p>
      ) : null}

      {capture.pending.length > 0 ? (
        <div className="table-wrap"><table className="mt-1">
          <tbody>
            {capture.pending.map((item) => (
              <tr key={item.id}>
                <td>{item.filename}</td>
                <td>
                  {item.error ? (
                    <span className="text-[color:var(--color-alarm)]">
                      not sent — {item.error}
                    </span>
                  ) : (
                    <span className="text-[color:var(--color-muted)]">
                      sending{item.attempts > 0 ? ` (attempt ${item.attempts + 1})` : ""}…
                    </span>
                  )}
                </td>
                <td className="w-32">
                  {item.error ? (
                    <>
                      <button
                        type="button"
                        onClick={() => capture.retry(item)}
                        className="btn"
                      >
                        Try again
                      </button>{" "}
                      <button
                        type="button"
                        onClick={() => capture.dismiss(item.id)}
                        className="btn"
                      >
                        Dismiss
                      </button>
                    </>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table></div>
      ) : null}

      {capture.recent.length > 0 ? (
        <div className="table-wrap"><table className="mt-1">
          <thead>
            <tr>
              <th>File</th>
              <th className="w-40">What happened to it</th>
            </tr>
          </thead>
          <tbody>
            {capture.recent.map((result, index) => (
              <tr key={`${result.short ?? result.filename}-${index}`}>
                <td>{result.filename}</td>
                <td>
                  {result.status === "failed" ? (
                    <span className="text-[color:var(--color-alarm)]">{result.error}</span>
                  ) : result.status === "reseen" ? (
                    <span title="Identical content was already in the record. Not stored twice.">
                      already in your record
                    </span>
                  ) : (
                    "stored in your folder"
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table></div>
      ) : null}
    </div>
  );
}
