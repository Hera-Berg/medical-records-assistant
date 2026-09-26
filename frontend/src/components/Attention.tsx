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
 *
 * **Only an alarm is a wall of words.** Routine news — things waiting for you,
 * files waiting to be read — is one line that says so, with the rest of the
 * explanation folded under "More" and a link to where it is dealt with. Four
 * stacked paragraphs above every page was the most daunting thing on screen,
 * and none of it was urgent. Folded is not hidden: the first line still says
 * what is waiting and how many.
 */

import type { Health } from "../types";
import { Link } from "../router";
import { StartItAgain } from "../installation";
import { CheckSteps } from "./CheckSteps";

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

export function Attention({
  health,
  error,
  quietEndpoint = false,
  quietReview = false,
  navigate,
}: {
  health: Health | null;
  error: string | null;
  navigate: (to: string) => void;
  /** Leave out the "waiting for you" notices: set on the screen they point at. */
  quietReview?: boolean;
  /**
   * Suppress the banner about the inference box.
   *
   * Set on the settings screen and the first run's welcome screen, which holds
   * the same controls. The banner exists so a rejected
   * key cannot be missed from any other screen; on a screen that holds
   * the control for it, and says in its own words what stopped, the banner is
   * the same event told twice in two voices two inches apart. The sidebar's
   * one-word state stays either way.
   */
  quietEndpoint?: boolean;
}) {
  if (error) {
    return (
      <Banner tone="alarm" title="The record app is not answering.">
        Nothing has been lost — everything is a file in your folder.{" "}
        <StartItAgain then="then reload this page" />
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

  if (quietEndpoint && endpoint.state !== "working") {
    // Still marked as said, so the copy of it inside `problems` is suppressed
    // with it rather than surviving as a bare "Something needs attention".
    said.add(endpoint.message);
  } else if (endpoint.state === "unauthorised") {
    said.add(endpoint.message);
    banners.push(
      <Banner key="auth" tone="alarm" title="The reading box refused the password.">
        It has probably been changed. Nothing is lost and nothing is being retried —{" "}
        {health.queue.blocked_auth > 0
          ? `${countWord(health.queue.blocked_auth, "file is", "files are")} waiting for a new one. `
          : "the queue is paused. "}
        Put the new password in <strong>Settings</strong>, under Read on another computer, and
        the waiting files start again by themselves.
      </Banner>,
    );
  } else if (endpoint.state === "stopped") {
    said.add(endpoint.message);
    banners.push(
      <Banner key="reader" tone="alarm" title="The reader on this computer has stopped.">
        {endpoint.message}
      </Banner>,
    );
  } else if (
    endpoint.state === "not-downloaded" &&
    endpoint.where === "this-computer"
  ) {
    said.add(endpoint.message);
    banners.push(
      <Banner
        key="download"
        tone="info"
        title="Reading documents on this computer needs a one-time download."
      >
        It is started from <strong>Settings</strong>, which says how big it is and where it
        comes from first. Anything you add in the meantime is kept and waits to be read.
      </Banner>,
    );
  } else if (endpoint.state === "misconfigured" || endpoint.state === "vision-not-working") {
    said.add(endpoint.message);
    banners.push(
      <Banner key="endpoint" tone="alarm" title="The reading box is not set up correctly.">
        {endpoint.message} Your files are stored and safe; they just have not been read.
        <CheckSteps steps={endpoint.steps} summary="Details" />
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

  // No banner for a demonstration record: whoever opened one knows it is a
  // demo, and a paragraph saying so above every page was noise. The top bar
  // still labels it, because what a demo extracts is still invented.

  const unread = health.review.could_not_read ?? 0;
  const review = quietReview ? null : (
    <Link to="/review" navigate={navigate}>
      Go to Waiting for you
    </Link>
  );
  if (unread > 0 && !quietReview) {
    banners.push(
      <Banner
        key="unread"
        tone="warn"
        title={`${countWord(unread, "document", "documents")} could not be fully read.`}
        action={review}
        folded
      >
        Nothing was guessed from the parts that could not be read. Check{" "}
        {unread === 1 ? "it" : "them"} on the <strong>Waiting for you</strong> screen —
        photograph again or type in anything that matters.
      </Banner>,
    );
  }

  const toConfirm = health.review.total - unread;
  if (toConfirm > 0 && !quietReview) {
    banners.push(
      <Banner
        key="review"
        tone="info"
        title={`${countWord(toConfirm, "thing is", "things are")} waiting for you to confirm.`}
        action={review}
        folded
      >
        {health.review.by_tier.high > 0 ? (
          <>
            {countWord(health.review.by_tier.high, "of them is", "of them are")} important —
            a medication, a dose or an allergy. Nothing like that is ever added to your
            record without you tapping to say so.{" "}
          </>
        ) : null}
        They are on the <strong>Waiting for you</strong> screen, and your record shows
        only what you have already agreed to until you get to them.
      </Banner>,
    );
  }

  if (health.queue.depth > 0) {
    banners.push(
      <Banner
        key="queue"
        tone="info"
        title={`${countWord(health.queue.depth, "file is", "files are")} waiting to be read.`}
        folded
      >
        They are already stored in your folder. Nothing is lost while they wait.
        {health.reading ? (
          <>
            {" "}
            {health.reading.pace}
            {health.reading.eta ? ` ${health.reading.eta}` : null}
            {endpoint.state === "sleeping" || endpoint.state === "starting"
              ? " The reader is waking up to read them."
              : null}
          </>
        ) : null}
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
        action={review}
        folded
      >
        About how your record is filed, not about your health.
        <ul className="mt-1 list-disc pl-5">
          {health.anomalies.items.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </Banner>,
    );
  }

  if (banners.length === 0) return null;
  return <div className="mt-2 mb-2 space-y-2 no-print">{banners}</div>;
}

function Banner({
  tone,
  title,
  children,
  action,
  folded = false,
}: {
  tone: Tone;
  title: string;
  children: React.ReactNode;
  /** Where this is dealt with, beside the title so it needs no reading first. */
  action?: React.ReactNode;
  /** One line, with the explanation under "More". Never for an alarm. */
  folded?: boolean;
}) {
  const spec = TONES[tone];
  const frame = {
    borderColor: "var(--color-rule)",
    borderLeftColor: spec.rule,
    background: spec.ground,
  };
  const heading = (
    <span className="font-semibold" style={{ color: spec.ink }}>
      {title}
    </span>
  );

  if (folded && tone !== "alarm") {
    return (
      <details className="rounded-xl border border-l-4 px-4 py-2" style={frame}>
        <summary className="flex cursor-pointer list-none flex-wrap items-baseline gap-x-3">
          {heading}
          {action}
          <span className="ml-auto text-[color:var(--color-muted)] underline">More</span>
        </summary>
        <div className="mt-1">{children}</div>
      </details>
    );
  }

  return (
    <div className="rounded-xl border border-l-4 px-4 py-2" style={frame}>
      <p>
        {heading} {children}
      </p>
      {action ? <p className="mt-1">{action}</p> : null}
    </div>
  );
}

/** "1 thing is" / "4 things are", without a bare number leading a sentence. */
function countWord(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}
