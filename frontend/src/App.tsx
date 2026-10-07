/**
 * The shell: the menu, the page heading, what needs a person, and the screen.
 *
 * Shaped like the assistants people already know how to use: a button at the
 * top left opens a menu down the left, and the page is one readable column.
 * The app opens on "Ask your record", because a box that says "ask" is the
 * least daunting first thing a record can show — and everything else it did
 * before is one tap away in the menu. The timeline moved to `/timeline`.
 *
 * Timeline, record, one entity, one artefact, the folder and settings — plus
 * capture, which is not a screen so much as something the whole window does.
 * The recorder lives inside capture rather than beside it: speaking into the
 * record is the same action as dropping a file in, arriving through a different
 * input, and a tab of its own would imply a conversation.
 *
 * The review inbox is here, and so is the consultation summary. The inbox is
 * the only screen that puts anything into the record, which is why it is the
 * one place a proposed value is shown at all — everywhere else a gated value
 * would be read as current. The summary screen writes too, but only a document:
 * it records that a sheet was prepared and never changes a claim.
 *
 * Asking the record is here too, and has its own tab rather than sitting inside
 * capture: it is a different mode, not a way of putting something in. It is
 * called "Ask your record" and never "chat" — the word promises advice, and the
 * first thing anyone types under that promise is "should I be worried about
 * this", which this application refuses. The label is the cheapest place to
 * stop the question being asked.
 *
 * What made it safe to build is what took until phase 10 to exist: retrieval
 * that is deterministic code, a citation on every sentence or no sentence, and
 * a refusal classifier in front of the whole thing. A chat box wired to the
 * model without those is the one screen this project must not ship, because it
 * would answer health questions fluently and cite nothing.
 *
 * **The heading is owned by whichever screen knows the answer.** The shell sets
 * a default from the route; an entity or an artefact replaces it once loaded,
 * because "Perindopril" is a better page title than "Record entry" and the
 * shell cannot know it without fetching what the screen is already fetching.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { Link, useRoute } from "./router";
import type { Health } from "./types";
import { rememberInstallation } from "./installation";
import { Artifact } from "./components/Artifact";
import { Ask } from "./components/Ask";
import { Attention } from "./components/Attention";
import { Boundary } from "./components/Boundary";
import { CapturePanel, DropOverlay, useCapture, useWindowCapture } from "./components/Capture";
import { Entity } from "./components/Entity";
import { Files } from "./components/Files";
import { Record } from "./components/Record";
import { Review } from "./components/Review";
import { Settings } from "./components/Settings";
import { MenuIcon, Sidebar } from "./components/Sidebar";
import { SummaryScreen } from "./components/Summary";
import { DoctorNotes } from "./components/DoctorNotes";
import { Timeline } from "./components/Timeline";
import { Welcome } from "./components/Welcome";

/** How often the sidebar and banners refresh. The route is cheap and opens no socket. */
const HEALTH_INTERVAL_MS = 5000;

/** Wide enough for the menu to sit beside the page rather than over it. */
const WIDE = "(min-width: 1024px)";
/** Whether the menu was left open on a wide screen. A convenience, per browser. */
const MENU_KEY = "health-agent.menu-open";

export interface PageHeader {
  title: string;
  subtitle: string;
  /** Shown beside the title. The entity's status lives here rather than being
   *  printed a second time inside the panel below it. */
  badge?: React.ReactNode;
}

export function App() {
  const [route, navigate] = useRoute();
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  // Bumped whenever the record changes, so every open screen re-reads. Cheaper
  // and more honest than each screen polling on its own.
  const [version, setVersion] = useState(0);
  const [header, setHeader] = useState<PageHeader>(() => defaultHeader(route.segments));

  const refresh = useCallback(() => setVersion((n) => n + 1), []);
  // Whether the first health answer has been seen, so the welcome question is
  // offered once when the record opens and never again until the next opening.
  const welcomed = useRef(false);
  const capture = useCapture(refresh);
  const dragging = useWindowCapture(capture.enqueue);
  const [wide, menuOpen, setMenuOpen] = useMenu();

  // On a phone the menu lies over the page, so choosing somewhere closes it.
  const go = useCallback(
    (to: string) => {
      if (!wide) setMenuOpen(false);
      navigate(to);
    },
    [wide, navigate, setMenuOpen],
  );

  // The route's own heading, restored on every navigation. A screen that knows
  // better replaces it after its own fetch resolves — which is why this cannot
  // be derived during render: it would win the race back and flicker.
  useEffect(() => {
    setHeader(defaultHeader(route.segments));
  }, [route.path]);

  useEffect(() => {
    let live = true;
    const poll = () =>
      api
        .health()
        .then((result) => {
          if (!live) return;
          rememberInstallation(result.packaged);
          setHealth(result);
          // The first run's last question comes before anything else, once per
          // opening. Not on every poll: the timeline is also at "/", so leaving
          // the question by the sidebar sent the next poll straight back to it.
          if (result.welcome && !welcomed.current && window.location.pathname === "/") {
            navigate("/welcome");
          }
          welcomed.current = true;
          setHealthError(null);
        })
        .catch((exc: Error) => live && setHealthError(exc.message));
    poll();
    const timer = window.setInterval(poll, HEALTH_INTERVAL_MS);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [version]);

  const [first, second, ...rest] = route.segments;
  const tail = [second, ...rest].filter(Boolean).join("/");

  let screen: React.ReactNode;
  if (!first) {
    screen = (
      <Ask
        navigate={navigate}
        onBoxState={refresh}
        documents={health?.record.artifacts ?? null}
      />
    );
  } else if (first === "timeline") {
    screen = <Timeline navigate={navigate} subject={tail || undefined} version={version} />;
  } else if (first === "record" && tail) {
    screen = (
      <Entity
        id={decodeURIComponent(tail)}
        navigate={navigate}
        version={version}
        setHeader={setHeader}
      />
    );
  } else if (first === "record") {
    screen = <Record navigate={navigate} version={version} />;
  } else if (first === "artifact" && tail) {
    screen = <Artifact short={tail} version={version} setHeader={setHeader} />;
  } else if (first === "files") {
    screen = (
      <Files
        path={tail ? tail.split("/").map(decodeURIComponent).join("/") : ""}
        navigate={navigate}
        version={version}
        onChanged={refresh}
        setHeader={setHeader}
      />
    );
  } else if (first === "add") {
    screen = (
      <CapturePanel capture={capture} onCaptured={refresh} navigate={navigate} />
    );
  } else if (first === "summary") {
    screen = (
      <SummaryScreen
        id={tail ? decodeURIComponent(tail) : undefined}
        version={version}
        navigate={navigate}
        onChanged={refresh}
        setHeader={setHeader}
      />
    );
  } else if (first === "doctor-notes") {
    screen = <DoctorNotes version={version} />;
  } else if (first === "review") {
    screen = <Review version={version} onChanged={refresh} navigate={navigate} />;
  } else if (first === "welcome") {
    screen = <Welcome navigate={navigate} onChanged={refresh} />;
  } else if (first === "settings") {
    screen = <Settings version={version} onChanged={refresh} setHeader={setHeader} />;
  } else if (first === "ask") {
    // Where asking used to live. Kept so an old bookmark still lands somewhere.
    screen = (
      <p>
        Asking your record is now the first page.{" "}
        <Link to="/" navigate={navigate}>
          Ask a question
        </Link>
        .
      </p>
    );
  } else {
    screen = (
      <p>
        There is no page at <code className="font-mono">{route.path}</code>.{" "}
        <Link to="/" navigate={navigate}>
          Back to the start
        </Link>
        .
      </p>
    );
  }

  const asking = !first;

  return (
    <div className="flex min-h-screen items-stretch">
      {dragging ? <DropOverlay /> : null}

      {menuOpen ? (
        wide ? (
          <div className="no-print sticky top-0 h-screen shrink-0 border-r border-[color:var(--color-rule)]">
            <Sidebar
              health={health}
              path={route.path}
              navigate={go}
              uploading={capture.uploading}
              onClose={() => setMenuOpen(false)}
            />
          </div>
        ) : (
          <div className="no-print fixed inset-0 z-40 flex">
            <div className="h-full">
              <Sidebar
                health={health}
                path={route.path}
                navigate={go}
                uploading={capture.uploading}
                onClose={() => setMenuOpen(false)}
              />
            </div>
            {/* The page behind, dimmed; tapping it closes the menu. */}
            <button
              type="button"
              aria-label="Close the menu"
              className="h-full flex-1 cursor-default bg-[color:var(--color-ink)]/40"
              onClick={() => setMenuOpen(false)}
            />
          </div>
        )
      ) : null}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="no-print sticky top-0 z-30 flex items-center gap-4 bg-[color:var(--color-paper)] px-2 py-2 sm:px-3">
          {menuOpen && wide ? null : (
            <button
              type="button"
              className="icon-btn relative"
              onClick={() => setMenuOpen(true)}
              aria-label="Open the menu"
              aria-controls="menu"
              aria-expanded={menuOpen}
            >
              <MenuIcon />
              {/* With the menu closed, the one count that matters stays in
                  view. A number, said again in words for a screen reader. */}
              {health && health.review.total > 0 ? (
                <span className="absolute -top-1.5 -right-2.5 min-w-[1.25rem] rounded-full border-2 border-[color:var(--color-paper)] bg-[color:var(--color-accent)] px-1 text-center leading-5 text-[color:var(--color-paper)]">
                  {health.review.total}
                  <span className="sr-only"> waiting for you</span>
                </span>
              ) : null}
            </button>
          )}
          <span className="flex min-w-0 flex-1 items-center gap-3">
            <h1 className="truncate font-semibold">
              {asking ? "Your health record" : header.title}
            </h1>
            {asking ? null : header.badge}
            {/* A label, not a banner: whoever opened a demonstration knows it
                is one, but a printed page or a screenshot must still say so. */}
            {health?.vault.demo ? (
              <span
                className="chip border-[color:var(--color-warn)] text-[color:var(--color-warn)]"
                title="Everything in this record was invented so the screens have something to show."
              >
                Demo
              </span>
            ) : null}
          </span>
          <Link
            to="/add"
            navigate={go}
            className="btn flex shrink-0 items-center gap-1.5 no-underline"
          >
            <span aria-hidden="true">+</span>
            <span>
              Add<span className="hidden sm:inline"> something</span>
            </span>
            {capture.uploading > 0 ? (
              <span className="text-[color:var(--color-muted)]">
                {" "}
                · {capture.uploading} sending
              </span>
            ) : null}
          </Link>
        </header>

        <div
          className={`mx-auto flex w-full flex-1 flex-col px-4 pb-8 sm:px-6 ${
            asking ? "max-w-3xl" : "max-w-[72rem]"
          }`}
        >
          <Attention
            health={health}
            error={healthError}
            navigate={go}
            /* The settings screen holds the control for the box and reports
               what stopped in its own words. One telling, not two. */
            quietEndpoint={first === "settings" || first === "welcome"}
            /* The waiting screen is the list those two notices point at. */
            quietReview={first === "review"}
          />

          {asking ? null : (
            <p className="mt-2 mb-5 max-w-2xl text-[color:var(--color-muted)]">
              {header.subtitle}
            </p>
          )}

          {/*
            Per screen, and reset by the route: a screen that cannot draw itself
            must not blank the menu, the heading and every other screen with it.
            The built bundle is served from disk while the server holds its own
            version in memory, so "the page is newer than the program answering
            it" is an ordinary state here, not a rare one.
          */}
          <main className="flex flex-1 flex-col">
            <Boundary resetKey={route.path}>{screen}</Boundary>
          </main>

          {asking ? null : (
            <footer className="mt-8 border-t border-[color:var(--color-rule)] pt-3 text-[color:var(--color-muted)] no-print">
              Everything here is a file in{" "}
              <code className="font-mono">{health?.vault.root ?? "your folder"}</code>. This
              page reports and cites; it does not interpret.
            </footer>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * Whether the menu is open, and whether the screen is wide enough for it to sit
 * beside the page.
 *
 * Wide screens open with it showing, the way the assistants people know do, and
 * remember if it was folded away. Narrow screens always start with it closed,
 * because there it covers the page. The remembered choice is a convenience in
 * this browser only and the page works the same without it.
 */
function useMenu(): [boolean, boolean, (open: boolean) => void] {
  const [wide, setWide] = useState(() => window.matchMedia(WIDE).matches);
  const [open, setOpenState] = useState(() => wide && readMenuPreference());

  useEffect(() => {
    const query = window.matchMedia(WIDE);
    const onChange = () => {
      setWide(query.matches);
      setOpenState(query.matches && readMenuPreference());
    };
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  // Escape closes the menu when it is lying over the page.
  useEffect(() => {
    if (!open || wide) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpenState(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, wide]);

  const setOpen = useCallback(
    (next: boolean) => {
      setOpenState(next);
      if (!wide) return;
      try {
        window.localStorage.setItem(MENU_KEY, next ? "open" : "closed");
      } catch {
        // Storage refused (a private window): the menu still works, unremembered.
      }
    },
    [wide],
  );

  return [wide, open, setOpen];
}

function readMenuPreference(): boolean {
  try {
    return window.localStorage.getItem(MENU_KEY) !== "closed";
  } catch {
    return true;
  }
}

/**
 * The heading for a route the shell can answer on its own.
 *
 * An entity and an artefact are deliberately vague here — they are replaced the
 * moment the screen below knows the name — and vague is the right failure: a
 * page that briefly says "One entry in your record" and then says "Perindopril"
 * has never said anything untrue.
 */
function defaultHeader(segments: string[]): PageHeader {
  const [first, second] = segments;
  if (!first) {
    return { title: "Your health record", subtitle: "" };
  }
  if (first === "timeline") {
    return {
      title: "Timeline",
      subtitle: second
        ? "Everything the record holds about one entry, newest first."
        : "Everything in your record, newest first. Tap a line to see the document it came from.",
    };
  }
  if (first === "record" && second) {
    return { title: "One entry in your record", subtitle: "Reading…" };
  }
  if (first === "record") {
    return {
      title: "Medications and more",
      subtitle:
        "Your medications, allergies, health problems and the people who look after you — put together from your own documents, and only from what you have confirmed.",
    };
  }
  if (first === "artifact") {
    return { title: "One of your documents", subtitle: "Reading…" };
  }
  if (first === "files") {
    return { title: "Your folder", subtitle: "Reading…" };
  }
  if (first === "welcome") {
    return {
      title: "Your record is ready",
      subtitle: "One more question, then it is yours to use. Everything else is in Settings.",
    };
  }
  if (first === "ask") {
    return { title: "Ask your record", subtitle: "" };
  }
  if (first === "doctor-notes") {
    return {
      title: "Doctor's notes",
      subtitle:
        "Notes a doctor types here are locked with a passphrase. They are kept apart from the rest of the record and never appear in it, on the timeline, on an appointment sheet or in an answer.",
    };
  }
  if (first === "review") {
    return {
      title: "Waiting for you",
      subtitle:
        "Things read from your documents that need a quick yes or no from you. Nothing important joins your record until you say so.",
    };
  }
  if (first === "summary") {
    return second
      ? { title: "Your sheet for this appointment", subtitle: "Reading…" }
      : {
          title: "For an appointment",
          subtitle:
            "One page to take with you: what changed, what you take, what you are allergic to, and the question you came to ask. Every line says which of your documents it came from.",
        };
  }
  if (first === "settings") {
    return {
      title: "Settings",
      subtitle:
        "Where your record is kept, which computer reads it, and what this app watches out for.",
    };
  }
  if (first === "add") {
    return {
      title: "Add something",
      subtitle:
        "A photo of a prescription, a letter, a test result, a note, or a voice recording. You don't need to say what it is — that is worked out afterwards.",
    };
  }
  return { title: "Not found", subtitle: "" };
}
