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
import type { Claim, EntityDetail } from "../types";
import { Cite, DateCell, Empty, Heading, StatusMark, TierMark } from "./marks";

export function Entity({
  id,
  navigate,
  version,
}: {
  id: string;
  navigate: (to: string) => void;
  version: number;
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

  if (error) return <Empty>{error}</Empty>;
  if (!data) return <Empty>Reading…</Empty>;

  return (
    <article>
      <div className="flex flex-wrap items-baseline gap-3 border-b border-[color:var(--color-rule-strong)] pb-1">
        <h1 className="text-lg font-semibold">{data.name}</h1>
        <StatusMark status={data.status} />
        <span className="font-mono text-[color:var(--color-muted)]">{data.id}</span>
        <Link to={`/timeline/${data.id}`} navigate={navigate} className="ml-auto no-print">
          timeline for this entity
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

      <dl className="mt-2 grid grid-cols-[10rem_1fr] gap-x-4">
        <Fact label="Started" value={data.started} />
        <Fact
          label="Last confirmed"
          value={
            data.last_confirmed && data.stale && data.last_confirmed_ago
              ? `${data.last_confirmed} — ${data.last_confirmed_ago}`
              : data.last_confirmed
          }
        />
        <Fact label="Expected to run out" value={data.expected_exhaustion} />
        <Fact label="Evidence tier" value={data.evidence_tier} />
        <Fact label="Sources" value={data.sources.join(", ") || null} mono />
        <Fact label="File" value={data.path} mono />
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

      <Heading>What the record holds</Heading>
      {data.slots.length === 0 ? (
        <Empty>Nothing has been confirmed about this yet.</Empty>
      ) : (
        <table>
          <thead>
            <tr>
              <th className="w-40">Fact</th>
              <th>Value</th>
              <th className="w-14">Tier</th>
              <th className="w-56">When</th>
              <th className="w-24">Source</th>
            </tr>
          </thead>
          <tbody>
            {data.slots.map((slot) => (
              <tr key={slot.predicate}>
                <td className="font-semibold">{slot.predicate}</td>
                <td>
                  {slot.conflicted ? (
                    <span>
                      <span className="font-semibold text-[color:var(--color-tier-inf)]">
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
        </table>
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
          <Heading>Needs review</Heading>
          <ul>
            {data.review.map((item, index) => (
              <li key={`${item.kind}-${index}`}>
                {item.summary}
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
          <summary className="cursor-pointer border-b border-[color:var(--color-rule-strong)] pb-0.5 text-lg font-semibold">
            The file itself
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
      <table>
        <thead>
          <tr>
            <th className="w-40">Fact</th>
            <th>Read as</th>
            <th className="w-14">Tier</th>
            <th>Replaced by</th>
            <th className="w-24">Source</th>
          </tr>
        </thead>
        <tbody>
          {earlier.map(({ slot, claim }) => (
            <tr key={claim.event_id}>
              <td className="font-semibold">{slot.predicate}</td>
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
      </table>
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
      {fallback.slice(0, 10)}{" "}
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
