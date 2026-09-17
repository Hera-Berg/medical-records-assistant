/**
 * The first run's second and last question: which computer reads your documents?
 *
 * Asked once the record is open, because the answers are the Settings screen's
 * own — the model list with what each was measured to do, the one-time download
 * with its exact size, connecting to another computer — and are not built
 * twice. Everything else lives in Settings.
 *
 * Nothing here has to be finished before moving on. The download can wait and
 * another computer can be asleep: anything added in the meantime is kept and
 * waits to be read, and the page says so.
 */

import { useEffect, useState } from "react";
import { api } from "../api";
import type { ReadsOn, Settings as SettingsData } from "../types";
import { EndpointSettings } from "./EndpointSettings";
import { ReaderSettings } from "./ReaderSettings";

export function Welcome({
  navigate,
  onChanged,
}: {
  navigate: (to: string) => void;
  onChanged: () => void;
}) {
  const [readsOn, setReadsOn] = useState<ReadsOn | null>(null);
  const [settings, setSettings] = useState<SettingsData | null>(null);
  const [leaving, setLeaving] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    if (readsOn === "another-computer" && settings === null) {
      api.settings().then(setSettings).catch((exc: Error) => setProblem(exc.message));
    }
  }, [readsOn, settings]);

  const done = () => {
    setLeaving(true);
    api
      .welcomeDone()
      .then(() => {
        onChanged();
        navigate("/");
      })
      .catch((exc: Error) => {
        setProblem(exc.message);
        setLeaving(false);
      });
  };

  return (
    <section>
      <p className="max-w-2xl">
        Documents you add — a photographed prescription, a letter, a lab report — are read
        by a program that finds the medications, doses and allergies in them, and asks you
        before anything important goes into your record. That program can run on this
        computer, or on another computer of yours.
      </p>

      <div className="mt-6">
        <ReaderSettings onChoice={setReadsOn} onChanged={onChanged} />
      </div>

      {readsOn === "another-computer" && settings ? (
        <div className="mt-8 border-t border-[color:var(--color-rule)] pt-6">
          <EndpointSettings settings={settings} onSettings={setSettings} onChanged={onChanged} />
        </div>
      ) : null}

      <div className="mt-8 flex flex-wrap items-baseline gap-3 border-t border-[color:var(--color-rule)] pt-6">
        <button type="button" className="btn btn-primary" onClick={done} disabled={leaving}>
          Go to my record
        </button>
        <span className="text-[color:var(--color-muted)]">
          {leaving
            ? "Opening…"
            : "Nothing above has to be finished first. Anything you add is kept, and waits to be read."}
        </span>
      </div>
      {problem ? <p className="mt-2 text-[color:var(--color-alarm)]">{problem}</p> : null}
    </section>
  );
}
