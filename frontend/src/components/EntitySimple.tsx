/**
 * One entry, for the person whose record it is: what it is, the few facts that
 * matter, and the document it came from — with everything else one tap away.
 *
 * The full page (every reading, superseded values, review history, provenance)
 * is unchanged and sits under "More about this entry". Nothing is hidden that a
 * person could need to check a fact; it is folded, not removed.
 */

import { useEffect, useState } from "react";
import { api } from "../api";
import type { PageHeader } from "../App";
import { Link } from "../router";
import type { Claim, EntityDetail, Prescription } from "../types";
import { Entity } from "./Entity";
import { ChevronLeft } from "./icons";
import { Cite, TierMark } from "./marks";
import { pathForKind, titleForKind } from "./RecordList";
import { longDate } from "./Today";

const FACT_WORDS: Record<string, string> = {
  dose: "Dose",
  reaction: "Reaction",
  started: "Started",
  status: "Status",
  role: "Role",
  contact: "Contact",
  reported: "Mentioned",
  resolved: "Said to have gone",
};

const noHeader = (_: PageHeader) => undefined;

export function EntitySimple({
  id,
  navigate,
  version,
}: {
  id: string;
  navigate: (to: string) => void;
  version: number;
}) {
  const [data, setData] = useState<EntityDetail | null>(null);
  const [scripts, setScripts] = useState<Prescription[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setData(null);
    setError(null);
    api
      .entity(id)
      .then((result) => live && setData(result))
      .catch((exc: Error) => live && setError(exc.message));
    if (id.startsWith("med:")) {
      api
        .prescriptions()
        .then((result) => live && setScripts(result.prescriptions.filter((p) => p.medicine_id === id)))
        .catch(() => live && setScripts([]));
    }
    return () => {
      live = false;
    };
  }, [id, version]);

  const kind = id.split(":")[0] ?? "med";
  const back = (
    <Link to={pathForKind(kind)} navigate={navigate} className="pt-back no-print">
      <ChevronLeft />
      {titleForKind(kind)}
    </Link>
  );

  if (error)
    return (
      <div className="pt-page">
        {back}
        <p role="alert" className="pt-alert">{error}</p>
      </div>
    );
  if (!data)
    return (
      <div className="pt-page">
        {back}
        <p className="pt-muted" role="status">Loading…</p>
      </div>
    );

  const slots = data.slots.filter((slot) => slot.predicate !== "name");
  const dose = slots.find((slot) => slot.predicate === "dose");
  const script = currentScript(scripts);
  const sourceArtifact =
    script?.source ??
    dose?.winner?.citation.artifact ??
    slots.find((slot) => slot.winner?.citation.artifact)?.winner?.citation.artifact ??
    data.sources[0] ??
    null;

  const headline =
    data.kind === "med"
      ? data.conflicted
        ? "Two documents give different doses"
        : data.dose
      : data.kind === "symptom" && data.last_reported
        ? `Last mentioned ${longDate(data.last_reported)}`
        : null;

  return (
    <div className="pt-page">
      {back}
      <h1 className="pt-h1">{data.name}</h1>
      {headline ? <p className="pt-headline">{headline}</p> : null}
      <StatusLine data={data} />

      <dl className="pt-facts">
        {data.kind === "med" && script?.plain_directions ? (
          <Fact label="Directions">{script.plain_directions}</Fact>
        ) : null}
        {data.kind === "med" && script?.prescriber ? <Fact label="Prescribed by">{script.prescriber}</Fact> : null}
        {data.kind === "med" && script ? (
          <Fact label="Script">
            <span className={script.expiry.state === "expired" ? "pt-warn-text" : undefined}>{script.expiry.label}</span>
          </Fact>
        ) : null}
        {slots
          .filter((slot) => slot.winner && !slot.conflicted && !(data.kind === "med" && slot.predicate === "dose"))
          .map((slot) => (
            <Fact key={slot.predicate} label={FACT_WORDS[slot.predicate] ?? slot.predicate}>
              {slot.winner!.value.literal}
            </Fact>
          ))}
        {data.last_confirmed ? <Fact label="Last confirmed">{longDate(data.last_confirmed)}</Fact> : null}
      </dl>

      {slots
        .filter((slot) => slot.conflicted)
        .map((slot) => (
          <section key={slot.predicate} className="pt-card pt-conflict" aria-label={`${FACT_WORDS[slot.predicate] ?? slot.predicate}: sources disagree`}>
            <p className="pt-label">{FACT_WORDS[slot.predicate] ?? slot.predicate} — sources disagree, neither has been chosen</p>
            <ul>
              {slot.readings.map((reading: Claim) => (
                <li key={reading.event_id}>
                  <span className="pt-strong">{reading.value.literal}</span> <TierMark tier={reading.evidence_tier} />{" "}
                  <Cite citation={reading.citation} navigate={navigate} />
                </li>
              ))}
            </ul>
            <Link to="/review" navigate={navigate}>Say which is right</Link>
          </section>
        ))}

      {sourceArtifact ? (
        <Link to={`/artifact/${encodeURIComponent(sourceArtifact)}`} navigate={navigate} className="pt-btn-quiet pt-wide">
          {script ? "See the prescription" : "See where this came from"}
        </Link>
      ) : null}

      <details className="pt-details pt-more">
        <summary>More about this entry</summary>
        <div className="pt-more-body">
          <Entity id={id} navigate={navigate} version={version} setHeader={noHeader} />
        </div>
      </details>
    </div>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="pt-fact">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function StatusLine({ data }: { data: EntityDetail }) {
  const parts: string[] = [];
  if (data.status === "stopped") parts.push("Stopped");
  if (data.stale) parts.push(data.last_confirmed_ago ? `Not confirmed since ${data.last_confirmed_ago}` : "Not confirmed lately");
  if (data.stop_reported) parts.push("You said you stopped taking this; it stays on the list until a prescription or result says so");
  if (data.review_count > 0) parts.push(`${data.review_count} ${data.review_count === 1 ? "thing" : "things"} waiting for you to check`);
  if (parts.length === 0) return null;
  return (
    <ul className="pt-status-line">
      {parts.map((part) => (
        <li key={part}>{part}</li>
      ))}
    </ul>
  );
}

/** The script that applies now: current treatment, most recently written. */
function currentScript(scripts: Prescription[]): Prescription | null {
  const current = scripts
    .filter((script) => script.treatment_status === "current")
    .sort((a, b) => (b.prescribed_date ?? "").localeCompare(a.prescribed_date ?? ""));
  return current[0] ?? null;
}
