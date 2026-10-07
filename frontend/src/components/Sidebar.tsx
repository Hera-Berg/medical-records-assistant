/**
 * The menu down the left: where you are, and where your record actually lives.
 *
 * Laid out the way the assistants people already use are: a pale column that a
 * button at the top left opens and closes. On a wide screen it sits beside the
 * page and pushes it over; on a phone it lies over the page with the page
 * dimmed behind it, and closes again as soon as somewhere is chosen — sixteen
 * rems of navigation down the side of a 390px screen leaves nothing for the
 * record itself. Either way it opens and closes at once, with no slide.
 *
 * Asking comes first, because it is the page the app opens on, and adding
 * something second, because those are the two things a person arrives to do.
 * The rest of the record is grouped under one heading beneath them.
 *
 * The footer is the part that earns the menu: it says, permanently and without
 * being asked, which folder holds the record and whether anything is currently
 * able to read new files. Both of those used to be shorthand in a strip along
 * the top — `box none configured` — which is a developer's status line, not an
 * answer to "where is my stuff and is it safe".
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
  "not-downloaded": { short: "Needs a one-time download", tone: "quiet" },
  sleeping: { short: "Sleeping — wakes when you add something", tone: "quiet" },
  starting: { short: "Starting up", tone: "quiet" },
  stopped: { short: "Stopped — see Settings", tone: "alarm" },
};

const DOT: Record<"good" | "quiet" | "alarm", string> = {
  good: "#2f8a64",
  quiet: "#8d9a92",
  alarm: "#b4413a",
};

export function Sidebar({
  health,
  path,
  navigate,
  uploading,
  onClose,
}: {
  health: Health | null;
  path: string;
  navigate: (to: string) => void;
  uploading: number;
  /** Close the menu. The shell decides whether choosing a page does too. */
  onClose: () => void;
}) {
  const endpoint = health
    ? health.endpoint.state === "working" && health.endpoint.where === "this-computer"
      ? { short: "Reading on this computer", tone: "good" as const }
      : (ENDPOINT_WORDS[health.endpoint.state] ?? ENDPOINT_WORDS.unknown)
    : null;
  const folder = health ? folderName(health.vault.root) : null;
  const item = { path, navigate };

  return (
    <aside
      id="menu"
      className="no-print flex h-full w-72 flex-col bg-[color:var(--color-nav)] text-[color:var(--color-ink)]"
      aria-label="Sections of your record"
    >
      <div className="flex items-center gap-2 px-3 pt-3 pb-2">
        <span
          aria-hidden="true"
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-[color:var(--color-accent)]"
        >
          <Mark />
        </span>
        <span className="min-w-0 flex-1 truncate font-semibold">Your health record</span>
        <button type="button" className="icon-btn" onClick={onClose} aria-label="Close the menu">
          <CloseMenu />
        </button>
      </div>

      <nav className="flex min-h-0 flex-1 flex-col overflow-y-auto px-2 pb-2">
        <div className="flex flex-col gap-0.5">
          <Item to="/" label="Ask your record" exact icon={<Question />} {...item} />
          <Item
            to="/add"
            label="Add something"
            icon={<Plus />}
            note={uploading > 0 ? `${uploading} sending` : undefined}
            {...item}
          />
        </div>

        <p className="mt-5 mb-1 px-3 text-[color:var(--color-muted)]">Your record</p>
        <div className="flex flex-col gap-0.5">
          <Item
            to="/review"
            label="Waiting for you"
            icon={<Inbox />}
            /* The count, not a dot. "4" says how much work it is; a dot says
               only that there is some, which is the thing people learn to
               ignore. */
            note={health && health.review.total > 0 ? String(health.review.total) : undefined}
            {...item}
          />
          <Item to="/timeline" label="Timeline" icon={<Clock />} {...item} />
          <Item to="/record" label="Medications and more" icon={<Book />} {...item} />
          <Item to="/summary" label="For an appointment" icon={<Sheet />} {...item} />
          <Item to="/doctor-notes" label="Doctor's notes" icon={<Lock />} {...item} />
          <Item to="/files" label="Files" icon={<Folder />} {...item} />
        </div>

        <div className="mt-auto flex flex-col gap-0.5 pt-4">
          <Item to="/settings" label="Settings" icon={<Gear />} {...item} />
        </div>
      </nav>

      <div className="border-t border-[color:var(--color-rule)] px-4 py-3">
        {/* A link, because the sentence "your record is a folder" should be
            something you can act on rather than only read. */}
        <p className="text-[color:var(--color-muted)]">
          Kept in{" "}
          <Link to="/files" navigate={navigate} className="text-[color:var(--color-ink)]">
            <span title={health?.vault.root ?? undefined}>{folder ?? "your folder"}</span>
          </Link>
          {/* A link, because "where is my record kept" and "tell it where my
              record is kept" are the same question asked twice. */}
          {health ? (
            <>
              {" · "}
              <Link
                to="/settings"
                navigate={navigate}
                className="text-[color:var(--color-muted)] no-underline hover:underline"
              >
                {SYNC_WORDS[health.vault.sync_profile] ?? "Synced by your own client"}
              </Link>
            </>
          ) : null}
        </p>

        {endpoint ? (
          <p className="mt-1 flex items-baseline gap-2">
            <span
              aria-hidden="true"
              className="inline-block h-2 w-2 shrink-0 rounded-full"
              style={{ background: DOT[endpoint.tone] }}
            />
            <span>
              <span className="text-[color:var(--color-muted)]">Reading your files: </span>
              {endpoint.short}
            </span>
          </p>
        ) : null}
      </div>
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
  const active = exact ? path === to : path === to || path.startsWith(`${to}/`);
  return (
    <Link
      to={to}
      navigate={navigate}
      className={`flex items-center gap-3 rounded-lg px-3 py-2 text-[color:var(--color-ink)] no-underline hover:bg-[color:var(--color-nav-hover)] ${
        active ? "bg-[color:var(--color-nav-active)] font-semibold" : ""
      }`}
    >
      <span aria-hidden="true" className="flex w-5 shrink-0 justify-center">
        {icon}
      </span>
      <span className="whitespace-nowrap">{label}</span>
      {note ? (
        <span className="ml-auto rounded-full bg-[color:var(--color-paper)] px-2 text-[color:var(--color-muted)]">
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
