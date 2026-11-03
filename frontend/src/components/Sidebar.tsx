/** Persistent workspace navigation; technical record details live in advanced settings. */

import { Link } from "../router";
import type { Health } from "../types";
import { clinicianPath } from "../clinicianAuth";
import type { Workspace } from "../workspace";

export function Sidebar({
  workspace,
  path,
  navigate,
  uploading,
  onClose,
}: {
  workspace: Workspace;
  health: Health | null;
  path: string;
  navigate: (to: string) => void;
  uploading: number;
  /** Close the menu. The shell decides whether choosing a page does too. */
  onClose?: () => void;
}) {
  const cleanPath = path.replace(/\/$/, "") || "/";
  const item = { clinician: workspace === "clinician", path: cleanPath.startsWith("/issue/") ? "/clinician" : cleanPath === "/overview" ? "/" : cleanPath === "/doctor-notes" ? "/progress-notes" : cleanPath === "/artifact" || cleanPath.startsWith("/artifact/") ? "/documents" : cleanPath, navigate };
  const clinician = workspace === "clinician";

  return (
    <aside
      id="menu"
      className={`app-sidebar no-print ${cleanPath === "/timeline" ? "timeline-text-navigation" : ""}`}
      aria-label="Sections of your record"
    >
      <div className="app-brand">
        <span
          aria-hidden="true"
          className="app-brand-mark"
        >
          <Mark />
        </span>
        <span className="app-brand-name">CareMind</span>
        {onClose ? <button type="button" className="icon-btn" onClick={onClose} aria-label="Close the menu">
          <CloseMenu />
        </button> : null}
      </div>

      <nav className="app-navigation">
        <p className="nav-group-label">{clinician ? "Clinician workspace" : "Patient & carer workspace"}</p>
        <div className="flex flex-col gap-0.5" aria-label="Workspace navigation">
          {clinician ? <>
            <Item to="/clinician" label="Review dashboard" exact icon={<Home />} {...item} />
            <Item to="/record" label="Clinical record" icon={<Book />} {...item} />
            <Item to="/documents" label="Documents" icon={<Folder />} {...item} />
            <Item to="/timeline" label="Timeline" icon={<Clock />} {...item} />
            <Item to="/progress-notes" label="Progress notes" icon={<Lock />} {...item} />
            <Item to="/care-team" label="Care team" icon={<Book />} {...item} />
            <Item to="/calendar" label="Calendar" icon={<Clock />} {...item} />
          </> : <>
            <Item to="/" label="Home" exact icon={<Home />} {...item} />
            <Item to="/add" label="Record an update" icon={<Plus />} note={uploading > 0 ? `${uploading} sending` : undefined} {...item} />
            <Item to="/notifications" label="Notifications" icon={<Bell />} {...item} />
            <Item to="/calendar" label="Calendar" icon={<Clock />} {...item} />
            <Item to="/documents" label="Documents" icon={<Folder />} {...item} />
            <Item to="/care-team" label="My care team" icon={<Book />} {...item} />
          </>}
        </div>

        <p className="nav-group-label nav-group-divider">Record tools</p>
        <div className="flex flex-col gap-0.5" aria-label="Record tools">
          {clinician ? <>
            <Item to="/add" label="Upload or record information" icon={<Plus />} note={uploading > 0 ? `${uploading} sending` : undefined} {...item} />
          </> : <>
            <Item to="/timeline" label="Timeline" icon={<Clock />} {...item} />
          </>}
          <Item to="/ask" label={clinician ? "Ask the patient record" : "Ask your record"} icon={<Question />} {...item} />
        </div>

        <div className="mt-auto flex flex-col gap-0.5 pt-4" aria-label="Profile and settings">
          <Item to="/profile" label={clinician ? "Patient profile" : "My profile"} icon={<Book />} {...item} />
          <Item to="/settings" label="Settings" icon={<Gear />} {...item} />
        </div>
      </nav>

    </aside>
  );
}

/** Three lines, the menu button everyone already knows. */
export function MenuIcon() {
  return (
    <svg viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.7">
      <path d="M3.5 5.5h13M3.5 10h13M3.5 14.5h13" strokeLinecap="round" />
    </svg>
  );
}

/** A panel with its left column marked: "fold the menu away". */
function CloseMenu() {
  return (
    <svg viewBox="0 0 20 20" width="19" height="19" fill="none" stroke="currentColor" strokeWidth="1.6">
      <rect x="2.75" y="3.75" width="14.5" height="12.5" rx="2" />
      <path d="M7.5 3.75v12.5" />
    </svg>
  );
}

/* A question mark, because that is what this one takes. Deliberately not a
   speech bubble: the rail should not promise a conversation with somebody. */
function Question() {
  return (
    <svg viewBox="0 0 20 20" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.6">
      <circle cx="10" cy="10" r="7.5" />
      <path d="M7.9 7.8a2.2 2.2 0 1 1 2.6 2.4v1.3" strokeLinecap="round" />
      <path d="M10.5 14.3h.01" strokeLinecap="round" strokeWidth="1.9" />
    </svg>
  );
}



function Item({
  clinician,
  to,
  label,
  path,
  navigate,
  exact,
  icon,
  note,
}: {
  clinician: boolean;
  to: string;
  label: string;
  path: string;
  navigate: (to: string) => void;
  exact?: boolean;
  icon: React.ReactNode;
  note?: string;
}) {
  const active = exact ? path === to : path === to || path.startsWith(`${to}/`);
  return (
    <Link
      to={clinician ? clinicianPath(to) : to}
      navigate={navigate}
      ariaCurrent={active ? "page" : undefined}
      className={`nav-link ${active ? "is-active" : ""}`}
    >
      <span aria-hidden="true" className="nav-item-icon flex w-5 shrink-0 justify-center">
        {icon}
      </span>
      <span className="nav-label">{label}</span>
      {note ? (
        <span className="badge nav-count">
          {note}
        </span>
      ) : null}
      {active ? <span className="sr-only"> (showing now)</span> : null}
    </Link>
  );
}

/* Icons, drawn here rather than fetched. An icon font or an SVG sprite from a
   CDN is a network call, and this page makes none. Each one sits beside its own
   word — none of them is asked to carry a meaning by itself. */

function Lock() {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <rect x="5" y="11" width="14" height="9" rx="2" />
      <path d="M8 11V8a4 4 0 0 1 8 0v3" strokeLinecap="round" />
    </svg>
  );
}

function Home() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round">
      <path d="M3 10l9-7 9 7v11h-6v-7H9v7H3z" />
    </svg>
  );
}

function Clock() {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" strokeLinecap="round" />
    </svg>
  );
}

function Book() {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path d="M5 4h9a3 3 0 0 1 3 3v13a2 2 0 0 0-2-2H5z" strokeLinejoin="round" />
      <path d="M19 6v14" strokeLinecap="round" />
    </svg>
  );
}

function Plus() {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 8v8M8 12h8" strokeLinecap="round" />
    </svg>
  );
}

function Folder() {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path
        d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function Gear() {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <circle cx="12" cy="12" r="3.2" />
      <path
        d="M12 3.5v2M12 18.5v2M20.5 12h-2M5.5 12h-2M18 6l-1.4 1.4M7.4 16.6 6 18M18 18l-1.4-1.4M7.4 7.4 6 6"
        strokeLinecap="round"
      />
    </svg>
  );
}

function Mark() {
  return (
    <svg width="30" height="30" viewBox="0 0 32 32" fill="none" aria-hidden="true">
      {[0,72,144,216,288].map(angle=><path key={angle} d="M16 15C9 10 10 3 16 2C22 3 23 10 16 15Z" transform={`rotate(${angle} 16 16)`} fill="currentColor" fillOpacity=".09" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round"/>)}
      <circle cx="16" cy="16" r="4.5" fill="var(--color-nav)"/>
      <path d="M16 13.3v5.4M13.3 16h5.4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/>
    </svg>
  );
}

function Bell(){return <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M9 21h6" strokeLinecap="round" strokeLinejoin="round"/></svg>;}
