/**
 * Ask: a conversation about the record, answered only from the record — and a
 * place to hand it new documents.
 *
 * **Still not advice.** The label says "Ask", the opening line says what it
 * will not do, and interpretation is refused at the question by the server's
 * classifier, before anything is retrieved. A label that promises advice
 * manufactures its own refusals.
 *
 * **Every sentence carries its source, visibly**, under the sentence, opening
 * the document itself. A sentence the server could not attribute never arrives.
 *
 * **No limit on the conversation.** It runs until the person starts again. The
 * server keeps the prompt within budget by dropping the oldest turns first; the
 * browser sends the words of every answered turn and nothing else.
 *
 * **Files go into the record the ordinary way.** Dropping, pasting or attaching
 * a photo or PDF here captures it exactly as Add does — stored first, then read
 * — and the conversation follows it: "saved", "being read", then whatever is
 * waiting for a yes or no, as the same review items the Check screen shows, with
 * the same actions and the same server checks. The model writes nothing.
 *
 * **A typed message is kept only if the person says so.** Questions are never
 * stored. Each message offers "Add to my record", which saves the words as a
 * plain-text note in the folder; the note is then read like a voice note, as
 * the person's own report.
 *
 * **The conversation itself is not kept.** It lives in this component's state
 * and nowhere else — not in the folder, not in localStorage, not in the log.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useClinicianLanguage, AudienceText } from "../recordLanguage";
import { api, ApiError, artifactUrl } from "../api";
import type { AskResponse, ArtifactMeta, FoundEntry, InboxItem } from "../types";
import { Link } from "../router";
import { Cite, TierMark } from "./marks";
import { ProposalCard } from "./Review";
import { ClipIcon, SendIcon } from "./icons";

/** Questions that show what this is for, in the shapes it actually handles. */
const EXAMPLES = [
  "What medicines am I taking?",
  "What allergies do I have?",
  "What changed in the last three months?",
  "What did my last letter from a specialist say?",
];

/** The same shapes, asked by someone else about the patient. */
const CLINICIAN_EXAMPLES = [
  "What medications is Jane taking?",
  "What allergies are recorded?",
  "What changed in the last three months?",
  "What did the last specialist letter say?",
];

/** How often a document handed over here is asked about while it is read. */
const READ_POLL_MS = 3000;
/** How long the conversation keeps asking before it says where to look instead. */
const READ_GIVE_UP_MS = 10 * 60 * 1000;
const TERMINAL_JOBS = ["unreadable", "needs-attention", "blocked-auth"];

const ACCEPT = "image/*,application/pdf,text/plain";

interface Question {
  kind: "question";
  id: number;
  question: string;
  answer: AskResponse | null;
  pending: boolean;
  error?: string;
  /** Set once the words have been added to the record as a note. */
  kept?: Kept;
}

interface Kept {
  state: "saving" | "saved" | "failed";
  short?: string;
  error?: string;
}

interface Handed {
  kind: "file";
  id: number;
  name: string;
  mime: string;
  state: "saving" | "saved" | "duplicate" | "failed";
  short?: string;
  error?: string;
}

type Turn = Question | Handed;

let nextId = 1;

export function Ask({
  navigate,
  onBoxState,
  onChanged,
  documents,
}: {
  navigate: (to: string) => void;
  /** How many documents the record holds, or null before the first health answer. */
  documents: number | null;
  /** Called when an answer reports something about the inference box, so the
   *  shell re-reads health rather than contradicting this screen. */
  onBoxState: () => void;
  /** Called when something was added to the record or decided. */
  onChanged: () => void;
}) {
  const clinician = useClinicianLanguage();
  const [question, setQuestion] = useState("");
  const [thread, setThread] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const box = useRef<HTMLTextAreaElement | null>(null);
  const picker = useRef<HTMLInputElement | null>(null);
  const latest = useRef<HTMLLIElement | null>(null);
  const [moved, setMoved] = useState(0);

  useEffect(() => {
    box.current?.focus();
  }, []);

  // The box grows with what is typed, up to a few lines, then scrolls.
  useEffect(() => {
    const field = box.current;
    if (!field) return;
    field.style.height = "auto";
    field.style.height = `${Math.min(field.scrollHeight, 180)}px`;
  }, [question]);

  // Brought into view when the person adds something — their own action, so
  // the page moves because they moved it. Never while an answer is being read.
  useEffect(() => {
    if (moved > 0) latest.current?.scrollIntoView({ block: "start" });
  }, [moved]);

  const update = useCallback((id: number, change: Partial<Question> | Partial<Handed>) => {
    setThread((current) =>
      current.map((turn) => (turn.id === id ? ({ ...turn, ...change } as Turn) : turn)),
    );
  }, []);

  const send = useCallback(
    (text: string) => {
      const asked = text.trim();
      if (!asked || busy) return;
      setBusy(true);
      setQuestion("");
      const id = nextId++;
      setThread((current) => [...current, { kind: "question", id, question: asked, answer: null, pending: true }]);
      setMoved((n) => n + 1);

      // Only exchanges that produced something are carried forward: a refused
      // question is not part of the conversation's subject.
      const history = thread
        .filter((turn): turn is Question => turn.kind === "question")
        .filter((turn) => turn.answer && turn.answer.state !== "refused")
        .map((turn) => ({
          question: turn.question,
          answer: (turn.answer?.sentences ?? []).map((s) => s.text).join(" "),
        }));

      api
        .ask(asked, history)
        .then((answer) => {
          update(id, { answer, pending: false });
          if (answer.box) onBoxState();
        })
        .catch((exc: ApiError) => update(id, { answer: null, pending: false, error: exc.message }))
        .finally(() => setBusy(false));
    },
    [busy, thread, onBoxState, update],
  );

  /** Hand files to the record. Stored first; read later; followed here. */
  const handOver = useCallback(
    (files: File[]) => {
      const usable = files.filter((file) => file.size > 0);
      if (usable.length === 0) return;
      setMoved((n) => n + 1);
      for (const file of usable) {
        const id = nextId++;
        setThread((current) => [
          ...current,
          { kind: "file", id, name: file.name || "Pasted image", mime: file.type, state: "saving" },
        ]);
        api
          .capture([file], "chat")
          .then((response) => {
            const result = response.results[0];
            if (!result || result.status === "failed" || !result.short) {
              update(id, { state: "failed", error: result?.error ?? "The record did not accept this file." });
              return;
            }
            update(id, { state: result.status === "reseen" ? "duplicate" : "saved", short: result.short });
            onChanged();
          })
          .catch((exc: Error) => update(id, { state: "failed", error: exc.message }));
      }
    },
    [onChanged, update],
  );

  const keep = useCallback(
    (turn: Question) => {
      update(turn.id, { kept: { state: "saving" } });
      const note = new File([turn.question], "note.txt", { type: "text/plain" });
      api
        .capture([note], "chat")
        .then((response) => {
          const result = response.results[0];
          if (!result || result.status === "failed" || !result.short) {
            update(turn.id, { kept: { state: "failed", error: result?.error ?? "It was not saved." } });
            return;
          }
          update(turn.id, { kept: { state: "saved", short: result.short } });
          onChanged();
        })
        .catch((exc: Error) => update(turn.id, { kept: { state: "failed", error: exc.message } }));
    },
    [onChanged, update],
  );

  // Dropping anywhere on this screen, and pasting a picture into it.
  useEffect(() => {
    let depth = 0;
    const hasFiles = (event: DragEvent) => Boolean(event.dataTransfer?.types.includes("Files"));
    const onEnter = (event: DragEvent) => {
      if (!hasFiles(event)) return;
      depth += 1;
      setDragging(true);
    };
    const onOver = (event: DragEvent) => {
      if (hasFiles(event)) event.preventDefault();
    };
    const onLeave = () => {
      depth = Math.max(0, depth - 1);
      if (depth === 0) setDragging(false);
    };
    const onDrop = (event: DragEvent) => {
      if (!event.dataTransfer?.files.length) return;
      event.preventDefault();
      depth = 0;
      setDragging(false);
      handOver(Array.from(event.dataTransfer.files));
    };
    const onPaste = (event: ClipboardEvent) => {
      const files = Array.from(event.clipboardData?.files ?? []);
      if (files.length === 0) return;
      event.preventDefault();
      handOver(files);
    };
    window.addEventListener("dragenter", onEnter);
    window.addEventListener("dragover", onOver);
    window.addEventListener("dragleave", onLeave);
    window.addEventListener("drop", onDrop);
    window.addEventListener("paste", onPaste);
    return () => {
      window.removeEventListener("dragenter", onEnter);
      window.removeEventListener("dragover", onOver);
      window.removeEventListener("dragleave", onLeave);
      window.removeEventListener("drop", onDrop);
      window.removeEventListener("paste", onPaste);
    };
  }, [handOver]);

  const empty = thread.length === 0;
  const clear = () => {
    setThread([]);
    setQuestion("");
    box.current?.focus();
  };

  return (
    <div className="ask">
      {dragging ? (
        <div className="ask-drop" aria-hidden="true">
          <p>Drop to add it to your record. It is saved first, then read.</p>
        </div>
      ) : null}

      {/* The clinician workspace's shell already carries the page's h1. */}
      {clinician ? (
        empty ? <h2 className="pt-h1 ask-title">What would you like to find in Jane’s record?</h2> : null
      ) : (
        <h1 className={empty ? "pt-h1 ask-title" : "sr-only"}>Ask your record</h1>
      )}

      {empty ? (
        <div className="ask-empty">
          <p className="ask-lede">
            <AudienceText
              patient="Ask about your medicines, letters and results, or drop in a photo or PDF to add it. Answers come only from your own documents, and each line shows which one. It won't tell you what something means or what to do — that's one for your doctor."
              clinician="Answers use only Jane’s source documents, with a source link for each line. This search does not provide clinical interpretation or treatment advice."
            />
          </p>

          {documents === 0 ? (
            <p className="ask-note">
              Your record is empty so far. Drop a photo of a prescription or a letter here to start.
            </p>
          ) : null}

          <div className="ask-composer-start">{composer()}</div>

          <ul className="ask-examples" aria-label="Example questions">
            {(clinician ? CLINICIAN_EXAMPLES : EXAMPLES).map((example) => (
              <li key={example}>
                <button type="button" className="pt-chip-btn" onClick={() => send(example)} disabled={busy}>
                  {example}
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <ol className="ask-thread" role="log" aria-label="Conversation" aria-live="polite">
          {thread.map((turn, index) => (
            <li
              key={turn.id}
              ref={index === thread.length - 1 ? latest : undefined}
              className="ask-turn"
            >
              {turn.kind === "question" ? (
                <Exchanged turn={turn} navigate={navigate} onKeep={() => keep(turn)} onChanged={onChanged} />
              ) : (
                <HandedOver turn={turn} navigate={navigate} onChanged={onChanged} />
              )}
            </li>
          ))}
        </ol>
      )}

      {empty ? null : <div className="ask-composer-wrap">{composer()}</div>}
    </div>
  );

  function composer() {
    return (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            send(question);
          }}
        >
          <label className="sr-only" htmlFor="ask-question">
            {clinician ? "Ask the patient record" : "Ask your record"}
          </label>
          <div className="composer ask-composer">
            <button
              type="button"
              className="icon-btn"
              onClick={() => picker.current?.click()}
              aria-label="Add a photo or document"
              title="Add a photo or document (or drop one anywhere here)"
            >
              <ClipIcon />
            </button>
            <textarea
              id="ask-question"
              ref={box}
              rows={1}
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={(event) => {
                // Enter asks; shift-Enter is a new line.
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  send(question);
                }
              }}
              className="ask-input"
              placeholder={clinician ? "Search Jane’s medications, letters and results…" : "Ask about your record, or drop in a photo…"}
            />
            <button
              type="submit"
              className="icon-btn icon-btn-primary"
              disabled={busy || !question.trim()}
              aria-label={busy ? "Still answering the last question" : "Send"}
              title={busy ? "Still answering the last question" : !question.trim() ? "Type a question first" : "Send"}
            >
              <SendIcon size={18} />
            </button>
            <input
              ref={picker}
              type="file"
              multiple
              accept={ACCEPT}
              className="sr-only"
              tabIndex={-1}
              aria-hidden="true"
              onChange={(event) => {
                handOver(Array.from(event.target.files ?? []));
                event.target.value = "";
              }}
            />
          </div>
          <p className="ask-under">
            <span>
              {busy
                ? "Reading your record…"
                : clinician
                  ? "Questions are not saved. Files you add, and messages you choose to add, go into the patient record."
                  : "Questions are not saved. Files you add, and messages you choose to add, go into your record."}
            </span>
            {thread.length > 0 ? (
              <button type="button" className="pt-link-btn" onClick={clear}>
                Start a new conversation
              </button>
            ) : null}
          </p>
        </form>
    );
  }
}

/* --------------------------------------------------------------- one question */

function Exchanged({
  turn,
  navigate,
  onKeep,
  onChanged,
}: {
  turn: Question;
  navigate: (to: string) => void;
  onKeep: () => void;
  onChanged: () => void;
}) {
  const { question, answer, pending, error, kept } = turn;
  return (
    <article className="ask-exchange">
      <div className="ask-mine">
        <p className="ask-bubble">
          <span className="sr-only">You: </span>
          {question}
        </p>
        <KeepControl kept={kept} onKeep={onKeep} navigate={navigate} />
      </div>

      {pending ? (
        <p className="pt-muted" role="status">
          <AudienceText patient="Reading your record…" clinician="Reading the patient record…" />
        </p>
      ) : null}

      {error ? <p className="pt-alert">{error}</p> : null}

      {answer ? <Answered answer={answer} navigate={navigate} /> : null}

      {kept?.state === "saved" && kept.short ? (
        <Following short={kept.short} navigate={navigate} onChanged={onChanged} what="note" />
      ) : null}
    </article>
  );
}

/** "Add to my record", and what became of it. Never automatic. */
function KeepControl({
  kept,
  onKeep,
  navigate,
}: {
  kept: Kept | undefined;
  onKeep: () => void;
  navigate: (to: string) => void;
}) {
  if (!kept) {
    return (
      <button type="button" className="pt-link-btn ask-keep" onClick={onKeep}>
        Add to my record<span className="sr-only">: save these words as a note</span>
      </button>
    );
  }
  if (kept.state === "saving") return <span className="pt-muted ask-keep">Saving…</span>;
  if (kept.state === "failed")
    return (
      <span className="pt-alert-text ask-keep">
        Not saved: {sentence(kept.error)}{" "}
        <button type="button" className="pt-link-btn" onClick={onKeep}>
          Try again
        </button>
      </span>
    );
  return (
    <span className="pt-muted ask-keep">
      Added to your record as a note.{" "}
      {kept.short ? (
        <Link to={`/artifact/${kept.short}`} navigate={navigate}>
          See it
        </Link>
      ) : null}
    </span>
  );
}

/* ------------------------------------------------------------ one file handed */

function HandedOver({
  turn,
  navigate,
  onChanged,
}: {
  turn: Handed;
  navigate: (to: string) => void;
  onChanged: () => void;
}) {
  const image = turn.mime.startsWith("image/") && turn.short;
  return (
    <article className="ask-exchange">
      <div className="ask-mine">
        <div className="ask-file">
          {image ? <img src={artifactUrl(turn.short!)} alt="" /> : <span className="ask-file-kind">{fileKind(turn.mime)}</span>}
          <span className="ask-file-name">
            <span className="sr-only">You added: </span>
            {turn.name}
          </span>
        </div>
      </div>
      {turn.state === "saving" ? (
        <p className="pt-muted" role="status">Saving it to your record…</p>
      ) : turn.state === "failed" ? (
        <p className="pt-alert">Not saved: {sentence(turn.error)} Nothing was added. You can try again, or use Add.</p>
      ) : (
        <>
          <p>
            {turn.state === "duplicate"
              ? "This was already in your record, so it was not stored twice."
              : "Saved to your record."}{" "}
            {turn.short ? (
              <Link to={`/artifact/${turn.short}`} navigate={navigate}>
                Open it
              </Link>
            ) : null}
          </p>
          {turn.short ? <Following short={turn.short} navigate={navigate} onChanged={onChanged} what="document" /> : null}
        </>
      )}
    </article>
  );
}

/** A server's sentence, ending in a full stop whatever it arrived with. */
function sentence(text: string | undefined): string {
  const words = (text ?? "").trim();
  if (!words) return "the record did not accept it.";
  return /[.!?]$/.test(words) ? words : `${words}.`;
}

function fileKind(mime: string): string {
  if (mime === "application/pdf") return "PDF";
  if (mime.startsWith("text/")) return "Text";
  if (mime.startsWith("audio/")) return "Audio";
  return "File";
}

/**
 * What happens to something after it is stored: read, then whatever needs a
 * person, as review cards. The sentences come from the server — the same ones
 * the document's own page shows — so this screen never words a reading itself.
 */
function Following({
  short,
  navigate,
  onChanged,
  what,
}: {
  short: string;
  navigate: (to: string) => void;
  onChanged: () => void;
  what: "document" | "note";
}) {
  const [meta, setMeta] = useState<ArtifactMeta | null>(null);
  const [items, setItems] = useState<InboxItem[] | null>(null);
  const [thisDevice, setThisDevice] = useState<string | null>(null);
  const [gaveUp, setGaveUp] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [correcting, setCorrecting] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reviewClosed, setReviewClosed] = useState(false);

  const loadItems = useCallback(() => {
    api
      .review()
      .then((queue) => {
        setThisDevice(queue.this_device ?? null);
        setItems(itemsFor(queue.tiers, short));
      })
      .catch(() => {
        // The review list may belong to the clinician workspace in a managed
        // demonstration; the document's own page still says what was read.
        setReviewClosed(true);
        setItems([]);
      });
  }, [short]);

  useEffect(() => {
    let live = true;
    const began = Date.now();
    let timer = 0;
    const poll = () => {
      api
        .artifactMeta(short)
        .then((result) => {
          if (!live) return;
          setMeta(result);
          const settled = readingDone(result);
          if (settled) {
            window.clearInterval(timer);
            loadItems();
            onChanged();
          } else if (Date.now() - began > READ_GIVE_UP_MS) {
            window.clearInterval(timer);
            setGaveUp(true);
          }
        })
        .catch(() => undefined);
    };
    timer = window.setInterval(poll, READ_POLL_MS);
    poll();
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [short, loadItems, onChanged]);

  const decide = useCallback(
    async (item: InboxItem, action: string, body: Record<string, unknown> = {}) => {
      setBusy(item.id);
      setError(null);
      try {
        const queue = await api.decide(item.id, { action, ...body });
        setItems(itemsFor(queue.tiers, short));
        setNotice(queue.decided?.message ?? null);
        setCorrecting(null);
        onChanged();
      } catch (exc) {
        setError(exc instanceof ApiError ? exc.message : String(exc));
      } finally {
        setBusy(null);
      }
    },
    [short, onChanged],
  );

  if (!meta) return <p className="pt-muted">Checking where it has got to…</p>;

  const finished = readingDone(meta);

  return (
    <div className="ask-following">
      <p>
        {gaveUp
          ? `Still waiting to be read. It is safe in your record; this conversation has stopped checking, and the ${what === "note" ? "note" : "document"}'s own page will show what was read.`
          : meta.reading.text}
      </p>

      {finished && items && items.length > 0 ? (
        <div className="ask-cards">
          <p className="pt-label-strong">
            {items.length === 1 ? "One thing needs your yes or no" : `${items.length} things need your yes or no`}
          </p>
          {notice ? <p className="pt-ok" role="status">{notice}</p> : null}
          {error ? (
            <p className="pt-alert" role="alert">
              <strong>That did not happen.</strong> {error}
            </p>
          ) : null}
          {items.map((item) => (
            <ProposalCard
              key={item.id}
              item={item}
              busy={busy === item.id}
              correcting={correcting === item.id}
              onCorrect={() => setCorrecting(item.id)}
              onCancelCorrect={() => setCorrecting(null)}
              decide={decide}
              navigate={navigate}
              thisDevice={thisDevice}
            />
          ))}
        </div>
      ) : finished && items && items.length === 0 && notice ? (
        <p className="pt-ok" role="status">{notice} Nothing else from this is waiting for you.</p>
      ) : finished && reviewClosed ? (
        <p className="pt-muted">Anything that needs checking is with your care team's review.</p>
      ) : null}
    </div>
  );
}

/**
 * Whether the reader has had its turn. Not the record's "finished", which also
 * waits for the person — the cards below are how the person gets their turn.
 */
function readingDone(meta: ArtifactMeta): boolean {
  if (["read", "nothing-found", "unreadable"].includes(meta.reading.state)) return true;
  return meta.job !== null && TERMINAL_JOBS.includes(meta.job.state);
}

/** The queue's items that came from this one artefact. */
function itemsFor(tiers: { high: InboxItem[]; medium: InboxItem[]; low: InboxItem[] }, short: string): InboxItem[] {
  const all = [...(tiers.high ?? []), ...(tiers.medium ?? []), ...(tiers.low ?? [])];
  return all.filter(
    (item) =>
      item.unread?.artifact === short ||
      item.proposed?.artifact === short ||
      (item.citations ?? []).some((citation) => citation.artifact === short) ||
      (item.readings ?? []).some((reading) => reading.artifact === short),
  );
}

/* -------------------------------------------------------------- an answer */

function Answered({
  answer,
  navigate,
}: {
  answer: AskResponse;
  navigate: (to: string) => void;
}) {
  if (answer.state === "refused") return <Refused answer={answer} navigate={navigate} />;

  return (
    <div className="mt-2">
      {answer.sentences.length > 0 ? (
        <div className="flex flex-col gap-2">
          {answer.sentences.map((sentence, index) => (
            <p key={index}>
              {sentence.text}{" "}
              <span className="whitespace-nowrap text-[color:var(--color-muted)]">
                —{" "}
                <Cite citation={sentence.citation} navigate={navigate} />
              </span>
            </p>
          ))}
        </div>
      ) : (
        <p
          className={
            answer.state === "empty"
              ? ""
              : "rounded-lg bg-[color:var(--color-warn-soft)] px-3 py-2 text-[color:var(--color-warn)]"
          }
        >
          {answer.message}
        </p>
      )}

      {/* What the record does not hold. Not muted and not an aside: it is the
          half of the answer that says the other half is not there, and a
          question asked twice because this was missed is a question answered
          from somewhere worse. */}
      {answer.note ? <p className="mt-2">{answer.note}</p> : null}

      {answer.tally ? (
        <p className="mt-2 text-[color:var(--color-muted)]">{answer.tally}</p>
      ) : null}

      {answer.state === "empty" ? (
        <p className="mt-2 text-[color:var(--color-muted)]">
          <AudienceText patient="Nothing here is answered from anywhere but your own documents, so a question your record does not cover has no answer rather than a general one." clinician="Answers come only from Jane’s source documents. Questions not covered by the patient record have no generated answer." />
        </p>
      ) : null}

      {answer.found.length > 0 ? (
        <Found
          entries={answer.found}
          total={answer.found_total}
          heading={
            answer.sentences.length > 0
              ? "What this was read from"
              : "What your record holds about this"
          }
          open={answer.sentences.length === 0}
          navigate={navigate}
        />
      ) : null}
    </div>
  );
}

function Refused({
  answer,
  navigate,
}: {
  answer: AskResponse;
  navigate: (to: string) => void;
}) {
  return (
    <div className="mt-2 rounded-lg border border-[color:var(--color-rule)] bg-[color:var(--color-shade)] px-3 py-3">
      <p>{answer.message}</p>
      <p className="mt-2">{answer.refusal_next}</p>
      <p className="mt-2">
        <a
          href="/summary"
          onClick={(event) => {
            if (event.metaKey || event.ctrlKey || event.shiftKey) return;
            event.preventDefault();
            navigate("/summary");
          }}
        >
          Make a sheet for an appointment
        </a>
      </p>
    </div>
  );
}

/**
 * The entries behind an answer.
 *
 * Folded away when there is an answer above them — the sentences already carry
 * their own sources, and a second list of the same documents is noise. Open
 * when there is not, because then this *is* the answer: what the record holds,
 * each line cited, found without the model having said anything.
 */
function Found({
  entries,
  total,
  heading,
  open,
  navigate,
}: {
  entries: FoundEntry[];
  total: number;
  heading: string;
  open: boolean;
  navigate: (to: string) => void;
}) {
  const body = (
    <div className="table-wrap mt-2">
      <table>
        <thead>
          <tr>
            <th>Where from</th>
            <th><AudienceText patient="In your record" clinician="In the patient record" /></th>
            <th>Source</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry, index) => (
            <tr key={index}>
              <td>
                {entry.corrected ? (
                  <span className="chip" title="You typed this correction yourself">
                    <AudienceText patient="Your correction" clinician="Recorded correction" />
                  </span>
                ) : entry.tier ? (
                  <TierMark tier={entry.tier} />
                ) : null}
              </td>
              <td>
                <span className="block">{entry.title}</span>
                <span className="block">{entry.text}</span>
                {entry.when ? (
                  <span className="block text-[color:var(--color-muted)]">{entry.when}</span>
                ) : null}
              </td>
              <td>
                <Cite citation={entry.citation} navigate={navigate} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {total > entries.length ? (
        <p className="mt-2 text-[color:var(--color-muted)]">
          {total - entries.length} more {total - entries.length === 1 ? "entry" : "entries"}{" "}
          <AudienceText patient="matched. Your record has all of them." clinician="matched. All are available in the patient record." />
        </p>
      ) : null}
    </div>
  );

  if (open) {
    return (
      <div className="mt-3">
        <p className="font-semibold">{heading}</p>
        {body}
      </div>
    );
  }
  return (
    <details className="mt-3">
      <summary className="cursor-pointer">{heading}</summary>
      {body}
    </details>
  );
}

