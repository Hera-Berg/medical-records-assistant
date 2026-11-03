/**
 * Everything that is not one of the main tabs, in one list. Nothing that was in
 * the old menu has gone; it has moved here.
 */

import { Link } from "../router";
import type { Health } from "../types";
import { ChevronRight } from "./icons";

interface Entry {
  to: string;
  label: string;
  hint?: string;
  count?: number;
  phoneOnly?: boolean;
}

export function More({ health, navigate }: { health: Health | null; navigate: (to: string) => void }) {
  const waiting = health?.review.total ?? 0;
  const groups: { title: string; entries: Entry[] }[] = [
    {
      title: "To do",
      entries: [
        { to: "/review", label: "Check what was read", hint: "A quick yes or no on things read from your documents", count: waiting },
        { to: "/notifications", label: "Messages from your care team" },
        { to: "/prepare", label: "Get ready for a visit", hint: "A one-page sheet to print and take with you" },
      ],
    },
    {
      title: "Your record",
      entries: [
        { to: "/calendar", label: "Appointments", phoneOnly: true },
        { to: "/documents", label: "Documents", hint: "Letters, results and scripts" },
        { to: "/allergies", label: "Allergies" },
        { to: "/conditions", label: "Conditions" },
        { to: "/symptoms", label: "Symptoms you mentioned" },
        { to: "/care-team", label: "Care team" },
        { to: "/profile", label: "Profile", hint: "Your details, contacts and preferences" },
      ],
    },
    {
      title: "This app",
      entries: [
        { to: "/settings", label: "Settings", hint: "Where your record is kept and what reads it" },
        { to: "/files", label: "Your folder", hint: "The files your record is made of" },
      ],
    },
  ];

  return (
    <div className="pt-page">
      <h1 className="pt-h1">More</h1>
      {groups.map((group) => (
        <section key={group.title} className="pt-section" aria-labelledby={`more-${group.title}`}>
          <h2 id={`more-${group.title}`} className="pt-h2">{group.title}</h2>
          <ul className="pt-rows">
            {group.entries.map((entry) => (
              <li key={entry.to} className={entry.phoneOnly ? "pt-phone-only" : undefined}>
                <Link to={entry.to} navigate={navigate} className="pt-row">
                  <span className="pt-row-main">
                    <span className="pt-row-name">{entry.label}</span>
                    {entry.hint ? <span className="pt-row-sub">{entry.hint}</span> : null}
                  </span>
                  {entry.count ? (
                    <span className="pt-count">
                      {entry.count}
                      <span className="sr-only"> waiting</span>
                    </span>
                  ) : null}
                  <ChevronRight />
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ))}

      <section className="pt-section" aria-labelledby="more-workspace">
        <h2 id="more-workspace" className="pt-h2">Workspace</h2>
        <ul className="pt-rows">
          <li>
            <a href="/clinician" className="pt-row">
              <span className="pt-row-main">
                <span className="pt-row-name">Clinician workspace</span>
                <span className="pt-row-sub">For the care team. Needs a clinician sign-in.</span>
              </span>
              <ChevronRight />
            </a>
          </li>
        </ul>
      </section>
    </div>
  );
}
