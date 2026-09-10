/**
 * One dense line saying what the system is doing, and what needs a person.
 *
 * The distinction this bar exists to keep is the one ``MODELS.md`` insists on:
 * **unreachable and unauthorised are different states.** A sleeping box drains
 * by itself and is worth a quiet note; a rejected key needs someone to act and
 * is worth an alarm. Collapsing them into one "offline" is how a rotated key
 * looks like a sleeping Mac and nobody investigates for a week — so they get
 * different words, different weight, and only one of them gets the word
 * "needs".
 *
 * Nothing here is a spinner. "Box offline — 6 items queued" is informative; a
 * spinner is not, and the queue is now frequently paused rather than slow.
 */

import type { EndpointState, Health } from "../types";

const ENDPOINT_WORDS: Record<EndpointState, { word: string; urgent: boolean }> = {
  working: { word: "reading", urgent: false },
  unreachable: { word: "offline", urgent: false },
  unauthorised: { word: "key rejected", urgent: true },
  misconfigured: { word: "misconfigured", urgent: true },
  "vision-not-working": { word: "not reading images", urgent: true },
  "not-configured": { word: "none configured", urgent: false },
  unknown: { word: "not checked", urgent: false },
};

export function StatusBar({
  health,
  error,
  uploading,
  onRebuild,
}: {
  health: Health | null;
  error: string | null;
  uploading: number;
  onRebuild: () => void;
}) {
  if (error) {
    return (
      <div className="border-b border-[color:var(--color-tier-inf)] bg-[color:var(--color-shade)] px-3 py-1">
        <span className="font-semibold text-[color:var(--color-tier-inf)]">
          The local server is not responding.
        </span>{" "}
        <span className="text-[color:var(--color-muted)]">
          Nothing has been lost — everything is in your folder. Restart it with{" "}
          <code className="font-mono">health-agent serve</code>.
        </span>
      </div>
    );
  }
  if (!health) {
    return (
      <div className="border-b border-[color:var(--color-rule)] px-3 py-1 text-[color:var(--color-muted)]">
        Reading your record…
      </div>
    );
  }

  const endpoint = ENDPOINT_WORDS[health.endpoint.state] ?? ENDPOINT_WORDS.unknown;

  return (
    <div className="border-b border-[color:var(--color-rule)] px-3 py-1">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-0.5">
        <Item
          label="box"
          value={endpoint.word}
          urgent={endpoint.urgent}
          title={health.endpoint.message}
        />
        {health.queue.depth > 0 ? (
          <Item
            label="queued"
            value={String(health.queue.depth)}
            title="Captured and waiting to be read. Nothing is lost while it waits."
          />
        ) : null}
        {health.queue.blocked_auth > 0 ? (
          <Item
            label="blocked"
            value={`${health.queue.blocked_auth} — needs a new key`}
            urgent
            title="These will not be retried until the key is replaced."
          />
        ) : null}
        <Item
          label="to review"
          value={
            health.review.total === 0
              ? "nothing"
              : `${health.review.total} (${health.review.by_tier.high} high)`
          }
          title="Reviewing arrives in phase 7. Until then, use the terminal."
        />
        {health.anomalies.count > 0 ? (
          <Item
            label="anomalies"
            value={String(health.anomalies.count)}
            title={health.anomalies.items.join("\n")}
          />
        ) : null}
        {uploading > 0 ? (
          <Item label="uploading" value={String(uploading)} title="In the background." />
        ) : null}
        <span className="ml-auto flex items-baseline gap-3 no-print">
          <span className="text-[color:var(--color-muted)]">
            {health.record.events} events · {health.record.artifacts} files
          </span>
          <button
            type="button"
            onClick={onRebuild}
            title="Regenerate wiki/ from the event log. Removes only what it wrote."
            className="border border-[color:var(--color-rule-strong)] px-2"
          >
            Rebuild
          </button>
        </span>
      </div>

      {health.problems.length > 0 ? (
        <ul className="mt-1 border-t border-[color:var(--color-rule)] pt-1">
          {health.problems.map((problem) => (
            <li key={problem} className="text-[color:var(--color-tier-inf)]">
              {problem}
            </li>
          ))}
        </ul>
      ) : null}

      {health.vault.demo ? (
        <p className="mt-1 border-t border-[color:var(--color-rule)] pt-1 font-semibold">
          This vault holds invented demonstration data, not a real record.
        </p>
      ) : null}
    </div>
  );
}

function Item({
  label,
  value,
  urgent,
  title,
}: {
  label: string;
  value: string;
  urgent?: boolean;
  title?: string;
}) {
  return (
    <span title={title} className="whitespace-nowrap">
      <span className="text-[color:var(--color-muted)]">{label} </span>
      <span
        className={urgent ? "font-semibold" : ""}
        style={urgent ? { color: "var(--color-tier-inf)" } : undefined}
      >
        {value}
      </span>
    </span>
  );
}
