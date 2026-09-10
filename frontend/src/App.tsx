/**
 * The shell: navigation, the status strip, and which screen is showing.
 *
 * Four screens in phase 5 — timeline, record, one entity, one artefact — plus
 * capture, which is not a screen so much as something the whole window does.
 *
 * There is no review inbox here and no consultation summary. Those are phases 7
 * and 8, and a half-built review flow would be a way for a high-consequence
 * claim to reach the record without a deliberate tap.
 */

import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { Link, useRoute } from "./router";
import type { Health } from "./types";
import { Artifact } from "./components/Artifact";
import { CapturePanel, DropOverlay, useCapture, useWindowCapture } from "./components/Capture";
import { Entity } from "./components/Entity";
import { Record } from "./components/Record";
import { StatusBar } from "./components/StatusBar";
import { Timeline } from "./components/Timeline";

/** How often the status strip refreshes. The route is cheap and opens no socket. */
const HEALTH_INTERVAL_MS = 5000;

export function App() {
  const [route, navigate] = useRoute();
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  // Bumped whenever the record changes, so every open screen re-reads. Cheaper
  // and more honest than each screen polling on its own.
  const [version, setVersion] = useState(0);

  const refresh = useCallback(() => setVersion((n) => n + 1), []);
  const capture = useCapture(refresh);
  const dragging = useWindowCapture(capture.enqueue);

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
    screen = <Entity id={decodeURIComponent(tail)} navigate={navigate} version={version} />;
  } else if (first === "record") {
    screen = <Record navigate={navigate} version={version} />;
  } else if (first === "artifact" && tail) {
    screen = <Artifact short={tail} version={version} />;
  } else if (first === "add") {
    screen = <CapturePanel capture={capture} onCaptured={refresh} />;
  } else {
    screen = (
      <p className="border border-dashed border-[color:var(--color-rule)] px-3 py-2">
        There is no page at <code className="font-mono">{route.path}</code>.{" "}
        <Link to="/" navigate={navigate}>
          Back to the timeline
        </Link>
        .
      </p>
    );
  }

  return (
    <>
      {dragging ? <DropOverlay /> : null}
      <header className="border-b border-[color:var(--color-ink)]">
        <nav className="flex flex-wrap items-baseline gap-4 px-3 py-1">
          <span className="font-semibold">Health record</span>
          <Tab to="/" label="Timeline" route={route} navigate={navigate} exact />
          <Tab to="/record" label="Record" route={route} navigate={navigate} />
          <Tab to="/add" label="Add" route={route} navigate={navigate} />
          {capture.uploading > 0 ? (
            <span className="text-[color:var(--color-muted)]">
              {capture.uploading} uploading
            </span>
          ) : null}
        </nav>
      </header>

      <StatusBar
        health={health}
        error={healthError}
        uploading={capture.uploading}
        onRebuild={rebuild}
      />

      <main className="px-3 py-2">{screen}</main>

      <footer className="mt-6 border-t border-[color:var(--color-rule)] px-3 py-1 text-[color:var(--color-muted)] no-print">
        Everything here is a file in{" "}
        <code className="font-mono">{health?.vault.root ?? "your vault"}</code>. This
        page reports and cites; it does not interpret.
      </footer>
    </>
  );
}

function Tab({
  to,
  label,
  route,
  navigate,
  exact,
}: {
  to: string;
  label: string;
  route: { path: string };
  navigate: (to: string) => void;
  exact?: boolean;
}) {
  const active = exact ? route.path === to : route.path.startsWith(to);
  return (
    <Link
      to={to}
      navigate={navigate}
      className={active ? "font-semibold text-[color:var(--color-ink)] no-underline" : ""}
    >
      {label}
    </Link>
  );
}
