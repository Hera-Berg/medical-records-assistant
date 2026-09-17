/**
 * The first run, before there is a record: where should it be kept?
 *
 * Someone with no coding ability should be able to press the one button on this
 * page without changing anything and end up with a working record. So the
 * first option is a folder on this computer, it is already chosen, and it is
 * named in words that need no idea of what syncing is. Dropbox, Google Drive and
 * Nextcloud follow for people who recognise them, and "somewhere else" takes a
 * path for people who know exactly where they want it.
 *
 * Nothing is written until the button is pressed. Before that, the server
 * works out what the button would do — make a new folder, or open a record that
 * is already there — and this page says so in a sentence, with the settings
 * file it would write, one tap away.
 *
 * The same page reports a record that cannot be opened: a folder that is not
 * where it was, a settings file that will not load, or this computer's
 * identity having come from another computer.
 */

import { useCallback, useEffect, useState } from "react";
import { api, setupApi } from "../api";
import type { SetupPlan, SetupState } from "../types";

const ELSEWHERE = "somewhere-else";

export function Setup({ initial }: { initial: SetupState }) {
  const [state, setState] = useState<SetupState>(initial);
  const [choosing, setChoosing] = useState(initial.stage === "choose");

  return (
    <main className="min-h-screen bg-[color:var(--color-ground)]">
      <div className="mx-auto max-w-3xl px-4 py-8 sm:px-6">
        <p className="font-semibold text-[color:var(--color-accent)]">Your health record</p>
        {state.stage === "problem" && state.problem && !choosing ? (
          <Problem state={state} onChoose={() => setChoosing(true)} onState={setState} />
        ) : (
          <Choose state={state} />
        )}
        {state.packaged ? <Unsigned platform={state.platform} /> : null}
      </div>
    </main>
  );
}

function Choose({ state }: { state: SetupState }) {
  const [key, setKey] = useState(state.locations[0]?.key ?? ELSEWHERE);
  const [elsewhere, setElsewhere] = useState("");
  const [name, setName] = useState(state.default_name);
  const [plan, setPlan] = useState<SetupPlan | null>(null);
  const [checking, setChecking] = useState(false);
  const [working, setWorking] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const chosen = state.locations.find((location) => location.key === key);
  const parent = key === ELSEWHERE ? elsewhere : (chosen?.parent ?? "");

  useEffect(() => {
    let live = true;
    setChecking(true);
    const timer = window.setTimeout(() => {
      setupApi
        .examine(parent, name)
        .then((result) => live && setPlan(result))
        .catch((exc: Error) => live && setProblem(exc.message))
        .finally(() => live && setChecking(false));
    }, 250);
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [parent, name]);

  const go = () => {
    if (!plan || !plan.action) return;
    setWorking(true);
    setProblem(null);
    setupApi
      .choose(parent, name, plan.action)
      .then(() => openRecord("/welcome"))
      .catch((exc: Error) => {
        setProblem(exc.message);
        setWorking(false);
      });
  };

  const ready = plan !== null && plan.refusal === null && !checking && !working;
  const why = working
    ? "Opening your record…"
    : checking || plan === null
      ? "Checking that folder…"
      : plan.refusal;

  return (
    <section>
      <h1 className="mt-2 text-xl font-semibold">Where should your record be kept?</h1>
      <p className="mt-2 max-w-2xl">
        Your record is an ordinary folder: your documents exactly as you added them, and
        plain text files that describe them. Anything that can open a folder can read it,
        with or without this app. Choose where that folder goes. If you are not sure, the
        first choice is the right one.
      </p>

      <fieldset className="mt-5" disabled={working}>
        <legend className="sr-only">Where the record folder goes</legend>
        {state.locations.map((location) => (
          <Option
            key={location.key}
            checked={key === location.key}
            onChoose={() => setKey(location.key)}
            title={location.label}
            detail={location.explanation}
            path={`${location.parent}`}
          />
        ))}
        <Option
          checked={key === ELSEWHERE}
          onChoose={() => setKey(ELSEWHERE)}
          title="Somewhere else"
          detail="Type the whole path of a folder, as your file manager shows it."
        >
          {key === ELSEWHERE ? (
            <input
              type="text"
              className="field mt-2 w-full font-mono"
              value={elsewhere}
              onChange={(event) => setElsewhere(event.target.value)}
              placeholder={state.locations[0]?.parent}
              aria-label="The folder the record goes inside"
            />
          ) : null}
        </Option>

        <label className="mt-4 block">
          <span className="block font-semibold">The record folder's name</span>
          <input
            type="text"
            className="field mt-1 w-full max-w-sm"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
      </fieldset>

      <PlanSays plan={plan} checking={checking} />

      <div className="mt-5 flex flex-wrap items-baseline gap-3">
        <button type="button" className="btn btn-primary" onClick={go} disabled={!ready}>
          {plan?.action === "join" ? "Open this record" : "Create my record here"}
        </button>
        {/* Why the button cannot be pressed, said beside the button. */}
        {!ready && why ? (
          <span
            className={
              plan?.refusal && !checking && !working
                ? "text-[color:var(--color-alarm)]"
                : "text-[color:var(--color-muted)]"
            }
          >
            {why}
          </span>
        ) : null}
      </div>
      {problem ? <p className="mt-2 text-[color:var(--color-alarm)]">{problem}</p> : null}
    </section>
  );
}

function PlanSays({ plan, checking }: { plan: SetupPlan | null; checking: boolean }) {
  if (!plan || plan.refusal || checking) return null;
  return (
    <div className="mt-5 rounded-lg border border-[color:var(--color-rule)] bg-[color:var(--color-paper)] p-3">
      {plan.action === "join" ? (
        <p>
          <span className="font-semibold">There is already a record in this folder.</span> It
          will be opened as it is, and nothing in it is changed. This computer is given its
          own name in the record, so it never writes over another computer's entries.
        </p>
      ) : (
        <p>
          <span className="font-semibold">A new folder will be made</span> at{" "}
          <span className="font-mono">{plan.target}</span>.
        </p>
      )}
      {plan.warning ? (
        <p className="mt-2 rounded-lg bg-[color:var(--color-warn-soft)] p-2">
          <span className="font-semibold">{plan.profile_label}. </span>
          {plan.warning}
        </p>
      ) : null}
      {plan.config_text ? (
        <details className="mt-2">
          <summary className="cursor-pointer text-[color:var(--color-muted)]">
            See the settings file it will contain
          </summary>
          <pre className="mt-1 overflow-x-auto whitespace-pre-wrap font-mono">
            {plan.config_text}
          </pre>
        </details>
      ) : null}
    </div>
  );
}

function Option({
  checked,
  onChoose,
  title,
  detail,
  path,
  children,
}: {
  checked: boolean;
  onChoose: () => void;
  title: string;
  detail: string;
  path?: string;
  children?: React.ReactNode;
}) {
  return (
    <label
      className={
        "mt-2 flex gap-3 rounded-lg border p-3 " +
        (checked
          ? "border-[color:var(--color-accent)] bg-[color:var(--color-accent-soft)]"
          : "border-[color:var(--color-rule)] bg-[color:var(--color-paper)]")
      }
    >
      <input
        type="radio"
        name="location"
        checked={checked}
        onChange={onChoose}
        className="mt-1 self-start"
      />
      <span className="min-w-0 flex-1">
        <span className="block font-semibold">{title}</span>
        <span className="mt-0.5 block text-[color:var(--color-muted)]">{detail}</span>
        {path ? <span className="mt-0.5 block break-all font-mono">{path}</span> : null}
        {children}
      </span>
    </label>
  );
}

const PROBLEM_TITLES: Record<string, string> = {
  "folder-missing": "Your record folder is not where it was.",
  "identity-elsewhere": "This computer's name in your record came from another computer.",
  "cannot-open": "Your record could not be opened.",
};

function Problem({
  state,
  onChoose,
  onState,
}: {
  state: SetupState;
  onChoose: () => void;
  onState: (next: SetupState) => void;
}) {
  const problem = state.problem!;
  const [working, setWorking] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const after = useCallback(
    (call: () => Promise<unknown>) => {
      setWorking(true);
      setNote(null);
      call()
        .then(() => waitForRecord())
        .then((opened) => {
          if (opened) {
            window.location.assign("/");
            return;
          }
          return setupApi.state().then((next) => {
            onState(next);
            setNote("It still could not be opened. What it says now is above.");
          });
        })
        .catch((exc: Error) => setNote(exc.message))
        .finally(() => setWorking(false));
    },
    [onState],
  );

  return (
    <section>
      <h1 className="mt-2 text-xl font-semibold">{PROBLEM_TITLES[problem.code]}</h1>
      <p className="mt-2 max-w-2xl">
        Nothing in your record has been changed or lost. This is the app being unable to
        open it, not the record going wrong.
      </p>
      <p className="mt-3 max-w-2xl rounded-lg border border-[color:var(--color-rule)] bg-[color:var(--color-paper)] p-3">
        {problem.message}
      </p>

      {problem.code === "identity-elsewhere" ? (
        <div className="mt-4 max-w-2xl">
          <p>
            Each computer writes its own entries under its own name, so that two computers
            never write into the same file. The name this computer has was made on another
            computer — usually because files were copied or restored from a backup. Giving
            this computer a new name keeps everything already in your record exactly as it
            is, and keeps the old name's file beside the new one.
          </p>
          <button
            type="button"
            className="btn btn-primary mt-3"
            disabled={working}
            onClick={() => after(setupApi.newIdentity)}
          >
            Give this computer a new name
          </button>
        </div>
      ) : null}

      <div className="mt-4 flex flex-wrap items-baseline gap-3">
        <button
          type="button"
          className={problem.code === "identity-elsewhere" ? "btn" : "btn btn-primary"}
          disabled={working}
          onClick={() => after(setupApi.retry)}
        >
          Try again
        </button>
        <button type="button" className="btn" disabled={working} onClick={onChoose}>
          {problem.code === "folder-missing" ? "Choose where it is now" : "Choose a different folder"}
        </button>
        {working ? <span className="text-[color:var(--color-muted)]">Opening…</span> : null}
      </div>
      {note ? <p className="mt-2">{note}</p> : null}
    </section>
  );
}

/**
 * Why the computer warned before this app opened. Only in the downloaded app,
 * and in the words of the warning the person has just seen. Never says the app
 * is signed or verified in any other way: it is neither.
 */
function Unsigned({ platform }: { platform: string | null }) {
  const mac = platform?.startsWith("macos");
  const windows = platform?.startsWith("windows");
  return (
    <details className="mt-10 max-w-2xl border-t border-[color:var(--color-rule)] pt-4">
      <summary className="cursor-pointer font-semibold">
        Why your computer warned you before this app opened
      </summary>
      <p className="mt-2">
        This app is not signed. Signing is a certificate bought from{" "}
        {mac ? "Apple" : windows ? "Microsoft or a company it trusts" : "Apple or Microsoft"}{" "}
        each year, and this project has not paid for one.{" "}
        {mac
          ? "So your Mac could not check who made it, and you had to allow it in System Settings, under Privacy & Security."
          : windows
            ? "So Windows showed “Windows protected your PC”, and you had to choose More info, then Run anyway."
            : "So your computer could not check who made it."}{" "}
        Each new version of the app will ask again.
      </p>
      <p className="mt-2">
        Not being signed does not change what the app does. Its source code is public, and
        the page you downloaded it from lists a checksum for every file, so you can confirm
        your copy is exactly the one that was published there. It cannot tell you whether
        what was published is safe; only reading the source, or trusting the people who
        did, can.
      </p>
    </details>
  );
}

/** Wait for the record's own server to replace the setup server, then go there. */
export function openRecord(path: string): void {
  waitForRecord().then((opened) => {
    if (opened) window.location.assign(path);
    else window.location.reload();
  });
}

/**
 * `true` once the record answers; `false` once it is clear the setup page is
 * still what is being served — the record could not be opened again. While the
 * app is between the two servers neither answers, and that is waited out: a
 * large record that is being joined can take a few seconds to open.
 */
async function waitForRecord(): Promise<boolean> {
  await pause(600);
  for (let attempt = 0; attempt < 240; attempt += 1) {
    try {
      await api.health();
      return true;
    } catch {
      /* not the record, or not answering yet */
    }
    if (attempt >= 2) {
      try {
        await setupApi.state();
        return false;
      } catch {
        /* nothing answering: still changing over */
      }
    }
    await pause(500);
  }
  return false;
}

function pause(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

