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

**PyAV decodes first, and ``ffmpeg`` on ``PATH`` is the fallback.** ffmpeg was
chosen because it is the only thing that reliably reads every container a
browser or a phone will produce — WebM/Opus from Chrome, MP4/AAC from Safari.
PyAV *is* ffmpeg's libraries, bound into Python, and it arrives with
``faster-whisper``; so the reason for choosing ffmpeg still holds, and a person
who installed this app does not also have to install a system program before a
voice note is typed up. If neither can read a file, that is a **reported
state**, exactly like a missing ``pdfplumber``: the transcript is unavailable
with a sentence saying what happened, never a crash and never an exception
reaching a capture path.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import wave
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from ..distribution import missing_library

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
    #: ``pyav`` or ``ffmpeg``. Recorded beside the transcript, never in the
    #: settings digest: the same samples reach the model either way, and
    #: re-transcribing every recording because the decoder changed would be work
    #: that produces nothing.
    decoder: str = "ffmpeg"


def _decode_with_pyav(source: Path, target: Path) -> float | None:
    """Decode to 16 kHz mono PCM with PyAV. Returns seconds, or ``None`` if unavailable.

    Raises :class:`DecodeUnavailable` for a file PyAV has and cannot read, so a
    broken recording is not handed to ffmpeg to fail a second time in different
    words — unless ffmpeg is there, in which case the caller tries it anyway.
    """
    try:
        import av  # noqa: PLC0415 - optional, arrives with faster-whisper
    except ImportError:
        return None

    frames = 0
    with wave.open(str(target), "wb") as out:
        out.setnchannels(CHANNELS)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        with av.open(str(source)) as container:
            streams = [s for s in container.streams if s.type == "audio"]
            if not streams:
                raise DecodeUnavailable("this recording holds no audio stream")
            resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
            for frame in container.decode(streams[0]):
                for converted in resampler.resample(frame):
                    data = converted.to_ndarray().tobytes()
                    out.writeframes(data)
                    frames += len(data) // 2
            for converted in resampler.resample(None):
                data = converted.to_ndarray().tobytes()
                out.writeframes(data)
                frames += len(data) // 2
    if frames == 0:
        raise DecodeUnavailable("this recording decoded to no audio at all")
    return round(frames / SAMPLE_RATE, 3)


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
    directory = tempfile.mkdtemp(prefix="health-agent-asr-")
    target = Path(directory) / "audio.wav"
    ffmpeg = ffmpeg_path()
    try:
        try:
            seconds = _decode_with_pyav(source, target)
        except Exception as exc:  # noqa: BLE001 - PyAV raises its own error family
            if ffmpeg is None:
                if isinstance(exc, DecodeUnavailable):
                    raise
                raise DecodeUnavailable(f"this recording could not be decoded: {exc}") from None
            seconds = None
        else:
            if seconds is not None:
                yield WorkingCopy(path=target, seconds=seconds, decoder="pyav")
                return

        if ffmpeg is None:
            # Stored as the job's reason, so it names no command: see
            # agent.distribution. ffmpeg is the fallback only a pip install has.
            raise DecodeUnavailable(
                missing_library(
                    "PyAV", None, "nothing on this computer can decode this recording",
                    stored=True,
                )
                + " The recording itself is safe in raw/ and nothing has been lost."
            )
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
