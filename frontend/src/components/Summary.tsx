/**
 * The consultation sheet: writing one, and reading one during the appointment.
 *
 * Two screens in one file because they are two views of one object. `/summary`
 * is where a person writes the question they came with and prepares the page;
 * `/summary/{id}` is that page on a phone in the room, where **a tap on any
 * line opens the document behind it, full screen**. That gesture is the whole
 * reason the on-screen version exists: the paper sheet is what gets handed
 * over, and the phone is what answers "where did that come from" when a
 * clinician asks.
 *
 * **Nothing here decides what is on the sheet.** Not the order of the sections,
 * not which medications appear, not what fits on a page. All of that is code in
 * `agent/summary/`, and a second selection rule living in the browser is
 * exactly how the printed page and the screen would come to disagree about what
 * someone is taking. This file renders what the server sends and adds one
 * thing: the tap.
 *
 * The preview is a real preview — it writes nothing and records nothing — so
 * the sheet can be read in full before it exists as an event and two files.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, artifactUrl } from "../api";
import type { PageHeader } from "../App";
import { Link } from "../router";
import type {
  ArtifactMeta,
  Summary as SheetData,
  SummaryLine,
  SummaryResponse,
  SummaryRow,
  SummarySection,
} from "../types";
import { Empty, Heading, longDate } from "./marks";

/** Long enough that typing a question does not fire a request per keystroke. */
const PREVIEW_DEBOUNCE_MS = 400;

export function SummaryScreen({
  id,
  version,
  navigate,
  onChanged,
  setHeader,
}: {
  id?: string;
  version: number;
  navigate: (to: string) => void;
  onChanged: () => void;
  setHeader: (header: PageHeader) => void;
}) {
  return id ? (
    <OneSheet id={id} version={version} navigate={navigate} setHeader={setHeader} />
  ) : (
    <Compose version={version} navigate={navigate} onChanged={onChanged} />
  );
}

/* ------------------------------------------------------------------ compose */

function Compose({
  version,
  navigate,
  onChanged,
}: {
  version: number;
  navigate: (to: string) => void;
  onChanged: () => void;
}) {
  const [question, setQuestion] = useState("");
  const [label, setLabel] = useState("");
  const [sheet, setSheet] = useState<SheetData | null>(null);
  const [past, setPast] = useState<SummaryRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    api
      .summaries()
      .then((result) => live && setPast(result.summaries))
      .catch(() => live && setPast([]));
    return () => {
      live = false;
    };
  }, [version]);

  // Debounced, because the question is typed and each keystroke would otherwise
  // re-project the whole record. Nothing is written either way.
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      api
        .previewSummary({ question, label })
        .then(setSheet)
        .catch((exc: Error) => setError(exc.message));
    }, PREVIEW_DEBOUNCE_MS);
    return () => window.clearTimeout(timer.current);
  }, [question, label, version]);

  const prepare = useCallback(() => {
    setBusy(true);
    setError(null);
    api
      .prepareSummary({ question, label })
      .then((result) => {
        onChanged();
        navigate(`/summary/${result.id}`);
      })
      .catch((exc: ApiError) => setError(exc.message))
      .finally(() => setBusy(false));
  }, [question, label, navigate, onChanged]);

  return (
    <div>
      <p>
        One page to take to an appointment. It lists what changed, what you take, what
        you are allergic to and what you are being treated for — each line saying which
        of your documents it came from — and it ends with the question you came to ask.
      </p>

      <Heading>What you came to ask</Heading>
      <p className="text-[color:var(--color-muted)]">
        Your words, printed exactly as you type them. Nothing on this sheet is written
        for you.
      </p>
      <textarea
        value={question}
        onChange={(event) => setQuestion(event.target.value)}
        rows={3}
        className="field mt-2 w-full"
        placeholder="The two statin scripts I was given say different doses. Which should I be taking?"
      />

      <div className="mt-3 flex flex-wrap items-end gap-3">
        <label className="block">
          <span className="block">Who is the appointment with?</span>
          <span className="block text-[color:var(--color-muted)]">
            Used to name the file in your folder.
          </span>
          <input
            value={label}
            onChange={(event) => setLabel(event.target.value)}
            className="field mt-1"
            placeholder="cardiology"
          />
        </label>
        <button
          type="button"
          className="btn btn-primary"
          onClick={prepare}
          disabled={busy}
        >
          {busy ? "Preparing…" : "Prepare this sheet"}
        </button>
      </div>
      <p className="mt-2 text-[color:var(--color-muted)]">
        Preparing it saves two files into your folder — one to read in any text editor,
        one to print — and records that you made it. Nothing below has been saved yet.
      </p>

      {error ? <Empty>{error}</Empty> : null}

      <Heading>How it will look</Heading>
      {sheet ? <SheetView sheet={sheet} /> : <Empty>Reading your record…</Empty>}

      {past.length > 0 ? (
        <>
          <Heading>Sheets you have prepared before</Heading>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Prepared</th>
                  <th>For</th>
                  <th>What you asked</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {past.map((row) => (
                  <tr key={row.id}>
                    <td className="whitespace-nowrap">{row.prepared_words}</td>
                    <td>{row.label || "—"}</td>
                    <td>
                      {row.withdrawn > 0 ? (
                        <span className="text-[color:var(--color-muted)]">
                          withdrawn — you have since rejected{" "}
                          {row.withdrawn === 1 ? "an entry" : "entries"} it was built
                          from
                        </span>
                      ) : (
                        row.question || <span className="text-[color:var(--color-muted)]">no question written</span>
                      )}
                    </td>
                    <td className="whitespace-nowrap">
                      <Link to={`/summary/${row.id}`} navigate={navigate}>
                        open
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}
    </div>
  );
}

/* ----------------------------------------------------------------- one sheet */

function OneSheet({
  id,
  version,
  navigate,
  setHeader,
}: {
  id: string;
  version: number;
  navigate: (to: string) => void;
  setHeader: (header: PageHeader) => void;
}) {
  const [response, setResponse] = useState<SummaryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setResponse(null);
    setError(null);
    api
      .summary(id)
      .then((result) => live && setResponse(result))
      .catch((exc: Error) => live && setError(exc.message));
    return () => {
      live = false;
    };
  }, [id, version]);

  useEffect(() => {
    if (!response) return;
    setHeader({
      title: "Your sheet for this appointment",
      subtitle:
        response.summary === null
          ? "This one is no longer shown."
          : "Tap any line to see the document it came from. Print it, or hand over the paper copy from your folder.",
    });
  }, [response, setHeader]);

  if (error) return <Empty>{error}</Empty>;
  if (!response) return <Empty>Reading…</Empty>;

  if (response.summary === null || !response.sections) {
    return (
      <div>
        <Empty>{response.message}</Empty>
        <p className="mt-3">
          <Link to="/summary" navigate={navigate}>
            Prepare a new one
          </Link>
        </p>
      </div>
    );
  }

  const sheet = response as SheetData;
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-3 no-print">
        <a href={sheet.print_url} target="_blank" rel="noreferrer" className="btn">
          Open the printable page
        </a>
        {sheet.exports?.html ? (
          <span className="text-[color:var(--color-muted)]">
            In your folder as{" "}
            <code className="font-mono">{sheet.exports.markdown}</code> and{" "}
            <code className="font-mono">{sheet.exports.html}</code>. The printable one
            opens even with this app shut down.
          </span>
        ) : null}
      </div>
      <SheetView sheet={sheet} tappable navigate={navigate} />
    </div>
  );
}

/* --------------------------------------------------------------- the sheet */

function SheetView({
  sheet,
  tappable,
  navigate,
}: {
  sheet: SheetData;
  tappable?: boolean;
  navigate?: (to: string) => void;
}) {
  const [open, setOpen] = useState<string | null>(null);

  return (
    <article>
      <p className="mt-1 text-[color:var(--color-muted)]">
        {sheet.dateline}. {sheet.standfirst}
      </p>
      {sheet.demo_warning ? (
        <p className="mt-2 border-2 border-[color:var(--color-alarm)] bg-[color:var(--color-alarm-soft)] px-3 py-2 font-semibold text-[color:var(--color-alarm)]">
          {sheet.demo_warning}
        </p>
      ) : null}

      {sheet.sections.map((section) => (
        <Section
          key={section.key}
          section={section}
          tappable={tappable}
          onOpen={setOpen}
        />
      ))}

      <Heading>What I came to ask</Heading>
      {sheet.question ? (
        <p className="border-l-2 border-[color:var(--color-ink)] pl-3">
          {sheet.question}
        </p>
      ) : (
        <p className="text-[color:var(--color-muted)]">
          Nothing written yet. This is the part a clinician reads first.
        </p>
      )}

      {sheet.overflowed ? (
        <p className="mt-4 text-[color:var(--color-muted)]">
          This runs past one page. Nothing was dropped from your medications or
          allergies to make it fit — a missing medication is worse than a second
          sheet.
        </p>
      ) : null}
      <p className="mt-4 border-t border-[color:var(--color-rule)] pt-2 text-[color:var(--color-muted)]">
        {sheet.waiting.sentence}
      </p>

      {open ? <FullScreen short={open} onClose={() => setOpen(null)} navigate={navigate} /> : null}
    </article>
  );
}

function Section({
  section,
  tappable,
  onOpen,
}: {
  section: SummarySection;
  tappable?: boolean;
  onOpen: (short: string) => void;
}) {
  return (
    <>
      <Heading>{section.heading}</Heading>
      {section.subnote ? (
        <p className="text-[color:var(--color-muted)]">{section.subnote}</p>
      ) : null}
      {section.lines.length === 0 ? (
        // What the page left out, where it left out everything. The empty note
        // would otherwise assert that nothing happened over the top of it.
        <p className="text-[color:var(--color-muted)]">
          {section.omitted_note ? `${section.omitted_note}.` : section.empty_note}
        </p>
      ) : (
        <div>
          {section.lines.map((line, index) => (
            <Row
              key={`${section.key}-${index}`}
              line={line}
              tappable={tappable}
              onOpen={onOpen}
            />
          ))}
        </div>
      )}
      {section.omitted_note ? (
        <p className="mt-1 text-[color:var(--color-muted)]">{section.omitted_note}.</p>
      ) : null}
    </>
  );
}

function Row({
  line,
  tappable,
  onOpen,
}: {
  line: SummaryLine;
  tappable?: boolean;
  onOpen: (short: string) => void;
}) {
  const artifact = line.sources.find((source) => source.artifact)?.artifact ?? null;
  const canOpen = Boolean(tappable && artifact);
  return (
    <div
      // The whole row, not a small link at the end of it. This is tapped on a
      // phone, mid-sentence, by someone who is also holding a conversation.
      onClick={canOpen ? () => onOpen(artifact as string) : undefined}
      className={
        "grid gap-x-4 border-b border-[color:var(--color-rule)] py-1.5 last:border-0 " +
        // Three columns on a desk, stacked on a phone. The sheet is read in a
        // consulting room on a 390px screen, where three columns turn "20mg
        // daily or 40mg daily" into four words on four lines — which is the one
        // place this document has to be readable at a glance.
        "sm:grid-cols-[11rem_minmax(0,1fr)_auto] " +
        (canOpen ? "cursor-pointer hover:bg-[color:var(--color-shade)]" : "")
      }
    >
      <div className="font-semibold">{line.label}</div>
      <div>
        {line.value}
        {line.alternatives.map((alternative) => (
          <span key={alternative}>
            {" "}
            <span className="text-[color:var(--color-muted)]">or</span> {alternative}
          </span>
        ))}
        {line.note || line.state ? (
          <span className="block text-[color:var(--color-muted)]">
            {[line.note, line.state].filter(Boolean).join(" · ")}
          </span>
        ) : null}
      </div>
      <div className="text-[color:var(--color-muted)] sm:text-right sm:whitespace-nowrap">
        {line.source_text}
      </div>
    </div>
  );
}

/* --------------------------------------------------------------- the source */

/**
 * The document behind one line, full screen.
 *
 * Deliberately a layer over the sheet rather than a navigation: the sheet is
 * being read aloud from, and losing your place in it to look at a photograph is
 * the thing that makes people stop checking. Escape and a tap outside both
 * close it.
 */
function FullScreen({
  short,
  onClose,
  navigate,
}: {
  short: string;
  onClose: () => void;
  navigate?: (to: string) => void;
}) {
  const [meta, setMeta] = useState<ArtifactMeta | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api
      .artifactMeta(short)
      .then((result) => live && setMeta(result))
      .catch((exc: Error) => live && setError(exc.message));
    return () => {
      live = false;
    };
  }, [short]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const url = artifactUrl(short);
  const mime = meta?.mime ?? "";
  return (
    <div
      role="dialog"
      aria-label="The document this line came from"
      onClick={onClose}
      className="fixed inset-0 z-50 flex flex-col bg-[color:var(--color-ink)]/92 p-3 no-print"
    >
      <div className="flex items-center gap-3 pb-2 text-[color:var(--color-paper)]">
        <span>{meta ? meta.path : "Opening…"}</span>
        <button type="button" className="btn ml-auto" onClick={onClose}>
          Close
        </button>
      </div>
      <div
        className="min-h-0 flex-1 overflow-auto bg-[color:var(--color-paper)]"
        onClick={(event) => event.stopPropagation()}
      >
        {error ? (
          <Empty>{error}</Empty>
        ) : !meta ? (
          <Empty>Opening…</Empty>
        ) : !meta.present ? (
          <Empty>
            The record says this document exists, but its bytes are not in your folder.
          </Empty>
        ) : mime.startsWith("image/") ? (
          <img src={url} alt="" className="mx-auto block max-h-full" />
        ) : mime.startsWith("audio/") ? (
          <audio src={url} controls className="mx-auto mt-6 block w-full max-w-xl" />
        ) : mime === "application/pdf" ? (
          <iframe src={url} title="The document" className="h-full w-full border-0" />
        ) : (
          <p className="p-4">
            <a href={url}>Open this file</a> — it is not something this page can show
            in place.
          </p>
        )}
      </div>
      {navigate && meta ? (
        <p className="pt-2 text-[color:var(--color-sidebar-muted)]">
          Added to your record {longDate(meta.ingested_ts?.slice(0, 10) ?? null)}.
        </p>
      ) : null}
    </div>
  );
}
