/**
 * The shell: the rail, the page heading, what needs a person, and the screen.
 *
 * Timeline, record, one entity, one artefact, the folder and settings — plus
 * capture, which is not a screen so much as something the whole window does.
 * The recorder lives inside capture rather than beside it: speaking into the
 * record is the same action as dropping a file in, arriving through a different
 * input, and a tab of its own would imply a conversation.
 *
 * The review inbox is here; the consultation summary is phase 8. The inbox is
 * the only screen that puts anything into the record, which is why it is the
 * one place a proposed value is shown at all — everywhere else a gated value
 * would be read as current.
 *
 * There is also no assistant to ask. Answering questions about the record is
 * phase 10 and it arrives with retrieval, citation-per-sentence and a refusal
 * classifier in front of it; a chat box wired to the model before those exist
 * is the one screen this project must not ship, because it would answer health
 * questions fluently and cite nothing.
 *
 * **The heading is owned by whichever screen knows the answer.** The shell sets
 * a default from the route; an entity or an artefact replaces it once loaded,
 * because "Perindopril" is a better page title than "Record entry" and the
 * shell cannot know it without fetching what the screen is already fetching.
 */

import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { Link, useRoute } from "./router";
import type { Health } from "./types";
import { Artifact } from "./components/Artifact";
import { Attention } from "./components/Attention";
import { Boundary } from "./components/Boundary";
import { CapturePanel, DropOverlay, useCapture, useWindowCapture } from "./components/Capture";
import { Entity } from "./components/Entity";
import { Files } from "./components/Files";
import { Record } from "./components/Record";
import { Review } from "./components/Review";
import { Settings } from "./components/Settings";
import { Sidebar } from "./components/Sidebar";
import { Timeline } from "./components/Timeline";

/** How often the sidebar and banners refresh. The route is cheap and opens no socket. */
const HEALTH_INTERVAL_MS = 5000;

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
  const capture = useCapture(refresh);
  const dragging = useWindowCapture(capture.enqueue);

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
          setHealth(result);
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

  const rebuild = useCallback(() => {
    api.rebuild().then(refresh).catch(refresh);
  }, [refresh]);

  const [first, second, ...rest] = route.segments;
  const tail = [second, ...rest].filter(Boolean).join("/");

  let screen: React.ReactNode;
  if (!first) {
    screen = <Timeline navigate={navigate} version={version} />;
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
  } else if (first === "review") {
    screen = <Review version={version} onChanged={refresh} navigate={navigate} />;
  } else if (first === "settings") {
    screen = <Settings version={version} onChanged={refresh} setHeader={setHeader} />;
  } else {
    screen = (
      <p>
        There is no page at <code className="font-mono">{route.path}</code>.{" "}
        <Link to="/" navigate={navigate}>
          Back to the timeline
        </Link>
        .
      </p>
    );
  }

  return (
    <div className="flex min-h-screen flex-col items-stretch md:flex-row">
      {dragging ? <DropOverlay /> : null}
      <Sidebar
        health={health}
        path={route.path}
        navigate={navigate}
        uploading={capture.uploading}
      />

      <div className="min-w-0 flex-1">
        <div className="mx-auto max-w-[78rem] px-4 py-5 sm:px-6 md:px-8 md:py-6">
          <header className="mb-5 flex flex-wrap items-start gap-x-8 gap-y-3">
            <div className="min-w-0">
              <span className="flex flex-wrap items-center gap-3">
                <h1 className="text-xl font-semibold">{header.title}</h1>
                {header.badge}
              </span>
              <p className="mt-1 max-w-2xl text-[color:var(--color-muted)]">
                {header.subtitle}
              </p>
            </div>
            <div className="ml-auto flex items-center gap-4 no-print">
              {health ? (
                <span className="text-right">
                  <span className="block font-semibold">
                    {health.record.artifacts}{" "}
                    {health.record.artifacts === 1 ? "document" : "documents"}
                  </span>
                  <span className="block text-[color:var(--color-muted)]">
                    {health.record.events} entries in the log
                  </span>
                </span>
              ) : null}
              <button
                type="button"
                onClick={rebuild}
                title="Read your folder again and rebuild these pages from it. It only removes pages it wrote itself."
                className="btn"
              >
                Rebuild pages
              </button>
            </div>
          </header>

          <Attention health={health} error={healthError} />

          {/*
            Per screen, and reset by the route: a screen that cannot draw itself
            must not blank the rail, the heading and every other screen with it.
            The built bundle is served from disk while the server holds its own
            version in memory, so "the page is newer than the program answering
            it" is an ordinary state here, not a rare one.
          */}
          <main className="panel">
            <Boundary resetKey={route.path}>{screen}</Boundary>
          </main>

          <footer className="mt-5 text-[color:var(--color-muted)] no-print">
            Everything here is a file in{" "}
            <code className="font-mono">{health?.vault.root ?? "your folder"}</code>. This
            page reports and cites; it does not interpret.
          </footer>
        </div>
      </div>
    </div>
  );
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
  if (!first || first === "timeline") {
    return {
      title: "Timeline",
      subtitle: second
        ? "Everything the record holds about one entry, newest first."
        : "Everything in your record, newest first. Each line says where it came from, and opens the document behind it.",
    };
  }
  if (first === "record" && second) {
    return { title: "One entry in your record", subtitle: "Reading…" };
  }
  if (first === "record") {
    return {
      title: "Your record",
      subtitle:
        "Medications, allergies, problems and people — put together from your own documents, and never from anything you have not confirmed.",
    };
  }
  if (first === "artifact") {
    return { title: "One of your documents", subtitle: "Reading…" };
  }
  if (first === "files") {
    return { title: "Your folder", subtitle: "Reading…" };
  }
  if (first === "review") {
    return {
      title: "Waiting for you",
      subtitle:
        "What has been read out of your documents and is waiting for you to say yes, no, or that it should say something else. Nothing important joins your record until you do.",
    };
  }
  if (first === "settings") {
    return {
      title: "Settings",
      subtitle: "Where your record is kept, and what this app watches out for.",
    };
  }
  if (first === "add") {
    return {
      title: "Add something",
      subtitle:
        "A photo of a script, a letter, a result, a note in your own words, or something said out loud. Nothing asks what kind of thing it is — that is worked out afterwards.",
    };
  }
  return { title: "Not found", subtitle: "" };
}
