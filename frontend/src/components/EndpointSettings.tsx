/**
 * The computer that reads your documents: three fields and a button.
 *
 * This is the screen a non-technical person meets first, and the version before
 * this one asked them to understand HTTP authentication headers before they got
 * to the address bar. Everything that made it a five-field form with two
 * buttons and three ways of reporting a failure is still here — it is folded
 * away, because folding is free and a wall of text is not.
 *
 * **Three fields.** Address, password, model. The header name and the word in
 * front of it live behind "Advanced", collapsed, and never opened for you: they
 * matter to the person whose server demands something unusual and to nobody
 * else, and showing them by default makes a three-field form look like a
 * five-field one. The summary says "changed" when they are not the defaults, so
 * a non-standard setting is never invisible.
 *
 * **One button.** Connect checks and then saves, because they are one
 * intention. Two buttons for one intention is two chances to do one of them and
 * believe the thing is set up. Nothing is written unless every check passed — a
 * saved endpoint that does not work is a queue that silently never drains.
 *
 * **One failure surface.** A sentence saying what stopped it, with what to do
 * about it under it and the step list one tap further. There were three: a red
 * box, a separate verdict line and a table, all describing one event in three
 * registers.
 *
 * **One sentence of prose.** The rest is behind "What is this?". Trust comes
 * from the sentence being true and short; the reader who most needs reassuring
 * is the least likely to reach the end of three paragraphs of it.
 *
 * Unchanged, because it is load-bearing rather than presentational: the
 * password field only writes, the reason a key may never go in the settings
 * file is still said beside it, the private-address guard still runs before
 * anything is saved, the model id still comes verbatim from the box's own
 * `/v1/models`, and the check still includes vision.
 */

import { useEffect, useState } from "react";
import { api } from "../api";
import type { ConnectResult, EndpointSettings as EndpointData, Settings } from "../types";

const RESULT_WORD: Record<string, string> = {
  ok: "Worked",
  failed: "Failed",
  "not-checked": "Not checked",
};

/* Tone is a word first and a colour second — a colour-blind reader and a
   printed page must both still say which step failed. */
const RESULT_INK: Record<string, string> = {
  ok: "var(--color-accent)",
  failed: "var(--color-alarm)",
  "not-checked": "var(--color-muted)",
};

export function EndpointSettings({
  settings,
  onSettings,
  onChanged,
}: {
  settings: Settings;
  onSettings: (next: Settings) => void;
  onChanged: () => void;
}) {
  const endpoint = settings.endpoint;

  const [address, setAddress] = useState(endpoint.base_url);
  const [model, setModel] = useState(endpoint.model);
  const [header, setHeader] = useState(endpoint.auth.header);
  const [scheme, setScheme] = useState(endpoint.auth.scheme);

  const [result, setResult] = useState<ConnectResult | null>(null);
  const [resultFor, setResultFor] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const [keyValue, setKeyValue] = useState("");
  const [keyNote, setKeyNote] = useState<string | null>(null);
  const [keyFailed, setKeyFailed] = useState(false);

  // Re-seeded when what is saved changes under us, and not otherwise: a
  // half-typed address must survive an unrelated refresh.
  const saved = JSON.stringify([
    endpoint.base_url,
    endpoint.model,
    endpoint.auth.header,
    endpoint.auth.scheme,
  ]);
  useEffect(() => {
    setAddress(endpoint.base_url);
    setModel(endpoint.model);
    setHeader(endpoint.auth.header);
    setScheme(endpoint.auth.scheme);
  }, [saved]);

  const here = JSON.stringify([address, model, header, scheme]);
  const changed = here !== saved;
  // A result describes what it was fetched for. Editing anything makes it a
  // statement about something else, so it stops being shown.
  const stale = result !== null && resultFor !== here;
  const live = stale ? null : result;

  const advancedChanged =
    header !== endpoint.defaults.header || scheme !== endpoint.defaults.scheme;

  const connect = (withModel: string) => {
    setBusy("connect");
    setError(null);
    const against = JSON.stringify([address, withModel, header, scheme]);
    api
      .connectEndpoint({ base_url: address, model: withModel, header, scheme })
      .then((next) => {
        setResult(next);
        // What the box was asked, plus what it answered with: connecting
        // without naming a model is answered by one, and the field fills in.
        setResultFor(
          next.model ? JSON.stringify([address, next.model, header, scheme]) : against,
        );
        if (next.model) setModel(next.model);
        onSettings(next.settings);
        onChanged();
      })
      .catch((exc: Error) => setError(exc.message))
      .finally(() => setBusy(null));
  };

  const storeKey = () => {
    setBusy("key");
    setError(null);
    setKeyNote(null);
    setKeyFailed(false);
    api
      .setEndpointKey(keyValue)
      .then((next) => {
        onSettings(next);
        onChanged();
        setKeyValue("");
        setKeyNote(keyStoredNote(next));
      })
      .catch((exc: Error) => {
        setError(exc.message);
        setKeyFailed(true);
      })
      .finally(() => setBusy(null));
  };

  const working = busy !== null;

  return (
    <section>
      <h2 className="text-lg font-semibold">The computer that reads your documents</h2>
      <p className="mt-1 max-w-2xl">{endpoint.explanation}</p>
      <details className="mt-1">
        <summary className="cursor-pointer text-[color:var(--color-muted)]">
          What is this?
        </summary>
        {endpoint.about.split("\n\n").map((paragraph) => (
          <p key={paragraph.slice(0, 24)} className="mt-1 max-w-2xl">
            {paragraph}
          </p>
        ))}
      </details>

      {endpoint.problem ? (
        <p className="mt-3 rounded-lg border border-[color:var(--color-alarm)] bg-[color:var(--color-alarm-soft)] p-3">
          <span className="font-semibold">
            Your settings file has an entry it cannot use.
          </span>{" "}
          {endpoint.problem}
        </p>
      ) : null}

      <div className="mt-4 grid gap-3 md:max-w-2xl">
        <label className="block">
          <span className="block font-semibold">Address</span>
          <input
            type="url"
            inputMode="url"
            spellCheck={false}
            className="field mt-1 w-full font-mono"
            value={address}
            placeholder="https://macbook-pro.tailnet.ts.net/v1"
            onChange={(event) => setAddress(event.target.value)}
          />
        </label>

        <PasswordField
          data={endpoint}
          value={keyValue}
          onChange={setKeyValue}
          onStore={storeKey}
          busy={busy === "key"}
          disabled={working}
          note={keyNote}
          alternatives={keyFailed ? endpoint.key_alternatives : null}
        />

        <ModelField
          value={model}
          offered={live?.models ?? []}
          asked={live !== null}
          onChange={setModel}
        />
      </div>

      <details className="mt-3 md:max-w-2xl">
        <summary className="cursor-pointer font-semibold">
          Advanced
          {advancedChanged ? (
            <span className="font-normal text-[color:var(--color-muted)]">
              {" "}
              — changed
            </span>
          ) : null}
        </summary>
        <p className="mt-1 text-[color:var(--color-muted)]">
          How your password is handed to that computer. Almost every server wants these
          exactly as they are; change them only if yours has told you to. Some want the
          password on its own with no word in front of it — clear the second box for
          that.
        </p>
        <div className="mt-1 flex flex-wrap gap-2">
          <label className="block">
            <span className="block text-[color:var(--color-muted)]">Header name</span>
            <input
              type="text"
              spellCheck={false}
              className="field mt-0.5 font-mono"
              value={header}
              onChange={(event) => setHeader(event.target.value)}
            />
          </label>
          <label className="block">
            <span className="block text-[color:var(--color-muted)]">
              Word in front of it
            </span>
            <input
              type="text"
              spellCheck={false}
              className="field mt-0.5 font-mono"
              value={scheme}
              placeholder="(none)"
              onChange={(event) => setScheme(event.target.value)}
            />
          </label>
        </div>
      </details>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => connect(model)}
          disabled={working || !address.trim()}
        >
          {busy === "connect" ? "Connecting…" : "Connect"}
        </button>
        {/* Why Connect cannot be pressed, beside it. */}
        {!working && !address.trim() ? (
          <span className="text-[color:var(--color-muted)]">Type the address first.</span>
        ) : working && busy !== "connect" ? (
          <span className="text-[color:var(--color-muted)]">
            Waiting for the password to finish storing.
          </span>
        ) : changed && endpoint.configured && !working ? (
          <span className="text-[color:var(--color-muted)]">
            Connect to check and save this.
          </span>
        ) : null}
      </div>

      {error ? (
        <p className="mt-3">
          <span className="font-semibold text-[color:var(--color-alarm)]">
            That did not work.
          </span>{" "}
          {error}
        </p>
      ) : null}

      <Status endpoint={endpoint} result={live} />
    </section>
  );
}

/**
 * The model, read off the box rather than typed.
 *
 * Empty and disabled until Connect has reached the machine, because before then
 * there is nothing true to put in it. The id has to match exactly — it is
 * checked on every document read — and the one string a person is most likely
 * to get subtly wrong is the one this can simply go and fetch.
 */
function ModelField({
  value,
  offered,
  asked,
  onChange,
}: {
  value: string;
  offered: string[];
  asked: boolean;
  onChange: (next: string) => void;
}) {
  // A box that answered but listed nothing leaves typing as the only way in.
  const mustType = asked && offered.length === 0;
  return (
    <label className="block">
      <span className="block font-semibold">Model</span>
      {mustType ? (
        <input
          type="text"
          spellCheck={false}
          className="field mt-1 w-full font-mono"
          value={value}
          placeholder="exactly as that server spells it"
          onChange={(event) => onChange(event.target.value)}
        />
      ) : (
        <select
          className="field mt-1 w-full font-mono"
          value={value}
          disabled={offered.length === 0}
          onChange={(event) => onChange(event.target.value)}
        >
          {offered.length === 0 ? (
            <option value={value}>
              {value || "Connect first — this is read from the computer"}
            </option>
          ) : (
            <>
              <option value="">Choose one…</option>
              {offered.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
              {value && !offered.includes(value) ? (
                <option value={value}>{value} — not offered by this computer</option>
              ) : null}
            </>
          )}
        </select>
      )}
    </label>
  );
}

/**
 * Set-only. There is no read path, here or on the server.
 *
 * The reason it may never go in the settings file sits beside it rather than
 * arriving later as a refusal: the obvious place for a person to put a key is
 * the file they can see, and that file is inside the folder that syncs.
 */
function PasswordField({
  data,
  value,
  onChange,
  onStore,
  busy,
  disabled,
  note,
  alternatives,
}: {
  data: EndpointData;
  value: string;
  onChange: (next: string) => void;
  onStore: () => void;
  busy: boolean;
  disabled: boolean;
  note: string | null;
  alternatives: string | null;
}) {
  const stored = data.key.state === "configured";
  return (
    <div>
      <span className="block font-semibold">Password</span>
      <div className="mt-1 flex flex-wrap items-center gap-2">
        <input
          type="password"
          autoComplete="off"
          spellCheck={false}
          className="field min-w-0 flex-1 font-mono"
          value={value}
          placeholder={stored ? "stored — type a new one to replace it" : "not set"}
          onChange={(event) => onChange(event.target.value)}
        />
        <button
          type="button"
          className="btn"
          onClick={onStore}
          disabled={disabled || !value.trim()}
        >
          {busy ? "Storing…" : "Store"}
        </button>
        {!busy && disabled ? (
          <span className="text-[color:var(--color-muted)]">Waiting for Connect to finish.</span>
        ) : !busy && !value.trim() ? (
          <span className="text-[color:var(--color-muted)]">Type a password to store it.</span>
        ) : null}
      </div>
      <p className="mt-0.5 text-[color:var(--color-muted)]">
        {data.key.state === "unusable" ? (
          <>
            <span className="font-semibold text-[color:var(--color-alarm)]">
              There is one, but it cannot be used.
            </span>{" "}
            {data.key.detail}
          </>
        ) : stored ? (
          <>
            Stored in the {data.key.source}, never in your settings file.
            {data.key.source?.startsWith("environment")
              ? " That beats the keychain, so storing a new one here changes nothing until it is unset."
              : ""}
          </>
        ) : (
          "Kept in this computer's keychain, never in your settings file."
        )}
      </p>
      <details className="mt-0.5">
        <summary className="cursor-pointer text-[color:var(--color-muted)]">
          Why not in my settings file?
        </summary>
        <p className="mt-1 max-w-2xl">{data.key_explanation}</p>
      </details>
      {alternatives ? (
        <details className="mt-1" open>
          <summary className="cursor-pointer text-[color:var(--color-muted)]">
            No keychain on this machine?
          </summary>
          <p className="mt-1 max-w-2xl">{alternatives}</p>
        </details>
      ) : null}
      {note ? <p className="mt-1">{note}</p> : null}
    </div>
  );
}

function keyStoredNote(next: Settings): string {
  const where = next.endpoint.key.source ?? "the keychain";
  const resumed = next.resumed ?? 0;
  const shadowed = where.startsWith("environment")
    ? ` It is stored, but ${where} still answers first — unset it to use the one you have just stored.`
    : "";
  const drained =
    resumed > 0
      ? ` ${resumed} ${resumed === 1 ? "document that was" : "documents that were"} waiting on a password went back in the queue.`
      : "";
  return `Stored in ${where}.${shadowed}${drained}`;
}

/**
 * One line, and everything else folded underneath it.
 *
 * Success is a single sentence naming what is doing the reading. A failure is a
 * single sentence naming what stopped, with what to do about it under it and
 * the full step list one tap further — the steps matter, but they are seven
 * rows of prose and they are not what a person needs in the first second.
 */
function Status({
  endpoint,
  result,
}: {
  endpoint: EndpointData;
  result: ConnectResult | null;
}) {
  if (!result) {
    if (endpoint.configured) {
      return (
        <p className="mt-3 text-[color:var(--color-muted)]">
          Set up, reading with <span className="font-mono">{endpoint.model}</span>.
          {endpoint.last_known.state !== "unknown"
            ? ` Last time anything tried: ${endpoint.last_known.message}`
            : ""}
        </p>
      );
    }
    return (
      <p className="mt-3 text-[color:var(--color-muted)]">
        Not set up yet. Everything you add is still stored and kept — nothing is read
        for you until this connects.
      </p>
    );
  }

  const failed = result.outcome === "failed";
  const lead = failed ? "Not connected." : result.ok ? "Connected." : "Almost.";
  return (
    <div className="mt-3">
      <p>
        <span
          className="font-semibold"
          style={{ color: failed ? "var(--color-alarm)" : "var(--color-accent)" }}
        >
          {lead}
        </span>{" "}
        {withoutLead(result.status)}
      </p>
      {failed && result.detail ? <p className="mt-1 max-w-2xl">{result.detail}</p> : null}
      {result.check ? (
        <details className="mt-1">
          {/* An action, not a label: "What was checked" is also the second
              column's heading, and the same four words twice on one page reads
              as a mistake. */}
          <summary className="cursor-pointer text-[color:var(--color-muted)]">
            See what was checked
          </summary>
          <div className="table-wrap mt-1">
            <table>
              <thead>
                <tr>
                  <th scope="col">Result</th>
                  <th scope="col">What was checked</th>
                </tr>
              </thead>
              <tbody>
                {result.check.steps.map((step) => (
                  <tr key={step.name}>
                    <td className="whitespace-nowrap">
                      <span
                        className="font-semibold"
                        style={{ color: RESULT_INK[step.state] }}
                      >
                        {RESULT_WORD[step.state]}
                      </span>
                    </td>
                    <td>
                      <span className="block">{step.title}</span>
                      {step.detail ? (
                        <span className="mt-0.5 block text-[color:var(--color-muted)]">
                          {step.detail}
                        </span>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      ) : null}
    </div>
  );
}

/** The verdict word is already in front of it; never say "Connected" twice. */
function withoutLead(status: string): string {
  const rest = status.replace(/^Connected\s*[—.-]?\s*/, "").trim();
  return rest ? rest.charAt(0).toUpperCase() + rest.slice(1) : status;
}
