/**
 * The timeline as a month on a grid, for the reader who remembers *when* rather
 * than *what* — "something in the week before the holidays".
 *
 * **A date the record does not know to the day is never put on a day.** A row
 * dated "around June 2026 (±15 days)" sitting on 15 June would be the timeline
 * faking precision, which CLAUDE.md calls worse than showing fuzz. So the grid
 * holds only rows whose date is exact, and every row whose band merely overlaps
 * this month is listed above it, band and all. A row with a wide band appears
 * in each month it overlaps, which is what its date says: it could be any of
 * them.
 *
 * The grid shows how many things happened on a day and the first of them; the
 * day itself opens the full rows below, in the same table the list uses, with
 * the evidence tier and the document beside each. A cell that tried to hold
 * every row would be a list squeezed into a box.
 *
 * Weeks start on Monday, from a fixed table rather than the browser's locale,
 * for the same reason the month names do.
 */

import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { Link } from "../router";
import type { TimelineRow, Tier } from "../types";
import { Cite, DateCell, Empty, MONTHS, TierMark } from "./marks";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** Enough for any one month. The list pages; a month on a grid should not have to. */
const MONTH_LIMIT = 5000;

export function TimelineCalendar({
  navigate,
  subject,
  tiers,
  version,
}: {
  navigate: (to: string) => void;
  subject?: string;
  tiers: Tier[];
  version: number;
}) {
  const [month, setMonth] = useState<string | null>(null);
  const [rows, setRows] = useState<TimelineRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [day, setDay] = useState<string | null>(null);

  // Open on the newest month that has anything in it, not on today: a record
  // whose last document arrived in March would otherwise open on an empty grid
  // that looks like a record with nothing in it.
  useEffect(() => {
    if (month) return;
    let live = true;
    api
      .timelineMonths()
      .then((result) => {
        if (!live) return;
        setMonth(result.months[0]?.month ?? todayMonth());
      })
      .catch(() => live && setMonth(todayMonth()));
    return () => {
      live = false;
    };
  }, [month]);

  useEffect(() => {
    if (!month) return;
    let live = true;
    setError(null);
    const [first, last] = monthBounds(month);
    api
      .timeline({ from: first, to: last, subject, tier: tiers, limit: MONTH_LIMIT })
      .then((result) => live && setRows(result.rows))
      .catch((exc: Error) => live && setError(exc.message));
    return () => {
      live = false;
    };
  }, [month, subject, tiers, version]);

  const { byDay, rough } = useMemo(() => place(rows ?? [], month), [rows, month]);

  if (!month) return null;

  const move = (step: number) => {
    setMonth(shiftMonth(month, step));
    setDay(null);
    setRows(null);
  };
  const chosen = day ? (byDay.get(day) ?? []) : [];

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <button type="button" className="btn" onClick={() => move(-1)}>
          ‹ Earlier
        </button>
        <h2 className="min-w-44 text-center text-lg font-semibold">{monthTitle(month)}</h2>
        <button type="button" className="btn" onClick={() => move(1)}>
          Later ›
        </button>
        {month !== todayMonth() ? (
          <button type="button" className="btn" onClick={() => move(monthsBetween(month, todayMonth()))}>
            This month
          </button>
        ) : null}
      </div>

      {error ? <Empty>Could not read the timeline: {error}</Empty> : null}

      {rough.length > 0 ? (
        <div className="mb-3">
          <p className="pb-1 text-[color:var(--color-muted)]">
            Dated only roughly, somewhere in or around {monthTitle(month)} — not placed on a day
            because the record does not say which:
          </p>
          <RowTable rows={rough} navigate={navigate} />
        </div>
      ) : null}

      <div className="table-wrap">
        <table className="calendar" aria-label={`${monthTitle(month)}, by day`}>
          <thead>
            <tr>
              {WEEKDAYS.map((name) => (
                <th key={name}>{name}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {weeks(month).map((week, index) => (
              <tr key={index}>
                {week.map((date, slot) => {
                  if (!date) return <td key={slot} className="calendar-blank" />;
                  const here = byDay.get(date) ?? [];
                  const selected = date === day;
                  return (
                    <td key={date} className={selected ? "calendar-chosen" : undefined}>
                      <button
                        type="button"
                        className="calendar-day"
                        disabled={here.length === 0}
                        aria-pressed={selected}
                        onClick={() => setDay(selected ? null : date)}
                        aria-label={`${Number(date.slice(8))} ${monthTitle(month)}: ${
                          here.length === 0
                            ? "nothing recorded"
                            : `${here.length} ${here.length === 1 ? "entry" : "entries"}`
                        }`}
                      >
                        <span className="font-semibold">{Number(date.slice(8))}</span>
                        {here[0] ? (
                          <>
                            <span className="block truncate">{here[0].text}</span>
                            {here.length > 1 ? (
                              <span className="block text-[color:var(--color-muted)]">
                                and {here.length - 1} more
                              </span>
                            ) : null}
                          </>
                        ) : null}
                      </button>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {rows && rows.length === 0 && !error ? (
        <Empty>Nothing in the record is dated in {monthTitle(month)}.</Empty>
      ) : null}

      {day && chosen.length > 0 ? (
        <div className="mt-4">
          <h3 className="pb-1 font-semibold">
            {Number(day.slice(8))} {monthTitle(month)}
          </h3>
          <RowTable rows={chosen} navigate={navigate} />
        </div>
      ) : rows && rows.length > 0 && rough.length < rows.length ? (
        <p className="mt-2 text-[color:var(--color-muted)]">
          Choose a day to see what happened on it.
        </p>
      ) : null}
    </div>
  );
}

function RowTable({ rows, navigate }: { rows: TimelineRow[]; navigate: (to: string) => void }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th className="w-32">Where from</th>
            <th className="w-56">When</th>
            <th>What</th>
            <th className="w-40">The document</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
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
                {row.reading ? (
                  <span className="mt-0.5 block text-[color:var(--color-muted)]">
                    {row.reading.text}
                  </span>
                ) : null}
              </td>
              <td>
                <Cite citation={row.citation} navigate={navigate} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Exact rows onto their day; everything else into the "roughly" list.
 *
 * "Exact" is the server's own judgement (`date.exact`), and the day is the
 * band's start, which for an exact date is the date. Re-deriving either from
 * the rendered string would be a second date parser that disagrees with the
 * first on precisely the rows that are hardest to read.
 */
function place(
  rows: TimelineRow[],
  month: string | null,
): { byDay: Map<string, TimelineRow[]>; rough: TimelineRow[] } {
  const byDay = new Map<string, TimelineRow[]>();
  const rough: TimelineRow[] = [];
  for (const row of rows) {
    const date = row.date.band.start;
    if (row.date.exact && row.date.band.end === date) {
      if (!month || date.slice(0, 7) !== month) continue;
      const list = byDay.get(date) ?? [];
      list.push(row);
      byDay.set(date, list);
    } else {
      rough.push(row);
    }
  }
  return { byDay, rough };
}

/** Each week of the month as seven ISO dates, Monday first; null outside the month. */
function weeks(month: string): (string | null)[][] {
  const [year, index] = parts(month);
  const days = daysIn(year, index);
  // 0 = Monday. Date.UTC so the browser's timezone cannot move the first day.
  const lead = (new Date(Date.UTC(year, index - 1, 1)).getUTCDay() + 6) % 7;
  const cells: (string | null)[] = Array(lead).fill(null);
  for (let d = 1; d <= days; d++) cells.push(`${month}-${String(d).padStart(2, "0")}`);
  while (cells.length % 7 !== 0) cells.push(null);
  const out: (string | null)[][] = [];
  for (let i = 0; i < cells.length; i += 7) out.push(cells.slice(i, i + 7));
  return out;
}

/** "2026-09" as [2026, 9]. */
function parts(month: string): [number, number] {
  const [year = "1970", index = "1"] = month.split("-");
  return [Number(year), Number(index)];
}

function daysIn(year: number, month: number): number {
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

function monthBounds(month: string): [string, string] {
  const [year, index] = parts(month);
  return [`${month}-01`, `${month}-${String(daysIn(year, index)).padStart(2, "0")}`];
}

function shiftMonth(month: string, step: number): string {
  const [year, index] = parts(month);
  const total = year * 12 + (index - 1) + step;
  return `${Math.floor(total / 12)}-${String((total % 12) + 1).padStart(2, "0")}`;
}

function monthsBetween(from: string, to: string): number {
  const [fy, fm] = parts(from);
  const [ty, tm] = parts(to);
  return ty * 12 + tm - (fy * 12 + fm);
}

function todayMonth(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

function monthTitle(month: string): string {
  const [year, index] = month.split("-");
  return `${MONTHS[Number(index) - 1] ?? index} ${year}`;
}
