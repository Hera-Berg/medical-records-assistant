/**
 * The record index: medications first, then allergies, problems and people.
 *
 * "Current medications" is a **generated view, not a stored file**. It is
 * computed from the same entities listed below it, so the two cannot disagree —
 * the moment a second copy of the medication list exists, one of them is wrong.
 *
 * Stale entries are on the list, deliberately and with the word STALE next to
 * them. Absence of evidence is never evidence of absence, and a medication
 * whose script should have run out is the entry a clinician most needs to ask
 * about, not one to hide.
 */

import { useEffect, useState } from "react";
import { api } from "../api";
import { Link } from "../router";
import type { MedicationRow, WikiIndex } from "../types";
import { Cite, Empty, Heading, StatusMark, TierMark } from "./marks";

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
        Nothing has been filed yet. Capture something, and once it has been read and
        you have confirmed what it says, it appears here.
      </Empty>
    );
  }

  return (
    <section>
      <Heading>Current medications</Heading>
      <p className="text-[color:var(--color-muted)]">
        Generated from the record each time it is asked for, never stored as a file.
      </p>
      {meds.length === 0 ? (
        <Empty>No medication has been confirmed yet.</Empty>
      ) : (
        <table>
          <thead>
            <tr>
              <th className="w-20">Status</th>
              <th>Medication</th>
              <th>Dose</th>
              <th className="w-14">Tier</th>
              <th className="w-28">Last confirmed</th>
              <th className="w-28">Expected to run out</th>
            </tr>
          </thead>
          <tbody>
            {meds.map((row) => (
              <tr key={row.id}>
                <td>
                  <StatusMark status={row.status} />
                </td>
                <td>
                  <Link to={`/record/${row.id}`} navigate={navigate}>
                    {row.name}
                  </Link>
                  {row.stop_reported ? (
                    <span className="text-[color:var(--color-muted)]">
                      {` — you reported stopping this on ${row.stop_reported}, from a `}
                      {row.stop_reported_tier} source; it stays on the list
                    </span>
                  ) : null}
                </td>
                <td>
                  <Dose row={row} />
                </td>
                <td>{row.evidence_tier ? <TierMark tier={row.evidence_tier} /> : null}</td>
                <td className="whitespace-nowrap">{row.last_confirmed ?? "—"}</td>
                <td className="whitespace-nowrap">{row.expected_exhaustion ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {(["allergy", "problem", "person"] as const).map((kind) => {
        const rows = data.kinds[kind] ?? [];
        return (
          <div key={kind}>
            <Heading>{KIND_TITLES[kind]}</Heading>
            {rows.length === 0 ? (
              <Empty>
                {kind === "allergy"
                  ? "No allergy has been confirmed. An unconfirmed one is not shown here — it waits in the review queue until you decide."
                  : `Nothing filed under ${KIND_TITLES[kind]?.toLowerCase()}.`}
              </Empty>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th className="w-20">Status</th>
                    <th>Name</th>
                    <th className="w-14">Tier</th>
                    <th className="w-28">Last confirmed</th>
                    <th className="w-32">Sources</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.id}>
                      <td>
                        <StatusMark status={row.status} />
                      </td>
                      <td>
                        <Link to={`/record/${row.id}`} navigate={navigate}>
                          {row.name}
                        </Link>
                        {row.is_stub ? (
                          <span className="text-[color:var(--color-muted)]">
                            {" "}
                            — merged into {row.merged_into}
                          </span>
                        ) : null}
                      </td>
                      <td>{row.evidence_tier ? <TierMark tier={row.evidence_tier} /> : null}</td>
                      <td className="whitespace-nowrap">{row.last_confirmed ?? "—"}</td>
                      <td className="font-mono">{row.sources.join(" ") || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        );
      })}

      {data.review.total > 0 ? (
        <>
          <Heading>Waiting for you</Heading>
          <p>
            {data.review.total} {data.review.total === 1 ? "item is" : "items are"} waiting
            to be reviewed — {data.review.by_tier.high ?? 0} of them high-consequence.
            Nothing high-consequence reaches the record without your tap.
          </p>
          <p className="text-[color:var(--color-muted)]">
            The review inbox is phase 7. Until it lands, use{" "}
            <code className="font-mono">health-agent rebuild</code> on the terminal to
            see what is queued.
          </p>
        </>
      ) : null}
    </section>
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
        {row.dose_readings.join(" / ")}
        <span className="block text-[color:var(--color-muted)]">
          sources disagree — nothing has been picked for you
        </span>
      </span>
    );
  }
  return <span className="text-[color:var(--color-muted)]">not stated</span>;
}

export { Cite };
