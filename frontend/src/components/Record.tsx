/**
 * The record index: medications first, then allergies, problems and people.
 *
 * "Current medications" is a **generated view, not a stored file**. It is
 * computed from the same entities listed below it, so the two cannot disagree —
 * the moment a second copy of the medication list exists, one of them is wrong.
 *
 * Entries that need confirming are on the list, deliberately, with the words
 * next to them. Absence of evidence is never evidence of absence, and a
 * medication whose script should have run out is the entry a clinician most
 * needs to ask about, not one to hide.
 */

import { useEffect, useState } from "react";
import { api } from "../api";
import { Link } from "../router";
import type { MedicationRow, WikiIndex } from "../types";
import { Cite, Empty, Heading, longDate, StatusMark, TierMark } from "./marks";

const KIND_TITLES: Record<string, string> = {
  allergy: "Allergies",
  problem: "Problems",
  person: "People",
};

export function Record({
  navigate,
  version,
}: {
  navigate: (to: string) => void;
  version: number;
}) {
  const [data, setData] = useState<WikiIndex | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api
      .wiki()
      .then((result) => live && setData(result))
      .catch((exc: Error) => live && setError(exc.message));
    return () => {
      live = false;
    };
  }, [version]);

  if (error) return <Empty>Could not read the record: {error}</Empty>;
  if (!data) return <Empty>Reading your record…</Empty>;

  const meds = data.current_medications;
  const empty =
    meds.length === 0 && Object.values(data.kinds).every((rows) => rows.length === 0);

  if (empty) {
    return (
      <Empty>
        Nothing has been filed yet. Add something, and once it has been read and you
        have confirmed what it says, it appears here.
      </Empty>
    );
  }

  return (
    <section>
      <h2 className="mb-1 text-lg font-semibold">Medications</h2>
      <p className="mb-2 text-[color:var(--color-muted)]">
        Worked out from your documents every time this page is opened, and never kept
        as a second list that could drift out of step with them.
      </p>
      {meds.length === 0 ? (
        <Empty>No medication has been confirmed yet.</Empty>
      ) : (
        <div className="table-wrap"><table>
          <thead>
            <tr>
              <th>Status</th>
              <th>Medication</th>
              <th>Dose</th>
              <th>Where from</th>
              <th className="w-32">Last confirmed</th>
              <th className="w-32">Expected to run out</th>
            </tr>
          </thead>
          <tbody>
            {meds.map((row) => (
              <tr key={row.id}>
                <td>
                  <StatusMark status={row.status} />
                </td>
                <td>
                  <Link to={`/record/${row.id}`} navigate={navigate} className="font-semibold">
                    {row.name}
                  </Link>
                  {/*
                    The short form in the cell, the sentence under the table.
                    A paragraph inside a table cell starves every other column
                    of width — on A4 it collapsed the dose to one word a line —
                    and this is the list a clinician is handed. The act stays
                    visible either way, which is the part that is not
                    negotiable.
                  */}
                  {row.stop_reported ? (
                    <span className="block text-[color:var(--color-muted)]">
                      you say you have stopped this
                    </span>
                  ) : null}
                </td>
                <td>
                  <Dose row={row} />
                </td>
                <td>{row.evidence_tier ? <TierMark tier={row.evidence_tier} /> : null}</td>
                <td className="whitespace-nowrap">
                  {longDate(row.last_confirmed) ?? "—"}
                  {/*
                    The elapsed phrase, not just the date. CLAUDE.md's own
                    example of a stale entry is "last confirmed 8 months ago",
                    and that is why: a date asks the reader to do arithmetic,
                    and the reader is a clinician skimming. It comes from the
                    server, computed against the same `as_of` the whole record
                    is derived from — a second clock in the browser would
                    disagree with it around every day boundary.
                  */}
                  {row.stale && row.last_confirmed_ago ? (
                    <span className="block text-[color:var(--color-muted)]">
                      {row.last_confirmed_ago}
                    </span>
                  ) : null}
                </td>
                <td className="whitespace-nowrap">
                  {longDate(row.expected_exhaustion) ?? "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}

      <StopReports meds={meds} />

      {(["allergy", "problem", "person"] as const).map((kind) => {
        const rows = data.kinds[kind] ?? [];
        return (
          <div key={kind}>
            <Heading>{KIND_TITLES[kind]}</Heading>
            {rows.length === 0 ? (
              <Empty>
                {kind === "allergy"
                  ? "No allergy has been confirmed. One that has been read but not confirmed is not shown here — it waits until you decide."
                  : `Nothing filed under ${KIND_TITLES[kind]?.toLowerCase()}.`}
              </Empty>
            ) : (
              <div className="table-wrap"><table>
                <thead>
                  <tr>
                    <th>Status</th>
                    <th>Name</th>
                    <th>Where from</th>
                    <th className="w-32">Last confirmed</th>
                    <th className="w-40">The documents</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.id}>
                      <td>
                        <StatusMark status={row.status} />
                      </td>
                      <td>
                        <Link
                          to={`/record/${row.id}`}
                          navigate={navigate}
                          className="font-semibold"
                        >
                          {row.name}
                        </Link>
                        {row.is_stub ? (
                          <span className="text-[color:var(--color-muted)]">
                            {" "}
                            — you merged this into {row.merged_into}
                          </span>
                        ) : null}
                      </td>
                      <td>{row.evidence_tier ? <TierMark tier={row.evidence_tier} /> : null}</td>
                      <td className="whitespace-nowrap">
                        {longDate(row.last_confirmed) ?? "—"}
                        {row.stale && row.last_confirmed_ago ? (
                          <span className="block text-[color:var(--color-muted)]">
                            {row.last_confirmed_ago}
                          </span>
                        ) : null}
                      </td>
                      <td className="text-[color:var(--color-muted)]">
                        {row.sources.length > 0
                          ? `${row.sources.length} ${row.sources.length === 1 ? "document" : "documents"}`
                          : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table></div>
            )}
          </div>
        );
      })}

      {data.review.total > 0 ? (
        <>
          <Heading>Waiting for you</Heading>
          <p>
            {data.review.total} {data.review.total === 1 ? "thing is" : "things are"}{" "}
            waiting to be confirmed — {data.review.by_tier.high ?? 0} of them important.
            Nothing important is added to your record without you saying so, so none of
            it is on the lists above.
          </p>
          <p className="text-[color:var(--color-muted)]">
            Confirming from this screen is not built yet. Until it is,{" "}
            <code className="font-mono">health-agent rebuild</code> on the terminal lists
            what is waiting.
          </p>
        </>
      ) : null}
    </section>
  );
}

/**
 * What you have said you stopped, under the list rather than inside it.
 *
 * A patient reporting they stopped taking something is real information — they
 * are the authority on what they actually take, while the prescriber is the
 * authority on what was prescribed — so it is never dropped and never quietly
 * turned into `stopped`. The medication keeps its status and gains this, in
 * words, where a clinician reading the printed list will see both.
 */
function StopReports({ meds }: { meds: MedicationRow[] }) {
  const reported = meds.filter((row) => row.stop_reported);
  if (reported.length === 0) return null;
  return (
    <div className="mt-3">
      {reported.map((row) => (
        <p key={row.id} className="text-[color:var(--color-muted)]">
          <span className="font-semibold text-[color:var(--color-ink)]">{row.name}</span> —
          you said you stopped this on {longDate(row.stop_reported)}. That came from a{" "}
          {row.stop_reported_tier} source, so it stays on the list until a prescriber's
          document says otherwise.
        </p>
      ))}
    </div>
  );
}

/**
 * The dose column.
 *
 * A conflicted medication has no winning dose, and rendering that as a dash
 * tells the reader it is unknown — when in fact two documents state it and
 * disagree. That is the silent pick rule 3 forbids, arrived at by omission
 * rather than by choosing. Both readings are shown, neither is ranked, and the
 * word "disagree" is there so the reason is not left to the colour of a chip.
 */
function Dose({ row }: { row: MedicationRow }) {
  if (row.dose) return <>{row.dose}</>;
  if (row.dose_readings.length > 0) {
    return (
      <span>
        {row.dose_readings.map((reading) => (
          <span key={reading} className="block">
            {reading}
          </span>
        ))}
        <span className="block text-[color:var(--color-muted)]">neither is chosen</span>
      </span>
    );
  }
  return <span className="text-[color:var(--color-muted)]">not stated</span>;
}

export { Cite };
