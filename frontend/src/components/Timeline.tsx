/**
 * The screen the user opens most. Reverse-chronological, grouped by month.
 *
 * Every row says three things before its text: what kind of evidence it rests
 * on, when it happened, and **which** of the four timestamps that "when" is.
 * The last of those is not a detail. A row placed on ingest time and presented
 * as when something happened is wrong by however long the file sat in a
 * downloads folder, and nothing about the page would reveal it.
 *
 * A table, not a feed of cards: this is list-shaped data and a card grid makes
 * it slower to scan while taking more room. The month headings are the one
 * concession to browsing rather than scanning — twenty-five rows of dates in
 * one column is a wall, and a reader looking for "that letter in August" is
 * looking for the month first.
 *
 * The filters say what they filter in words. They were `RX LAB DEV PT INF
 * FILE`, which is a legend to learn before you can narrow your own record.
 */

import { useEffect, useState } from "react";
import { api } from "../api";
import { Link } from "../router";
import type { Timeline as TimelineData, TimelineRow, Tier } from "../types";
import { ALL_TIERS, Cite, DateCell, Empty, MONTHS, TierMark, tierLabel } from "./marks";

export function Timeline({
  navigate,
  subject,
  version,
}: {
  navigate: (to: string) => void;
  subject?: string;
  version: number;
}) {
  const [data, setData] = useState<TimelineData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [tiers, setTiers] = useState<Tier[]>([]);

  useEffect(() => {
    let live = true;
    setError(null);
    api
      .timeline({ from: from || undefined, to: to || undefined, subject, tier: tiers })
      .then((result) => live && setData(result))
      .catch((exc: Error) => live && setError(exc.message));
    return () => {
      live = false;
    };
  }, [from, to, subject, tiers, version]);

  const toggleTier = (tier: Tier) =>
    setTiers((current) =>
      current.includes(tier) ? current.filter((t) => t !== tier) : [...current, tier],
    );

  const filtered = Boolean(from || to || tiers.length > 0);

  return (
    <section>
      <div className="mb-3 flex flex-wrap items-center gap-x-5 gap-y-2 border-b border-[color:var(--color-rule)] pb-3 no-print">
        <span className="flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-2">
            <span className="text-[color:var(--color-muted)]">From</span>
            <input
              type="date"
              value={from}
              onChange={(e) => setFrom(e.target.value)}
              className="field"
            />
          </label>
          <label className="flex items-center gap-2">
            <span className="text-[color:var(--color-muted)]">to</span>
            <input
              type="date"
              value={to}
              onChange={(e) => setTo(e.target.value)}
              className="field"
            />
          </label>
        </span>

        <span className="flex flex-wrap items-center gap-2">
          <span className="text-[color:var(--color-muted)]">Show</span>
          {ALL_TIERS.map((tier) => {
            const on = tiers.includes(tier);
            return (
              <button
                key={tier}
                type="button"
                onClick={() => toggleTier(tier)}
                aria-pressed={on}
                className={`chip cursor-pointer ${on ? "font-semibold" : ""}`}
                style={
                  on
                    ? {
                        borderColor: "var(--color-accent)",
                        background: "var(--color-accent)",
                        color: "var(--color-paper)",
                      }
                    : {
                        borderColor: "var(--color-rule-strong)",
                        background: "var(--color-paper)",
                      }
                }
              >
                {tierLabel(tier)}
              </button>
            );
          })}
        </span>

        {filtered ? (
          <button
            type="button"
            onClick={() => {
              setFrom("");
              setTo("");
              setTiers([]);
            }}
            className="btn"
          >
            Show everything
          </button>
        ) : null}

        {subject ? (
          <Link to="/" navigate={navigate} className="ml-auto">
            showing one entry only — show the whole record
          </Link>
        ) : null}
      </div>

      {error ? <Empty>Could not read the timeline: {error}</Empty> : null}

      {data && data.rows.length === 0 ? (
        <Empty>
          {data.total === 0 && !filtered
            ? "Nothing in the record yet. Drop a file anywhere on this page, or paste one."
            : "Nothing in the record matches those filters."}
        </Empty>
      ) : null}

      {data && data.rows.length > 0 ? (
        <>
          <p className="pb-2 text-[color:var(--color-muted)]">
            {data.total} {data.total === 1 ? "entry" : "entries"}
            {data.rows.length < data.total ? `, showing ${data.rows.length}` : ""}
          </p>
          <div className="table-wrap"><table>
            <thead>
              <tr>
                <th className="w-32">Where from</th>
                <th className="w-56">When</th>
                <th>What</th>
                <th className="w-40">The document</th>
              </tr>
            </thead>
            <tbody>
              {withMonths(data.rows).map((entry) =>
                entry.kind === "month" ? (
                  <tr key={`month-${entry.month}`}>
                    <th
                      colSpan={4}
                      className="pt-4 text-lg font-semibold text-[color:var(--color-ink)]"
                    >
                      {monthName(entry.month)}
                    </th>
                  </tr>
                ) : (
                  <tr key={entry.row.event_id}>
                    <td>
                      <TierMark tier={entry.row.marker} />
                    </td>
                    <td>
                      <DateCell date={entry.row.date} kind={entry.row.date_label} />
                    </td>
                    <td>
                      {entry.row.subject_id ? (
                        <Link to={`/record/${entry.row.subject_id}`} navigate={navigate}>
                          {entry.row.text}
                        </Link>
                      ) : (
                        entry.row.text
                      )}
                      {/* What happens to this document next. Every artefact row
                          says it, including "read, and you have decided about
                          all of it" — a row that goes quiet is indistinguishable
                          from one nothing has looked at. */}
                      {entry.row.reading ? (
                        <span
                          className={
                            "mt-0.5 block " +
                            (entry.row.reading.awaiting > 0
                              ? "text-[color:var(--color-ink)]"
                              : "text-[color:var(--color-muted)]")
                          }
                        >
                          {entry.row.reading.text}
                        </span>
                      ) : null}
                    </td>
                    <td>
                      <Cite citation={entry.row.citation} navigate={navigate} />
                    </td>
                  </tr>
                ),
              )}
            </tbody>
          </table></div>
        </>
      ) : null}
    </section>
  );
}

type Entry = { kind: "month"; month: string } | { kind: "row"; row: TimelineRow };

/**
 * Month headings, inserted where the month changes.
 *
 * Driven by the row's own `month`, which the server derives from the same date
 * it renders in the row. Deriving it here from the rendered string would be a
 * second parser that disagrees with the first one on exactly the rows that are
 * hardest to read — the uncertain ones, whose rendered date is a band.
 */
function withMonths(rows: TimelineRow[]): Entry[] {
  const out: Entry[] = [];
  let seen: string | null = null;
  for (const row of rows) {
    if (row.month !== seen) {
      seen = row.month;
      out.push({ kind: "month", month: row.month });
    }
    out.push({ kind: "row", row });
  }
  return out;
}

function monthName(month: string): string {
  const [year, index] = month.split("-");
  const name = MONTHS[Number(index) - 1];
  return name ? `${name} ${year}` : month;
}
