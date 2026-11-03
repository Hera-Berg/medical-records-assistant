/**
 * Home: what is next, what needs a look, and the record in three lines.
 *
 * Reads the same reconciled record every other screen reads and writes
 * nothing. Each part loads on its own, so one part failing says so in its own
 * place rather than blanking the page — and a part that failed to load never
 * reads as an empty record ("no allergies" is a fact; "could not load" is not).
 */

import { useEffect, useState } from "react";
import { api } from "../api";
import { Link } from "../router";
import type { Health, Prescription, SharedCare, WikiIndex } from "../types";
import { ChevronRight } from "./icons";
import { appointmentWhen, nextAppointment } from "./appointments";

type Loaded<T> = { state: "loading" } | { state: "ok"; value: T } | { state: "failed" };

function useLoad<T>(load: () => Promise<T>, version: number): Loaded<T> {
  const [result, setResult] = useState<Loaded<T>>({ state: "loading" });
  useEffect(() => {
    let live = true;
    load()
      .then((value) => live && setResult({ state: "ok", value }))
      .catch(() => live && setResult({ state: "failed" }));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version]);
  return result;
}

export function Today({
  health,
  navigate,
  version,
  notice,
}: {
  health: Health | null;
  navigate: (to: string) => void;
  version: number;
  /** The app-wide notices (reader not set up, app not answering). */
  notice?: React.ReactNode;
}) {
  const wiki = useLoad(() => api.wiki(), version);
  const calendar = useLoad(() => api.calendar(), version);
  const scripts = useLoad(() => api.prescriptions(), version);
  const care = useLoad(() => api.sharedCare(), version);
  const recent = useLoad(() => api.clinicalTimeline(false), version);

  const next = calendar.state === "ok" ? nextAppointment(calendar.value.events, calendar.value.today) : null;
  const looks = needsALook(health, scripts, care);

  return (
    <div className="pt-page">
      <h1 className="pt-h1">Today</h1>

      <section aria-labelledby="next-heading" className="pt-card pt-next">
        {calendar.state === "loading" ? (
          <p className="pt-muted" role="status">Looking for your next appointment…</p>
        ) : calendar.state === "failed" ? (
          <p>
            <span id="next-heading" className="pt-label">Next appointment</span>
            Your appointments could not be loaded just now.{" "}
            <Link to="/calendar" navigate={navigate}>Open Appointments</Link>
          </p>
        ) : next ? (
          <>
            <div>
              <h2 id="next-heading" className="pt-label">Next appointment</h2>
              <p className="pt-next-title">{next.title}</p>
              <p className="pt-next-when">{appointmentWhen(next, calendar.value.today)}</p>
            </div>
            <Link
              to={`/prepare?appointment=${encodeURIComponent(next.id)}`}
              navigate={navigate}
              className="pt-btn"
            >
              Get ready<span className="sr-only"> for {next.title}</span>
            </Link>
          </>
        ) : (
          <>
            <div>
              <h2 id="next-heading" className="pt-label">Next appointment</h2>
              <p>Nothing booked yet.</p>
            </div>
            <Link to="/prepare" navigate={navigate} className="pt-btn-quiet">
              Make a sheet for a visit
            </Link>
          </>
        )}
      </section>

      {notice}

      {looks.length > 0 ? (
        <section aria-labelledby="look-heading" className="pt-section">
          <h2 id="look-heading" className="sr-only">Needs a look</h2>
          <ul className="pt-notices">
            {looks.map((look) => (
              <li key={look.key}>
                <Link to={look.to} navigate={navigate} className="pt-notice">
                  <span>
                    <span className="pt-notice-lead">Needs a look: </span>
                    {look.text}
                  </span>
                  <ChevronRight />
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <section aria-labelledby="record-heading" className="pt-section">
        <h2 id="record-heading" className="sr-only">Your record</h2>
        <ul className="pt-rows">
          <RecordRow to="/medicines" label="Medicines" wiki={wiki} kind="med" navigate={navigate} />
          <RecordRow to="/allergies" label="Allergies" wiki={wiki} kind="allergy" navigate={navigate} />
          <RecordRow to="/conditions" label="Conditions" wiki={wiki} kind="problem" navigate={navigate} />
          {wiki.state === "ok" && (wiki.value.kinds.symptom ?? []).length > 0 ? (
            <RecordRow to="/symptoms" label="Symptoms you mentioned" wiki={wiki} kind="symptom" navigate={navigate} />
          ) : null}
        </ul>
      </section>

      <section aria-labelledby="recent-heading" className="pt-section">
        <div className="pt-section-head">
          <h2 id="recent-heading" className="pt-h2">Recent</h2>
          <Link to="/timeline" navigate={navigate}>See all history</Link>
        </div>
        {recent.state === "loading" ? (
          <p className="pt-muted" role="status">Loading…</p>
        ) : recent.state === "failed" ? (
          <p className="pt-muted">Recent updates could not be loaded just now.</p>
        ) : recent.value.events.length === 0 ? (
          <p className="pt-muted">Nothing yet. Use Add to put in a photo, a letter or a note.</p>
        ) : (
          <ul className="pt-rows">
            {recent.value.events.slice(0, 3).map((row) => (
              <li key={row.id}>
                <Link to={row.source_url || "/timeline"} navigate={navigate} className="pt-row">
                  <span className="pt-row-main">
                    <span className="pt-row-name">{row.title}</span>
                    <span className="pt-row-sub">
                      {longDate(row.date)}
                      {row.author ? ` · ${row.author}` : ""}
                    </span>
                  </span>
                  <ChevronRight />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function RecordRow({
  to,
  label,
  wiki,
  kind,
  navigate,
}: {
  to: string;
  label: string;
  wiki: Loaded<WikiIndex>;
  kind: string;
  navigate: (to: string) => void;
}) {
  let value: string;
  if (wiki.state === "loading") value = "…";
  else if (wiki.state === "failed") value = "Could not load";
  else {
    const rows = (wiki.value.kinds[kind] ?? []).filter((row) => !row.is_stub && !row.merged_into);
    const current = kind === "med" ? rows.filter((row) => row.status !== "stopped") : rows;
    value =
      current.length === 0
        ? kind === "allergy"
          ? "None confirmed"
          : "None recorded"
        : String(current.length);
  }
  return (
    <li>
      <Link to={to} navigate={navigate} className="pt-row">
        <span className="pt-row-name">{label}</span>
        <span className="pt-row-value">
          {value}
          {/^\d+$/.test(value) ? <span className="sr-only"> recorded</span> : null}
        </span>
        <ChevronRight />
      </Link>
    </li>
  );
}

interface Look {
  key: string;
  text: string;
  to: string;
}

function needsALook(
  health: Health | null,
  scripts: Loaded<{ prescriptions: Prescription[] }>,
  care: Loaded<SharedCare>,
): Look[] {
  const looks: Look[] = [];
  const waiting = health?.review.total ?? 0;
  if (waiting > 0) {
    looks.push({
      key: "review",
      text: `${waiting} ${waiting === 1 ? "thing" : "things"} read from your documents ${waiting === 1 ? "needs" : "need"} a quick yes or no`,
      to: "/review",
    });
  }
  const unread = health?.review.could_not_read ?? 0;
  if (unread > 0) {
    looks.push({
      key: "unread",
      text: `${unread} ${unread === 1 ? "document" : "documents"} could not be fully read`,
      to: "/review",
    });
  }
  if (scripts.state === "ok") {
    const seen = new Set<string>();
    for (const script of scripts.value.prescriptions) {
      if (script.treatment_status !== "current" || script.expiry?.state !== "expired") continue;
      if (seen.has(script.medicine)) continue;
      seen.add(script.medicine);
      looks.push({
        key: `expired-${script.id}`,
        text: `${script.medicine} script has expired`,
        to: script.medicine_id ? `/record/${encodeURIComponent(script.medicine_id)}` : "/medicines",
      });
    }
  }
  if (care.state === "ok") {
    // A message nobody has said they have seen. Acknowledging is an explicit act
    // on its own screen; reading Home is not one.
    const open = (care.value.messages ?? []).filter(
      (message) => (message.acknowledgements ?? []).length === 0,
    ).length;
    if (open > 0) {
      looks.push({
        key: "messages",
        text: `${open} ${open === 1 ? "message" : "messages"} from your care team`,
        to: "/notifications",
      });
    }
  }
  return looks;
}

const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

/** "8 October 2026", from a calendar date. Never through the browser's locale. */
export function longDate(iso: string | null | undefined): string {
  if (!iso) return "Date not recorded";
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!match) return iso;
  return `${Number(match[3])} ${MONTHS[Number(match[2]) - 1]} ${match[1]}`;
}
