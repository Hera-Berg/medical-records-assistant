"""Photograph the interface in the states nobody can reach by clicking around.

``tools/render.mjs`` prints what a screen *says*; this shows what it *looks
like*, and for the recorder that is most of the point. Half of the recorder's
states are ones a developer on a working laptop with a working microphone never
sees — permission refused, no input device at all, a page opened at a LAN
address where ``getUserMedia`` silently does nothing — and those are exactly the
states where a person is stuck and reading the screen carefully.

Each state is produced by a real browser against a real server. Nothing here
stubs the app: the "recording" shot is a browser recording a synthesised voice
note through Chromium's fake audio device, and the "done" shot is the transcript
that came back from ``faster-whisper`` reading the file that browser uploaded.
A screenshot of a mocked state is a picture of the mock.

    health-agent serve --vault /path/to/vault &
    python frontend/tools/shots.py --out /tmp/shots

Needs ``playwright`` and its chromium (``python -m playwright install
chromium``). Neither is a dependency of the application: this is a developer
tool and the recording it takes pictures of runs in the user's own browser.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ORIGIN = "http://127.0.0.1:7777"

#: A page 1100 wide is the rail plus a comfortable column, which is what the
#: interface is designed around. Not a phone: the phone layout has its own
#: shots at the end.
DESKTOP = {"width": 1100, "height": 900}
PHONE = {"width": 390, "height": 844}

#: What the page says once a transcript has landed and before the box has read
#: it for medications. That sentence is shown in every finished state of the
#: recorder, including a recording that turned out to hold no speech.
TYPED_UP = "Typed up \u2014 waiting to be read for medications."

#: Chromium's fake microphone plays a file instead of capturing silence, which
#: is what makes a real transcript come back at the end of a real recording.
FAKE_AUDIO = [
    "--use-fake-device-for-media-stream",
    "--use-fake-ui-for-media-stream",
]


def synthesise(path: Path) -> Path:
    """A WAV for Chromium's fake microphone to play into the page.

    espeak-ng and ffmpeg, the same pair the corpus fixtures use. The words are
    the corpus's rambling voice note: two drug names, a practitioner and a
    deliberately vague date, so the shot shows a transcript worth reading rather
    than a lorem ipsum.
    """
    from tests.fixtures.corpus import RAMBLING_NOTE

    raw = path / "spoken.wav"
    subprocess.run(
        ["espeak-ng", "-v", "en-gb", "-s", "145", "-w", str(raw), RAMBLING_NOTE],
        check=True,
        capture_output=True,
    )
    # Chromium wants 16-bit PCM at a rate it can resample from.
    fixed = path / "fake-mic.wav"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(raw), "-ac", "1", "-ar", "48000",
         "-c:a", "pcm_s16le", str(fixed), "-y"],
        check=True,
        capture_output=True,
    )
    return fixed


def shoot(page, out: Path, name: str) -> None:
    page.wait_for_timeout(400)
    target = out / f"{name}.png"
    page.screenshot(path=str(target), full_page=True)
    print(f"  {target}")


def review_shots(play, origin: str, out: Path) -> None:
    """The review inbox, in every state it can hold.

    Each one is produced against a real server with a real vault rather than
    posed: the demo stream seeds one item of every review kind, and the states
    that need a decision to exist — an emptied queue, a decision that arrived
    too late — are reached by actually making the decision in the browser.

    The awkward shapes are the point. A conflict, a stop below prescriber tier,
    a withdrawal that must name its artefact and not its content: those are
    where a queue that reads well on the happy path stops reading well.
    """
    browser = play.chromium.launch()
    context = browser.new_context(viewport=DESKTOP)
    page = context.new_page()

    page.goto(f"{origin}/review")
    page.wait_for_selector("text=Important")
    shoot(page, out, "20-inbox-full")

    # One item, in close-up. The high group's first row is the ordinary case:
    # a high-consequence claim with confirm, correct and reject.
    page.get_by_role("button", name="Correct").first.click()
    page.wait_for_selector("text=It should say")
    shoot(page, out, "21-correcting-inline")
    page.get_by_role("button", name="Cancel").first.click()

    # A stop proposal: its own action, its own words, and no keystroke.
    stop = page.locator("article", has_text="Confirm the stop").first
    stop.scroll_into_view_if_needed()
    shoot(page, out, "22-stop-proposal")

    # A conflict: both readings, neither chosen, and no confirm offered.
    conflict = page.locator("article", has_text="disagree").first
    conflict.scroll_into_view_if_needed()
    shoot(page, out, "23-conflict")

    # A withdrawal: names its artefact, never its content.
    withdrawn = page.locator("article", has_text="later rejected").first
    withdrawn.scroll_into_view_if_needed()
    shoot(page, out, "24-withdrawal")

    # A dateable item: the phrase, and the candidate offered as a button.
    dateable = page.locator("article", has_text="dates it only as").first
    dateable.scroll_into_view_if_needed()
    shoot(page, out, "25-dateable")

    # --- a decision that arrived too late -------------------------------
    #
    # Two tabs, which is the ordinary way this happens: one confirms, the other
    # is still holding the list from before. Not simulated — the second tab
    # really does post an id that has already been decided.
    second = context.new_page()
    second.goto(f"{origin}/review")
    second.wait_for_selector("text=Important")

    page.get_by_role("button", name="Confirm", exact=True).first.click()
    page.wait_for_timeout(600)
    shoot(page, out, "26-confirmed")

    second.get_by_role("button", name="Confirm", exact=True).first.click()
    second.wait_for_selector("text=already confirmed")
    shoot(second, out, "27-already-decided-elsewhere")
    second.close()

    # --- an emptied queue ----------------------------------------------
    #
    # Reached by deciding everything, so the empty state is one a person can
    # actually arrive at rather than one only a fresh vault ever sees.
    #
    # Every kind is decided by its own action, which is the point: there is no
    # single button that empties this queue, and a script that pretended
    # otherwise would be photographing a screen the app does not have.
    deadline = time.time() + 90
    order = (
        "This one is wrong",       # a conflict, per reading
        "Yes, I meant to reject it",  # a withdrawal
        "Confirm the stop",           # a stop, by name
        "Reject",                     # anything ordinary
    )
    while time.time() < deadline:
        page.reload()
        page.wait_for_timeout(300)
        clicked = False
        for name in order:
            button = page.get_by_role("button", name=name)
            if button.count():
                button.first.click()
                page.wait_for_timeout(500)
                clicked = True
                break
        if not clicked:
            break
    page.reload()
    page.wait_for_timeout(500)
    shoot(page, out, "28-inbox-empty")

    small = context.new_page()
    small.set_viewport_size(PHONE)
    small.goto(f"{origin}/review")
    small.wait_for_timeout(500)
    shoot(small, out, "29-inbox-phone")
    small.close()

    context.close()
    browser.close()


def main() -> int:
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="shots", help="where the PNGs go")
    parser.add_argument("--origin", default=ORIGIN)
    parser.add_argument(
        "--no-audio",
        action="store_true",
        help="skip the states that need espeak-ng and ffmpeg",
    )
    parser.add_argument(
        "--review-only",
        action="store_true",
        help=(
            "photograph only the review inbox. Point this at a server holding a "
            "demo vault: the demo stream seeds one item of every review kind, "
            "and this makes real decisions against it, so run it on a vault you "
            "are willing to have changed"
        ),
    )
    parser.add_argument(
        "--waiting-only",
        action="store_true",
        help=(
            "record one note and photograph the waiting state, then stop. Point "
            "this at a server started with --no-worker: transcription of a short "
            "note finishes in about a second, so on a working server the waiting "
            "state is real but too brief to photograph. A server that is not "
            "draining holds the same state indefinitely — it is what a full queue "
            "looks like, not a posed one"
        ),
    )
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="health-agent-shots-"))

    with sync_playwright() as play:
        if args.review_only:
            review_shots(play, args.origin, out)
            print(f"\n{len(list(out.glob('*.png')))} screenshots in {out}")
            return 0

        if args.waiting_only:
            spoken = synthesise(work)
            live = play.chromium.launch(
                args=[*FAKE_AUDIO, f"--use-file-for-fake-audio-capture={spoken}"]
            )
            context = live.new_context(viewport=DESKTOP)
            context.grant_permissions(["microphone"])
            page = context.new_page()
            page.goto(f"{args.origin}/add")
            page.get_by_role("button", name="Turn on the microphone").click()
            page.wait_for_selector("text=Start recording")
            page.get_by_role("button", name="Start recording").click()
            page.wait_for_timeout(3000)
            page.get_by_role("button", name="Stop").click()
            page.wait_for_selector("text=Being written down", timeout=30000)
            shoot(page, out, "06-being-written-down")
            context.close()
            live.close()
            return 0

        # --- states with no microphone at all ------------------------------
        #
        # A plain headless chromium has no audio input, which is the real
        # no-device case rather than a simulated one.
        bare = play.chromium.launch()
        context = bare.new_context(viewport=DESKTOP)
        page = context.new_page()

        page.goto(f"{args.origin}/add")
        page.wait_for_selector("text=Or say it out loud")
        shoot(page, out, "01-idle")

        page.get_by_role("button", name="Turn on the microphone").click()
        page.wait_for_timeout(800)
        shoot(page, out, "02-no-device")

        page.goto(f"{args.origin}/settings")
        page.wait_for_selector("text=Where your folder is")
        shoot(page, out, "07-settings")

        # `click`, not `check`: the radio moves to the pending choice and the
        # change is not saved until the warning below is answered.
        page.get_by_role("radio", name="Synced by Dropbox").click()
        page.wait_for_selector("text=Before you change this")
        shoot(page, out, "08-settings-warning")

        context.close()
        bare.close()

        # --- permission refused --------------------------------------------
        #
        # A context that is *not* granted the microphone. getUserMedia rejects
        # with NotAllowedError, which is what a person who pressed "Block" sees.
        denied_browser = play.chromium.launch(
            args=["--use-fake-device-for-media-stream"]
        )
        denied = denied_browser.new_context(viewport=DESKTOP)
        denied.clear_permissions()
        page = denied.new_page()
        page.goto(f"{args.origin}/add")
        page.get_by_role("button", name="Turn on the microphone").click()
        page.wait_for_timeout(900)
        shoot(page, out, "03-permission-denied")
        denied.close()
        denied_browser.close()

        # --- the working path ----------------------------------------------
        if not args.no_audio:
            spoken = synthesise(work)
            live = play.chromium.launch(
                args=[*FAKE_AUDIO, f"--use-file-for-fake-audio-capture={spoken}"]
            )
            context = live.new_context(viewport=DESKTOP)
            context.grant_permissions(["microphone"])
            page = context.new_page()

            page.goto(f"{args.origin}/add")
            page.get_by_role("button", name="Turn on the microphone").click()
            page.wait_for_selector("text=Start recording")
            shoot(page, out, "04-ready")

            page.get_by_role("button", name="Start recording").click()
            page.wait_for_selector("text=Stop")
            # Long enough for the meter to be lit and the clock to have moved:
            # a shot at zero seconds is a picture of a button, not of recording.
            page.wait_for_timeout(4000)
            shoot(page, out, "05-recording")

            page.get_by_role("button", name="Stop").click()

            # The waiting state is real but short: Whisper small on a few
            # seconds of speech finishes in about a second on this machine, so
            # it is caught by watching for it rather than by sleeping and hoping.
            # If it is missed, that is said out loud instead of a shot being
            # posed — a screenshot of a state the app did not actually hold is
            # worse than no screenshot of it.
            caught = False
            deadline = time.time() + 20
            while time.time() < deadline:
                if page.get_by_text("Being written down").count():
                    shoot(page, out, "06-being-written-down")
                    caught = True
                    break
                if page.get_by_text(TYPED_UP).count():
                    break
                page.wait_for_timeout(80)
            if not caught:
                print("  (the waiting state passed too quickly to photograph)")

            page.wait_for_selector(f"text={TYPED_UP}", timeout=180000)
            shoot(page, out, "06b-typed-up")

            page.goto(f"{args.origin}/")
            page.wait_for_selector("text=Timeline")
            shoot(page, out, "09-timeline")

            phone = live.new_context(viewport=PHONE)
            phone.grant_permissions(["microphone"])
            small = phone.new_page()
            small.goto(f"{args.origin}/add")
            small.wait_for_selector("text=Or say it out loud")
            shoot(small, out, "10-phone")
            phone.close()

            context.close()
            live.close()

    print(f"\n{len(list(out.glob('*.png')))} screenshots in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
