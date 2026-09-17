/**
 * The small repeated pieces, and the two rules they all obey.
 *
 * **Colour carries no meaning on its own.** Every one of these renders a word
 * as well as a hue, and each keeps a fixed position in its row. A colour-blind
 * reader and a printed sheet must both work, and the consultation summary
 * exists to be printed and handed to a clinician — so anything encoded only in
 * hue is gone exactly when it matters most.
 *
 * **The word is the one the reader would use.** These labels were `RX`, `LAB`,
 * `PT`, `INF` and `STALE`: a legend the owner of the record has to learn before
 * their own medication list means anything. The tier is the thing a clinician
 * must tell apart in a second and the patient must trust, so it is spelled out
 * — "Prescription", "Lab result", "From you". The abbreviations bought a column
 * of width and cost the record its readability, which is a bad trade in a
 * document whose whole purpose is to be understood by the person it is about.
 */

import type { Citation, FuzzyDate, Status, Tier } from "../types";
import { Link } from "../router";

interface Spec {
  label: string;
  title: string;
  colour: string;
  ground: string;
}

/** Evidence tier, as a clinician has to be able to tell apart in a second. */
const TIERS: Record<Tier, Spec> = {
  "prescriber-issued": {
    label: "Prescription",
    title: "Prescriber-issued — a script, discharge summary or specialist letter",
    colour: "var(--color-tier-rx)",
    ground: "#e8f0ea",
  },
  "lab-issued": {
    label: "Lab result",
    title: "Lab-issued — a pathology or imaging report",
    colour: "var(--color-tier-lab)",
    ground: "#e9edf7",
  },
  "device-recorded": {
    label: "Device",
    title: "Device-recorded — a wearable, BP cuff or glucometer",
    colour: "var(--color-tier-dev)",
    ground: "#eeeaf5",
  },
  "patient-reported": {
    label: "From you",
    title: "Patient-reported — a voice note, typed entry or meal photo",
    colour: "var(--color-tier-pt)",
    ground: "#f6eee7",
  },
  inferred: {
    label: "Suggested",
    title: "Worked out by the model from other entries — always needs your confirmation",
    colour: "var(--color-tier-inf)",
    ground: "#f7eaea",
  },
  artefact: {
    label: "File",
    title: "A file added to the record; nothing has read it yet",
    colour: "var(--color-tier-file)",
    ground: "#eff0f1",
  },
};

export function TierMark({ tier }: { tier: Tier }) {
  const spec = TIERS[tier] ?? TIERS.artefact;
  return (
    <span
      title={spec.title}
      className="chip"
      style={{ color: spec.colour, borderColor: spec.colour, background: spec.ground }}
    >
      {spec.label}
    </span>
  );
}

/**
 * Which model read the document, beside the evidence tier and never merged into
 * it. The tier says what kind of document it is; this says what read it — a
 * fact from the event, not a grade.
 */
export function ReadByMark({
  readBy,
  thisDevice,
}: {
  readBy: { name: string; runtime: string | null; device: string } | null;
  thisDevice: string | null;
}) {
  if (!readBy) return null;
  let where = "";
  if (readBy.runtime === "bundled") {
    where = readBy.device === thisDevice ? " on this computer" : ` on ${readBy.device}`;
  } else if (readBy.runtime === "endpoint") {
    where = " on another computer";
  }
  return (
    <span className="text-[color:var(--color-muted)]">
      read by {readBy.name}
      {where}
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
 * about, which is why its word asks for the one thing that would settle it —
 * "Needs confirming" — rather than describing the record's mood.
 */
const STATUSES: Record<Status, Spec> = {
  active: {
    label: "Active",
    title: "Confirmed and current",
    colour: "var(--color-tier-rx)",
    ground: "#e8f0ea",
  },
  stale: {
    label: "Needs confirming",
    title:
      "Still on the list, and nothing has confirmed it since it was expected to run out",
    colour: "var(--color-tier-pt)",
    ground: "#f6eee7",
  },
  stopped: {
    label: "Stopped",
    title: "Stopped by an explicit act of yours. The file and its history are kept",
    colour: "var(--color-muted)",
    ground: "#f0f1ed",
  },
  conflicted: {
    label: "Sources disagree",
    title: "Two sources disagree. Both are shown; nothing has been picked for you",
    colour: "var(--color-tier-inf)",
    ground: "#f7eaea",
  },
};

export function StatusMark({ status }: { status: Status }) {
  const spec = STATUSES[status] ?? STATUSES.active;
  return (
    <span
      title={spec.title}
      className="chip"
      style={{ color: spec.colour, borderColor: spec.colour, background: spec.ground }}
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
      <span>{date.render}</span>
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
 * The link says what the source **is** — "Photograph", "Lab result PDF", "Your
 * correction" — and not the six hex characters of its hash. The hash is the
 * record's filing system and it belongs in the folder and on the entity page,
 * not in the column a patient reads to find out where a dose came from. The
 * whole citation sentence, hash included, is on the link's title.
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
  const kind = citation.text.split(",")[0] || "the source";
  if (!citation.resolved) {
    return (
      <span title={citation.text} className="text-[color:var(--color-alarm)]">
        source missing
      </span>
    );
  }
  if (citation.artifact) {
    return (
      <Link
        to={`/artifact/${citation.artifact}`}
        navigate={navigate}
        className="whitespace-nowrap"
      >
        <span title={citation.text}>{kind}</span>
      </Link>
    );
  }
  return (
    <span title={citation.text} className="text-[color:var(--color-muted)]">
      {kind}
    </span>
  );
}

/**
 * An ISO date as a person writes it: `2026-08-06` → `6 August 2026`.
 *
 * Split, not parsed. `new Date("2026-08-06")` is UTC midnight, which in any
 * timezone behind Greenwich prints as the fifth of August — a whole day of
 * error introduced by formatting, in a record whose dates are the point.
 * `toLocaleDateString` would additionally make the output depend on the
 * browser's locale, and the rest of this project renders month names from a
 * fixed table for exactly that reason.
 *
 * Anything that is not a plain ISO date is returned untouched rather than
 * guessed at.
 */
export const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

export function longDate(iso: string | null): string | null {
  if (!iso) return iso;
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!match) return iso;
  const month = MONTHS[Number(match[2]) - 1];
  if (!month) return iso;
  return `${Number(match[3])} ${month} ${match[1]}`;
}

/** The same, for a full timestamp. The zone is kept — it is part of the fact. */
export function longStamp(ts: string | null): string | null {
  if (!ts) return ts;
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(ts);
  if (!match) return ts;
  const day = longDate(`${match[1]}-${match[2]}-${match[3]}`);
  const zone = ts.endsWith("Z") ? " UTC" : "";
  return `${day} at ${match[4]}:${match[5]}${zone}`;
}

export function Empty({ children }: { children: React.ReactNode }) {
  return (
    <p className="rounded-lg border border-dashed border-[color:var(--color-rule-strong)] bg-[color:var(--color-shade)] px-3 py-2 text-[color:var(--color-muted)]">
      {children}
    </p>
  );
}

export function Heading({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="mt-6 mb-2 border-b border-[color:var(--color-rule)] pb-1 text-lg font-semibold">
      {children}
    </h2>
  );
}
