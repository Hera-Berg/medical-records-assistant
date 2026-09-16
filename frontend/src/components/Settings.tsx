/**
 * Two settings: where your folder lives, and which computer reads your documents.
 *
 * The first is a question, not an instruction.
 *
 * This screen is mostly words, and the words are the point.
 * The reading a person arrives at unprompted is that choosing "Dropbox" here
 * *puts* their record in Dropbox, and that reading is wrong in a way that hurts
 * both ways round: someone believing it will pick Dropbox while their record
 * sits on a laptop, and someone who thinks the setting is cosmetic will leave
 * it on "this computer" while their whole medical history syncs to a shared
 * account. So the explanation comes first, before the choice, and each option
 * says what choosing it actually changes.
 *
 * Choosing a synced option is an **admission**, not an instruction: the user is
 * telling the record that their history is already inside someone else's
 * storage. The answer to that is a warning shown *before* the change is
 * applied, and it names the two things that follow — anyone in that account can
 * read everything, and a folder share cannot be taken back — plus the one
 * mistake that turns "synced" into "disclosed", which is putting a password in
 * a file that syncs.
 *
 * Going back to "a folder on this computer" needs no confirmation. It widens
 * what the app watches for and hands nothing to anybody.
 */

import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { PageHeader } from "../App";
import type { ReadsOn, Settings as SettingsData, SyncOption } from "../types";
import { EndpointSettings } from "./EndpointSettings";
import { ReaderSettings } from "./ReaderSettings";

export function Settings({
  version,
  onChanged,
  setHeader,
}: {
  version: number;
  onChanged: () => void;
  setHeader: (header: PageHeader) => void;
}) {
  const [settings, setSettings] = useState<SettingsData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<SyncOption | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);
  // The connection form belongs under "Read on another computer" and nowhere
  // else: shown under "this computer" it reads as something still to fill in.
  const [readsOn, setReadsOn] = useState<ReadsOn | null>(null);

  const load = useCallback(() => {
    api
      .settings()
      .then((result) => {
        setSettings(result);
        setError(null);
        setHeader({
          title: "Settings",
          subtitle:
            "Where your record is kept, which computer reads it, and what this app watches out for.",
        });
      })
      .catch((exc: Error) => setError(exc.message));
  }, [setHeader]);

  useEffect(load, [load, version]);

  const choose = (option: SyncOption) => {
    setSaved(null);
    setError(null);
    if (option.current) return;
    // "A folder on this computer" hands nothing to anyone, so it applies at
    // once. Every other option is an admission and is confirmed.
    if (option.warning === null) apply(option.value);
    else setPending(option);
  };

  const apply = (value: string) => {
    setSaving(true);
    api
      .setSyncProfile(value)
      .then((result) => {
        setSettings(result);
        setPending(null);
        setSaved(value);
        onChanged();
      })
      .catch((exc: Error) => {
        setError(exc.message);
        setPending(null);
      })
      .finally(() => setSaving(false));
  };

  if (error && !settings) {
    return <p className="text-[color:var(--color-alarm)]">{error}</p>;
  }
  if (!settings) return <p className="text-[color:var(--color-muted)]">Reading…</p>;

  const current = settings.sync_profile.options.find((option) => option.current);

  return (
    <section>
      <h2 className="text-lg font-semibold">Where your folder is</h2>
      <p className="mt-1 max-w-2xl">{settings.explanation}</p>

      {settings.config.conflict_forks.length > 0 ? (
        <div className="mt-3 rounded-lg border border-[color:var(--color-alarm)] bg-[color:var(--color-alarm-soft)] p-3">
          <p className="font-semibold">
            Your sync app has made a second copy of your settings file.
          </p>
          <p className="mt-1">
            {settings.config.conflict_forks.join(", ")} is sitting beside{" "}
            <code className="font-mono">config.toml</code>. Two computers have saved
            different versions and your sync app has given up choosing between them.
            This setting cannot be changed until you open both files, keep the one you
            want, and delete the other — writing a third version now is how the whole
            file goes missing.
          </p>
        </div>
      ) : null}

      <fieldset className="mt-4" disabled={saving || settings.config.conflict_forks.length > 0}>
        <legend className="sr-only">Where your folder is kept</legend>
        {/* Why these cannot be chosen, said where the choices are. */}
        {settings.config.conflict_forks.length > 0 ? (
          <p className="font-semibold text-[color:var(--color-alarm)]">
            Locked until the second copy of your settings file, described just above, is
            sorted out.
          </p>
        ) : saving ? (
          <p className="text-[color:var(--color-muted)]">Saving…</p>
        ) : null}
        {settings.sync_profile.options.map((option) => (
          <label
            key={option.value}
            className={
              "mt-2 flex gap-3 rounded-lg border p-3 " +
              (option.current
                ? "border-[color:var(--color-accent)] bg-[color:var(--color-accent-soft)]"
                : pending?.value === option.value
                  ? "border-[color:var(--color-warn)] bg-[color:var(--color-warn-soft)]"
                  : "border-[color:var(--color-rule)]")
            }
          >
            <input
              type="radio"
              name="sync_profile"
              value={option.value}
              /* The pending choice, not the saved one. A radio that does not
                 move when it is clicked reads as a broken control — the
                 confirmation below says plainly that nothing is saved yet, and
                 Cancel puts the dot back. */
              checked={(pending?.value ?? settings.sync_profile.current) === option.value}
              onChange={() => choose(option)}
              /* Aligned to the option's name, not to the middle of a block
                 whose height depends on how much its description says. */
              className="mt-1 self-start"
            />
            <span>
              <span className="block font-semibold">
                {option.label}
                {option.current ? (
                  <span className="font-normal text-[color:var(--color-muted)]">
                    {" "}
                    — what your settings file says now
                  </span>
                ) : pending?.value === option.value ? (
                  <span className="font-normal text-[color:var(--color-warn)]">
                    {" "}
                    — not saved yet
                  </span>
                ) : null}
              </span>
              <span className="mt-0.5 block text-[color:var(--color-muted)]">
                {option.effects.join(" ")}
              </span>
            </span>
          </label>
        ))}
      </fieldset>

      {pending ? (
        <div className="mt-4 rounded-lg border border-[color:var(--color-warn)] bg-[color:var(--color-warn-soft)] p-3">
          <p className="font-semibold">Before you change this</p>
          <p className="mt-1">{pending.warning}</p>
          <p className="mt-1 text-[color:var(--color-muted)]">
            This setting only records where your folder already is. Choosing it does not
            move anything, and this app never signs in to {pending.label.replace("Synced by ", "")}.
          </p>
          <div className="mt-3 flex gap-3">
            <button
              type="button"
              onClick={() => apply(pending.value)}
              className="btn btn-primary"
              disabled={saving}
            >
              {saving ? "Saving…" : "Yes — my folder is already there"}
            </button>
            <button type="button" onClick={() => setPending(null)} className="btn">
              Cancel
            </button>
          </div>
        </div>
      ) : null}

      {saved && !pending ? (
        <p className="mt-3 text-[color:var(--color-muted)]">
          Saved. Nothing was moved or copied — the only thing that changed is the line{" "}
          <code className="font-mono">sync_profile = "{saved}"</code> in your settings
          file.
        </p>
      ) : null}

      {settings.conflicts && settings.conflicts.length > 0 ? (
        <div className="mt-3 rounded-lg border border-[color:var(--color-warn)] bg-[color:var(--color-warn-soft)] p-3">
          <p className="font-semibold">Now that it knows, it has found something.</p>
          <ul className="mt-1 list-disc pl-5">
            {settings.conflicts.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {error ? <p className="mt-3 text-[color:var(--color-alarm)]">{error}</p> : null}

      {current?.warning ? (
        <p className="mt-4 border-t border-[color:var(--color-rule)] pt-4">
          {current.warning}
        </p>
      ) : null}

      <div className="mt-8 border-t border-[color:var(--color-rule)] pt-6">
        <ReaderSettings onChoice={setReadsOn} onChanged={onChanged} />
      </div>

      {readsOn === "another-computer" ? (
        <div className="mt-8 border-t border-[color:var(--color-rule)] pt-6">
          <EndpointSettings
            settings={settings}
            onSettings={setSettings}
            onChanged={onChanged}
          />
        </div>
      ) : null}

      <h2 className="mt-8 border-t border-[color:var(--color-rule)] pt-6 text-lg font-semibold">
        Your settings file
      </h2>
      <p className="mt-1">
        <code className="font-mono">{settings.config.path}</code>
      </p>
      <p className="mt-1 text-[color:var(--color-muted)]">
        A plain text file you can open and edit yourself. This app changes only the few
        lines it is asked to from this screen, and leaves your own comments and spacing
        exactly as they are. It syncs along with everything else in your folder, which is
        why no password or key ever belongs in it.
        {settings.config.writable ? null : " This app cannot write to it at the moment."}
      </p>
    </section>
  );
}
