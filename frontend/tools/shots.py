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

``--endpoint-only`` is the exception to "point this at a running server". The
inference endpoint's states need a machine on the other end behaving in a
specific way — refusing the key, throwing pictures away — plus a vault with no
endpoint to start from and a credential that is emphatically not the
developer's. So that mode builds the whole arrangement itself, in a temporary
directory, and tears it down afterwards:

    python frontend/tools/shots.py --endpoint-only --out /tmp/shots

It starts its own ``health-agent serve`` on a throwaway demo vault, with
``HEALTH_AGENT_CONFIG_HOME`` pointed at a temporary directory so that **nothing
touches the real keychain, the real device identity or a real vault**. The far
end is ``fake_box.py``, a real HTTP server on loopback that misbehaves to order.
Everything between the browser and that box — the address guard, the credential
resolution, the probe, the classification of a 401 as terminal — is the
application, unmodified.
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


# --- the inference endpoint -------------------------------------------------
#
# The one section that builds its own world. Everything it needs is hostile to
# borrowing: it has to start from a vault with no endpoint, it has to have a
# credential that is not the developer's, and the machine on the far end has to
# be persuadable into failing in five specific ways.

BOX_PORT = 7999
APP_PORT = 7789
SHOT_KEY = "sk-fake-box-key-for-screenshots-0001"


def endpoint_world(work: Path):
    """A demo vault, an isolated config home, a server and a fake box.

    ``HEALTH_AGENT_CONFIG_HOME`` is the load-bearing line. Without it this
    script would resolve the developer's own device identity and, worse, could
    read or write their real inference key — taking a photograph is not a reason
    to go near either. Pointed at a temporary directory, the credential the app
    finds is the throwaway one written below and nothing else exists to find.
    """
    import os

    root = Path(__file__).resolve().parents[2]
    vault = work / "vault"
    home = work / "config-home"
    home.mkdir(parents=True, exist_ok=True)

    environment = {
        **os.environ,
        "HEALTH_AGENT_CONFIG_HOME": str(home),
        "HEALTH_DEVICE": "shots-device",
        # Nothing may inherit a real key and quietly make a failing state pass.
        "HEALTH_VLM_TOKEN": "",
    }
    environment.pop("HEALTH_VLM_TOKEN")

    subprocess.run(
        [sys.executable, "-m", "agent.cli", "demo", str(vault)],
        check=True, capture_output=True, cwd=root, env=environment,
    )

    server = subprocess.Popen(
        [sys.executable, "-m", "agent.cli", "serve", "--vault", str(vault),
         "--port", str(APP_PORT), "--no-worker"],
        cwd=root, env=environment,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import fake_box

    globals()["fake_box"] = fake_box
    box_server, box = fake_box.serve(BOX_PORT, "working")
    return vault, home, server, box_server, box, f"http://127.0.0.1:{APP_PORT}"


def wait_for(origin: str, seconds: float = 20.0) -> None:
    from urllib.error import URLError
    from urllib.request import urlopen

    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urlopen(f"{origin}/api/health", timeout=1):
                return
        except (URLError, OSError):
            time.sleep(0.2)
    raise SystemExit(f"the server never came up on {origin}")


def write_key(home: Path) -> None:
    """A credential the app will find, outside any vault and mode 0600.

    Deliberately *not* through the settings screen's own field, which writes to
    the OS keychain. Photographing a state is not worth overwriting the key a
    developer actually uses, and the field's write path is covered by tests
    instead. What the screen then shows — "found in the credentials file" — is a
    real state of a real resolution order, not a posed one.
    """
    path = home / "credentials"
    path.write_text(SHOT_KEY + "\n", encoding="utf-8")
    path.chmod(0o600)


def set_mode(mode: str | None = None, models: list[str] | None = None) -> None:
    import json as _json
    from urllib.request import Request, urlopen

    payload: dict = {}
    if mode is not None:
        payload["mode"] = mode
    if models is not None:
        payload["models"] = models
    request = Request(
        f"http://127.0.0.1:{BOX_PORT}/v1/mode",
        data=_json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        response.read()


def press_connect(page) -> None:
    """One button, which checks and then saves. Waits for the line it produces."""
    page.get_by_role("button", name="Connect", exact=True).click()
    page.wait_for_selector(
        "text=/Connected\\.|Not connected\\.|Almost\\./", timeout=30000
    )
    page.wait_for_timeout(300)


def endpoint_shots(play, out: Path, work: Path) -> None:
    """Every state the endpoint section can hold, each one genuinely reached."""
    vault, home, server, box_server, box, origin = endpoint_world(work)
    browser = None
    url = f"http://127.0.0.1:{BOX_PORT}/v1"
    try:
        wait_for(origin)
        browser = play.chromium.launch()
        context = browser.new_context(viewport=DESKTOP)
        page = context.new_page()

        # 1. Nothing set up. A fresh demo vault carries no endpoint, and no key
        #    exists anywhere this server can see. Three fields and one button.
        page.goto(f"{origin}/settings")
        page.wait_for_selector("text=Not set up yet")
        shoot(page, out, "30-endpoint-unconfigured")

        # 2. An address typed, a password stored, and the model field still
        #    empty — because until something has reached the box there is
        #    nothing true to put in it.
        write_key(home)
        page.reload()
        page.wait_for_selector("text=The computer that reads your documents")
        page.get_by_role("textbox", name="Address").fill(url)
        shoot(page, out, "31-endpoint-before-connecting")

        # 3. A box that runs more than one model: a question, not a failure, and
        #    one that could not have been asked before it was reached.
        set_mode(models=[fake_box.MODEL, "Qwen3.5-4B", "whisper-large-v3"])
        press_connect(page)
        shoot(page, out, "32-endpoint-choose-model")

        # 4. Connected. One line, and the model that is doing the reading.
        page.locator("select").select_option(fake_box.MODEL)
        press_connect(page)
        shoot(page, out, "33-endpoint-connected")

        # 5. The same state with the steps opened, which is where the vision
        #    check becomes visible. It is folded by default and it matters.
        page.get_by_text("See what was checked").click()
        page.wait_for_timeout(200)
        shoot(page, out, "34-endpoint-connected-steps")

        # 6. Each failure, produced by a box that genuinely behaves that way.
        #    One sentence each; the detail is the paragraph under it.
        set_mode(models=[fake_box.MODEL])
        for mode, name in (
            ("unauthorised", "35-endpoint-unauthorised"),
            ("blind", "36-endpoint-vision-blind"),
            ("unconstrained", "37-endpoint-grammar"),
            ("mismatched", "38-endpoint-model-mismatch"),
        ):
            set_mode(mode)
            page.reload()
            page.wait_for_selector("text=The computer that reads your documents")
            press_connect(page)
            shoot(page, out, name)
        set_mode("working")

        # 7. Unreachable, by actually stopping the machine. A refused socket is
        #    not the same as a rejected key and the screen has to say so.
        #
        #    `server_close` as well as `shutdown`: the first stops the serving
        #    loop and leaves the listening socket open, so connections are
        #    accepted by the kernel and then hang — which is a *timeout*, not a
        #    refusal, and waits out the client's three-minute read timeout. A
        #    closed socket is what a sleeping machine actually looks like.
        box_server.shutdown()
        box_server.server_close()
        page.reload()
        page.wait_for_selector("text=The computer that reads your documents")
        press_connect(page)
        shoot(page, out, "39-endpoint-unreachable")

        # 8. A public address, refused before a byte is sent, in its own words.
        page.get_by_role("textbox", name="Address").fill("https://api.openai.com/v1")
        press_connect(page)
        shoot(page, out, "40-endpoint-public-refused")

        # 9. The advanced disclosure, which is the only place the header shape
        #    appears at all. Opened deliberately, never for you.
        page.reload()
        page.wait_for_selector("text=Advanced")
        page.get_by_text("Advanced", exact=False).first.click()
        page.wait_for_timeout(200)
        shoot(page, out, "41-endpoint-advanced")

        small = context.new_page()
        small.set_viewport_size(PHONE)
        small.goto(f"{origin}/settings")
        small.wait_for_selector("text=The computer that reads your documents")
        small.locator("text=The computer that reads your documents").scroll_into_view_if_needed()
        shoot(small, out, "42-endpoint-phone")
        small.close()

        context.close()
    finally:
        if browser is not None:
            browser.close()
        try:
            box_server.shutdown()
            box_server.server_close()
        except OSError:
            pass
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=10)
        # Said out loud: a temporary vault with a throwaway key in it should be
        # findable if something went wrong, and gone if nothing did.
        print(f"  (worked in {work})")


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
        "--endpoint-only",
        action="store_true",
        help=(
            "photograph the inference endpoint section. Ignores --origin: this "
            "one starts its own server on a throwaway demo vault, with an "
            "isolated config home so it cannot touch your keychain, your device "
            "identity or any real vault, and its own fake inference box on "
            "loopback"
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
        if args.endpoint_only:
            endpoint_shots(play, out, work)
            print(f"\n{len(list(out.glob('*.png')))} screenshots in {out}")
            return 0

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
