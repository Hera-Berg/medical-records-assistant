/**
 * One artefact, full size, with what the record knows about it.
 *
 * This is the end of every citation in the app. A claim is only as good as the
 * page it was read off, and the whole architecture rests on being able to get
 * from a value on screen to the photograph it came from in one click.
 *
 * The four timestamps are listed separately and `captured_ts` says "not known"
 * rather than borrowing from a neighbour. That gap is real information: it is
 * the difference between a photo taken today and one imported from three years
 * of files.
 */

import { useEffect, useState } from "react";
import { api, artifactUrl } from "../api";
import type { PageHeader } from "../App";
import type { ArtifactMeta } from "../types";
import { Empty, Heading, longStamp } from "./marks";

/** What a stored file is, said as the person who added it would say it. */
function kindOf(mime: string): string {
  const [kind, sub] = mime.split("/");
  if (kind === "image") return "Photograph or image";
  if (kind === "audio") return "Audio recording";
  if (kind === "video") return "Video";
  if (sub === "pdf") return "PDF document";
  if (kind === "text") return "Text file";
  return "File";
}

export function Artifact({
  short,
  version,
  setHeader,
}: {
  short: string;
  version: number;
  setHeader: (header: PageHeader) => void;
}) {
  const [meta, setMeta] = useState<ArtifactMeta | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [asked, setAsked] = useState(0);

  useEffect(() => {
    let live = true;
    if (asked === 0) setMeta(null);
    setError(null);
    api
      .artifactMeta(short)
      .then((result) => live && setMeta(result))
      .catch((exc: Error) => live && setError(exc.message));
    return () => {
      live = false;
    };
  }, [short, version, asked]);

  useEffect(() => {
    if (!meta) return;
    setHeader({
      title: kindOf(meta.mime),
      subtitle:
        "The original, exactly as it arrived. Every citation in your record ends here.",
    });
  }, [meta, setHeader]);

  if (error) return <Empty>{error}</Empty>;
  if (!meta) return <Empty>Reading…</Empty>;

  return (
    <article>
      <div className="flex flex-wrap items-baseline gap-3">
        <span className="text-[color:var(--color-muted)]">{meta.mime}</span>
        {meta.reseen_count > 0 ? (
          <span className="text-[color:var(--color-muted)]">
            seen again {meta.reseen_count}{" "}
            {meta.reseen_count === 1 ? "time" : "times"} since
          </span>
        ) : null}
        <a href={artifactUrl(meta.short)} className="ml-auto no-print">
          open the original file
        </a>
      </div>

      {!meta.present ? (
        <Empty>
          The record says this exists, but its bytes are not in the vault. Restore it
          from a backup or the sync client's trash, or add the same file again — every
          citation pointing here resolves to nothing until you do.
        </Empty>
      ) : (
        <Preview meta={meta} />
      )}

      {meta.is_recording ? <Spoken meta={meta} /> : null}

      <WhatNext meta={meta} onAsked={() => setAsked((count) => count + 1)} />

      <Heading>What the record knows about this file</Heading>
      <dl className="grid grid-cols-[14rem_1fr] gap-x-4 gap-y-1">
        <Row label="Added to the record" value={longStamp(meta.ingested_ts)} />
        <Row
          label="Taken or recorded"
          value={longStamp(meta.captured_ts)}
          absent={
            meta.is_recording
              ? "not known — nothing recorded when this was made"
              : "not known — a file picked from disk does not say when it was made"
          }
        />
        <Row
          label="Document dated"
          value={longStamp(meta.artifact_ts)}
          absent="not known — this needs the document to be read"
        />
        <Row label="How it was added" value={meta.source} />
        <Row label="Its file in your folder" value={meta.path} mono />
        <Row label="Fingerprint of the bytes" value={meta.digest} mono />
        <Row
          label="Size"
          value={meta.bytes != null ? `${meta.bytes.toLocaleString()} bytes` : null}
        />
      </dl>
      <p className="mt-2 text-[color:var(--color-muted)]">
        The original is never modified. Everything the model sees is a working copy that
        is not kept.
      </p>
    </article>
  );
}

/**
 * What happens to this document next.
 *
 * The one section that has to be here whatever state the artefact is in, and
 * the reason it exists is that the silence was being read as completion:
 * a document sitting in a queue behind nine others, or a transcript nothing in
 * this build will read for medications, both looked exactly like a document
 * that had been dealt with.
 *
 * The sentence is the server's, not this screen's. The same words appear on the
 * timeline row, and — minus the part about the queue, which is a cache and
 * cannot be written down — in `wiki/`. One document describing its own state
 * three different ways in three places is how a reader stops trusting any of
 * them.
 */
function WhatNext({ meta, onAsked }: { meta: ArtifactMeta; onAsked: () => void }) {
  const reading = meta.reading;
  const [asking, setAsking] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  if (!reading) return null;

  const again = () => {
    setAsking(true);
    setProblem(null);
    api
      .readAgain(meta.short)
      .then(onAsked)
      .catch((exc: Error) => setProblem(exc.message))
      .finally(() => setAsking(false));
  };
  const wanted = reading.awaiting > 0 || reading.deferred;
  return (
    <>
      <Heading>What happens to this next</Heading>
      <p className={"mt-1 " + (wanted ? "" : "text-[color:var(--color-muted)]")}>
        {reading.text}
      </p>
      {/* The short sentence above is what the timeline has room for. This is
          the page someone opened deliberately to find out more, so the
          reassurance that belongs with each state lives here. */}
      {reading.deferred ? (
        <p className="mt-1 text-[color:var(--color-muted)]">
          Your medication list has not changed because of this. The recording and
          what it says are both kept, and both can be quoted. Reading a transcript
          for medications, doses and allergies is the next piece of work; when it
          arrives this recording will be read then, and nothing needs saying again.
        </p>
      ) : null}
      {reading.state === "not-read" ? (
        <p className="mt-1 text-[color:var(--color-muted)]">
          It is stored in your folder either way, and nothing is lost while it
          waits.
        </p>
      ) : null}
      {/* The in-app answer to "then read it again": once whatever stopped it is
          fixed — the file restored, the reading computer reconnected — this puts
          it back in the queue. Offered only for a reading that stopped waiting
          on a person; the server refuses anything else. */}
      {reading.may_retry ? (
        <div className="mt-2 no-print">
          <button type="button" className="btn" onClick={again} disabled={asking}>
            {meta.is_recording ? "Try typing it up again" : "Try reading it again"}
          </button>
          <span className="ml-2 text-[color:var(--color-muted)]">
            {asking
              ? "Asking…"
              : "Once what stopped it is fixed. Nothing is lost if it stops again."}
          </span>
          {problem ? <p className="mt-1">{problem}</p> : null}
        </div>
      ) : null}
      {reading.awaiting > 0 ? (
        <p className="mt-1 text-[color:var(--color-muted)]">
          Nothing from it has been added to your record yet. A medication, a dose or
          an allergy never is without you tapping to say so.
        </p>
      ) : null}
    </>
  );
}

/**
 * What a recording said, and when in it each word was said.
 *
 * The player is directly above this, so a segment's timestamp is something a
 * reader can act on: click the time, the audio jumps there. That is the whole
 * point of storing word-level timestamps — a claim from a voice note cites four
 * seconds, not "a voice note in September", and this is the screen where those
 * four seconds can actually be heard.
 *
 * Discarded segments are reported as a count and never as text. They are the
 * model's inventions over silence; the log keeps them so a run of them can be
 * noticed, and printing them here would put invented sentences on the same page
 * as true ones.
 */
function Spoken({ meta }: { meta: ArtifactMeta }) {
  const transcript = meta.transcript;

  // Nothing here when there is no transcript: the section below says why, and
  // an empty "What was said" above an explanation reads as a failed heading.
  if (!transcript) return null;

  const jump = (seconds: number) => {
    const player = document.querySelector("audio, video") as HTMLMediaElement | null;
    if (!player) return;
    player.currentTime = seconds;
    player.play().catch(() => undefined);
  };

  return (
    <>
      <Heading>What was said</Heading>
      {transcript.text ? (
        <div className="mt-1">
          {transcript.segments.map((segment, index) => (
            <p key={index} className="mt-1 flex gap-3">
              <button
                type="button"
                onClick={() => jump(segment.start)}
                className="shrink-0 font-mono text-[color:var(--color-link)] underline"
                title="Play the recording from here"
              >
                {stamp(segment.start)}
              </button>
              <span>{segment.text}</span>
            </p>
          ))}
        </div>
      ) : (
        <p className="mt-1">
          There was no speech in this recording. Nothing has been written down from it,
          and the recording is kept.
        </p>
      )}
      <p className="mt-2 text-[color:var(--color-muted)]">
        Written down on this computer{modelName(transcript.model)}. The recording was
        not sent anywhere.
        {transcript.dropped > 0
          ? ` ${transcript.dropped} part${transcript.dropped === 1 ? "" : "s"} of the recording ${transcript.dropped === 1 ? "was" : "were"} left out because ${transcript.dropped === 1 ? "it was" : "they were"} not speech.`
          : ""}
        {transcript.supersedes
          ? " This replaced an earlier attempt at writing it down; the first one is still in your event log."
          : ""}
      </p>
      <p className="mt-1 text-[color:var(--color-muted)]">
        The recording is what your record keeps. These words are worked out from it and
        can be worked out again.
      </p>
    </>
  );
}

/**
 * The model, named the way a sentence can hold it.
 *
 * The identity string is ``small`` — accurate, and "written down by small" reads
 * as a sentence with a word missing. The identifier belongs in the record's
 * filing system; here it needs the noun it is the name of.
 */
function modelName(model: string | null): string {
  if (!model) return "";
  return ` by the speech model (Whisper ${model})`;
}

/** ``m:ss`` into a recording. Not a date — a position. */
function stamp(seconds: number): string {
  const whole = Math.floor(seconds);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

function Preview({ meta }: { meta: ArtifactMeta }) {
  const url = artifactUrl(meta.short);
  const kind = meta.mime.split("/")[0];

  if (!meta.renders_inline) {
    return (
      <p className="mt-2">
        This file type is not displayed in the page — it is served as a download
        instead, because the vault syncs from storage this app does not control and a
        document that can carry script must not run here.{" "}
        <a href={url}>Save it</a> to open it yourself.
      </p>
    );
  }
  if (kind === "image") {
    return (
      /*
        Capped, not shown at full size. A 12-megapixel photograph of a script
        renders four screens tall and pushes the four timestamps below the fold
        — on the one page whose job is to let someone check a citation in a
        second. The original is one click away and is never altered.
      */
      <img
        src={url}
        alt={`The stored file ${meta.short}`}
        className="mt-3 max-h-[34rem] w-auto max-w-full rounded-lg border border-[color:var(--color-rule)] object-contain"
      />
    );
  }
  if (meta.mime === "application/pdf") {
    return (
      <object
        data={url}
        type="application/pdf"
        className="mt-2 h-[36rem] w-full border border-[color:var(--color-rule)]"
      >
        <p>
          Your browser will not display this PDF in the page. <a href={url}>Open it</a>.
        </p>
      </object>
    );
  }
  if (kind === "audio") {
    return <audio controls src={url} className="mt-2 w-full" />;
  }
  if (kind === "video") {
    return (
      <video
        controls
        src={url}
        className="mt-2 max-h-[36rem] w-full border border-[color:var(--color-rule)]"
      />
    );
  }
  return (
    <p className="mt-2">
      <a href={url}>Open the original</a>.
    </p>
  );
}

function Row({
  label,
  value,
  absent,
  mono,
}: {
  label: string;
  value: string | null;
  absent?: string;
  mono?: boolean;
}) {
  return (
    <>
      <dt className="text-[color:var(--color-muted)]">{label}</dt>
      <dd className={mono ? "font-mono break-all" : ""}>
        {value ?? (
          <span className="text-[color:var(--color-muted)]">{absent ?? "not known"}</span>
        )}
      </dd>
    </>
  );
}
