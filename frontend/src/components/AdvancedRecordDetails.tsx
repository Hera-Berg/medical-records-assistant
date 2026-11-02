import { Link } from "../router";
import type { EndpointState, Health } from "../types";

const ENDPOINT_WORDS: Record<
  EndpointState,
  { short: string; tone: "good" | "quiet" | "alarm" }
> = {
  working: { short: "Ready to read new files", tone: "good" },
  unreachable: { short: "Asleep — files wait safely", tone: "quiet" },
  unauthorised: { short: "Password rejected", tone: "alarm" },
  misconfigured: { short: "Set up incorrectly", tone: "alarm" },
  "vision-not-working": { short: "Cannot read pictures", tone: "alarm" },
  "not-configured": { short: "Not set up yet", tone: "quiet" },
  unknown: { short: "Not checked yet", tone: "quiet" },
  "not-downloaded": { short: "Needs a one-time download", tone: "quiet" },
  sleeping: { short: "Sleeping — wakes when you add something", tone: "quiet" },
  starting: { short: "Starting up", tone: "quiet" },
  stopped: { short: "Stopped — see Settings", tone: "alarm" },
};


/** Technical details belong in settings, not in the workspace navigation. */
export function AdvancedRecordDetails({ health, root, navigate }: {
  health: Health | null;
  root: string;
  navigate: (to: string) => void;
}) {
  const state = health?.endpoint;
  const reader = state
    ? state.state === "working" && state.where === "this-computer"
      ? "Reading on this computer"
      : (ENDPOINT_WORDS[state.state] ?? ENDPOINT_WORDS.unknown).short
    : "Checking reader status…";
  return (
    <dl className="data-list">
      <div className="data-row"><dt>Record storage</dt><dd><code className="storage-path">{root}</code> <Link to="/files" navigate={navigate} className="source-link">Browse record files</Link></dd></div>
      <div className="data-row"><dt>Storage mode</dt><dd>{health?.vault.sync_profile ?? "Checking…"}</dd></div>
      <div className="data-row"><dt>File reader</dt><dd>{reader}</dd></div>
    </dl>
  );
}
