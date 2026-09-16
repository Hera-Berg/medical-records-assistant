/**
 * Speaking into the record.
 *
 * This sits on "Add something", beside drag-and-drop, because it is the same
 * action arriving through a different input. **It is deliberately not a tab of
 * its own.** A voice tab would imply a conversation — something that answers —
 * and this is dictation into a record that reports and cites and never replies.
 *
 * One large button, and nothing else to decide. No category, no title, no tags:
 * what kind of thing this is gets worked out afterwards, and asking someone to
 * classify their own symptoms in a corridor is asking the wrong person at the
 * worst moment.
 *
 * Three things here are not obvious and each has cost somebody a day:
 *
 * **Device labels are empty until permission is granted.** `enumerateDevices`
 * returns entries with blank labels before `getUserMedia` has ever succeeded —
 * so the picker shows a list of nothing, which looks exactly like a bug and is
 * not one. Permission is requested first, then the devices are enumerated
 * again.
 *
 * **`getUserMedia` needs a secure context.** `127.0.0.1` and `localhost`
 * qualify; `192.168.1.x` does not. Opening this on a phone over the LAN gives a
 * button that silently does nothing, so the button is replaced by an
 * explanation naming the address that would work.
 *
 * **The recording is written to IndexedDB before it is uploaded.** See
 * `outbox.ts`: a recording exists nowhere else, and a reload mid-upload must
 * not be able to lose it.
 *
 * `captured_ts` is sent from here and from nowhere else in the application.
 * This is the one path that genuinely knows when the bytes were made, because
 * this page watched it happen.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { Outgoing } from "../outbox";

/** Where the last-used microphone is remembered between visits. */
const DEVICE_KEY = "health-agent.microphone";

/** How often the level meter samples. Not an animation — a state readout. */
const METER_MS = 100;

type Phase =
  | "idle"
  | "asking"
  | "ready"
  | "recording"
  | "denied"
  | "no-device"
  | "unsupported"
  | "insecure";

export interface RecorderState {
  phase: Phase;
  devices: MediaDeviceInfo[];
  deviceId: string | null;
  elapsed: number;
  level: number;
  error: string | null;
  fellBack: string | null;
}

/** Chrome produces webm/opus, Safari mp4. Whatever it makes is what is stored. */
function preferredType(): string | undefined {
  const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];
  for (const type of candidates) {
    if (typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported(type)) {
      return type;
    }
  }
  return undefined;
}

function extensionFor(type: string): string {
  if (type.includes("mp4")) return "m4a";
  if (type.includes("ogg")) return "ogg";
  return "webm";
}

/**
 * Whether this page may open a microphone at all.
 *
 * Both halves are checked. `isSecureContext` is the browser's own answer and is
 * the one that decides; the hostname is what lets the explanation say *why*,
 * and say what to open instead.
 */
export function secureEnough(): { ok: boolean; host: string } {
  const host = window.location.hostname;
  const loopback = host === "127.0.0.1" || host === "localhost" || host === "[::1]";
  return { ok: (window.isSecureContext ?? false) || loopback, host };
}

/** Canonical UTC, to the second. The format the event log accepts. */
function canonicalNow(): string {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

export function Recorder({
  onRecorded,
  pending,
}: {
  onRecorded: (blob: Blob, filename: string, capturedTs: string) => void;
  pending: Outgoing[];
}) {
  const [state, setState] = useState<RecorderState>(() => {
    const secure = secureEnough();
    return {
      phase: secure.ok ? "idle" : "insecure",
      devices: [],
      deviceId: null,
      elapsed: 0,
      level: 0,
      error: null,
      fellBack: null,
    };
  });

  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const audio = useRef<AudioContext | null>(null);
  const analyser = useRef<AnalyserNode | null>(null);
  const started = useRef<string | null>(null);
  const timers = useRef<number[]>([]);

  const stopEverything = useCallback(() => {
    for (const timer of timers.current) window.clearInterval(timer);
    timers.current = [];
    stream.current?.getTracks().forEach((track) => track.stop());
    stream.current = null;
    audio.current?.close().catch(() => undefined);
    audio.current = null;
    analyser.current = null;
  }, []);

  /**
   * List the microphones.
   *
   * Called again after permission is granted, which is the whole point: before
   * that, every `label` is an empty string and the picker is a list of blanks.
   */
  const enumerate = useCallback(async () => {
    let devices: MediaDeviceInfo[] = [];
    try {
      devices = (await navigator.mediaDevices.enumerateDevices()).filter(
        (device) => device.kind === "audioinput",
      );
    } catch {
      devices = [];
    }
    setState((current) => {
      if (devices.length === 0 && current.phase === "ready") {
        return { ...current, devices, phase: "no-device" };
      }
      const remembered = current.deviceId ?? window.localStorage.getItem(DEVICE_KEY);
      const stillThere = devices.some((device) => device.deviceId === remembered);
      return {
        ...current,
        devices,
        deviceId: stillThere ? remembered : devices[0]?.deviceId ?? null,
        // The headset was unplugged. Said out loud, because falling back to the
        // laptop microphone silently is how someone records a consultation from
        // across the room and finds out afterwards.
        fellBack:
          remembered && !stillThere && devices.length > 0
            ? "The microphone you used last time is not plugged in. Using the default one."
            : null,
      };
    });
  }, []);

  useEffect(() => {
    if (state.phase === "insecure") return;
    if (!navigator.mediaDevices?.enumerateDevices || typeof MediaRecorder === "undefined") {
      // Not the same as having no microphone, and it must not say so: nothing
      // the user plugs in will help, and "no microphone found" would have them
      // looking for a cable.
      setState((current) => ({ ...current, phase: "unsupported" }));
      return;
    }
    enumerate();
    const onChange = () => enumerate();
    navigator.mediaDevices.addEventListener("devicechange", onChange);
    return () => navigator.mediaDevices.removeEventListener("devicechange", onChange);
  }, [enumerate, state.phase]);

  useEffect(() => stopEverything, [stopEverything]);

  const permit = useCallback(async () => {
    setState((current) => ({ ...current, phase: "asking", error: null }));
    try {
      const granted = await navigator.mediaDevices.getUserMedia({ audio: true });
      granted.getTracks().forEach((track) => track.stop());
      setState((current) => ({ ...current, phase: "ready" }));
      await enumerate();
    } catch (exc) {
      const name = (exc as DOMException).name;
      setState((current) => ({
        ...current,
        phase: name === "NotFoundError" ? "no-device" : "denied",
        error: null,
      }));
    }
  }, [enumerate]);

  const start = useCallback(async () => {
    let live: MediaStream;
    try {
      live = await navigator.mediaDevices.getUserMedia({
        audio: state.deviceId ? { deviceId: { exact: state.deviceId } } : true,
      });
    } catch (exc) {
      const name = (exc as DOMException).name;
      if (name === "NotAllowedError") {
        setState((current) => ({ ...current, phase: "denied" }));
        return;
      }
      // The remembered device has gone since it was listed. Fall back to the
      // default rather than refusing to record.
      try {
        live = await navigator.mediaDevices.getUserMedia({ audio: true });
        setState((current) => ({
          ...current,
          deviceId: null,
          fellBack: "That microphone is no longer available. Using the default one.",
        }));
      } catch {
        setState((current) => ({
          ...current,
          phase: "no-device",
          error: "No microphone would open.",
        }));
        return;
      }
    }

    const type = preferredType();
    let recording: MediaRecorder;
    try {
      recording = new MediaRecorder(live, type ? { mimeType: type } : undefined);
    } catch (exc) {
      live.getTracks().forEach((track) => track.stop());
      setState((current) => ({
        ...current,
        error: `This browser could not start recording: ${(exc as Error).message}`,
      }));
      return;
    }

    const chunks: BlobPart[] = [];
    recording.ondataavailable = (event) => {
      if (event.data.size > 0) chunks.push(event.data);
    };
    recording.onstop = () => {
      const kind = recording.mimeType || type || "audio/webm";
      const blob = new Blob(chunks, { type: kind });
      const when = started.current ?? canonicalNow();
      stopEverything();
      setState((current) => ({ ...current, phase: "ready", elapsed: 0, level: 0 }));
      if (blob.size > 0) {
        onRecorded(
          blob,
          `voice-note-${when.replace(/[-:]/g, "")}.${extensionFor(kind)}`,
          when,
        );
      }
    };

    // A live level meter, so the meter answers "is this thing on" without the
    // user having to record something and play it back to find out.
    try {
      const context = new AudioContext();
      const node = context.createAnalyser();
      node.fftSize = 1024;
      context.createMediaStreamSource(live).connect(node);
      audio.current = context;
      analyser.current = node;
    } catch {
      /* No meter. Recording still works, which is what matters. */
    }

    stream.current = live;
    recorder.current = recording;
    started.current = canonicalNow();
    recording.start();
    setState((current) => ({ ...current, phase: "recording", elapsed: 0, error: null }));

    const begin = Date.now();
    timers.current.push(
      window.setInterval(() => {
        setState((current) => ({ ...current, elapsed: (Date.now() - begin) / 1000 }));
      }, 200),
    );
    timers.current.push(
      window.setInterval(() => {
        const node = analyser.current;
        if (!node) return;
        const samples = new Uint8Array(node.frequencyBinCount);
        node.getByteTimeDomainData(samples);
        let peak = 0;
        for (const sample of samples) peak = Math.max(peak, Math.abs(sample - 128) / 128);
        setState((current) => ({ ...current, level: peak }));
      }, METER_MS),
    );
  }, [onRecorded, state.deviceId, stopEverything]);

  const stop = useCallback(() => {
    recorder.current?.state === "recording" && recorder.current.stop();
  }, []);

  const toggle = useCallback(() => {
    if (state.phase === "recording") stop();
    else if (state.phase === "ready") start();
    else if (state.phase === "idle") permit();
  }, [permit, start, state.phase, stop]);

  // Space bar toggles — but not while someone is typing a note in the field
  // below, where a space is a space.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.code !== "Space") return;
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(target.tagName)) {
        return;
      }
      if (target?.isContentEditable) return;
      event.preventDefault();
      toggle();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toggle]);

  const queued = pending.filter((item) => item.source === "recorder");

  return (
    <section className="no-print" aria-label="Record a voice note">
      <h2 className="text-lg font-semibold">Or say it out loud</h2>
      <p className="mt-1">
        Talk the way you would to a person. Dates can be vague — “around Easter” is
        kept as you said it. The recording is yours and is kept for good; what it
        says is written down beside it.
      </p>

      {state.phase === "insecure" ? <Insecure /> : null}
      {state.phase === "denied" ? <Denied onRetry={permit} /> : null}
      {state.phase === "no-device" ? <NoDevice onRetry={enumerate} /> : null}
      {state.phase === "unsupported" ? <Unsupported /> : null}

      {state.phase === "idle" || state.phase === "asking" ? (
        <div className="mt-4">
          <button
            type="button"
            onClick={permit}
            disabled={state.phase === "asking"}
            className="btn btn-primary"
          >
            {state.phase === "asking" ? "Waiting for you…" : "Turn on the microphone"}
          </button>
          <p className="mt-2 text-[color:var(--color-muted)]">
            {state.phase === "asking"
              ? "Your browser is asking whether this page may use your microphone. It is asking about this computer only — nothing is sent anywhere."
              : "Your browser will ask first. Until you say yes it cannot even tell this page what microphones you have — that is a rule of the browser, not something that has gone wrong."}
          </p>
        </div>
      ) : null}

      {state.phase === "ready" || state.phase === "recording" ? (
        <>
          <div className="mt-4 flex flex-wrap items-center gap-4">
            <button
              type="button"
              onClick={toggle}
              className={
                state.phase === "recording"
                  ? "rounded-xl border border-[color:var(--color-alarm)] bg-[color:var(--color-alarm)] px-8 py-4 text-lg font-semibold text-[color:var(--color-paper)]"
                  : "rounded-xl border border-[color:var(--color-accent)] bg-[color:var(--color-accent)] px-8 py-4 text-lg font-semibold text-[color:var(--color-paper)]"
              }
            >
              {state.phase === "recording" ? "Stop" : "Start recording"}
            </button>

            <span>
              <span className="block font-mono text-lg">{clock(state.elapsed)}</span>
              <Meter level={state.level} recording={state.phase === "recording"} />
            </span>

            <span className="text-[color:var(--color-muted)]">
              {state.phase === "recording"
                ? "Recording. Press the space bar or Stop when you are done."
                : "Press the space bar to start."}
            </span>
          </div>

          <Devices
            devices={state.devices}
            deviceId={state.deviceId}
            disabled={state.phase === "recording"}
            onPick={(id) => {
              window.localStorage.setItem(DEVICE_KEY, id);
              setState((current) => ({ ...current, deviceId: id, fellBack: null }));
            }}
          />
        </>
      ) : null}

      {state.fellBack ? (
        <p className="mt-2 text-[color:var(--color-warn)]">{state.fellBack}</p>
      ) : null}
      {state.error ? (
        <p className="mt-2 text-[color:var(--color-alarm)]">{state.error}</p>
      ) : null}

      {queued.length > 0 ? (
        <p className="mt-3 text-[color:var(--color-muted)]">
          {queued.length === 1
            ? "1 recording is still being sent to your own computer."
            : `${queued.length} recordings are still being sent to your own computer.`}{" "}
          They are saved in this browser until they arrive, so you can close this page.
        </p>
      ) : null}
    </section>
  );
}

/**
 * The level meter.
 *
 * Twelve blocks, not a smooth bar: it is a readout to be glanced at, and the
 * page has no animation anywhere. A silent input shows one dim block, which is
 * distinguishable at a glance from a meter that is not running at all.
 */
function Meter({ level, recording }: { level: number; recording: boolean }) {
  const blocks = 12;
  const lit = recording ? Math.max(1, Math.round(level * blocks)) : 0;
  return (
    <span
      className="mt-1 flex gap-0.5"
      role="meter"
      aria-label="Microphone level"
      aria-valuenow={Math.round(level * 100)}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      {Array.from({ length: blocks }, (_, index) => (
        <span
          key={index}
          className="h-3 w-2 rounded-[1px]"
          style={{
            background:
              index < lit ? "var(--color-accent)" : "var(--color-rule)",
          }}
        />
      ))}
    </span>
  );
}

function Devices({
  devices,
  deviceId,
  disabled,
  onPick,
}: {
  devices: MediaDeviceInfo[];
  deviceId: string | null;
  disabled: boolean;
  onPick: (id: string) => void;
}) {
  if (devices.length <= 1) return null;
  return (
    <p className="mt-3">
      <label htmlFor="microphone" className="text-[color:var(--color-muted)]">
        Microphone{" "}
      </label>
      <select
        id="microphone"
        className="field"
        value={deviceId ?? ""}
        disabled={disabled}
        onChange={(event) => onPick(event.target.value)}
      >
        {devices.map((device, index) => (
          <option key={device.deviceId} value={device.deviceId}>
            {device.label || `Microphone ${index + 1}`}
          </option>
        ))}
      </select>
      {disabled ? (
        <span className="text-[color:var(--color-muted)]">
          {" "}
          — stop recording to change it
        </span>
      ) : null}
    </p>
  );
}

function Insecure() {
  const { host } = secureEnough();
  return (
    <div className="mt-4 rounded-lg border border-[color:var(--color-warn)] bg-[color:var(--color-warn-soft)] p-3">
      <p className="font-semibold">Recording will not work at this address.</p>
      <p className="mt-1">
        You have opened this page at <code className="font-mono">{host}</code>. Browsers
        only allow a page to use the microphone over a secure connection, and an
        address on your home network is not one — so the button would be there and
        would quietly do nothing.
      </p>
      <p className="mt-1">
        Open{" "}
        <code className="font-mono">
          http://127.0.0.1:{window.location.port || "7777"}
        </code>{" "}
        on the computer that holds your record. Everything else on this page works
        from here, including adding a file.
      </p>
    </div>
  );
}

function Denied({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="mt-4 rounded-lg border border-[color:var(--color-warn)] bg-[color:var(--color-warn-soft)] p-3">
      <p className="font-semibold">Your browser is not letting this page use the microphone.</p>
      <p className="mt-1">
        Nothing is wrong with your record. Allow the microphone for this page in your
        browser’s settings — usually the small icon at the left of the address bar —
        and then try again.
      </p>
      <button type="button" onClick={onRetry} className="btn mt-2">
        Try again
      </button>
    </div>
  );
}

function NoDevice({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="mt-4 rounded-lg border border-[color:var(--color-rule-strong)] bg-[color:var(--color-shade)] p-3">
      <p className="font-semibold">No microphone found.</p>
      <p className="mt-1">
        Plug one in, or check that the one you have is switched on. The list updates by
        itself when something is connected.
      </p>
      <button type="button" onClick={onRetry} className="btn mt-2">
        Look again
      </button>
    </div>
  );
}

function Unsupported() {
  return (
    <div className="mt-4 rounded-lg border border-[color:var(--color-rule-strong)] bg-[color:var(--color-shade)] p-3">
      <p className="font-semibold">This browser cannot record audio.</p>
      <p className="mt-1">
        Nothing you plug in will change that — it is the browser, not your equipment.
        Record on your phone or with any other app and add the file above; it is kept
        and written down exactly the same way.
      </p>
    </div>
  );
}

function clock(seconds: number): string {
  const whole = Math.floor(seconds);
  const minutes = Math.floor(whole / 60);
  return `${String(minutes).padStart(2, "0")}:${String(whole % 60).padStart(2, "0")}`;
}
