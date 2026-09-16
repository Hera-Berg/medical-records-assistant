/**
 * The computer that reads your documents: where it is, and whether it works.
 *
 * Almost nothing here is new. The address guard, the key resolution, the
 * keychain write and the startup probe are all server-side and all older than
 * this screen. What this adds is that a person can point their record at their
 * own machine without editing a TOML file — and the editing was the dangerous
 * part, because the obvious place to put a key is the settings file you can
 * see, and that file is inside the folder that syncs.
 *
 * Four things are deliberate.
 *
 * **The key field only writes.** It says "set" or "not set" and takes a new
 * value. There is no read path, no route that would answer one, and nothing on
 * screen that shows the key, a prefix of it or its length. Beside it is the
 * sentence saying why it cannot go in the settings file, because a rule that
 * only appears as a refusal teaches nothing.
 *
 * **The model is chosen, not typed.** The id has to match what the box reports
 * exactly — it is checked on every read — and it has already differed from what
 * someone typed here once, by a vendor prefix. So the list is fetched from the
 * machine itself and stored verbatim. Typing is still possible, because the box
 * is asleep a good fraction of the time and a field you cannot fill while
 * offline is a field that traps you.
 *
 * **Testing and saving are separate, and testing writes nothing.** A test that
 * could only check what was already saved would make a person save a bad
 * address in order to discover it was bad. Editing any field marks an earlier
 * result stale rather than leaving a green tick under changed text.
 *
 * **Each step of the test is reported on its own line.** "Could not connect"
 * and "connected, refused the password" are different problems with different
 * answers, and the one that matters most is vision: a box that quietly discards
 * pictures passes every other check and ignores every photograph of a
 * prescription, which reads as the model being bad at reading.
 */

import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { EndpointCheck, EndpointSettings as EndpointData, Settings } from "../types";

interface Draft {
  base_url: string;
  model: string;
  header: string;
  scheme: string;
}

const draftOf = (endpoint: EndpointData): Draft => ({
  base_url: endpoint.base_url,
  model: endpoint.model,
  header: endpoint.auth.header,
  scheme: endpoint.auth.scheme,
});

/** What a test was run against, so editing anything marks the result stale. */
const signature = (draft: Draft) =>
  JSON.stringify([draft.base_url, draft.model, draft.header, draft.scheme]);

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
  const [draft, setDraft] = useState<Draft>(() => draftOf(endpoint));
  const [saved, setSaved] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const [models, setModels] = useState<string[] | null>(null);
  const [modelsNote, setModelsNote] = useState<string | null>(null);
  const [typing, setTyping] = useState(false);

  const [check, setCheck] = useState<EndpointCheck | null>(null);
  const [checkedAgainst, setCheckedAgainst] = useState<string | null>(null);

  const [keyValue, setKeyValue] = useState("");
  const [keyNote, setKeyNote] = useState<string | null>(null);

  // The saved values are the starting point, and they change under us whenever
  // a save lands. Re-seeding on the saved signature rather than on the object
  // keeps a half-typed URL from being wiped by an unrelated refresh.
  const savedSignature = signature(draftOf(endpoint));
  useEffect(() => {
    setDraft(draftOf(endpoint));
  }, [savedSignature]);

  const dirty = signature(draft) !== savedSignature;
  const stale = check !== null && checkedAgainst !== signature(draft);

  const edit = (patch: Partial<Draft>) => {
    setDraft((current) => ({ ...current, ...patch }));
    setSaved(null);
    setError(null);
  };

  const apply = useCallback(
    (next: Settings) => {
      onSettings(next);
      onChanged();
    },
    [onSettings, onChanged],
  );

  const fetchModels = () => {
    setBusy("models");
    setError(null);
    api
      .endpointModels({
        base_url: draft.base_url,
        header: draft.header,
        scheme: draft.scheme,
      })
      .then((result) => {
        setModels(result.models);
        setModelsNote(result.message);
        setTyping(result.models.length === 0);
        // Nothing is chosen for the user. One offered model is still a choice,
        // and a field that fills itself in is a field nobody reads.
      })
      .catch((exc: Error) => setError(exc.message))
      .finally(() => setBusy(null));
  };

  const test = () => {
    setBusy("test");
    setError(null);
    const against = signature(draft);
    api
      .testEndpoint(draft)
      .then((result) => {
        setCheck(result);
        setCheckedAgainst(against);
        if (result.settings) onSettings(result.settings);
        onChanged();
      })
      .catch((exc: Error) => setError(exc.message))
      .finally(() => setBusy(null));
  };

  const save = () => {
    setBusy("save");
    setError(null);
    api
      .setEndpoint(draft)
      .then((next) => {
        apply(next);
        setSaved(next.endpoint.base_url);
      })
      .catch((exc: Error) => setError(exc.message))
      .finally(() => setBusy(null));
  };

  const storeKey = () => {
    setBusy("key");
    setError(null);
    setKeyNote(null);
    api
      .setEndpointKey(keyValue)
      .then((next) => {
        apply(next);
        setKeyValue("");
        setKeyNote(keyStoredNote(next));
      })
      .catch((exc: Error) => setError(exc.message))
      .finally(() => setBusy(null));
  };

  const working = busy !== null;

  return (
    <section>
      <h2 className="text-lg font-semibold">The computer that reads your documents</h2>
      <p className="mt-1 max-w-2xl">{endpoint.explanation}</p>

      {endpoint.problem ? (
        <div className="mt-3 rounded-lg border border-[color:var(--color-alarm)] bg-[color:var(--color-alarm-soft)] p-3">
          <p className="font-semibold">Your settings file has an entry it cannot use.</p>
          <p className="mt-1">{endpoint.problem}</p>
        </div>
      ) : null}

      {/*
        Only while the form still matches the file. Once something has been
        typed, "no computer is set up yet" sits directly above a filled-in
        address and — after a test — a table of green ticks, which reads as the
        screen disagreeing with itself. The "Not saved yet." marker beside Save
        is what says it from that point on.
      */}
      {!endpoint.configured && !endpoint.problem && !dirty ? (
        <p className="mt-3 rounded-lg border border-[color:var(--color-rule)] bg-[color:var(--color-shade)] p-3">
          <span className="font-semibold">No computer is set up yet.</span> Everything you
          add is still stored and kept — photographs, letters, notes, recordings. Until
          this is set, nothing is read for you, so nothing arrives on the{" "}
          <strong>Waiting for you</strong> screen. Anything captured in the meantime is
          read when it is.
        </p>
      ) : null}

      <div className="mt-4 grid gap-4 md:max-w-3xl">
        <label className="block">
          <span className="block font-semibold">Its address</span>
          <span className="mt-0.5 block text-[color:var(--color-muted)]">
            The web address of the model server on that machine, ending in{" "}
            <code className="font-mono">/v1</code>. A Tailscale name such as{" "}
            <code className="font-mono">macbook-pro.tailnet.ts.net</code> is the usual
            answer.
          </span>
          <input
            type="url"
            inputMode="url"
            spellCheck={false}
            className="field mt-1 w-full font-mono"
            value={draft.base_url}
            placeholder="https://macbook-pro.tailnet.ts.net/v1"
            onChange={(event) => edit({ base_url: event.target.value })}
          />
        </label>

        <div>
          <span className="block font-semibold">Which model it runs</span>
          <span className="mt-0.5 block text-[color:var(--color-muted)]">
            Taken from the machine itself and stored exactly as it spells it. The record
            checks this on every document it reads, so a name that is nearly right stops
            the reading rather than being ignored — which is the point: which model
            produced a claim has to stay answerable in a year's time.
          </span>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            {models && models.length > 0 && !typing ? (
              <select
                className="field min-w-0 flex-1 font-mono"
                value={draft.model}
                onChange={(event) => edit({ model: event.target.value })}
              >
                <option value="">Choose one…</option>
                {models.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
                {draft.model && !models.includes(draft.model) ? (
                  <option value={draft.model}>
                    {draft.model} — not offered by this computer
                  </option>
                ) : null}
              </select>
            ) : (
              <input
                type="text"
                spellCheck={false}
                className="field min-w-0 flex-1 font-mono"
                value={draft.model}
                placeholder="Qwen3.8-Flash-Next-oQ4e-mtp"
                onChange={(event) => edit({ model: event.target.value })}
              />
            )}
            <button
              type="button"
              className="btn"
              onClick={fetchModels}
              disabled={working || !draft.base_url}
            >
              {busy === "models" ? "Asking…" : "Fetch the list"}
            </button>
            {models && models.length > 0 ? (
              <button type="button" className="btn" onClick={() => setTyping(!typing)}>
                {typing ? "Choose from the list" : "Type it instead"}
              </button>
            ) : null}
          </div>
          {modelsNote ? (
            <p className="mt-1 text-[color:var(--color-muted)]">{modelsNote}</p>
          ) : null}
        </div>

        {/*
          Two fields almost nobody touches, and the summary states both, so
          closing this hides nothing — it only stops the common case paying for
          the rare one.
        */}
        <details>
          <summary className="cursor-pointer font-semibold">
            How the password is sent{" "}
            <span className="font-normal text-[color:var(--color-muted)]">
              — {draft.header}: {draft.scheme ? `${draft.scheme} ` : ""}your password
            </span>
          </summary>
          <p className="mt-1 text-[color:var(--color-muted)]">
            Most servers want these left alone. Some want the password on its own with no
            word in front of it — clear the second box for that.
          </p>
          <div className="mt-1 flex flex-wrap gap-2">
            <label className="block">
              <span className="block text-[color:var(--color-muted)]">Header name</span>
              <input
                type="text"
                spellCheck={false}
                className="field mt-0.5 font-mono"
                value={draft.header}
                onChange={(event) => edit({ header: event.target.value })}
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
                value={draft.scheme}
                placeholder="(none)"
                onChange={(event) => edit({ scheme: event.target.value })}
              />
            </label>
          </div>
        </details>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button
          type="button"
          className="btn"
          onClick={test}
          disabled={working || !draft.base_url || !draft.model}
        >
          {busy === "test" ? "Testing…" : "Test this connection"}
        </button>
        <button
          type="button"
          className="btn btn-primary"
          onClick={save}
          disabled={working || !dirty || !draft.base_url || !draft.model}
        >
          {busy === "save" ? "Saving…" : "Save"}
        </button>
        {dirty ? (
          <span className="text-[color:var(--color-warn)]">Not saved yet.</span>
        ) : null}
        {saved && !dirty ? (
          <span className="text-[color:var(--color-muted)]">
            Saved to your settings file. Your password was not written to it.
          </span>
        ) : null}
      </div>

      {/*
        A panel with a sentence of our own above it, not a bare red paragraph.
        The refusals this can carry are the server's, and they are written to be
        read — but they start mid-thought ("the inference endpoint … resolves
        to …") because they were written for a terminal, and the longest of them
        is the address guard's, which is the one a person is most likely to meet
        and least likely to have expected.
      */}
      {error ? (
        <div className="mt-3 rounded-lg border border-[color:var(--color-alarm)] bg-[color:var(--color-alarm-soft)] p-3">
          <p className="font-semibold">That was not saved.</p>
          <p className="mt-1">{error}</p>
        </div>
      ) : null}

      <Result check={check} stale={stale} endpoint={endpoint} />

      <h3 className="mt-6 border-t border-[color:var(--color-rule)] pt-4 font-semibold">
        Its password
      </h3>
      <p className="mt-1 max-w-2xl text-[color:var(--color-muted)]">
        {endpoint.key_explanation}
      </p>
      <p className="mt-2">
        <KeyStatus data={endpoint} />
      </p>
      <div className="mt-2 flex flex-wrap items-end gap-2">
        <label className="block">
          <span className="block text-[color:var(--color-muted)]">
            {endpoint.key.state === "configured"
              ? "Replace it with a new one"
              : "Set the password"}
          </span>
          <input
            type="password"
            autoComplete="off"
            spellCheck={false}
            className="field mt-0.5 w-72 max-w-full font-mono"
            value={keyValue}
            placeholder="••••••••"
            onChange={(event) => setKeyValue(event.target.value)}
          />
        </label>
        <button
          type="button"
          className="btn"
          onClick={storeKey}
          disabled={working || !keyValue.trim()}
        >
          {busy === "key" ? "Storing…" : "Store it in the keychain"}
        </button>
      </div>
      <p className="mt-1 text-[color:var(--color-muted)]">
        Once stored it is never shown again, here or anywhere else. To change it, put a
        new one in — there is nothing to read back.
      </p>
      {keyNote ? <p className="mt-1">{keyNote}</p> : null}
    </section>
  );
}

/** Whether a password is available, and which place answered. Never the value. */
function KeyStatus({ data }: { data: EndpointData }) {
  if (data.key.state === "configured") {
    return (
      <>
        <span className="font-semibold">Stored.</span>{" "}
        <span className="text-[color:var(--color-muted)]">
          Found in the {data.key.source}.
          {data.key.source && data.key.source.startsWith("environment")
            ? " That beats the keychain, so storing a new one below will not change what" +
              " is sent until it is unset."
            : ""}
        </span>
      </>
    );
  }
  if (data.key.state === "unusable") {
    return (
      <>
        <span className="font-semibold text-[color:var(--color-alarm)]">
          There is one, but it cannot be used.
        </span>{" "}
        {data.key.detail}
      </>
    );
  }
  return (
    <>
      <span className="font-semibold">Not set.</span>{" "}
      <span className="text-[color:var(--color-muted)]">
        Nothing will be read until there is one.
      </span>
    </>
  );
}

function keyStoredNote(next: Settings): string {
  const where = next.endpoint.key.source ?? "the keychain";
  const resumed = next.resumed ?? 0;
  const shadowed = where.startsWith("environment")
    ? ` It is stored, but ${where} still answers first, so that is what is being sent — unset it to use the one you have just stored.`
    : "";
  const drained =
    resumed > 0
      ? ` ${resumed} ${resumed === 1 ? "document that was" : "documents that were"} waiting on a password went back in the queue.`
      : "";
  return `Stored in ${where}.${shadowed}${drained}`;
}

/**
 * The test, step by step.
 *
 * A table because it is list-shaped, and one row per step including the steps
 * that were never reached: a list that shortened itself would make "it stopped
 * here" look the same as "this is not checked on this machine".
 */
function Result({
  check,
  stale,
  endpoint,
}: {
  check: EndpointCheck | null;
  stale: boolean;
  endpoint: EndpointData;
}) {
  if (!check) {
    return (
      <div className="mt-3 text-[color:var(--color-muted)]">
        <p>
          Not tested from here yet. Testing changes nothing and saves nothing — it asks
          the computer a short list of questions in order and tells you which one it
          stopped at.
        </p>
        {/*
          What the app already knows, which is not nothing: the worker probes
          the box on its own, and a screen that said "not tested yet" while a
          banner two inches above said the key had been rejected would be the
          app disagreeing with itself.
        */}
        {endpoint.configured && endpoint.last_known.state !== "unknown" ? (
          <p className="mt-1">
            <span className="font-semibold">Last time anything tried:</span>{" "}
            {endpoint.last_known.message}
          </p>
        ) : null}
      </div>
    );
  }
  return (
    <div className="mt-3">
      {stale ? (
        <p className="mb-2 rounded-lg border border-[color:var(--color-warn)] bg-[color:var(--color-warn-soft)] px-3 py-2">
          <span className="font-semibold">This result is out of date.</span> Something
          above has been changed since the test ran. Test again.
        </p>
      ) : (
        <p className="mb-2">
          <span className="font-semibold">
            {check.ok ? "It works." : "It does not work yet."}
          </span>{" "}
          {check.message}
        </p>
      )}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col">Result</th>
              <th scope="col">What was checked</th>
            </tr>
          </thead>
          <tbody>
            {check.steps.map((step) => (
              <tr key={step.name}>
                <td className="whitespace-nowrap">
                  <span className="font-semibold" style={{ color: RESULT_INK[step.state] }}>
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
    </div>
  );
}
