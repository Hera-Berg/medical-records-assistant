/**
 * Doctor's notes: typed by a clinician, locked with a passphrase, and kept out
 * of everything else the record produces.
 *
 * Sealing happens in this page (`../sealing`); the server only ever holds
 * ciphertext. Three states: no passphrase yet, locked, open. The key exists
 * only while the notes are open, in this component's memory, and is dropped
 * when the page is left, when "Lock" is pressed, or after ten minutes without
 * a keystroke or a click — a laptop handed back across a desk should not stay
 * open on the doctor's notes.
 *
 * The page says plainly what the lock does and does not hide: anyone with the
 * folder can see that notes exist and when each was saved, and nobody without
 * the passphrase can read who wrote them or what they say. And it says, before
 * the passphrase is set, that a lost passphrase is a lost note: there is no
 * reset, because a reset is a second way in.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { createCompartment, openNote, sealNote, unlock, type NoteContent } from "../sealing";
import type { DoctorNotes as Notes, NotesCompartment } from "../types";
import { Empty, longDate, longStamp } from "./marks";

const MIN_PASSPHRASE = 12;
const IDLE_LOCK_MS = 10 * 60 * 1000;

interface Opened {
  compartment: NotesCompartment;
  key: CryptoKey;
}

export function DoctorNotes({ version }: { version: number }) {
  const [data, setData] = useState<Notes | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [opened, setOpened] = useState<Opened | null>(null);

  const load = useCallback(() => {
    api
      .doctorNotes()
      .then(setData)
      .catch((exc: Error) => setError(exc.message));
  }, []);

  useEffect(load, [load, version]);

  // Lock after ten minutes without anyone touching the page.
  useEffect(() => {
    if (!opened) return;
    let timer = window.setTimeout(() => setOpened(null), IDLE_LOCK_MS);
    const reset = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => setOpened(null), IDLE_LOCK_MS);
    };
    window.addEventListener("keydown", reset);
    window.addEventListener("pointerdown", reset);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("keydown", reset);
      window.removeEventListener("pointerdown", reset);
    };
  }, [opened]);

  if (error) return <Empty>Could not read the doctor's notes: {error}</Empty>;
  if (!data) return null;

  if (data.compartments.length === 0) {
    return (
      <SetPassphrase
        onCreated={(next, compartment, key) => {
          setData(next);
          setOpened({ compartment, key });
        }}
      />
    );
  }
  if (!opened) {
    return <Unlock data={data} onOpened={setOpened} />;
  }
  return (
    <OpenNotes
      data={data}
      opened={opened}
      onSaved={setData}
      onLock={() => setOpened(null)}
    />
  );
}

function WhatTheLockDoes() {
  return (
    <p className="max-w-2xl text-[color:var(--color-muted)]">
      Anyone who can open your record's folder can see that these notes exist and the date each
      one was saved. Who wrote a note and what it says can only be read with the passphrase. The
      passphrase is not kept anywhere — not in the folder, not on this computer.
    </p>
  );
}

function SetPassphrase({
  onCreated,
}: {
  onCreated: (data: Notes, compartment: NotesCompartment, key: CryptoKey) => void;
}) {
  const [passphrase, setPassphrase] = useState("");
  const [repeat, setRepeat] = useState("");
  const [understood, setUnderstood] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const why =
    passphrase.length < MIN_PASSPHRASE
      ? `The passphrase needs at least ${MIN_PASSPHRASE} characters.`
      : passphrase !== repeat
        ? "The two passphrases are not the same yet."
        : !understood
          ? "Tick the box above first."
          : null;

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      const { key, body } = await createCompartment(passphrase);
      const next = await api.createNotesCompartment(body);
      const compartment = next.compartments.find((c) => c.id === body.id);
      if (!compartment) throw new Error("the passphrase was not saved");
      onCreated(next, compartment, key);
    } catch (exc) {
      setError((exc as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="flex max-w-2xl flex-col gap-3">
      <p>
        Set a passphrase to lock the doctor's notes. Whoever sets it, and anyone they tell, can
        read the notes. Nobody else can — including you, if you do not know it.
      </p>
      <WhatTheLockDoes />
      <label className="flex flex-col gap-1">
        <span>Passphrase</span>
        <input
          type="password"
          autoComplete="new-password"
          className="field"
          value={passphrase}
          onChange={(e) => setPassphrase(e.target.value)}
        />
      </label>
      <label className="flex flex-col gap-1">
        <span>The same passphrase again</span>
        <input
          type="password"
          autoComplete="new-password"
          className="field"
          value={repeat}
          onChange={(e) => setRepeat(e.target.value)}
        />
      </label>
      <label className="flex items-start gap-2">
        <input
          type="checkbox"
          className="mt-1"
          checked={understood}
          onChange={(e) => setUnderstood(e.target.checked)}
        />
        <span>
          I understand that if this passphrase is lost, nobody can open these notes again. There
          is no way to reset it, and it cannot be changed later.
        </span>
      </label>
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          className="btn btn-primary"
          disabled={Boolean(why) || busy}
          onClick={create}
        >
          {busy ? "Locking…" : "Set the passphrase"}
        </button>
        {why ? <span className="text-[color:var(--color-muted)]">{why}</span> : null}
      </div>
      {error ? <p className="text-[color:var(--color-alarm)]">Not saved: {error}</p> : null}
    </section>
  );
}

function Unlock({ data, onOpened }: { data: Notes; onOpened: (opened: Opened) => void }) {
  const [passphrase, setPassphrase] = useState("");
  const [busy, setBusy] = useState(false);
  const [wrong, setWrong] = useState(false);

  const attempt = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setWrong(false);
    // Oldest first: if two computers each set a passphrase before seeing the
    // other's, the first one set is the record's.
    for (const compartment of data.compartments) {
      const key = await unlock(compartment, passphrase);
      if (key) {
        setBusy(false);
        onOpened({ compartment, key });
        return;
      }
    }
    setBusy(false);
    setWrong(true);
  };

  const count = data.notes.length;
  return (
    <form className="flex max-w-2xl flex-col gap-3" onSubmit={attempt}>
      <p>
        {count === 0
          ? "No notes yet. Enter the passphrase to write one."
          : `${count} ${count === 1 ? "note" : "notes"}, locked. Enter the passphrase to read or add to them.`}
      </p>
      <label className="flex flex-col gap-1">
        <span>Passphrase</span>
        <input
          type="password"
          autoComplete="current-password"
          className="field"
          value={passphrase}
          onChange={(e) => {
            setPassphrase(e.target.value);
            setWrong(false);
          }}
        />
      </label>
      <div className="flex flex-wrap items-center gap-3">
        <button type="submit" className="btn btn-primary" disabled={!passphrase || busy}>
          {busy ? "Opening…" : "Open the notes"}
        </button>
        {!passphrase ? (
          <span className="text-[color:var(--color-muted)]">Type the passphrase first.</span>
        ) : null}
      </div>
      {wrong ? (
        <p className="text-[color:var(--color-alarm)]">That passphrase does not open these notes.</p>
      ) : null}
      <WhatTheLockDoes />
    </form>
  );
}

type Shown = { event_id: string; ts: string; device: string; content: NoteContent | null };

function OpenNotes({
  data,
  opened,
  onSaved,
  onLock,
}: {
  data: Notes;
  opened: Opened;
  onSaved: (data: Notes) => void;
  onLock: () => void;
}) {
  const [shown, setShown] = useState<Shown[] | null>(null);
  const [author, setAuthor] = useState("");
  const [written, setWritten] = useState(today);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const textRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    let live = true;
    Promise.all(
      data.notes.map(async (note) => ({
        event_id: note.event_id,
        ts: note.ts,
        device: note.device,
        content: note.compartment === opened.compartment.id ? await openNote(opened.key, note) : null,
      })),
    ).then((result) => live && setShown(result));
    return () => {
      live = false;
    };
  }, [data, opened]);

  const why = !author.trim()
    ? "Say who is writing it."
    : !text.trim()
      ? "The note is empty."
      : null;

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const body = await sealNote(opened.key, opened.compartment.id, {
        author: author.trim(),
        written,
        text: text.trim(),
      });
      onSaved(await api.addDoctorNote(body));
      setText("");
      textRef.current?.focus();
    } catch (exc) {
      setError((exc as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const unreadable = shown?.filter((note) => note.content === null).length ?? 0;

  return (
    <section className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center gap-3">
        <span>The notes are open.</span>
        <button type="button" className="btn" onClick={onLock}>
          Lock
        </button>
        <span className="text-[color:var(--color-muted)]">
          They lock by themselves after ten minutes untouched, or when you leave this page.
        </span>
      </div>

      <div className="flex max-w-2xl flex-col gap-3">
        <h2 className="font-semibold">Write a note</h2>
        <div className="flex flex-wrap gap-3">
          <label className="flex flex-col gap-1">
            <span>Written by</span>
            <input
              className="field"
              value={author}
              onChange={(e) => setAuthor(e.target.value)}
              placeholder="Dr Nguyen"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span>Date</span>
            <input
              type="date"
              className="field"
              value={written}
              onChange={(e) => setWritten(e.target.value)}
            />
          </label>
        </div>
        <label className="flex flex-col gap-1">
          <span>Note</span>
          <textarea
            ref={textRef}
            className="field min-h-40"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
        </label>
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            className="btn btn-primary"
            disabled={Boolean(why) || busy}
            onClick={save}
          >
            {busy ? "Saving…" : "Save the note"}
          </button>
          {why ? <span className="text-[color:var(--color-muted)]">{why}</span> : null}
        </div>
        <p className="text-[color:var(--color-muted)]">
          A saved note cannot be changed or removed. To correct one, write another below it.
        </p>
        {error ? <p className="text-[color:var(--color-alarm)]">Not saved: {error}</p> : null}
      </div>

      <div>
        <h2 className="pb-1 font-semibold">Notes</h2>
        {shown && shown.length === 0 ? <Empty>No notes yet.</Empty> : null}
        {unreadable > 0 ? (
          <p className="pb-2 text-[color:var(--color-alarm)]">
            {unreadable} {unreadable === 1 ? "note was" : "notes were"} locked with a different
            passphrase, set on another computer before this one saw it, and{" "}
            {unreadable === 1 ? "is" : "are"} not shown.
          </p>
        ) : null}
        {shown
          ?.filter((note) => note.content !== null)
          .map((note) => (
            <article
              key={note.event_id}
              className="border-t border-[color:var(--color-rule)] py-3"
            >
              <p>
                <span className="font-semibold">{note.content!.author || "Not signed"}</span>
                {note.content!.written ? `, ${longDate(note.content!.written)}` : null}
              </p>
              <p className="whitespace-pre-wrap">{note.content!.text}</p>
              <p className="text-[color:var(--color-muted)]">
                Saved {longStamp(note.ts)} on {note.device}
              </p>
            </article>
          ))}
      </div>
    </section>
  );
}

function today(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(
    now.getDate(),
  ).padStart(2, "0")}`;
}
