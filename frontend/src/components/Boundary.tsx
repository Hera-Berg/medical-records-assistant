/**
 * One screen failing must not take the whole app with it.
 *
 * React unmounts the entire tree when a render throws, which turns a mistake in
 * one screen into a blank white page — no rail, no heading, nothing to click,
 * and no clue what happened. For a record whose whole promise is that the data
 * is safe in a folder, a blank page is the worst available way to say "one
 * component read a field that wasn't there".
 *
 * The specific way this happens here is worth naming, because it is not a rare
 * bug: **the built bundle and the running server can be different versions.**
 * The bundle is committed and served from disk, so a server process that has
 * been running since before an update happily serves the *new* interface while
 * answering with the *old* API shape. The new screen reads a field the old
 * answer does not have and throws. `/api/build` exists precisely because that
 * drift is expected; this is what makes it survivable rather than merely
 * detectable.
 *
 * So the boundary says what probably happened and what to do, and everything
 * around it keeps working — the rail still navigates, and the other screens,
 * which do not touch the changed shape, still read the record.
 */

import { Component } from "react";
import { StartItAgain } from "../installation";

interface Props {
  /** Changing this resets the boundary — navigating away from a broken screen. */
  resetKey: string;
  children: React.ReactNode;
}

interface State {
  error: Error | null;
}

export class Boundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidUpdate(previous: Props) {
    if (previous.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null });
    }
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      <div>
        <h2 className="text-lg font-semibold" style={{ color: "var(--color-alarm)" }}>
          This screen could not be drawn.
        </h2>
        <p className="mt-1">
          Nothing is wrong with your record — everything in it is a file in your folder,
          and nothing here writes to it. This is the page failing to display, not the
          record failing.
        </p>
        <p className="mt-2">
          The usual cause is that the app was updated while the server was still running,
          so the screen you are looking at is newer than the program answering it.{" "}
          <strong>
            <StartItAgain then="then reload this page" />
          </strong>
        </p>
        <p className="mt-2 text-[color:var(--color-muted)]">
          The other screens still work — the rail on the left will take you to them.
        </p>
        <details className="mt-2">
          <summary className="cursor-pointer text-[color:var(--color-muted)]">
            What went wrong, in the program's own words
          </summary>
          <pre className="mt-1 overflow-x-auto text-[color:var(--color-muted)]">
            {error.message}
          </pre>
        </details>
      </div>
    );
  }
}
