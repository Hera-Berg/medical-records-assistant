/**
 * What needs a person, said in sentences.
 *
 * This replaced a status strip that read `box key rejected  to review 4 (3
 * high)  anomalies 1`. Every fact in that line was correct and none of it told
 * the owner of the record what had happened or what to do, which for the one
 * reader this app has is the whole job.
 *
 * The distinction the banners exist to keep is the one ``MODELS.md`` insists
 * on: **unreachable and unauthorised are different states.** A sleeping box
 * drains by itself and gets a quiet line in the sidebar; a rejected key needs
 * someone to act and gets an alarm here, in words that name the cause. Only one
 * of them is allowed to interrupt.
 *
 * Nothing here is a spinner. "Six files are waiting to be read" is informative;
 * a spinner is not, and the queue is now frequently paused rather than slow.
 *
 * Tone is a left rule and a tinted ground. It never carries the meaning — every
 * banner says what it means in its first sentence, so the page survives being
 * printed, and survives a reader who cannot tell the tints apart.
 */

import type { Health } from "../types";

type Tone = "alarm" | "warn" | "info";

const TONES: Record<Tone, { rule: string; ground: string; ink: string }> = {
  alarm: {
    rule: "var(--color-alarm)",
    ground: "var(--color-alarm-soft)",
    ink: "var(--color-alarm)",
  },
  warn: {
    rule: "var(--color-warn)",
    ground: "var(--color-warn-soft)",
    ink: "var(--color-warn)",
  },
  info: {
    rule: "var(--color-accent)",
    ground: "var(--color-accent-soft)",
    ink: "var(--color-accent)",
  },
};

export function Attention({ health, error }: { health: Health | null; error: string | null }) {
  if (error) {
    return (
      <Banner tone="alarm" title="The record app is not answering.">
        Nothing has been lost — everything is a file in your folder. Start it again with{" "}
        <code className="font-mono">health-agent serve</code>.
      </Banner>
    );
  }
  if (!health) return null;

  const endpoint = health.endpoint;
  const banners: React.ReactNode[] = [];
  /*
    `problems` carries the endpoint's own message when the endpoint is the
    problem, so rendering both stacked two banners saying the same thing in two
    voices — one of them the machine's. Said once, in the words that name what
    to do about it.
  */
  const said = new Set<string>();

  if (endpoint.state === "unauthorised") {
    said.add(endpoint.message);
    banners.push(
      <Banner key="auth" tone="alarm" title="The reading box refused the password.">
        It has probably been changed. Nothing is lost and nothing is being retried —{" "}
        {health.queue.blocked_auth > 0
          ? `${countWord(health.queue.blocked_auth, "file is", "files are")} waiting for a new one. `
          : "the queue is paused. "}
        Set a new password with <code className="font-mono">health-agent set-key</code>, then
        start the queue again.
      </Banner>,
    );
  } else if (endpoint.state === "misconfigured" || endpoint.state === "vision-not-working") {
    said.add(endpoint.message);
    banners.push(
      <Banner key="endpoint" tone="alarm" title="The reading box is not set up correctly.">
        {endpoint.message} Your files are stored and safe; they just have not been read.
      </Banner>,
    );
  }

  for (const problem of health.problems) {
    if (said.has(problem)) continue;
    banners.push(
      <Banner key={problem} tone="alarm" title="Something needs attention.">
        {problem}
      </Banner>,
    );
  }

  if (health.vault.demo) {
    banners.push(
      <Banner key="demo" tone="warn" title="This is a demonstration, not a real record.">
        Everything here was invented so the screens have something to show. Nothing in it
        came from a person.
      </Banner>,
    );
  }

  if (health.review.total > 0) {
    banners.push(
      <Banner
        key="review"
        tone="info"
        title={`${countWord(health.review.total, "thing is", "things are")} waiting for you to confirm.`}
      >
        {health.review.by_tier.high > 0 ? (
          <>
            {countWord(health.review.by_tier.high, "of them is", "of them are")} important —
            a medication, a dose or an allergy. Nothing like that is ever added to your
            record without you tapping to say so.{" "}
          </>
        ) : null}
        Confirming from this screen is not built yet; until it is, they wait, and your
        record shows only what you have already agreed to.
      </Banner>,
    );
  }

  if (health.queue.depth > 0) {
    banners.push(
      <Banner
        key="queue"
        tone="info"
        title={`${countWord(health.queue.depth, "file is", "files are")} waiting to be read.`}
      >
        They are already stored in your folder. Nothing is lost while they wait.
      </Banner>,
    );
  }

  /*
    Anomalies are things the record noticed about itself — a malformed line, a
    decision that names no claim. CLAUDE.md requires them to be surfaced where
    they cannot scroll past unseen, and a number in a strip with the detail
    hidden in a tooltip is exactly scrolling past unseen: a tooltip does not
    exist on a phone and does not exist on paper. The count is the summary and
    the sentences are one tap away.
  */
  if (health.anomalies.count > 0) {
    banners.push(
      <Banner
        key="anomalies"
        tone="warn"
        title={
          health.anomalies.count === 1
            ? "One note about the record itself."
            : `${health.anomalies.count} notes about the record itself.`
        }
      >
        These are about the record's own filing, not about your health.
        <details className="mt-1">
          <summary className="cursor-pointer">
            {health.anomalies.count === 1 ? "See the note" : "See the notes"}
          </summary>
          <ul className="mt-1 list-disc pl-5">
            {health.anomalies.items.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </details>
      </Banner>,
    );
  }

  if (banners.length === 0) return null;
  return <div className="mb-4 space-y-2 no-print">{banners}</div>;
}

function Banner({
  tone,
  title,
  children,
}: {
  tone: Tone;
  title: string;
  children: React.ReactNode;
}) {
  const spec = TONES[tone];
  return (
    <div
      className="rounded-lg border border-l-4 px-4 py-2"
      style={{
        borderColor: "var(--color-rule)",
        borderLeftColor: spec.rule,
        background: spec.ground,
      }}
    >
      <p>
        <span className="font-semibold" style={{ color: spec.ink }}>
          {title}
        </span>{" "}
        {children}
      </p>
    </div>
  );
}

/** "1 thing is" / "4 things are", without a bare number leading a sentence. */
function countWord(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}
