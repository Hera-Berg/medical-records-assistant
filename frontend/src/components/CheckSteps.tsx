/**
 * Every check the record ran against the computer that reads documents.
 *
 * The same table wherever a failure is reported — after pressing Connect, and
 * beside a banner raised by the check the app ran on its own — so "what failed,
 * and what to change" never needs a terminal. Every sentence in it was written
 * on the server and chosen by the check's name; nothing the far end said is in
 * it, because that text can quote what was sent.
 */

import type { EndpointStep } from "../types";

const RESULT_WORD: Record<string, string> = {
  ok: "Worked",
  failed: "Failed",
  "not-checked": "Not checked",
};

/* Tone is a word first and a colour second — a colour-blind reader and a
   printed page both get the word. */
const RESULT_INK: Record<string, string> = {
  ok: "var(--color-accent)",
  failed: "var(--color-alarm)",
  "not-checked": "var(--color-muted)",
};

export function CheckSteps({
  steps,
  summary = "See what was checked",
}: {
  steps: EndpointStep[];
  summary?: string;
}) {
  if (steps.length === 0) return null;
  return (
    <details className="mt-1">
      <summary className="cursor-pointer text-[color:var(--color-muted)]">{summary}</summary>
      <div className="table-wrap mt-1">
        <table>
          <thead>
            <tr>
              <th scope="col">Result</th>
              <th scope="col">What was checked</th>
            </tr>
          </thead>
          <tbody>
            {steps.map((step) => (
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
    </details>
  );
}
