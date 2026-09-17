/**
 * Which installation is answering, and the instructions that depend on it.
 *
 * The app downloaded from a release page has no terminal its owner necessarily
 * knows how to open, so an instruction naming a command is a dead end there. A
 * pip install does have one. The server says which it is on every
 * `/api/health`, and this remembers the answer — including across a reload,
 * because the moment an instruction matters most is when the server has
 * stopped answering and cannot be asked.
 *
 * Commands appear only inside `<TerminalOnly>`. A test walks every component
 * and fails on `health-agent …` or `pip install` anywhere else.
 */

import type { ReactNode } from "react";

const STORAGE_KEY = "health-record.installation";

let known: boolean | null = null;

export function rememberInstallation(packaged: boolean): void {
  known = packaged;
  try {
    window.localStorage.setItem(STORAGE_KEY, packaged ? "app" : "terminal");
  } catch {
    // Storage can be refused. The answer in memory still holds for this tab.
  }
}

/** `true` for the app, `false` for a pip install, `null` when never told. */
export function installation(): boolean | null {
  if (known !== null) return known;
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored === "app") return true;
    if (stored === "terminal") return false;
  } catch {
    // Fall through to not knowing.
  }
  return null;
}

/** Rendered only on a pip install, where there is a terminal to type it into. */
export function TerminalOnly({ children }: { children: ReactNode }) {
  return installation() === false ? <>{children}</> : null;
}

/**
 * How to start the record again, in the words that fit this installation.
 * Unknown gets a sentence that is true of both and names no command.
 */
export function StartItAgain({ then }: { then?: string }) {
  const tail = then ? `, ${then}` : "";
  const packaged = installation();
  if (packaged === true) {
    return (
      <>
        Quit the Health Record app from its icon in the menu bar or the system tray, open it
        again{tail}.
      </>
    );
  }
  if (packaged === false) {
    return (
      <TerminalOnly>
        Stop the server and start it again with{" "}
        <code className="font-mono">health-agent serve</code>
        {tail}.
      </TerminalOnly>
    );
  }
  return <>Close the Health Record app and open it again{tail}.</>;
}
