/**
 * One kind of thing in the record, as a plain list: Medicines, Allergies,
 * Conditions, Symptoms.
 *
 * Every state is a word on the row, never only a colour: "Script expired",
 * "Not confirmed lately", "Two documents disagree". A medication whose sources
 * disagree shows no dose here at all — showing either one would be choosing —
 * and its own page shows both with their sources.
 *
 * Nothing leaves the list because it stopped being mentioned. Stopped
 * medicines are kept, folded under their own heading.
 */

import { useEffect, useState } from "react";
import { api } from "../api";
import { Link } from "../router";
import type { EntitySummary, Prescription, WikiIndex } from "../types";
import { ChevronRight, PrintIcon } from "./icons";

export type ListKind = "med" | "allergy" | "problem" | "symptom";

const TITLES: Record<ListKind, { title: string; empty: string; intro: string }> = {
  med: {
    title: "Medicines",
    empty: "No medicines in your record yet. Add a photo of a script or a pharmacy label and they will appear here once you have checked them.",
    intro: "",
  },
  allergy: {
    title: "Allergies",
    empty: "No allergies confirmed. That is not the same as having none — it means none has been recorded and checked yet.",
    intro: "",
  },
  problem: {
    title: "Conditions",
    empty: "No conditions recorded yet.",
    intro: "",
  },
  symptom: {
    title: "Symptoms you mentioned",
    empty: "No symptoms mentioned yet.",
    intro: "When each was mentioned — not whether it is happening now.",
  },
};

export function kindForPath(first: string | undefined): ListKind | null {
  switch (first) {
    case "medicines":
      return "med";
    case "allergies":
      return "allergy";
    case "conditions":
      return "problem";
    case "symptoms":
      return "symptom";
    default:
      return null;
  }
}

export function pathForKind(kind: string): string {
  return { med: "/medicines", allergy: "/allergies", problem: "/conditions", symptom: "/symptoms" }[kind] ?? "/medicines";
}

export function titleForKind(kind: string): string {
  return (TITLES as Record<string, { title: string }>)[kind]?.title ?? "Your record";
}

export function RecordList({
  kind,
  navigate,
  version,
}: {
  kind: ListKind;
  navigate: (to: string) => void;
  version: number;
}) {
  const [wiki, setWiki] = useState<WikiIndex | null>(null);
  const [scripts, setScripts] = useState<Prescription[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setError(null);
    api
      .wiki()
      .then((result) => live && setWiki(result))
      .catch((exc: Error) => live && setError(exc.message));
    if (kind === "med") {
      api
        .prescriptions()
        .then((result) => live && setScripts(result.prescriptions))
        .catch(() => live && setScripts([]));
    }
    return () => {
      live = false;
    };
  }, [kind, version]);

  const text = TITLES[kind];
  const rows = (wiki?.kinds[kind] ?? []).filter((row) => !row.is_stub && !row.merged_into);
  const current = kind === "med" ? rows.filter((row) => row.status !== "stopped") : rows;
  const stopped = kind === "med" ? rows.filter((row) => row.status === "stopped") : [];

  return (
    <div className="pt-page">
      <div className="pt-title-row">
        <h1 className="pt-h1">{text.title}</h1>
        {current.length > 0 ? (
          <button type="button" className="pt-btn-quiet pt-btn-small no-print" onClick={() => window.print()}>
            <PrintIcon size={16} />
            Print<span className="sr-only"> this list</span>
          </button>
        ) : null}
      </div>
      {text.intro ? <p className="pt-muted pt-intro">{text.intro}</p> : null}

      {error ? (
        <p role="alert" className="pt-alert">
          This list could not be loaded just now. {error}
        </p>
      ) : !wiki ? (
        <p className="pt-muted" role="status">Loading…</p>
      ) : current.length === 0 ? (
        <p className="pt-empty">{text.empty}</p>
      ) : (
        <ul className="pt-rows">
          {current.map((row) => (
            <Row key={row.id} row={row} scripts={scripts} navigate={navigate} />
          ))}
        </ul>
      )}

      {kind === "med" && wiki ? (
        <details className="pt-details">
          <summary>Stopped medicines ({stopped.length})</summary>
          {stopped.length === 0 ? (
            <p className="pt-muted">None. A medicine is only marked stopped when a prescription, a result or you say so — never because it stopped being mentioned.</p>
          ) : (
            <ul className="pt-rows">
              {stopped.map((row) => (
                <Row key={row.id} row={row} scripts={scripts} navigate={navigate} />
              ))}
            </ul>
          )}
        </details>
      ) : null}
    </div>
  );
}

function Row({
  row,
  scripts,
  navigate,
}: {
  row: EntitySummary;
  scripts: Prescription[];
  navigate: (to: string) => void;
}) {
  const notes = rowNotes(row, scripts);
  const value = rowValue(row);
  return (
    <li>
      <Link to={`/record/${encodeURIComponent(row.id)}`} navigate={navigate} className="pt-row">
        <span className="pt-row-main">
          <span className="pt-row-name">{row.name}</span>
          {notes.map((note) => (
            <span key={note.text} className={`pt-row-sub ${note.tone === "warn" ? "is-warn" : ""}`}>
              {note.text}
            </span>
          ))}
        </span>
        {value ? <span className="pt-row-value">{value}</span> : null}
        <ChevronRight />
      </Link>
    </li>
  );
}

function rowValue(row: EntitySummary): string | null {
  if (row.kind === "med") {
    if (row.conflicted) return "Doses differ";
    return row.dose;
  }
  if (row.kind === "symptom") return row.last_reported_ago ? `Mentioned ${row.last_reported_ago}` : null;
  return null;
}

function rowNotes(row: EntitySummary, scripts: Prescription[]): { text: string; tone: "warn" | "plain" }[] {
  const notes: { text: string; tone: "warn" | "plain" }[] = [];
  if (row.status === "stopped") notes.push({ text: "Stopped", tone: "plain" });
  if (row.conflicted) notes.push({ text: "Two documents disagree — open to see both", tone: "warn" });
  if (row.stale)
    notes.push({
      text: row.last_confirmed_ago ? `Not confirmed since ${row.last_confirmed_ago}` : "Not confirmed lately",
      tone: "warn",
    });
  if (row.stop_reported) notes.push({ text: "You said you stopped this", tone: "plain" });
  if (row.kind === "med" && row.status !== "stopped") {
    const current = scripts.filter((script) => script.medicine_id === row.id && script.treatment_status === "current");
    if (current.length > 0 && current.every((script) => script.expiry?.state === "expired")) {
      notes.push({ text: "Script expired", tone: "warn" });
    }
  }
  if (row.review_count > 0)
    notes.push({ text: `${row.review_count} ${row.review_count === 1 ? "thing" : "things"} to check`, tone: "warn" });
  return notes;
}
