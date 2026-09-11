/**
 * The left rail: where you are, and where your record actually lives.
 *
 * Three destinations and a footer. The footer is the part that earns the rail:
 * it says, permanently and without being asked, which folder holds the record
 * and whether anything is currently able to read new files. Both of those used
 * to be shorthand in a strip along the top — `box none configured` — which is a
 * developer's status line, not an answer to "where is my stuff and is it safe".
 *
 * **Unreachable and unauthorised stay different states**, here as everywhere. A
 * sleeping box drains by itself and is worth a quiet line; a rejected key needs
 * a person and is worth an alarm, which is raised in the banner above the page
 * rather than whispered down here. Collapsing them into one "offline" is how a
 * rotated key looks like a sleeping Mac and nobody investigates for a week.
 *
 * Every state word is a word. The dot beside it repeats the state in colour and
 * carries none of the meaning on its own.
 */

import { Link } from "../router";
import type { EndpointState, Health } from "../types";

const SYNC_WORDS: Record<string, string> = {
  local: "A folder on this computer",
  dropbox: "Synced by Dropbox",
  gdrive: "Synced by Google Drive",
  nextcloud: "Synced by Nextcloud",
  other: "Synced by your own client",
};

/**
 * What the inference box is doing, in a sentence a patient can act on.
 *
 * `tone` is only ever used to pick the dot's colour; the sentence says the same
 * thing on its own.
 */
export const ENDPOINT_WORDS: Record<
  EndpointState,
  { short: string; tone: "good" | "quiet" | "alarm" }
> = {
  working: { short: "Ready to read new files", tone: "good" },
  unreachable: { short: "Asleep — files wait safely", tone: "quiet" },
  unauthorised: { short: "Password rejected", tone: "alarm" },
  misconfigured: { short: "Set up incorrectly", tone: "alarm" },
  "vision-not-working": { short: "Cannot read pictures", tone: "alarm" },
  "not-configured": { short: "Not set up yet", tone: "quiet" },
  unknown: { short: "Not checked yet", tone: "quiet" },
};

const DOT: Record<"good" | "quiet" | "alarm", string> = {
  good: "#4ea883",
  quiet: "#8d9a92",
  alarm: "#e0736b",
};

export function Sidebar({
  health,
  path,
  navigate,
  uploading,
}: {
  health: Health | null;
  path: string;
  navigate: (to: string) => void;
  uploading: number;
}) {
  const endpoint = health
    ? (ENDPOINT_WORDS[health.endpoint.state] ?? ENDPOINT_WORDS.unknown)
    : null;
  const folder = health ? folderName(health.vault.root) : null;

  return (
    <aside
      /*
        A rail on a desktop, a bar on a phone. The record is opened on a phone
        in a waiting room, and sixteen rems of navigation down the side of a
        390px screen leaves nothing for the record itself.
      */
      className="no-print flex shrink-0 flex-col bg-[color:var(--color-sidebar)] text-[color:var(--color-sidebar-ink)] md:sticky md:top-0 md:h-screen md:w-64"
      aria-label="Sections of your record"
    >
      <div className="flex items-center gap-3 px-4 pt-4 pb-4 md:pt-5 md:pb-6">
        <span
          aria-hidden="true"
          className="flex h-9 w-9 items-center justify-center rounded-lg bg-[color:var(--color-accent)]"
        >
          <Mark />
        </span>
        <span>
          <span className="block font-semibold">Your health record</span>
          <span className="block text-[color:var(--color-sidebar-muted)]">
            Kept in your own folder
          </span>
        </span>
      </div>

      <nav className="flex flex-wrap gap-0.5 px-2 pb-3 md:block md:gap-1 md:pb-0">
        <Item to="/" label="Timeline" path={path} navigate={navigate} exact icon={<Clock />} />
        <Item to="/record" label="Your record" path={path} navigate={navigate} icon={<Book />} />
        <Item
          to="/review"
          label="Waiting for you"
          path={path}
          navigate={navigate}
          icon={<Inbox />}
          /* The count, not a dot. "4" says how much work it is; a dot says
             only that there is some, which is the thing people learn to
             ignore. */
          note={health && health.review.total > 0 ? String(health.review.total) : undefined}
        />
        <Item
          to="/summary"
          label="For an appointment"
          path={path}
          navigate={navigate}
          icon={<Sheet />}
        />
        <Item to="/files" label="Files" path={path} navigate={navigate} icon={<Folder />} />
        <Item
          to="/add"
          label="Add something"
          path={path}
          navigate={navigate}
          icon={<Plus />}
          note={uploading > 0 ? `${uploading} sending` : undefined}
        />
        <Item
          to="/settings"
          label="Settings"
          path={path}
          navigate={navigate}
          icon={<Gear />}
        />
      </nav>

      <div className="mt-auto hidden border-t border-white/12 px-4 py-4 md:block">
        <p className="text-[color:var(--color-sidebar-muted)]">Your folder</p>
        {/* A link, because the sentence "your record is a folder" should be
            something you can act on rather than only read. */}
        <Link
          to="/files"
          navigate={navigate}
          className="block font-semibold break-words text-[color:var(--color-sidebar-ink)]"
        >
          <span title={health?.vault.root ?? undefined}>{folder ?? "…"}</span>
        </Link>
        {/* A link, because "where is my record kept" and "tell it where my
            record is kept" are the same question asked twice. */}
        <Link
          to="/settings"
          navigate={navigate}
          className="text-[color:var(--color-sidebar-muted)] no-underline hover:underline"
        >
          {health ? (SYNC_WORDS[health.vault.sync_profile] ?? "Synced by your own client") : ""}
        </Link>

        {endpoint ? (
          <p className="mt-3 flex items-baseline gap-2">
            <span
              aria-hidden="true"
              className="inline-block h-2 w-2 shrink-0 rounded-full"
              style={{ background: DOT[endpoint.tone] }}
            />
            <span>
              <span className="block text-[color:var(--color-sidebar-muted)]">
                Reading your files
              </span>
              <span className="block">{endpoint.short}</span>
            </span>
          </p>
        ) : null}
      </div>
    </aside>
  );
}

/** A sheet of paper, which is what this one actually produces. */
function Sheet() {
  return (
    <svg viewBox="0 0 20 20" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.6">
      <path d="M5 2.5h7l3 3V17a.5.5 0 0 1-.5.5h-9A.5.5 0 0 1 5 17V3a.5.5 0 0 1 .5-.5Z" />
      <path d="M12 2.5V6h3" />
      <path d="M7.5 9.5h5M7.5 12.5h5" />
    </svg>
  );
}

function Inbox() {
  return (
    <svg viewBox="0 0 20 20" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.6">
      <path d="M3 11.5V15a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-3.5" />
      <path d="M3 11.5 5 5h10l2 6.5" />
      <path d="M3 11.5h4l1 2h4l1-2h4" />
    </svg>
  );
}


function Item({
  to,
  label,
  path,
  navigate,
  exact,
  icon,
  note,
}: {
  to: string;
  label: string;
  path: string;
  navigate: (to: string) => void;
  exact?: boolean;
  icon: React.ReactNode;
  note?: string;
}) {
  const active = exact ? path === to : path.startsWith(to);
  return (
    <Link
      to={to}
      navigate={navigate}
      className={`flex items-center gap-2 rounded-lg px-3 py-2 md:gap-3 text-[color:var(--color-sidebar-ink)] no-underline hover:bg-white/8 ${
        active ? "bg-white/12 font-semibold" : ""
      }`}
    >
      <span aria-hidden="true" className="flex w-5 shrink-0 justify-center">
        {icon}
      </span>
      <span className="whitespace-nowrap">{label}</span>
      {note ? (
        <span className="ml-auto text-[color:var(--color-sidebar-muted)]">{note}</span>
      ) : null}
      {active ? <span className="sr-only"> (showing now)</span> : null}
    </Link>
  );
}

/* Icons, drawn here rather than fetched. An icon font or an SVG sprite from a
   CDN is a network call, and this page makes none. Each one sits beside its own
   word — none of them is asked to carry a meaning by itself. */

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
    <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="#ffffff" strokeWidth="2">
      <path d="M3 12h4l2-5 3 10 2-5h7" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/** The last segment of the vault path — what the folder is called in Finder. */
function folderName(root: string): string {
  const parts = root.split("/").filter(Boolean);
  return parts[parts.length - 1] ?? root;
}
