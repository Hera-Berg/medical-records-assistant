import { useEffect, useState } from "react";
import { isDemoRecord } from "../record";
import { api } from "../api";
import type { CareTeamMember, CareTeamProfile } from "../types";

const PHOTOS: Record<string, { file: string; position: string }> = {
  morganbell: { file: "istockphoto-1532552096-612x612.jpg", position: "49% 23%" },
  jamessmith: { file: "doctor-2337835_640.jpg", position: "50% 22%" },
  matthewthompson: { file: "images (12).jpeg", position: "50% 20%" },
};
const GROUPS = [
  ["facility", "Facility team"],
  ["external", "External specialists & allied health"],
  ["family", "Family & carers"],
] as const;
const display = (text: string) => text.replace(/\s*\(fictional\)/gi, "");
const initials = (name: string) => display(name).replace(/^Dr\.?\s+/i, "").split(/\s+/).filter(Boolean).map(word => Array.from(word)[0]).slice(0, 2).join("").toUpperCase();

function Member({ member, demo }: { member: CareTeamMember; demo: boolean }) {
  const photo = demo ? PHOTOS[member.id] : undefined;
  const practice = display(member.organisation);
  const documentedLocation = display(member.location ?? "");
  const location = documentedLocation === practice ? "" : documentedLocation.startsWith(`${practice} · `) && practice
    ? documentedLocation.slice(practice.length + 3) : documentedLocation;
  return (
    <li className="care-team-card surface" data-member={member.id}>
      <figure className="care-team-portrait">
        <div className="care-team-frame">
          {photo ? (
            <img src={`/assets/care-team/${encodeURIComponent(photo.file)}`} alt={`Illustrative portrait for ${member.name}`} style={{ objectPosition: photo.position }} />
          ) : member.kind === "organisation" ? (
            <svg aria-label="Organisation" role="img" viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M5 21V4h14v17M3 21h18M9 21v-5h6v5M8 8h2m4 0h2M8 12h2m4 0h2" />
            </svg>
          ) : <span aria-hidden="true">{initials(member.name)}</span>}
        </div>
        {photo ? <figcaption>stock photo</figcaption> : null}
      </figure>
      <div className="care-team-details">
        <h3>{display(member.name)}</h3>
        <p className="care-team-role">{member.role}</p>
        {member.organisation ? <p className="care-team-practice">{display(member.organisation)}</p> : null}
        {location ? <p className="care-team-location">{location}</p> : null}
      </div>
    </li>
  );
}

export function CareTeam({ version }: { version: number }) {
  const [data, setData] = useState<CareTeamProfile | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    api.profile().then(result => {
      if (!Array.isArray(result.care_team) || result.care_team.some(member =>
        !member.id || !GROUPS.some(([group]) => group === member.group))) {
        throw new Error("The running app needs a restart to load the updated care-team data. Stop it with Control-C in its terminal, then run the launch command again and refresh this page.");
      }
      if (live) { setData(result); setError(null); }
    })
      .catch((err: Error) => { if (live) setError(err.message); });
    return () => { live = false; };
  }, [version]);
  if (error) return <p role="alert">Could not load your care team: {error}</p>;
  if (!data) return <p role="status">Loading your care team…</p>;
  const demo = data.demo_scenario === "jane-two-year-v1";
  return (
    <div className="care-team-page">
      {GROUPS.map(([group, title]) => {
        const members = data.care_team.filter(member => member.group === group);
        return members.length ? (
          <section key={group} aria-labelledby={`care-team-${group}`}>
            <h2 className="section-heading" id={`care-team-${group}`}>{title}</h2>
            <ul className="care-team-grid">{members.map(member => <Member key={member.id} member={member} demo={demo} />)}</ul>
          </section>
        ) : null;
      })}
      {!data.care_team.length ? <p>No care-team details recorded yet. Add your usual GP and pharmacy in your profile.</p> : null}
      {demo && !isDemoRecord() ? <p className="metadata care-team-disclosure">Names and care-team details are fictional. Photographs illustrate demo profiles.</p> : null}
    </div>
  );
}
