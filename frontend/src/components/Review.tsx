import { AudienceText } from "../recordLanguage";
/**
 * The review inbox: everything waiting on a person, most consequential first.
 *
 * This is the only screen in the application that puts something into the
 * record, and nearly every decision in it is about making that deliberate.
 *
 * **The diff is the screen.** Every other view withholds a proposed value,
 * because a value shown beside a current one gets read as current. Here the
 * whole point is showing what a tap would agree to — "now" above "this document
 * says", each labelled in words, with the document itself beside it.
 *
 * **Stopping a medication is not a confirmation.** It has its own button, its
 * own wording, and the keyboard shortcut deliberately skips it. A run of `y`
 * down a queue must not be able to drop a medication, and the server refuses a
 * generic confirm against one — this is the second copy of that rule, not the
 * only one.
 *
 * **There is no "confirm all".** A weekly queue folded per fact is a handful of
 * taps, and a bulk accept beside high-consequence items is exactly the
 * tap-through queue rule 4 warns about. If the weekly review is ever too long
 * to finish, the folding is what needs fixing.
 *
 * **A decision that arrived too late is not an error.** Two devices, or a tab
 * left open, hit that routinely: the answer says what happened to the thing
 * they tapped and the list underneath is already correct.
 *
 * **Nothing rejected is ever drawn.** A rejected reading raises no item, and a
 * withdrawal carries no claims — so the queue has nothing to hide, which is
 * what makes the property hold rather than depend on this file remembering it.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, artifactUrl } from "../api";
import type { InboxItem, ReviewQueue } from "../types";
import { Cite, Empty, longDate, ReadByMark, TierMark } from "./marks";
import { StartItAgain } from "../installation";

const TIERS = ["high", "medium", "low"] as const;

const TIER_HEADINGS: Record<string, { title: string; blurb: string }> = {
  high: {
    title: "Important",
    blurb:
      "Medications, doses and allergies. Nothing here is added to your record on its own, however sure the reading looked — except where it says otherwise on the row, which is where you have already told the record something and it is showing you what it did with it.",
  },
  medium: {
    title: "Worth a look",
    blurb:
      "Symptoms, appointments, results and people. These add themselves after a week if you do not get to them, and are marked as unreviewed until you do.",
  },
  low: {
    title: "Already added",
    blurb: "Small things that added themselves. You can undo any of them from the timeline.",
  },
};

/** What each action's button says. The verb is the act, never "OK". */
const ACTION_WORDS: Record<string, string> = {
  confirm: "Confirm",
  "confirm-stop": "Confirm the stop",
  reject: "Reject",
  correct: "Correct",
  date: "Save the date",
  "confirm-again": "Yes, I meant to confirm it",
  "keep-rejected": "Yes, I meant to reject it",
  "dealt-with": "I've dealt with this",
};

export function Review({
  version,
  onChanged,
  navigate,
  oneAtATime = false,
}: {
  version: number;
  onChanged: () => void;
  navigate: (to: string) => void;
  /** The patient and carer workspace: one thing at a time, in plain words. */
  oneAtATime?: boolean;
}) {
  const [asList, setAsList] = useState(false);
  const [queue, setQueue] = useState<ReviewQueue | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [cursor, setCursor] = useState(0);
  const [correcting, setCorrecting] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api
      .review()
      .then((result) => live && setQueue(result))
      .catch((exc: Error) => live && setError(exc.message));
    return () => {
      live = false;
    };
  }, [version]);

  const items = queue ? TIERS.flatMap((tier) => queue.tiers?.[tier] ?? []) : [];

  /*
    An answer from a server older than this page. The bundle is committed and
    served from disk, so a process that has been running since before an update
    serves this screen while answering in the shape it knew — no `actions`, no
    `id`, nothing to act on. Saying so is better than drawing a queue whose
    buttons cannot work, and far better than reading a field that is not there.
  */
  const stale = queue !== null && (queue.actionable !== true || items.some((item) => !item.id));

  const decide = useCallback(
    async (
      item: InboxItem,
      action: string,
      body: { value?: string; target?: string; occurred_at?: any } = {},
    ) => {
      setBusy(item.id);
      setError(null);
      try {
        const result = await api.decide(item.id, { action, ...body });
        setQueue(result);
        setNotice(result.decided?.message ?? null);
        setCorrecting(null);
        // Every other open screen re-reads: a confirmation here changes the
        // record, and a medication list still showing the old value one tab
        // away is the same fact in two states.
        onChanged();
      } catch (exc) {
        setError(exc instanceof ApiError ? exc.message : String(exc));
      } finally {
        setBusy(null);
      }
    },
    [onChanged],
  );

  // Keep the cursor inside the list as items leave it.
  useEffect(() => {
    setCursor((current) => Math.min(current, Math.max(items.length - 1, 0)));
  }, [items.length]);

  useKeyboard({
    items,
    cursor,
    setCursor,
    editing: correcting !== null,
    decide,
    onCorrect: setCorrecting,
  });

  if (error && !queue) return <Empty>Could not read the review list: {error}</Empty>;
  if (!queue) return <Empty>Reading what is waiting…</Empty>;

  if (stale) {
    return (
      <Empty>
        This screen is newer than the program answering it, so the {items.length}{" "}
        {items.length === 1 ? "thing" : "things"} waiting cannot be shown here yet.
        Nothing is lost. <StartItAgain then="then reload this page" />
      </Empty>
    );
  }

  if (oneAtATime && !asList) {
    return (
      <CheckOne
        items={items}
        cursor={cursor}
        setCursor={setCursor}
        busy={busy}
        correcting={correcting}
        setCorrecting={setCorrecting}
        decide={decide}
        navigate={navigate}
        notice={notice}
        error={error}
        thisDevice={queue.this_device ?? null}
        onList={() => setAsList(true)}
      />
    );
  }

  return (
    <section>
      {oneAtATime ? (
        <p className="mb-3">
          <button type="button" className="pt-link-btn" onClick={() => setAsList(false)}>
            Back to one at a time
          </button>
        </p>
      ) : null}
      {notice ? (
        <p
          className="mb-3 rounded-lg border border-l-4 px-4 py-2"
          style={{
            borderColor: "var(--color-rule)",
            borderLeftColor: "var(--color-accent)",
            background: "var(--color-accent-soft)",
          }}
          role="status"
        >
          {notice}
        </p>
      ) : null}
      {error ? (
        <p
          className="mb-3 rounded-lg border border-l-4 px-4 py-2"
          style={{
            borderColor: "var(--color-rule)",
            borderLeftColor: "var(--color-alarm)",
            background: "var(--color-alarm-soft)",
          }}
          role="alert"
        >
          <span className="font-semibold" style={{ color: "var(--color-alarm)" }}>
            That did not happen.
          </span>{" "}
          {error}
        </p>
      ) : null}

      {items.length === 0 ? (
        <Empty><AudienceText patient={"Nothing is waiting for you. Everything read so far has been decided, and your record shows only what you agreed to."} clinician={"Nothing is waiting for you. Everything read so far has been decided, and the patient record shows only what you agreed to."} /></Empty>
      ) : (
        <>
          <p className="mb-4">
            {items.length === 1 ? "One thing is" : `${items.length} things are`} waiting.{" "}
            <span className="text-[color:var(--color-muted)]">
              Move with <Key>j</Key> and <Key>k</Key>, confirm with <Key>y</Key>, reject
              with <Key>n</Key>, correct with <Key>c</Key>. Stopping a medication is never
              a keystroke.
            </span>
          </p>

          {TIERS.map((tier) => {
            const rows = queue.tiers?.[tier] ?? [];
            if (rows.length === 0) return null;
            const heading = TIER_HEADINGS[tier] ?? { title: tier, blurb: "" };
            return (
              <div key={tier} className="mb-6">
                <h2 className="text-lg font-semibold">{heading.title}</h2>
                <p className="mb-3 text-[color:var(--color-muted)]">{heading.blurb}</p>
                {rows.map((item) => (
                  <Item
                    key={item.id}
                    item={item}
                    selected={items[cursor]?.id === item.id}
                    busy={busy === item.id}
                    correcting={correcting === item.id}
                    onCorrect={() => setCorrecting(item.id)}
                    onCancelCorrect={() => setCorrecting(null)}
                    onSelect={() => setCursor(items.findIndex((row) => row.id === item.id))}
                    decide={decide}
                    navigate={navigate}
                    thisDevice={queue.this_device ?? null}
                  />
                ))}
              </div>
            );
          })}
        </>
      )}

      <Anomalies anomalies={queue.anomalies ?? { count: 0, items: [] }} />
    </section>
  );
}

/**
 * One item: what it would change, what it came from, and what you can do.
 *
 * The layout puts the diff first and the actions last, in reading order, so
 * nobody reaches a button before the sentence that says what it does.
 */
function Item({
  item,
  selected,
  busy,
  correcting,
  onCorrect,
  onCancelCorrect,
  onSelect,
  decide,
  navigate,
  thisDevice,
}: {
  item: InboxItem;
  thisDevice: string | null;
  selected: boolean;
  busy: boolean;
  correcting: boolean;
  onCorrect: () => void;
  onCancelCorrect: () => void;
  onSelect: () => void;
  decide: (item: InboxItem, action: string, body?: any) => void;
  navigate: (to: string) => void;
}) {
  return (
    <article
      onClick={onSelect}
      aria-current={selected ? "true" : undefined}
      className="mb-2 rounded-lg border px-4 py-3"
      style={{
        borderColor: selected ? "var(--color-rule-strong)" : "var(--color-rule)",
        background: "var(--color-paper)",
        /* Selection is a rule and a ground, not a colour with a meaning. */
        boxShadow: selected ? "inset 3px 0 0 0 var(--color-accent)" : undefined,
      }}
    >
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="font-semibold">{item.name}</span>
        <span className="text-[color:var(--color-muted)]">{item.predicate_label}</span>
        {item.proposed ? <TierMark tier={item.proposed.evidence_tier} /> : null}
        {item.proposed ? (
          <ReadByMark readBy={item.proposed.read_by} thisDevice={thisDevice} />
        ) : null}
        {item.sources_folded > 1 ? (
          <span className="text-[color:var(--color-muted)]">
            {item.sources_folded} documents say this — one decision covers them all
          </span>
        ) : null}
      </div>

      <Body item={item} navigate={navigate} decide={decide} />

      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1">
        {/*
          Each reading of a conflict already carries its own citation beside its
          own value, which is the only place the pairing is legible. Repeating
          them here printed "Photograph  Photograph" under two readings that
          came from different photographs.
        */}
        {(item.readings ?? []).length > 1
          ? null
          : (item.citations ?? []).map((citation) => (
              <Cite key={citation.key} citation={citation} navigate={navigate} />
            ))}
        <Thumbnail item={item} />
      </div>

      {correcting ? (
        <CorrectField item={item} onCancel={onCancelCorrect} decide={decide} />
      ) : (
        <Actions item={item} busy={busy} onCorrect={onCorrect} decide={decide} />
      )}
    </article>
  );
}

/**
 * The change itself.
 *
 * A withdrawal has no body beyond its own sentence, on purpose: it exists
 * because content was retracted, and printing the content would put back
 * exactly what the user said is not true of them.
 */
function Body({
  item,
  navigate,
  decide,
}: {
  item: InboxItem;
  navigate: (to: string) => void;
  decide: (item: InboxItem, action: string, body?: any) => void;
}) {
  /*
    The projection writes its summaries for the log and the wiki, in the
    record's own vocabulary — "problem:insomnia name: you decided about a
    reading of artefact 3be48e". True, and not a sentence to put in front of
    the person whose record it is. The kinds that need explaining get their
    words here, where the subject already has a name and the source already has
    a citation.
  */
  if (item.kind === "could-not-read") {
    /* Written on the server for the person, and it names what could not be read
       and never a value — there is none. The document itself is one tap away. */
    return (
      <div className="mt-1">
        <p>{item.summary}</p>
        <p className="mt-1 text-[color:var(--color-muted)]">
          Nothing was guessed. When you have checked the document — or photographed it
          again, or added what it says as a note — say so here.
        </p>
      </div>
    );
  }

  if (item.kind === "withdrawn-by-rejection") {
    return (
      <p className="mt-1"><AudienceText patient={"You confirmed something read out of this document, and later rejected it. The later decision is the one that counts, so it has been taken out of your record — and what it said is deliberately not repeated here. Was rejecting it what you meant?"} clinician={"You confirmed something read out of this document, and later rejected it. The later decision is the one that counts, so it has been taken out of the patient record — and what it said is deliberately not repeated here. Was rejecting it what you meant?"} /></p>
    );
  }

  if (item.kind === "reported-stop") {
    return (
      <p className="mt-1">
        You confirmed that you stopped taking{" "}
        <span className="font-semibold">{item.name}</span>, and that is recorded on its
        page with the source.{" "}
        <span className="font-semibold"><AudienceText patient={"It stays on your medication list"} clinician={"It stays on the patient’s medication list"} /></span>, because
        only a prescription or a lab result can take a medication off it. A clinician
        seeing both what was prescribed and what you actually take is better served than
        by either alone. If it should come off the list, correct it here.
      </p>
    );
  }

  if ((item.readings ?? []).length > 1) {
    return (
      <div className="mt-1">
        <p>
          {(item.readings ?? []).length === 2 ? "Two" : (item.readings ?? []).length} documents of equal
          standing disagree, and neither has been chosen. Correct it to say what is right,
          or reject the reading that is wrong — rejecting the item as a whole would take
          out both, along with the evidence that they disagreed.
        </p>
        {(item.readings ?? []).map((reading) => (
          <p key={reading.event_id} className="mt-1 flex flex-wrap items-center gap-2">
            <span className="font-semibold">{reading.value.literal}</span>
            <TierMark tier={reading.evidence_tier} />
            <Cite citation={reading.citation} navigate={navigate} />
            {/*
              Per reading, because a rejection has to name which one is wrong.
              The server refuses an unaddressed rejection on a conflict for the
              same reason, so a single item-level button here would have been a
              button that could not work.
            */}
            <button
              type="button"
              className="btn"
              onClick={() => decide(item, "reject", { target: reading.event_id })}
            >
              This one is wrong
            </button>
          </p>
        ))}
      </div>
    );
  }

  /*
    A dateable item's value is already in the record and unchanged; the open
    question is when. Rendering the diff printed "Now: since around Easter / It
    says: since around Easter" above the sentence that actually explains it,
    which is a row of noise on the one line a reader needs.
  */
  if (item.dateable) {
    return (
      <div className="mt-1">
        <DateNote item={item} />
      </div>
    );
  }

  return (
    <div className="mt-1">
      <Diff item={item} />
      {item.kind === "stop-proposed" ? <StopNote item={item} /> : null}
    </div>
  );
}

/**
 * Now, and what the document says — two labelled lines rather than two colours.
 *
 * Nothing here is red or green. Colour carries no meaning alone anywhere in this
 * interface, and this is the screen where a misread would matter most.
 */
function Diff({ item }: { item: InboxItem }) {
  const proposed = item.proposed;
  if (!proposed) return null;
  const current = item.current;
  const unchanged = current && current.value.key === proposed.value.key;

  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3">
      <dt className="text-[color:var(--color-muted)]">Now</dt>
      <dd>
        {current ? (
          <span>{current.value.literal}</span>
        ) : (
          <span className="text-[color:var(--color-muted)]">
            nothing recorded — this would be the first
          </span>
        )}
      </dd>
      <dt className="text-[color:var(--color-muted)]">
        {item.sources_folded > 1 ? "They say" : "It says"}
      </dt>
      <dd className="font-semibold">
        {proposed.value.literal}
        {/*
          Only where confirming is actually on offer. On a dateable item there
          is no confirm button — the value is already in the record and the open
          question is *when* — so "confirming adds this document as a source"
          described an action the row does not have.
        */}
        {unchanged && (item.actions ?? []).includes("confirm") ? (
          <span className="ml-2 font-normal text-[color:var(--color-muted)]">
            the same as what is recorded — confirming adds this document as a source
          </span>
        ) : null}
      </dd>
      {proposed.occurred_at ? (
        <>
          <dt className="text-[color:var(--color-muted)]">Dated</dt>
          <dd>{proposed.occurred_at.exact ? longDate(proposed.occurred_at.iso) : proposed.occurred_at.render}</dd>
        </>
      ) : null}
    </dl>
  );
}

/** What confirming a stop will actually do, said before the button is reached. */
function StopNote({ item }: { item: InboxItem }) {
  if (!item.stop) return null;
  return (
    <p className="mt-2">
      {item.stop.transitions ? (
        <>
          Confirming takes <span className="font-semibold">{item.name}</span>{" "}<AudienceText patient={"off your medication list. Its page and its whole history are kept."} clinician={"off the patient’s medication list. Its page and its whole history are kept."} /></>
      ) : (
        <>
          Confirming records that you stopped taking{" "}
          <span className="font-semibold">{item.name}</span>.{" "}
          <span className="font-semibold"><AudienceText patient={"It stays on your medication list"} clinician={"It stays on the patient’s medication list"} /></span>, because
          only a prescription or a lab result can take it off — a clinician seeing both
          what was prescribed and what you actually take is better served than by either
          alone.
        </>
      )}
    </p>
  );
}

/** The phrase the source used, and the dates it might mean. Offers only. */
function DateNote({ item }: { item: InboxItem }) {
  if (!item.dateable) return null;
  return (
    <>
      <p>
        Recorded as{" "}
        <span className="font-semibold">{item.proposed?.value.literal}</span>.
      </p>
      <p className="mt-1">
        The source dates it only as “{item.dateable.span}”, so nothing has been placed on
        the timeline for it. {item.dateable.candidates.length > 0
          ? "The date below is worked out from the document's own date — the day is arithmetic, the year is a guess, so it is offered rather than used."
          : "If you remember when, it can be placed."}
      </p>
    </>
  );
}

/**
 * The artefact beside the claim, so the source is one glance away.
 *
 * Only where there is one source. A conflict has two documents and no winner;
 * showing a picture of one of them puts a thumb on a scale the whole screen is
 * built to keep level, and the readings each link their own document anyway.
 */
function Thumbnail({ item }: { item: InboxItem }) {
  if ((item.readings ?? []).length > 1 || item.sources_folded > 1) return null;
  const artifact = (item.citations ?? []).find((citation) => citation.artifact)?.artifact;
  if (!artifact) return null;
  return (
    <a href={`/artifact/${artifact}`} className="shrink-0">
      {/*
        The artefact route serves the original bytes with its real type. A
        photograph shows; a PDF or a recording has no thumbnail and falls back
        to its own page, which is where the transcript and the player are.
      */}
      <img
        src={artifactUrl(artifact)}
        alt={`The document this came from (${artifact})`}
        loading="lazy"
        className="max-h-24 rounded border"
        style={{ borderColor: "var(--color-rule)" }}
        onError={(event) => {
          (event.currentTarget as HTMLImageElement).hidden = true;
        }}
      />
    </a>
  );
}

function Actions({
  item,
  busy,
  onCorrect,
  decide,
}: {
  item: InboxItem;
  busy: boolean;
  onCorrect: () => void;
  decide: (item: InboxItem, action: string, body?: any) => void;
}) {
  if ((item.actions ?? []).length === 0) {
    return (
      <p className="mt-2 text-[color:var(--color-muted)]">
        There is nothing to decide here yet — this is shown so it is not lost.
      </p>
    );
  }
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2">
      {item.dateable ? <DateActions item={item} decide={decide} busy={busy} /> : null}
      {(item.actions ?? []).map((action) => {
        if (action === "date") return null;
        // A conflict's rejections are attached to their readings above: one
        // button here could not say which reading it meant.
        if (action === "reject" && (item.readings ?? []).length > 1) return null;
        if (action === "correct") {
          return (
            <button key={action} type="button" className="btn" onClick={onCorrect} disabled={busy}>
              Correct
            </button>
          );
        }
        return (
          <button
            key={action}
            type="button"
            className="btn"
            disabled={busy}
            onClick={() => decide(item, action)}
            /*
              A stop gets the emphasis and the longer word. It is a different
              act from confirming a reading, and it reads as one.
            */
            style={
              action === "confirm-stop"
                ? { borderColor: "var(--color-ink)", fontWeight: 600 }
                : undefined
            }
          >
            {ACTION_WORDS[action] ?? action}
          </button>
        );
      })}
      {busy ? (
        <span className="text-[color:var(--color-muted)]">Saving your decision…</span>
      ) : null}
    </div>
  );
}

/**
 * The candidate date, offered as a button and never applied on its own.
 *
 * Which day Easter fell on is arithmetic; which year the speaker meant is a
 * guess. So the computation is done in code and the guess is the user's.
 */
function DateActions({
  item,
  decide,
  busy,
}: {
  item: InboxItem;
  decide: (item: InboxItem, action: string, body?: any) => void;
  busy: boolean;
}) {
  const [typed, setTyped] = useState("");
  if (!item.dateable) return null;
  return (
    <div className="flex flex-wrap items-center gap-2">
      {item.dateable.candidates.map((candidate) => (
        <button
          key={candidate.occurred_at.value}
          type="button"
          className="btn"
          disabled={busy}
          title={candidate.reason}
          onClick={() => decide(item, "date", { occurred_at: candidate.occurred_at })}
        >
          {candidate.label} — {candidate.rendered}
        </button>
      ))}
      <label className="flex items-center gap-2">
        <span className="text-[color:var(--color-muted)]">or the date you remember</span>
        <input
          type="date"
          value={typed}
          onChange={(event) => setTyped(event.target.value)}
          className="rounded border px-2 py-1"
          style={{ borderColor: "var(--color-rule)" }}
        />
      </label>
      <button
        type="button"
        className="btn"
        disabled={busy || !typed}
        onClick={() =>
          decide(item, "date", {
            occurred_at: { value: typed, precision: "day", uncertainty_days: 0 },
          })
        }
      >
        Save the date
      </button>
      {!busy && !typed ? (
        <span className="text-[color:var(--color-muted)]">Pick a date first.</span>
      ) : null}
    </div>
  );
}

/** An inline field, never a modal: correcting is part of reading the item. */
function CorrectField({
  item,
  onCancel,
  decide,
}: {
  item: InboxItem;
  onCancel: () => void;
  decide: (item: InboxItem, action: string, body?: any) => void;
}) {
  const [value, setValue] = useState(item.proposed?.value.literal ?? "");
  const field = useRef<HTMLInputElement>(null);

  useEffect(() => {
    field.current?.focus();
    field.current?.select();
  }, []);

  return (
    <form
      className="mt-2 flex flex-wrap items-center gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        if (value.trim()) decide(item, "correct", { value });
      }}
    >
      <label className="flex items-center gap-2">
        <span className="text-[color:var(--color-muted)]">It should say</span>
        <input
          ref={field}
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Escape") onCancel();
          }}
          className="rounded border px-2 py-1 font-semibold"
          style={{ borderColor: "var(--color-rule-strong)", minWidth: "14rem" }}
        />
      </label>
      <button type="submit" className="btn" disabled={!value.trim()}>
        {value.trim() ? "Save the correction" : "Type what it should say"}
      </button>
      <button type="button" className="btn" onClick={onCancel}>
        Cancel
      </button>
      <span className="text-[color:var(--color-muted)]">
        Your wording wins over anything read from a document, now or later.
      </span>
    </form>
  );
}

/**
 * Things the record noticed about its own filing.
 *
 * CLAUDE.md puts the count here so it cannot scroll past unseen. They are not
 * about anyone's health, and the sentence says so before the list does.
 */
function Anomalies({ anomalies }: { anomalies: { count: number; items: string[] } }) {
  if (anomalies.count === 0) return null;
  return (
    <div className="mt-6 border-t pt-3" style={{ borderColor: "var(--color-rule)" }}>
      <h2 className="text-lg font-semibold">
        {anomalies.count === 1
          ? "One note about the record itself"
          : `${anomalies.count} notes about the record itself`}
      </h2>
      <p className="text-[color:var(--color-muted)]"><AudienceText patient={"These are about how the record is filed, not about your health."} clinician={"These are about how the record is filed, not about the patient’s health."} /></p>
      <ul className="mt-1 list-disc pl-5">
        {anomalies.items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

/**
 * j/k to move, y/n to decide, c to correct.
 *
 * `y` and `n` act only where the act is an ordinary confirmation or rejection.
 * A stop proposal and a withdrawal are skipped deliberately — both need a
 * choice the person has read, and a keystroke that could drop a medication
 * partway down a queue is precisely what rule 4 forbids. The server refuses
 * those actions as well, so this is the convenience, not the guard.
 */
function useKeyboard({
  items,
  cursor,
  setCursor,
  editing,
  decide,
  onCorrect,
}: {
  items: InboxItem[];
  cursor: number;
  setCursor: (fn: (current: number) => number) => void;
  editing: boolean;
  decide: (item: InboxItem, action: string, body?: any) => void;
  onCorrect: (id: string) => void;
}) {
  useEffect(() => {
    if (editing) return;
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      // Never steal a keystroke from something being typed into.
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;

      const item = items[cursor];
      if (event.key === "j") {
        setCursor((current) => Math.min(current + 1, items.length - 1));
      } else if (event.key === "k") {
        setCursor((current) => Math.max(current - 1, 0));
      } else if (event.key === "y" && item?.actions?.includes("confirm")) {
        decide(item, "confirm");
      } else if (event.key === "n" && item?.actions?.includes("reject")) {
        // Not offered on a conflict either: rejecting there has to name which
        // reading is wrong, and a keystroke cannot say which.
        if ((item.readings ?? []).length <= 1) decide(item, "reject");
      } else if (event.key === "c" && item?.actions?.includes("correct")) {
        // Opens the field; it does not submit anything. Typing a correction is
        // the deliberate act, and this only saves reaching for the mouse first.
        onCorrect(item.id);
        event.preventDefault();
      }
      if (["j", "k"].includes(event.key)) event.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [items, cursor, setCursor, editing, decide]);
}

function Key({ children }: { children: React.ReactNode }) {
  return (
    <kbd
      className="rounded border px-1 font-mono"
      style={{ borderColor: "var(--color-rule)", background: "var(--color-shade)" }}
    >
      {children}
    </kbd>
  );
}


/* ----------------------------------------------- one at a time, plain words */

/** What the patient-facing buttons say. The question above them is "Is this right?". */
const PLAIN_WORDS: Record<string, string> = {
  confirm: "Yes",
  reject: "No",
  correct: "Fix it",
};

/**
 * The review queue one item at a time, for the patient and carer workspace.
 *
 * The same items, the same actions and the same server checks as the list —
 * only the presentation differs. A stop is still its own button with its own
 * words, a conflict still asks which reading is wrong, and `y` still skips a
 * stop.
 */
function CheckOne({
  items,
  cursor,
  setCursor,
  busy,
  correcting,
  setCorrecting,
  decide,
  navigate,
  notice,
  error,
  thisDevice,
  onList,
}: {
  items: InboxItem[];
  cursor: number;
  setCursor: (fn: (current: number) => number) => void;
  busy: string | null;
  correcting: string | null;
  setCorrecting: (id: string | null) => void;
  decide: (item: InboxItem, action: string, body?: any) => void;
  navigate: (to: string) => void;
  notice: string | null;
  error: string | null;
  thisDevice: string | null;
  onList: () => void;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  const item = items[cursor];
  const shown = useRef<string | null>(null);

  // A new item replaced the one just decided: say so to a screen reader by
  // moving focus to the counter, which names where in the queue this is.
  useEffect(() => {
    if (!item) return;
    if (shown.current !== null && shown.current !== item.id) heading.current?.focus();
    shown.current = item.id;
  }, [item]);

  if (!item) {
    return (
      <div className="pt-page">
        <h1 className="pt-h1">Check</h1>
        {notice ? <p className="pt-ok" role="status">{notice}</p> : null}
        <p className="pt-empty">
          All done. Nothing is waiting for you — your record shows only what you have agreed to.
        </p>
        <button type="button" className="pt-btn-quiet" onClick={() => navigate("/")}>
          Back to Home
        </button>
      </div>
    );
  }

  return (
    <div className="pt-page">
      <div className="pt-title-row">
        <h1 className="pt-h1" ref={heading} tabIndex={-1}>
          Check <span className="pt-muted">· {cursor + 1} of {items.length}</span>
        </h1>
        <button type="button" className="pt-link-btn" onClick={onList}>
          See all as a list
        </button>
      </div>

      {notice ? <p className="pt-ok" role="status">{notice}</p> : null}
      {error ? (
        <p className="pt-alert" role="alert">
          <strong>That did not happen.</strong> {error}
        </p>
      ) : null}

      <ProposalCard
        item={item}
        busy={busy === item.id}
        correcting={correcting === item.id}
        onCorrect={() => setCorrecting(item.id)}
        onCancelCorrect={() => setCorrecting(null)}
        decide={decide}
        navigate={navigate}
        thisDevice={thisDevice}
      />

      <div className="pt-check-nav">
        <button
          type="button"
          className="pt-link-btn"
          disabled={cursor === 0}
          onClick={() => setCursor((current) => Math.max(current - 1, 0))}
        >
          Previous
        </button>
        <button
          type="button"
          className="pt-link-btn"
          disabled={cursor >= items.length - 1}
          onClick={() => setCursor((current) => Math.min(current + 1, items.length - 1))}
        >
          Skip for now
        </button>
      </div>
      <p className="pt-muted pt-foot">
        Keys: <Key>y</Key> yes, <Key>n</Key> no, <Key>c</Key> fix it, <Key>j</Key>/<Key>k</Key> next and previous.
        Stopping a medicine is never a keystroke.
      </p>
    </div>
  );
}

/**
 * One review item as a card, in plain words. Used one at a time on the Check
 * screen, and inline in Ask when a document dropped into the conversation has
 * been read.
 */
export function ProposalCard({
  item,
  busy,
  correcting,
  onCorrect,
  onCancelCorrect,
  decide,
  navigate,
  thisDevice,
}: {
  item: InboxItem;
  busy: boolean;
  correcting: boolean;
  onCorrect: () => void;
  onCancelCorrect: () => void;
  decide: (item: InboxItem, action: string, body?: any) => void;
  navigate: (to: string) => void;
  thisDevice: string | null;
}) {
  const plain =
    item.proposed !== null &&
    !item.dateable &&
    (item.readings ?? []).length <= 1 &&
    !["could-not-read", "withdrawn-by-rejection", "reported-stop"].includes(item.kind);
  const questionId = `check-q-${item.id}`;
  // A stop is not asked as "is this right?": it has its own button and its own
  // sentence saying what confirming does.
  const askable = item.kind !== "stop-proposed" && (item.actions ?? []).some((action) => action in PLAIN_WORDS);

  return (
    <article className="pt-check" aria-label={`${item.name}: ${item.predicate_label}`}>
      <CheckPreview item={item} navigate={navigate} />

      {plain ? <ItSays item={item} /> : (
        <div className="pt-check-body">
          <p className="pt-strong">
            {item.name} <span className="pt-muted">· {item.predicate_label}</span>
          </p>
          <Body item={item} navigate={navigate} decide={decide} />
        </div>
      )}
      {item.proposed ? (
        <p className="pt-muted pt-read-by">
          <TierMark tier={item.proposed.evidence_tier} />{" "}
          <ReadByMark readBy={item.proposed.read_by} thisDevice={thisDevice} />
        </p>
      ) : null}

      {correcting ? (
        <CorrectField item={item} onCancel={onCancelCorrect} decide={decide} />
      ) : (item.actions ?? []).length === 0 ? (
        <p className="pt-muted">There is nothing to decide here yet — it is shown so it is not lost.</p>
      ) : (
        <>
          {askable ? (
            <p id={questionId} className="pt-label-strong">
              Is this right?
            </p>
          ) : null}
          <div className="pt-decide" role="group" aria-labelledby={askable ? questionId : undefined}>
            {item.dateable ? <DateActions item={item} decide={decide} busy={busy} /> : null}
            {orderActions(item.actions ?? []).map((action) => {
              if (action === "date") return null;
              if (action === "reject" && (item.readings ?? []).length > 1) return null;
              const words = PLAIN_WORDS[action] ?? ACTION_WORDS[action] ?? action;
              const primary = action === "confirm" || action === "confirm-stop" || action === "dealt-with";
              return (
                <button
                  key={action}
                  type="button"
                  className={primary ? "pt-btn" : "pt-btn-quiet"}
                  disabled={busy}
                  onClick={() => (action === "correct" ? onCorrect() : decide(item, action))}
                >
                  {words}
                  {action in PLAIN_WORDS ? (
                    <span className="sr-only">
                      {action === "confirm" ? ", this is right" : action === "reject" ? ", this is wrong" : ", type what it should say"}
                    </span>
                  ) : null}
                </button>
              );
            })}
          </div>
          {busy ? <p className="pt-muted" role="status">Saving your decision…</p> : null}
        </>
      )}
    </article>
  );
}

function orderActions(actions: string[]): string[] {
  const rank = (action: string) =>
    ({ confirm: 0, "confirm-stop": 0, "dealt-with": 0, "confirm-again": 1, "keep-rejected": 2, correct: 3, reject: 4 })[action] ?? 5;
  return [...actions].sort((a, b) => rank(a) - rank(b));
}

/** "It says": the reading, large, with what the record holds now beneath it. */
function ItSays({ item }: { item: InboxItem }) {
  const proposed = item.proposed!;
  const current = item.current;
  const unchanged = current && current.value.key === proposed.value.key;
  return (
    <div className="pt-it-says">
      <p className="pt-label">{item.sources_folded > 1 ? `${item.sources_folded} documents say` : "It says"}</p>
      <p className="pt-it-says-name">{item.name}</p>
      <p className="pt-it-says-value">
        {/* A dose reads as a dose on its own; anything else says what it is. */}
        <span className={item.predicate === "dose" ? "sr-only" : "pt-muted"}>{item.predicate_label}: </span>
        {proposed.value.literal}
      </p>
      {proposed.occurred_at ? (
        <p className="pt-muted">
          Dated {proposed.occurred_at.exact ? longDate(proposed.occurred_at.iso) : proposed.occurred_at.render}
        </p>
      ) : null}
      {item.kind === "stop-proposed" ? null : (
        <p className="pt-muted">
          {unchanged
            ? "The same as your record already says — yes adds this document as a source."
            : current
              ? `Your record says now: ${current.value.literal}`
              : "Nothing recorded for this yet — this would be the first."}
        </p>
      )}
      {item.kind === "stop-proposed" ? <StopNote item={item} /> : null}
    </div>
  );
}

/** The document beside the reading, large enough to check it against. */
function CheckPreview({ item, navigate }: { item: InboxItem; navigate: (to: string) => void }) {
  if ((item.readings ?? []).length > 1) return null;
  const citation = (item.citations ?? []).find((c) => c.artifact);
  if (!citation?.artifact) return null;
  const artifact = citation.artifact;
  return (
    <figure className="pt-check-doc">
      <a
        href={`/artifact/${artifact}`}
        onClick={(event) => {
          if (event.metaKey || event.ctrlKey || event.shiftKey) return;
          event.preventDefault();
          navigate(`/artifact/${artifact}`);
        }}
      >
        <img
          src={artifactUrl(artifact)}
          alt=""
          loading="lazy"
          onError={(event) => {
            (event.currentTarget as HTMLImageElement).hidden = true;
          }}
        />
        <figcaption>
          {citation.text}
          <span className="sr-only"> — open the document this was read from</span>
        </figcaption>
      </a>
    </figure>
  );
}
