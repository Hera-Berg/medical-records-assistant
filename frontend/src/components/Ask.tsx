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
import { Cite, TierMark } from "./marks";

/** Questions that show what this is for, in the shapes it actually handles. */
const EXAMPLES = [
  "What am I taking for my blood pressure?",
  "When did I start perindopril?",
  "What allergies do I have?",
  "What did Dr Nguyen's letter say?",
  "What changed in the last three months?",
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
}: {
  navigate: (to: string) => void;
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

  useEffect(() => {
    box.current?.focus();
  }, []);

  // Deliberately no scrolling-into-view. A conversation capped at three
  // questions, with the box that asks them directly underneath, never grows
  // past the window — and moving the page under someone who is reading is the
  // same class of thing as the animation this interface does without.

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

  return (
    <div>
      {/* What the heading above does not already say. It said what this is for;
          this says what it will not do, which is the part someone needs to read
          before they type rather than after they are refused. */}
      <p>
        It cannot tell you what anything means, whether something is serious, or what to
        do. That is a question for a person — this answers only from your own documents,
        and only about what is in them.
      </p>

      {thread.length === 0 ? (
        <div className="mt-4">
          <p className="text-[color:var(--color-muted)]">
            {busy ? "For example — once this answer has come back:" : "For example:"}
          </p>
          <ul className="mt-2 flex flex-col items-start gap-1">
            {EXAMPLES.map((example) => (
              <li key={example}>
                <button
                  type="button"
                  className="btn text-left"
                  onClick={() => send(example)}
                  disabled={busy}
                >
                  {example}
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="mt-4 flex flex-col gap-5">
        {thread.map((item, index) => (
          <Exchanged key={`${index}-${item.question}`} exchange={item} navigate={navigate} />
        ))}
      </div>

      <form
        className="mt-5 border-t border-[color:var(--color-rule)] pt-4"
        onSubmit={(event) => {
          event.preventDefault();
          send(question);
        }}
      >
        <label className="block" htmlFor="ask-question">
          <span className="font-semibold">Ask your record</span>
        </label>
        <textarea
          id="ask-question"
          ref={box}
          rows={2}
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
          className="field mt-2 w-full"
          placeholder="What am I taking for my blood pressure?"
        />
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <button type="submit" className="btn btn-primary" disabled={busy || exhausted}>
            {busy ? "Reading your record…" : "Ask"}
          </button>
          {thread.length > 0 ? (
            <button
              type="button"
              className="btn"
              onClick={() => {
                setThread([]);
                setQuestion("");
                box.current?.focus();
              }}
            >
              Start again
            </button>
          ) : null}
          <span className="text-[color:var(--color-muted)]">
            {exhausted
              ? "That is three questions in this conversation. Start again to ask about something else."
              : turnsLeft !== null
                ? `${turnsLeft} more ${turnsLeft === 1 ? "question" : "questions"} before this conversation starts again.`
                : "Nothing you ask is saved, here or in your folder."}
          </span>
        </div>
      </form>
    </div>
  );
}

/* --------------------------------------------------------------- one exchange */

function Exchanged({
  exchange,
  navigate,
}: {
  exchange: Exchange;
  navigate: (to: string) => void;
}) {
  const { question, answer, pending, error } = exchange;
  return (
    <article>
      <p className="font-semibold">{question}</p>

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
