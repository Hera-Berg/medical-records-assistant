/**
 * The screen the user opens most. Dense, reverse-chronological, scannable.
 *
 * Every row says three things before its text: what kind of evidence it rests
 * on, when it happened, and **which** of the four timestamps that "when" is.
 * The last of those is not a detail. A row placed on ingest time and presented
 * as when something happened is wrong by however long the file sat in a
 * downloads folder, and nothing about the page would reveal it.
 *
 * A table, not a feed of cards: this is list-shaped data and a card grid makes
 * it slower to scan while taking more room.
 */

import { useEffect, useState } from "react";
import { api } from "../api";
import { Link } from "../router";
import type { Timeline as TimelineData, Tier } from "../types";
import { ALL_TIERS, Cite, DateCell, Empty, TierMark, tierLabel } from "./marks";

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

  return (
    <section>
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 border-b border-[color:var(--color-rule)] pb-1 no-print">
        <label>
          <span className="text-[color:var(--color-muted)]">from </span>
          <input
            type="date"
            value={from}
            onChange={(e) => setFrom(e.target.value)}
            className="border border-[color:var(--color-rule-strong)] px-1"
          />
        </label>
        <label>
          <span className="text-[color:var(--color-muted)]">to </span>
          <input
            type="date"
            value={to}
            onChange={(e) => setTo(e.target.value)}
            className="border border-[color:var(--color-rule-strong)] px-1"
          />
        </label>
        <span className="flex flex-wrap items-baseline gap-1">
          {ALL_TIERS.map((tier) => (
            <button
              key={tier}
              type="button"
              onClick={() => toggleTier(tier)}
              aria-pressed={tiers.includes(tier)}
              title={tier}
              className={`border px-1 font-mono ${
                tiers.includes(tier)
                  ? "border-[color:var(--color-ink)] bg-[color:var(--color-ink)] text-[color:var(--color-paper)] font-semibold"
                  : "border-[color:var(--color-rule-strong)]"
              }`}
            >
              {tierLabel(tier)}
            </button>
          ))}
        </span>
        {(from || to || tiers.length > 0) && (
          <button
            type="button"
            onClick={() => {
              setFrom("");
              setTo("");
              setTiers([]);
            }}
            className="border border-[color:var(--color-rule-strong)] px-2"
          >
            Clear
          </button>
        )}
        {subject ? (
          <Link to="/" navigate={navigate} className="ml-auto">
            showing {subject} only — show everything
          </Link>
        ) : null}
      </div>

      {error ? <Empty>Could not read the timeline: {error}</Empty> : null}

      {data && data.rows.length === 0 ? (
        <Empty>
          {data.total === 0 && !from && !to && tiers.length === 0
            ? "Nothing in the record yet. Drop a file anywhere on this page, or paste one."
            : "Nothing in the record matches those filters."}
        </Empty>
      ) : null}

      {data && data.rows.length > 0 ? (
        <>
          <p className="py-1 text-[color:var(--color-muted)]">
            {data.total} {data.total === 1 ? "entry" : "entries"}
            {data.rows.length < data.total ? `, showing ${data.rows.length}` : ""}
          </p>
          <table>
            <thead>
              <tr>
                <th className="w-14">Tier</th>
                <th className="w-56">When</th>
                <th>What</th>
                <th className="w-24">Source</th>
              </tr>
            </thead>
            <tbody>
              {data.rows.map((row) => (
                <tr key={row.event_id}>
                  <td>
                    <TierMark tier={row.marker} />
                  </td>
                  <td>
                    <DateCell date={row.date} kind={row.date_label} />
                  </td>
                  <td>
                    {row.subject_id ? (
                      <Link to={`/record/${row.subject_id}`} navigate={navigate}>
                        {row.text}
                      </Link>
                    ) : (
                      row.text
                    )}
                  </td>
                  <td>
                    <Cite citation={row.citation} navigate={navigate} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : null}
    </section>
  );
}
