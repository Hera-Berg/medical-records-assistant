/**
 * One entity, and everything still standing behind it.
 *
 * The sections mirror the generated markdown page, deliberately: what the
 * folder holds and what the screen shows must not diverge, because the folder
 * is what survives this app. The page's own bytes are at the bottom, so the two
 * can be compared without leaving the screen.
 *
 * Two things this page does not do.
 *
 * It never prints a **pending** high-consequence value. The proposal is named,
 * counted and cited; the value belongs to the review inbox, where the diff is
 * the point of the screen. A proposed value printed beside a current one gets
 * read as current.
 *
 * It never prints **rejected** content, under any heading. A rejection is a
 * retraction, and reprinting it re-asserts what the user said is not true of
 * them. The API cannot send it, so this cannot render it.
 */

import { useEffect, useState } from "react";
import { api } from "../api";
import { Link } from "../router";
import type { PageHeader } from "../App";
import type { Claim, EntityDetail, ReviewItem } from "../types";
import { Cite, DateCell, Empty, Heading, longDate, StatusMark, TierMark } from "./marks";

/**
 * A predicate, in the reader's words.
 *
 * The left column of the facts table used to print the schema's own key —
 * `dose`, `started`, `reaction` — which is fine for four of them and reads as
 * a database dump for the rest. Anything not in this table falls through to the
 * predicate itself rather than being prettified by a rule: an invented English
 * phrase for a predicate nobody has looked at is a worse failure than a word
 * that looks technical, because it might not mean what it says.
 */
const PREDICATE_WORDS: Record<string, string> = {
  dose: "Dose",
  status: "Status",
  started: "Started",
  name: "Name",
  reaction: "Reaction",
  role: "Role",
  contact: "Contact",
};

function predicateWord(predicate: string): string {
  return PREDICATE_WORDS[predicate] ?? predicate;
}

export function Entity({
  id,
  navigate,
  version,
  setHeader,
}: {
  id: string;
  navigate: (to: string) => void;
  version: number;
  setHeader: (header: PageHeader) => void;
}) {
  const [data, setData] = useState<EntityDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setData(null);
    setError(null);
    api
      .entity(id)
      .then((result) => live && setData(result))
      .catch((exc: Error) => live && setError(exc.message));
    return () => {
      live = false;
    };
  }, [id, version]);

  /*
    The page's own name, once it is known. The shell showed "One entry in your
    record" until this resolved, which is vague and true; replacing it with the
    entity's name is the whole reason a screen is allowed to set its heading.
  */
  useEffect(() => {
    if (!data) return;
    setHeader({
      title: data.name,
      subtitle: "Everything your record holds about this, and the documents behind it.",
      badge: <StatusMark status={data.status} />,
    });
  }, [data, setHeader]);

  if (error) return <Empty>{error}</Empty>;
  if (!data) return <Empty>Reading…</Empty>;

  return (
    <article>
      <div className="flex flex-wrap items-baseline gap-3 no-print">
        <Link to={`/timeline/${data.id}`} navigate={navigate} className="ml-auto">
          show only this on the timeline
        </Link>
      </div>

      {data.is_stub ? (
        <p className="mt-2">
          This was merged into{" "}
          <Link to={`/record/${data.merged_into}`} navigate={navigate}>
            {data.merged_into}
          </Link>
          . The page is kept because the merge was your decision, and it is reversible.
        </p>
      ) : null}

      <dl className="grid grid-cols-[12rem_1fr] gap-x-4 gap-y-1">
        <Fact label="Started" value={longDate(data.started)} />
        <Fact
          label="Last confirmed"
          value={
            data.last_confirmed && data.stale && data.last_confirmed_ago
              ? `${longDate(data.last_confirmed)} — ${data.last_confirmed_ago}`
              : longDate(data.last_confirmed)
          }
        />
        <Fact label="Expected to run out" value={longDate(data.expected_exhaustion)} />
        {data.evidence_tier ? (
          <>
            <dt className="text-[color:var(--color-muted)]">Where it came from</dt>
            <dd>
              <TierMark tier={data.evidence_tier} />
            </dd>
          </>
        ) : null}
        <Fact
          label="Documents behind it"
          value={
            data.sources.length > 0
              ? `${data.sources.length} ${data.sources.length === 1 ? "document" : "documents"}`
              : null
          }
        />
        <Fact label="Its file in your folder" value={data.path} mono />
      </dl>

      {/*
        `has_spans` and not merely `dispense != null`. A dose claim's own
        frequency reaches the dispense block as a fallback, so a medication whose
        script stated no quantity still has a `dispense` object — and a Supply
        section built from that reports only that it has nothing to report. The
        wiki page omits it in exactly that case, and the two must not diverge:
        a screen showing a section the folder does not have is a screen that
        cannot be checked against the folder.
      */}
      {data.dispense?.has_spans ? (
        <>
          <Heading>Supply</Heading>
          <p>
            The script said {spans(data.dispense)}.{" "}
            {data.dispense.days_supply != null ? (
              <>That is {data.dispense.days_supply} days.</>
            ) : (
              <span className="text-[color:var(--color-muted)]">
                {data.dispense.unreadable ??
                  "Not enough to work out how long it lasts, so no exhaustion date was computed."}
              </span>
            )}
          </p>
          <p className="text-[color:var(--color-muted)]">
            Those are the words on the document. The arithmetic was done here, never by
            the model.
          </p>
        </>
      ) : null}

      <Heading>What your record says</Heading>
      {data.slots.length === 0 ? (
        <Empty>Nothing has been confirmed about this yet.</Empty>
      ) : (
        <div className="table-wrap"><table>
          <thead>
            <tr>
              <th className="w-40">Fact</th>
              <th>What the record says</th>
              <th className="w-32">Where from</th>
              <th className="w-56">When</th>
              <th className="w-40">The document</th>
            </tr>
          </thead>
          <tbody>
            {data.slots.map((slot) => (
              <tr key={slot.predicate}>
                <td className="font-semibold">{predicateWord(slot.predicate)}</td>
                <td>
                  {slot.conflicted ? (
                    <span>
                      <span className="font-semibold text-[color:var(--color-alarm)]">
                        Two sources disagree.
                      </span>{" "}
                      Neither has been picked for you.
                      <ul className="mt-0.5">
                        {slot.readings.map((reading) => (
                          <li key={reading.event_id}>
                            <TierMark tier={reading.evidence_tier} />{" "}
                            {reading.value.literal} —{" "}
                            <Cite citation={reading.citation} navigate={navigate} />
                          </li>
                        ))}
                      </ul>
                    </span>
                  ) : slot.winner ? (
                    <>
                      {slot.winner.value.literal}
                      {slot.winner.is_correction ? (
                        <span className="text-[color:var(--color-muted)]"> (your correction)</span>
                      ) : null}
                    </>
                  ) : (
                    <span className="text-[color:var(--color-muted)]">
                      nothing confirmed
                    </span>
                  )}
                  {slot.pending.count > 0 ? (
                    <div className="text-[color:var(--color-muted)]">
                      A proposed change is waiting for you to review it —{" "}
                      {slot.pending.citations.map((citation, index) => (
                        <span key={citation.key}>
                          {index > 0 ? ", " : ""}
                          <Cite citation={citation} navigate={navigate} />
                        </span>
                      ))}
                      . Its value is not shown here until you have decided.
                    </div>
                  ) : null}
                  {slot.contradicted_by.length > 0 ? (
                    <div className="text-[color:var(--color-muted)]">
                      A later reading disagrees with your correction. Your correction
                      stands until you decide otherwise.
                    </div>
                  ) : null}
                </td>
                {/*
                  A conflicted slot has no winner, so these three columns have
                  nothing of their own to say — each reading carries its own tier
                  and source inside the value cell instead. An explicit dash
                  beats three blank cells, which read as missing data rather than
                  as a question the record has deliberately left open.
                */}
                <td>
                  {slot.winner ? <TierMark tier={slot.winner.evidence_tier} /> : <Dash />}
                </td>
                <td>{slot.winner ? <When claim={slot.winner} /> : <Dash />}</td>
                <td>
                  {slot.winner ? (
                    <Cite citation={slot.winner.citation} navigate={navigate} />
                  ) : (
                    <Dash />
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}

      <EarlierReadings data={data} navigate={navigate} />

      {data.stop_report ? (
        <>
          <Heading>You reported stopping this</Heading>
          <p>
            Reported {data.stop_report.when?.render ?? "at an unknown time"}, from a{" "}
            {data.stop_report.tier} source —{" "}
            <Cite citation={data.stop_report.citation} navigate={navigate} />. The status
            has not been changed to <em>stopped</em>, because only a prescriber- or
            lab-issued document can do that. Both are shown so a clinician sees what was
            prescribed and what you say you take.
          </p>
        </>
      ) : null}

      {data.salt_names.length > 0 ? (
        <>
          <Heading>Salt variants</Heading>
          <ul>
            {data.salt_names.map((name) => (
              <li key={name.subject_id}>
                A label read <Literal>{name.literal}</Literal>, filed under {data.name} —{" "}
                <Cite citation={name.citation} navigate={navigate} />
              </li>
            ))}
          </ul>
          <p className="text-[color:var(--color-muted)]">
            Salt choice can change the number, so two salts giving different doses show
            as a conflict rather than being merged silently.
          </p>
        </>
      ) : null}

      {data.review.length > 0 ? (
        <>
          <Heading>Waiting for you</Heading>
          <ul>
            {data.review.map((item, index) => (
              <li key={`${item.kind}-${index}`}>
                <span className="font-semibold">{predicateWord(item.predicate)}</span>
                {" — "}
                {reviewSentence(item)}
                {item.citations.length > 0 ? (
                  <>
                    {" — "}
                    {item.citations.map((citation, position) => (
                      <span key={citation.key}>
                        {position > 0 ? ", " : ""}
                        <Cite citation={citation} navigate={navigate} />
                      </span>
                    ))}
                  </>
                ) : null}
              </li>
            ))}
          </ul>
        </>
      ) : null}

      {data.anomalies.length > 0 ? (
        <>
          <Heading>Notes about this entry</Heading>
          <ul>
            {data.anomalies.map((anomaly, index) => (
              <li key={index}>
                {anomaly.text}
                {anomaly.citation ? (
                  <>
                    {" — "}
                    <Cite citation={anomaly.citation} navigate={navigate} />
                  </>
                ) : null}
              </li>
            ))}
          </ul>
        </>
      ) : null}

      {data.markdown ? (
        <details className="mt-5 no-print">
          <summary className="cursor-pointer border-b border-[color:var(--color-rule)] pb-1 text-lg font-semibold">
            The file in your folder
          </summary>
          <p className="mt-1 text-[color:var(--color-muted)]">
            <code className="font-mono">{data.path}</code> — regenerated from the event
            log on every rebuild. Editing it by hand will not survive one.
          </p>
          <pre className="mt-1 overflow-x-auto border border-[color:var(--color-rule)] bg-[color:var(--color-shade)] p-2 font-mono whitespace-pre-wrap">
            {data.markdown}
          </pre>
        </details>
      ) : null}
    </article>
  );
}

/**
 * A review item's sentence, without the slug it opens with.
 *
 * The projection writes `med:atorvastatin dose: 2 sources … disagree`, which is
 * right for a log line and wrong on the entity's own page: the reader is
 * looking at Atorvastatin, and `med:atorvastatin dose:` is the internal filing
 * key repeated back at them. The prefix is stripped only when it matches
 * exactly — a summary in some other shape is printed whole rather than trimmed
 * by guesswork, because the one thing worse than a slug is a sentence with its
 * first clause silently removed.
 */
function reviewSentence(item: ReviewItem): string {
  const prefix = `${item.subject_id} ${item.predicate}: `;
  return item.summary.startsWith(prefix) ? item.summary.slice(prefix.length) : item.summary;
}

/**
 * Superseded readings, rendered rather than discarded.
 *
 * "What did I correct, and from what" has to be answerable from the record
 * alone. A correction whose original has vanished is unverifiable, and a
 * mistyped correction becomes undetectable.
 */
function EarlierReadings({
  data,
  navigate,
}: {
  data: EntityDetail;
  navigate: (to: string) => void;
}) {
  const earlier = data.slots.flatMap((slot) =>
    slot.superseded.map((claim) => ({ slot, claim })),
  );
  if (earlier.length === 0) return null;

  return (
    <>
      <Heading>Earlier readings</Heading>
      <div className="table-wrap"><table>
        <thead>
          <tr>
            <th className="w-40">Fact</th>
            <th>Read as</th>
            <th className="w-32">Where from</th>
            <th>Replaced by</th>
            <th className="w-40">The document</th>
          </tr>
        </thead>
        <tbody>
          {earlier.map(({ slot, claim }) => (
            <tr key={claim.event_id}>
              <td className="font-semibold">{predicateWord(slot.predicate)}</td>
              <td>{claim.value.literal}</td>
              <td>
                <TierMark tier={claim.evidence_tier} />
              </td>
              <td>
                {slot.winner ? (
                  <>
                    {slot.winner.value.literal}
                    {slot.winner.is_correction ? " — your correction" : ""}
                  </>
                ) : (
                  "—"
                )}
              </td>
              <td>
                <Cite citation={claim.citation} navigate={navigate} />
              </td>
            </tr>
          ))}
        </tbody>
      </table></div>
    </>
  );
}

/**
 * When a claim happened, saying which timestamp answered.
 *
 * The four diverge constantly, so the qualifier travels with the date. An
 * unresolvable phrase — "around Easter" — is shown verbatim rather than turned
 * into a date, because computing one would invent the year.
 */
function When({ claim }: { claim: Claim }) {
  if (claim.occurred_at) return <DateCell date={claim.occurred_at} />;
  if (claim.occurred_span) {
    return (
      <span>
        <span className="font-mono">“{claim.occurred_span}”</span>
        <span className="block text-[color:var(--color-muted)]">
          no date recorded — the source said only this
        </span>
      </span>
    );
  }
  const fallback = claim.artifact_ts ?? claim.captured_ts ?? claim.ingested_ts;
  if (!fallback) return <span className="text-[color:var(--color-muted)]">—</span>;
  const label = claim.artifact_ts
    ? "document dated"
    : claim.captured_ts
      ? "captured"
      : "recorded";
  return (
    <span className="whitespace-nowrap">
      {longDate(fallback.slice(0, 10))}{" "}
      <span className="text-[color:var(--color-muted)]">{label}</span>
    </span>
  );
}

function Fact({
  label,
  value,
  mono,
}: {
  label: string;
  value: string | null;
  mono?: boolean;
}) {
  if (!value) return null;
  return (
    <>
      <dt className="text-[color:var(--color-muted)]">{label}</dt>
      <dd className={mono ? "font-mono" : ""}>{value}</dd>
    </>
  );
}

/** A span the source itself wrote. Rendered as it was written, never normalised. */
function Literal({ children }: { children: React.ReactNode }) {
  return <span className="font-mono">{children}</span>;
}

function Dash() {
  return <span className="text-[color:var(--color-muted)]">—</span>;
}

/**
 * The supply spans a source actually wrote, as a phrase.
 *
 * Only the ones that are there. Listing "no quantity, no repeat count" spends
 * a line of a skimmed page saying what is absent, and the absent ones are
 * already why no exhaustion date was computed — which the next sentence says.
 */
function spans(dispense: NonNullable<EntityDetail["dispense"]>) {
  const parts = [dispense.quantity, dispense.frequency, dispense.repeats].filter(
    (part): part is string => Boolean(part),
  );
  return parts.map((part, index) => (
    <span key={part}>
      {index > 0 ? ", " : ""}
      <Literal>{part}</Literal>
    </span>
  ));
}
