/**
 * Ask your record: questions about what is in the folder, answered from it.
 *
 * **Not a chat.** The label on the rail says "Ask your record" and not "Chat",
 * and that is load-bearing rather than cosmetic: "chat" promises advice, so the
 * first thing anyone types into it is "should I be worried about this" — which
 * this app refuses, correctly, producing a bad first experience created
 * entirely by the word above the box. "Ask your record" sets the frame, and the
 * refusal becomes the rare case instead of the opening one.
 *
 * So there is no assistant here, no typing indicator, no avatar and no "how can
 * I help". There is a question, an answer, and under every sentence of that
 * answer the document it came from.
 *
 * **But it is laid out like the assistants people already know**, because that
 * shape is the least daunting thing a first screen can be: a greeting and a box
 * in the middle of an empty page, a few example questions under it, your own
 * question in a bubble on the right and the answer beneath it, and the box
 * staying at the bottom of the window. The shape is borrowed; the promise is
 * not — the words around it still say what it will not do.
 *
 * **Every sentence carries its source, visibly.** Not a footnote marker to
 * chase — the source sits under the sentence, named by what it is
 * ("Photograph", "Lab result"), and opens the document itself. A sentence the
 * server could not attribute never arrives here at all; it is dropped before
 * the answer is sent.
 *
 * **What was found is shown even when nothing was written.** The box being
 * asleep, or an answer that could not be traced, both leave the deterministic
 * half of the work intact — which is most of the value and all of the
 * citations. The screen shows those entries rather than an error.
 *
 * **The conversation remembers three questions, then starts again.** Enough for
 * "what did Dr Nguyen recommend?" then "when was that?", which is how people
 * actually ask. The server does the remembering; this sends the words back and
 * is told how many turns are left.
 *
 * **Nothing here is kept.** The thread lives in this component's state and in
 * no other place — not in the folder, not in localStorage, not in the log.
 * Reloading the page is what clearing the history consists of, and a question
 * is not an event.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api";
import type { AskResponse, FoundEntry } from "../types";
import { Link } from "../router";
import { Cite, TierMark } from "./marks";

/** Questions that show what this is for, in the shapes it actually handles. */
const EXAMPLES = [
  "What medications am I taking?",
  "What allergies do I have?",
  "What changed in the last three months?",
  "What did my last letter from a specialist say?",
];

interface Exchange {
  question: string;
  answer: AskResponse | null;
  /** Set while this one is still being answered. */
  pending: boolean;
  error?: string;
}

export function Ask({
  navigate,
  onBoxState,
  documents,
}: {
  navigate: (to: string) => void;
  /** How many documents the record holds, or null before the first health answer. */
  documents: number | null;
  /** Called when an answer reports something about the inference box.
   *
   *  The rail polls every few seconds, so without this it can sit saying
   *  "Ready to read new files" above a panel saying the password was rejected —
   *  one screen contradicting itself, which is worse than either sentence alone.
   *  Nothing in the record changed; this only asks the shell to re-read health. */
  onBoxState: () => void;
}) {
  const [question, setQuestion] = useState("");
  const [thread, setThread] = useState<Exchange[]>([]);
  const [busy, setBusy] = useState(false);
  const box = useRef<HTMLTextAreaElement | null>(null);
  const latest = useRef<HTMLElement | null>(null);
  const [asked, setAsked] = useState(0);

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

  // Brought into view once, when the person asks — their own action, so the
  // page moves because they moved it. Never while an answer is being read:
  // moving the page under someone who is reading is the same class of thing
  // as the animation this interface does without.
  useEffect(() => {
    if (asked > 0) latest.current?.scrollIntoView({ block: "start" });
  }, [asked]);

  // From the last exchange that *counted*. A refused question is not part of
  // the conversation — it is not sent back as history — so counting it here
  // would show a number that never moves and implies one was spent.
  const turnsLeft = lastCounted(thread)?.answer?.turns_left ?? null;
  const exhausted = turnsLeft === 0;

  const send = useCallback(
    (text: string) => {
      const asked = text.trim();
      if (!asked || busy) return;
      setBusy(true);
      setQuestion("");
      setThread((current) => [...current, { question: asked, answer: null, pending: true }]);
      setAsked((n) => n + 1);

      // Only exchanges that produced something are carried forward: a refused
      // question is not part of the conversation's subject, and sending it back
      // would have the next question inherit from a question nothing answered.
      const history = thread
        .filter((item) => item.answer && item.answer.state !== "refused")
        .map((item) => ({
          question: item.question,
          answer: (item.answer?.sentences ?? []).map((s) => s.text).join(" "),
        }));

      api
        .ask(asked, history)
        .then((answer) => {
          setThread((current) =>
            replaceLast(current, { question: asked, answer, pending: false }),
          );
          if (answer.box) onBoxState();
        })
        .catch((exc: ApiError) =>
          setThread((current) =>
            replaceLast(current, {
              question: asked,
              answer: null,
              pending: false,
              error: exc.message,
            }),
          ),
        )
        .finally(() => setBusy(false));
    },
    [busy, thread, onBoxState],
  );

  const empty = thread.length === 0;
  const clear = () => {
    setThread([]);
    setQuestion("");
    box.current?.focus();
  };

  return (
    <div className="flex flex-1 flex-col">
      {empty ? (
        <div className="flex flex-1 flex-col justify-center py-8">
          <h2 className="text-center text-xl font-semibold">
            What would you like to find in your record?
          </h2>
          {/* What it will not do, said before anyone types rather than after
              they are refused — in one sentence, not a paragraph. */}
          <p className="mx-auto mt-2 max-w-xl text-center text-[color:var(--color-muted)]">
            Answers come only from your own documents, and each line shows which one. It
            won't tell you what something means or what to do — that's one for your doctor.
          </p>

          {documents === 0 ? (
            <p className="mx-auto mt-4 max-w-xl rounded-xl bg-[color:var(--color-accent-soft)] px-4 py-3 text-center">
              Your record is empty so far, so there is nothing to ask about yet.{" "}
              <Link to="/add" navigate={navigate}>
                Add your first document
              </Link>{" "}
              — a photo of a prescription or a letter is a good start.
            </p>
          ) : null}

          <div className="mt-6">{composer()}</div>

          <ul className="mt-4 grid gap-2 sm:grid-cols-2">
            {EXAMPLES.map((example) => (
              <li key={example}>
                <button
                  type="button"
                  className="btn h-full w-full rounded-xl px-4 py-2.5 text-left"
                  onClick={() => send(example)}
                  disabled={busy}
                >
                  {example}
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <>
          <div className="flex flex-1 flex-col gap-8 pt-4 pb-6">
            {thread.map((item, index) => (
              <Exchanged
                key={`${index}-${item.question}`}
                exchange={item}
                navigate={navigate}
                anchor={index === thread.length - 1 ? latest : undefined}
              />
            ))}
          </div>
          {/* Stays at the bottom of the window, the way the box does in every
              assistant people already use. */}
          <div className="sticky bottom-0 bg-[color:var(--color-paper)] pt-2 pb-4">
            {composer()}
          </div>
        </>
      )}
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
          Ask your record
        </label>
        <div className="composer flex items-end gap-2">
          <textarea
            id="ask-question"
            ref={box}
            rows={1}
            value={question}
            disabled={exhausted}
            onChange={(event) => setQuestion(event.target.value)}
            onKeyDown={(event) => {
              // Enter asks; shift-Enter is a new line. A question is one
              // sentence, so the common case should not need a second key.
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                send(question);
              }
            }}
            className="min-h-[2.25rem] flex-1 resize-none self-center border-0 bg-transparent py-1.5 outline-none focus-visible:outline-none"
            placeholder={
              exhausted ? "Start again to ask something new" : "Ask about your medications, letters, results…"
            }
          />
          <button
            type="submit"
            className="icon-btn icon-btn-primary"
            disabled={busy || exhausted || !question.trim()}
            aria-label={busy ? "Reading your record" : "Ask"}
            title={
              busy
                ? "Still reading your record for the last question"
                : exhausted
                  ? "Start again to ask something new"
                  : !question.trim()
                    ? "Type a question first"
                    : "Ask"
            }
          >
            <Arrow />
          </button>
        </div>
        <p className="mt-2 flex flex-wrap items-baseline justify-center gap-x-3 text-center text-[color:var(--color-muted)]">
          <span>
            {busy
              ? "Reading your record…"
              : exhausted
                ? "That is three questions in this conversation. Start again to ask about something else."
                : turnsLeft !== null
                  ? `${turnsLeft} more ${turnsLeft === 1 ? "question" : "questions"} before this conversation starts again.`
                  : "Nothing you ask is saved, here or in your folder."}
          </span>
          {thread.length > 0 ? (
            <button type="button" className="underline" onClick={clear}>
              Start again
            </button>
          ) : null}
        </p>
      </form>
    );
  }
}

/** An arrow pointing up: "send", as every assistant draws it. */
function Arrow() {
  return (
    <svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M10 15.5v-11M5.5 9 10 4.5 14.5 9" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/* --------------------------------------------------------------- one exchange */

function Exchanged({
  exchange,
  navigate,
  anchor,
}: {
  exchange: Exchange;
  navigate: (to: string) => void;
  anchor?: React.RefObject<HTMLElement | null>;
}) {
  const { question, answer, pending, error } = exchange;
  return (
    <article ref={anchor} className="scroll-mt-16">
      <p className="ml-auto w-fit max-w-[85%] rounded-2xl bg-[color:var(--color-shade)] px-4 py-2 whitespace-pre-wrap">
        <span className="sr-only">You asked: </span>
        {question}
      </p>

      {pending ? (
        <p className="mt-1 text-[color:var(--color-muted)]">
          Reading your record…
        </p>
      ) : null}

      {error ? (
        <p className="mt-1 rounded-lg bg-[color:var(--color-alarm-soft)] px-3 py-2 text-[color:var(--color-alarm)]">
          {error}
        </p>
      ) : null}

      {answer ? <Answered answer={answer} navigate={navigate} /> : null}
    </article>
  );
}

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
          Nothing here is answered from anywhere but your own documents, so a question
          your record does not cover has no answer rather than a general one.
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
            <th>In your record</th>
            <th>Source</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry, index) => (
            <tr key={index}>
              <td>
                {entry.corrected ? (
                  <span className="chip" title="You typed this correction yourself">
                    Your correction
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
          matched. Your record has all of them.
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

/* -------------------------------------------------------------------- helpers */

function lastCounted(items: Exchange[]): Exchange | undefined {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index];
    if (item?.answer && item.answer.state !== "refused") return item;
  }
  return undefined;
}

function replaceLast(items: Exchange[], value: Exchange): Exchange[] {
  return [...items.slice(0, -1), value];
}
