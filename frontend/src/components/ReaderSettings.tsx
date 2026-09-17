/**
 * Which computer reads your documents, and — for this one — its one-time download.
 *
 * Two choices, both real. Reading on this computer is the default because it
 * needs nothing set up: install, open, add a photo. Reading on another computer
 * is for someone with a faster machine of their own, and opens the connection
 * form beneath.
 *
 * **Asked before anything happens, on every record.** The download is the only
 * thing this app fetches from outside the user's own machines, so it starts
 * only from a confirmation naming the exact bytes still to fetch, the sites
 * they come from and where they are kept — and the server refuses a start that
 * does not send that exact figure back. A demonstration record asks the same
 * way: the files belong to this computer, not to the record, and a demo read
 * by a local model is the safest way to watch reading work.
 *
 * **A disabled control says why, beside itself.**
 *
 * **The model is a real choice, and it is chosen knowing.** Every model in the
 * list shows its download size, the memory it needs, its speed here or "not
 * measured", and what it got correct, left for you, and wrong on the sample
 * documents — in the list, not behind a link. A failure someone found that
 * nothing in the app can catch is said beside the model it belongs to. That is
 * why offering a model that did not pass every test is acceptable: the person
 * picking it can see what it did. A list of radio options rather than a
 * drop-down, because a drop-down's options cannot carry those sentences and the
 * numbers would be hidden until something was already chosen.
 *
 * **Honest about speed.** A small model on a laptop is slow, and a queue that is
 * slow must not look like one that is broken. Before anything has been read
 * here the screen gives the range for this kind of computer; after, what it
 * actually took.
 *
 * **Honest about what has been tried.** A build pinned for a kind of computer
 * nobody has run it on is said to be untried, in those words.
 *
 * Progress is a sentence with two numbers in it. No bar, no ring, no animation:
 * "1.2 of 3.6 GB" is read in one glance and survives a screenshot.
 */

import { useCallback, useEffect, useState } from "react";
import { reader as readerApi } from "../api";
import type { ReaderInfo, ReadsOn } from "../types";

const DESCRIPTIONS: Record<ReadsOn, string> = {
  "this-computer":
    "A model runs here, on this computer. Nothing to set up except a one-time download, and nothing from your record leaves this computer.",
  "another-computer":
    "A faster computer you own reads them, reached over your own private network. It needs its address and password.",
};

const BUSY = new Set(["downloading", "verifying", "unpacking"]);

export function ReaderSettings({
  onChoice,
  onChanged,
}: {
  /** Tells the settings screen which form belongs under this one. */
  onChoice: (readsOn: ReadsOn) => void;
  onChanged: () => void;
}) {
  const [info, setInfo] = useState<ReaderInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [minutes, setMinutes] = useState<string>("");

  const apply = useCallback(
    (next: ReaderInfo) => {
      setInfo(next);
      setError(null);
      onChoice(next.choice.reads_on);
    },
    [onChoice],
  );

  const load = useCallback(() => {
    readerApi
      .info()
      .then(apply)
      .catch((exc: Error) => setError(exc.message));
  }, [apply]);

  useEffect(load, [load]);

  useEffect(() => {
    if (info) setMinutes(String(info.choice.sleep_after_minutes));
  }, [info?.choice.sleep_after_minutes]);

  // Polled only while bytes are moving, so an idle settings screen asks nothing.
  const moving = info ? BUSY.has(info.files.state) : false;
  useEffect(() => {
    if (!moving) return;
    const timer = window.setInterval(load, 1000);
    return () => window.clearInterval(timer);
  }, [moving, load]);

  const act = (call: () => Promise<ReaderInfo>) => {
    setSaving(true);
    call()
      .then((next) => {
        apply(next);
        onChanged();
      })
      .catch((exc: Error) => setError(exc.message))
      .finally(() => setSaving(false));
  };

  if (!info) {
    return error ? (
      <p className="text-[color:var(--color-alarm)]">{error}</p>
    ) : (
      <p className="text-[color:var(--color-muted)]">Reading…</p>
    );
  }

  const here = info.choice.reads_on === "this-computer";

  return (
    <section>
      <h2 className="text-lg font-semibold">Which computer reads your documents</h2>
      <p className="mt-1 max-w-2xl">
        This is set separately on each of your computers. Changing it here changes nothing on
        the others, and nothing in your record's folder.
      </p>

      <fieldset className="mt-3" disabled={saving}>
        <legend className="sr-only">Which computer reads your documents</legend>
        {saving ? <p className="text-[color:var(--color-muted)]">Saving…</p> : null}
        {info.options.map((option) => {
          const unavailable = option.value === "this-computer" && !info.platform.supported;
          return (
            <label
              key={option.value}
              className={
                "mt-2 flex gap-3 rounded-lg border p-3 " +
                (option.current
                  ? "border-[color:var(--color-accent)] bg-[color:var(--color-accent-soft)]"
                  : "border-[color:var(--color-rule)]")
              }
            >
              <input
                type="radio"
                name="reads_on"
                value={option.value}
                checked={option.current}
                disabled={unavailable}
                onChange={() => act(() => readerApi.choose(option.value))}
                className="mt-1 self-start"
              />
              <span>
                <span className="block font-semibold">
                  {option.label}
                  {option.current ? (
                    <span className="font-normal text-[color:var(--color-muted)]">
                      {" "}
                      — chosen on this computer
                    </span>
                  ) : null}
                </span>
                <span className="mt-0.5 block text-[color:var(--color-muted)]">
                  {unavailable
                    ? `There is no version of the reader for ${info.platform.label}.`
                    : DESCRIPTIONS[option.value]}
                </span>
              </span>
            </label>
          );
        })}
      </fieldset>

      {here ? (
        <ThisComputer
          info={info}
          saving={saving}
          act={act}
          minutes={minutes}
          setMinutes={setMinutes}
        />
      ) : null}

      {error ? <p className="mt-3 text-[color:var(--color-alarm)]">{error}</p> : null}
    </section>
  );
}

function ThisComputer({
  info,
  saving,
  act,
  minutes,
  setMinutes,
}: {
  info: ReaderInfo;
  saving: boolean;
  act: (call: () => Promise<ReaderInfo>) => void;
  minutes: string;
  setMinutes: (value: string) => void;
}) {
  const files = info.files;
  const [asking, setAsking] = useState(false);
  const complete = files.state === "complete";
  const moving = BUSY.has(files.state);
  const status = info.reader;

  return (
    <div className="mt-4 space-y-3">
      <ModelList info={info} saving={saving} act={act} />

      {!info.platform.verified ? (
        <Note tone="warn" title={`Not yet tried on ${info.platform.label}.`}>
          The reader is set up for this kind of computer but nobody has run it on one yet. If
          it does not start, choose another computer above.
        </Note>
      ) : null}

      {info.memory.low ? (
        <Note tone="warn" title="This computer has less than 8 GB of memory.">
          Reading here will be slow, and other programs will be slower while it reads. A
          faster computer of your own will do better, if you have one.
        </Note>
      ) : null}

      <div className="rounded-lg border border-[color:var(--color-rule)] bg-[color:var(--color-paper)] p-3">
        <p className="font-semibold">
          {complete
            ? "Downloaded and checked."
            : moving
              ? progressTitle(files.state)
              : "A one-time download is needed."}
        </p>

        {complete ? (
          <p className="mt-1 text-[color:var(--color-muted)]">
            {gigabytes(files.total_bytes)} kept in{" "}
            <code className="font-mono break-all">{files.location}</code>, outside your
            record's folder, so it is never synced.
          </p>
        ) : (
          <>
            <p className="mt-1">{files.explanation}</p>
            {info.speed ? (
              /* Said before the download, not after it: someone deciding
                 whether to spend 3.6 GB on this should know a document takes
                 a minute, not find out from a queue that seems stuck. */
              <p className="mt-1">
                <strong>{info.speed.pace}</strong> Another computer of your own can read them instead.
              </p>
            ) : null}
            <table className="mt-2 w-full border-collapse">
              <tbody>
                {files.bundles.map((bundle) => (
                  <tr key={bundle.id} className="border-t border-[color:var(--color-rule)]">
                    <td className="py-1 pr-3">{bundle.title}</td>
                    <td className="py-1 pr-3 text-right whitespace-nowrap">
                      {gigabytes(bundle.size_bytes)}
                    </td>
                    <td className="py-1 text-[color:var(--color-muted)] whitespace-nowrap">
                      {bundle.licence} licence
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-2 text-[color:var(--color-muted)]">
              From {files.hosts.join(" and ")}, and the servers those sites send large files
              from. Kept in <code className="font-mono break-all">{files.location}</code>.
              {info.disk.free_bytes !== null
                ? ` ${gigabytes(info.disk.free_bytes)} free there.`
                : null}
            </p>

            {moving ? (
              <p className="mt-2">
                {/* "on this computer", not "downloaded": part of it may have been
                    here already, placed by hand or left by an earlier attempt. */}
                <strong>
                  {gigabytes(files.done_bytes)} of {gigabytes(files.total_bytes)}
                </strong>{" "}
                on this computer. You can keep using the app, and anything you add waits to be
                read.
              </p>
            ) : null}

            {files.message ? <p className="mt-2">{files.message}</p> : null}

            {moving ? (
              <div className="mt-3">
                <button type="button" className="btn" onClick={() => act(readerApi.cancel)}>
                  Stop
                </button>
              </div>
            ) : asking ? (
              <div className="mt-3 rounded-lg border border-[color:var(--color-warn)] bg-[color:var(--color-warn-soft)] p-3">
                <p className="font-semibold">
                  Download {files.remaining_bytes.toLocaleString("en")} bytes (
                  {gigabytes(files.remaining_bytes)})?
                </p>
                <p className="mt-1">
                  From {files.hosts.join(" and ")}, into{" "}
                  <code className="font-mono break-all">{files.location}</code>. Once, for this
                  computer. Nothing from your record is sent.
                </p>
                <div className="mt-2 flex flex-wrap items-center gap-3">
                  <button
                    type="button"
                    className="btn btn-primary"
                    disabled={saving}
                    onClick={() => {
                      act(() => readerApi.download(files.remaining_bytes));
                      setAsking(false);
                    }}
                  >
                    {saving ? "Starting…" : `Yes, download ${gigabytes(files.remaining_bytes)}`}
                  </button>
                  <button type="button" className="btn" onClick={() => setAsking(false)}>
                    Cancel
                  </button>
                </div>
              </div>
            ) : (
              <div className="mt-3">
                <button type="button" className="btn btn-primary" onClick={() => setAsking(true)}>
                  {files.done_bytes > 0
                    ? `Continue — ${gigabytes(files.remaining_bytes)} to go…`
                    : `Download ${gigabytes(files.remaining_bytes)}…`}
                </button>
              </div>
            )}
          </>
        )}
      </div>

      {complete && status ? (
        <div className="rounded-lg border border-[color:var(--color-rule)] bg-[color:var(--color-paper)] p-3">
          <p>
            <span className="font-semibold">{STATE_WORDS[status.state] ?? "The reader"}</span>{" "}
            {status.message}
          </p>
          {info.speed ? (
            <p className="mt-1 text-[color:var(--color-muted)]">{info.speed.pace}</p>
          ) : null}
          {status.state === "stopped" ? (
            <div className="mt-2">
              {status.log_path ? (
                <p className="text-[color:var(--color-muted)]">
                  Its log is at <code className="font-mono break-all">{status.log_path}</code>.
                </p>
              ) : null}
              <button
                type="button"
                className="btn btn-primary mt-2"
                disabled={saving}
                onClick={() => act(readerApi.retry)}
              >
                {saving ? "Starting…" : "Try again"}
              </button>
            </div>
          ) : null}
        </div>
      ) : null}

      <form
        className="flex flex-wrap items-baseline gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          const value = Number.parseInt(minutes, 10);
          if (Number.isNaN(value)) return;
          act(() => readerApi.choose("this-computer", value));
        }}
      >
        <label htmlFor="sleep-after">Unload the reader after</label>
        <input
          id="sleep-after"
          type="number"
          min={0}
          max={1440}
          value={minutes}
          onChange={(event) => setMinutes(event.target.value)}
          className="w-20 rounded border border-[color:var(--color-rule-strong)] px-2 py-1"
        />
        <span>minutes with nothing to read, to give its memory back.</span>
        <button
          type="submit"
          className="btn"
          disabled={saving || !validMinutes(minutes) || minutes === String(info.choice.sleep_after_minutes)}
        >
          {/* The reason it cannot be pressed is its label. */}
          {saving
            ? "Saving…"
            : !validMinutes(minutes)
              ? "Enter 0 to 1440 minutes"
              : minutes === String(info.choice.sleep_after_minutes)
                ? "Saved"
                : "Save"}
        </button>
        <span className="basis-full text-[color:var(--color-muted)]">
          It wakes by itself when you add something; the first document after that takes a few
          seconds longer. 0 keeps it loaded all the time.
        </span>
      </form>
    </div>
  );
}

function ModelList({
  info,
  saving,
  act,
}: {
  info: ReaderInfo;
  saving: boolean;
  act: (call: () => Promise<ReaderInfo>) => void;
}) {
  return (
    <fieldset disabled={saving}>
      <legend className="font-semibold">Which model reads them</legend>
      <p className="text-[color:var(--color-muted)]">
        Each one says what it was measured to do. You can change this at any time; a model
        you have not downloaded yet asks before it downloads anything.
      </p>
      {saving ? <p className="text-[color:var(--color-muted)]">Saving…</p> : null}
      {info.models.map((model) => (
        <label
          key={model.id}
          className={
            "mt-2 flex gap-3 rounded-lg border p-3 " +
            (model.current
              ? "border-[color:var(--color-accent)] bg-[color:var(--color-accent-soft)]"
              : "border-[color:var(--color-rule)] bg-[color:var(--color-paper)]")
          }
        >
          <input
            type="radio"
            name="local_model"
            value={model.id}
            checked={model.current}
            onChange={() =>
              act(() =>
                readerApi.choose("this-computer", info.choice.sleep_after_minutes, model.id),
              )
            }
            className="mt-1 self-start"
          />
          <span className="block">
            <span className="block font-semibold">
              {model.title}
              {model.recommended ? (
                <span className="font-normal"> — recommended</span>
              ) : null}
              {model.current ? (
                <span className="font-normal text-[color:var(--color-muted)]">
                  {" "}
                  (chosen on this computer)
                </span>
              ) : null}
            </span>
            <span className="block">
              {gigabytes(model.size_bytes)} download
              {model.downloaded ? " (already downloaded)" : ""} · needs about{" "}
              {gigabytes(model.ram_needed_bytes)} of memory · {model.licence} licence
            </span>
            <span className="block">{model.speed.sentence}</span>
            <span className="block">{model.accuracy.sentence}</span>
            {model.known_failures.map((failure) => (
              <span key={failure} className="mt-1 block">
                <strong>Found in testing:</strong> {failure}
              </span>
            ))}
            {model.memory_warning ? (
              <span className="mt-1 block">
                <strong>Memory:</strong> {model.memory_warning}
              </span>
            ) : null}
          </span>
        </label>
      ))}
    </fieldset>
  );
}

const STATE_WORDS: Record<string, string> = {
  "not-downloaded": "Not downloaded.",
  sleeping: "Sleeping.",
  starting: "Starting.",
  ready: "Ready.",
  stopped: "Stopped.",
  unsupported: "Not available.",
  "in-use-elsewhere": "In use elsewhere.",
};

function validMinutes(value: string): boolean {
  if (!/^\d+$/.test(value.trim())) return false;
  const minutes = Number.parseInt(value, 10);
  return minutes >= 0 && minutes <= 1440;
}

function progressTitle(state: string): string {
  if (state === "verifying") return "Checking what arrived…";
  if (state === "unpacking") return "Unpacking…";
  return "Downloading…";
}

function Note({
  tone,
  title,
  children,
}: {
  tone: "warn";
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div
      className="rounded-lg border border-l-4 px-3 py-2"
      style={{
        borderColor: "var(--color-rule)",
        borderLeftColor: `var(--color-${tone})`,
        background: `var(--color-${tone}-soft)`,
      }}
    >
      <p>
        <span className="font-semibold">{title}</span> {children}
      </p>
    </div>
  );
}

function gigabytes(bytes: number): string {
  if (bytes < 1024 ** 3) return `${Math.max(1, Math.round(bytes / 1024 ** 2))} MB`;
  const value = bytes / 1024 ** 3;
  // "8 GB", not "8.0 GB"; "3.2 GB" where the tenth says something.
  return Number.isInteger(value) ? `${value} GB` : `${value.toFixed(1)} GB`;
}
