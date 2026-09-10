"""The working copy: 16 kHz mono WAV, in a temp dir, deleted afterwards.

``CLAUDE.md``: "Store the original as-is in ``raw/``; transcode a 16kHz mono WAV
copy into a temp dir for Whisper and delete it after."

Three things this arrangement buys, and each is the reason for one line of it:

**The original is never touched.** ``MediaRecorder`` produces ``audio/webm;opus``
in Chrome and ``audio/mp4`` in Safari, and both are stored exactly as the browser
produced them. What the model reads is a derived copy, the same way the vision
path sends a downscaled working image and never the photograph.

**The copy is outside the vault.** The vault syncs. A 90-second WAV is roughly
three megabytes, and writing one into a synced folder for every recording means
uploading and deleting three megabytes per voice note through somebody's
Dropbox — for a file whose whole purpose is to exist for forty seconds.

**The copy is deleted in a ``finally``.** Including when transcription raises,
including when the process is interrupted mid-read. It holds a verbatim copy of
someone's voice describing their health; leaving it in ``/tmp`` because a model
threw is not acceptable.

``ffmpeg`` does the decoding rather than a Python audio library, because it is
the only thing that reliably reads every container a browser or a phone will
produce. If it is absent that is a **reported state**, exactly like a missing
``pdfplumber``: the transcript is unavailable with a sentence saying what to
install, never a crash and never an exception reaching a capture path.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

#: What Whisper wants. Anything else is resampled by the library at load time,
#: so doing it once here is both faster and one fewer thing to be surprised by.
SAMPLE_RATE = 16000
CHANNELS = 1

#: A voice note is minutes, not hours. A decode that has not finished in this
#: long is a file that is not going to decode, and the queue must not be held by
#: one artefact.
DECODE_TIMEOUT_S = 300


class DecodeUnavailable(Exception):
    """Nothing could decode this. A reported state, never raised past the runner."""


@dataclass(frozen=True)
class WorkingCopy:
    """A decoded WAV and what the decoder said about the source."""

    path: Path
    seconds: float | None = None


def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


def _duration(path: Path) -> float | None:
    """Seconds, from ffprobe. Absent rather than guessed if ffprobe is not there."""
    probe = shutil.which("ffprobe")
    if probe is None:
        return None
    try:
        result = subprocess.run(
            [
                probe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        return round(float(result.stdout.strip()), 3)
    except ValueError:
        return None


@contextmanager
def working_copy(source: Path) -> Iterator[WorkingCopy]:
    """Decode *source* to a temporary 16 kHz mono WAV, and delete it afterwards.

    The temporary directory is the system's, never the vault's, and it goes away
    with the context whatever happened inside it.
    """
    ffmpeg = ffmpeg_path()
    if ffmpeg is None:
        raise DecodeUnavailable(
            "ffmpeg is not installed, so this recording could not be decoded for "
            "the speech model. The recording itself is safe in raw/ and nothing "
            "has been lost: install ffmpeg and run `health-agent transcribe` to "
            "type it up."
        )

    directory = tempfile.mkdtemp(prefix="health-agent-asr-")
    target = Path(directory) / "audio.wav"
    try:
        result = subprocess.run(
            [
                ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(source),
                # No video stream, whatever the container claims to hold.
                "-vn",
                "-ac",
                str(CHANNELS),
                "-ar",
                str(SAMPLE_RATE),
                "-c:a",
                "pcm_s16le",
                "-f",
                "wav",
                "-y",
                str(target),
            ],
            capture_output=True,
            text=True,
            timeout=DECODE_TIMEOUT_S,
            check=False,
        )
        if result.returncode != 0 or not target.exists() or target.stat().st_size == 0:
            detail = (result.stderr or "").strip().splitlines()
            raise DecodeUnavailable(
                "this recording could not be decoded"
                + (f": {detail[-1]}" if detail else "")
            )
        yield WorkingCopy(path=target, seconds=_duration(target))
    except subprocess.TimeoutExpired:
        raise DecodeUnavailable(
            f"decoding this recording took longer than {DECODE_TIMEOUT_S} seconds "
            f"and was stopped"
        ) from None
    except OSError as exc:
        raise DecodeUnavailable(f"this recording could not be decoded: {exc}") from exc
    finally:
        # Deleted whatever happened above, including when the model raised. It
        # is a verbatim copy of someone's voice and it lives for as long as one
        # transcription takes.
        shutil.rmtree(directory, ignore_errors=True)
