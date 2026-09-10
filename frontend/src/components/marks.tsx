/**
 * The small repeated pieces, and the rule they all obey.
 *
 * **Colour carries no meaning on its own.** Every one of these renders a word
 * or an abbreviation as well as a hue, and each keeps a fixed position in its
 * row. A colour-blind reader and a printed sheet must both work, and the
 * consultation summary exists to be printed and handed to a clinician — so
 * anything encoded only in hue is gone exactly when it matters most.
 */

import type { Citation, FuzzyDate, Status, Tier } from "../types";
import { Link } from "../router";

/** Evidence tier, as a clinician has to be able to tell apart in a second. */
const TIERS: Record<Tier, { label: string; title: string; colour: string }> = {
  "prescriber-issued": {
    label: "RX",
    title: "Prescriber-issued — a script, discharge summary or specialist letter",
    colour: "var(--color-tier-rx)",
  },
  "lab-issued": {
    label: "LAB",
    title: "Lab-issued — a pathology or imaging report",
    colour: "var(--color-tier-lab)",
  },
  "device-recorded": {
    label: "DEV",
    title: "Device-recorded — a wearable, BP cuff or glucometer",
    colour: "var(--color-tier-dev)",
  },
  "patient-reported": {
    label: "PT",
    title: "Patient-reported — a voice note, typed entry or meal photo",
    colour: "var(--color-tier-pt)",
  },
  inferred: {
    label: "INF",
    title: "Inferred by the model from other claims — always needs confirmation",
    colour: "var(--color-tier-inf)",
  },
  artefact: {
    label: "FILE",
    title: "A file added to the record; nothing has read it yet",
    colour: "var(--color-tier-file)",
  },
};

export function TierMark({ tier }: { tier: Tier }) {
  const spec = TIERS[tier] ?? TIERS.artefact;
  return (
    <span
      title={spec.title}
      className="inline-block w-11 shrink-0 border px-1 text-center font-mono tabular-nums"
      style={{ color: spec.colour, borderColor: spec.colour }}
    >
      {spec.label}
    </span>
  );
}

export function tierLabel(tier: Tier): string {
  return (TIERS[tier] ?? TIERS.artefact).label;
}

export const ALL_TIERS = Object.keys(TIERS) as Tier[];

/**
 * Entity status.
 *
 * `stale` is a first-class state and not a warning: absence of evidence is
 * never evidence of absence, so a medication whose script should have run out
 * stays on the list saying so. It is the entry a clinician most needs to ask
 * about.
 */
const STATUSES: Record<Status, { label: string; title: string; colour: string }> = {
  active: { label: "ACTIVE", title: "Confirmed and current", colour: "var(--color-tier-rx)" },
  stale: {
    label: "STALE",
    title:
      "Still on the list, and nothing has confirmed it since it was expected to run out",
    colour: "var(--color-tier-pt)",
  },
  stopped: {
    label: "STOPPED",
    title: "Stopped by an explicit act of yours. The file and its history are kept",
    colour: "var(--color-muted)",
  },
  conflicted: {
    label: "CONFLICT",
    title: "Two sources disagree. Both are shown; nothing has been picked for you",
    colour: "var(--color-tier-inf)",
  },
};

export function StatusMark({ status }: { status: Status }) {
  const spec = STATUSES[status] ?? STATUSES.active;
  return (
    <span
      title={spec.title}
      className="inline-block w-20 shrink-0 border px-1 text-center font-mono"
      style={{ color: spec.colour, borderColor: spec.colour }}
    >
      {spec.label}
    </span>
  );
}

/**
 * A date, with its uncertainty drawn as a band rather than hidden.
 *
 * A timeline that fakes precision is worse than one that shows fuzz. An exact
 * date is a plain date; anything else prints the range it could actually be,
 * and draws a span with end caps beneath it — a shape, so the fuzz survives
 * being printed in black and white.
 */
export function DateCell({ date, kind }: { date: FuzzyDate; kind?: string }) {
  return (
    <span className="block whitespace-nowrap">
      <span className={date.exact ? "" : "font-mono"}>{date.render}</span>
      {kind ? <span className="text-[color:var(--color-muted)]"> {kind}</span> : null}
      {date.exact ? null : (
        <>
          <span className="band" aria-hidden="true" />
          <span className="block text-[color:var(--color-muted)]">
            {date.band.start} – {date.band.end}
          </span>
        </>
      )}
    </span>
  );
}

/**
 * A footnote, pointing where the wiki's own footnote points.
 *
 * An unresolved citation is rendered saying so rather than dropped. A sentence
 * that quietly loses its footnote is a lie about how well sourced the record
 * is.
 */
export function Cite({
  citation,
  navigate,
}: {
  citation: Citation;
  navigate: (to: string) => void;
}) {
  if (!citation.resolved) {
    return (
      <span title={citation.text} className="text-[color:var(--color-tier-inf)]">
        source missing
      </span>
    );
  }
  if (citation.artifact) {
    return (
      <Link to={`/artifact/${citation.artifact}`} navigate={navigate} className="font-mono">
        {citation.artifact}
      </Link>
    );
  }
  return (
    <span title={citation.text} className="text-[color:var(--color-muted)]">
      {citation.text.split(",")[0]}
    </span>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return (
    <p className="border border-dashed border-[color:var(--color-rule)] px-3 py-2 text-[color:var(--color-muted)]">
      {children}
    </p>
  );
}

export function Heading({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="mt-5 mb-1 border-b border-[color:var(--color-rule-strong)] pb-0.5 text-lg font-semibold">
      {children}
    </h2>
  );
}
