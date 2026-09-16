"""Speech with nothing installed but the app: PyAV decodes, pinned weights load.

Two phase-11 promises. A voice note is typed up without a system ffmpeg,
because PyAV — ffmpeg's libraries, bound into Python — arrives with
faster-whisper. And the speech model is never fetched behind anyone's back:
``WhisperModel("small")`` downloads from the hub on first use, so a name is only
ever loaded from a cache already on disk.
"""

from __future__ import annotations

import builtins
import math
import shutil
import struct
import subprocess
import sys
import types
import wave

import pytest

from agent.asr import audio as audio_mod
from agent.asr import transcribe as transcribe_mod
from agent.runtime import manifest
from agent.runtime.store import Store


def _tone(path, seconds=1.0, rate=44100, channels=2):
    with wave.open(str(path), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(rate)
        frames = bytearray()
        for i in range(int(seconds * rate)):
            sample = int(8000 * math.sin(2 * math.pi * 440 * i / rate))
            frames += struct.pack("<h", sample) * channels
        out.writeframes(bytes(frames))


def test_pyav_decodes_to_16k_mono_without_ffmpeg(tmp_path, monkeypatch):
    pytest.importorskip("av")
    source = tmp_path / "note.wav"
    _tone(source)
    monkeypatch.setattr(audio_mod, "ffmpeg_path", lambda: None)
    with audio_mod.working_copy(source) as copy:
        assert copy.decoder == "pyav"
        with wave.open(str(copy.path)) as decoded:
            assert decoded.getframerate() == audio_mod.SAMPLE_RATE
            assert decoded.getnchannels() == 1
        assert copy.seconds == pytest.approx(1.0, abs=0.05)
        directory = copy.path.parent
    assert not directory.exists(), "the working copy of someone's voice is deleted"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg to make a WebM")
def test_pyav_reads_what_chrome_records(tmp_path, monkeypatch):
    pytest.importorskip("av")
    wav = tmp_path / "tone.wav"
    _tone(wav, seconds=2.0, rate=48000, channels=1)
    webm = tmp_path / "note.webm"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", str(wav), "-c:a", "libopus", str(webm)],
        check=True,
    )
    monkeypatch.setattr(audio_mod, "ffmpeg_path", lambda: None)
    with audio_mod.working_copy(webm) as copy:
        assert copy.decoder == "pyav"
        assert copy.seconds == pytest.approx(2.0, abs=0.1)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_ffmpeg_is_the_fallback_when_pyav_is_missing(tmp_path, monkeypatch):
    source = tmp_path / "note.wav"
    _tone(source)
    real_import = builtins.__import__

    def no_av(name, *args, **kwargs):
        if name == "av":
            raise ImportError("no av here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_av)
    with audio_mod.working_copy(source) as copy:
        assert copy.decoder == "ffmpeg"


def test_with_neither_the_recording_is_kept_and_the_reason_given(tmp_path, monkeypatch):
    source = tmp_path / "note.wav"
    _tone(source)
    real_import = builtins.__import__

    def no_av(name, *args, **kwargs):
        if name == "av":
            raise ImportError("no av here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_av)
    monkeypatch.setattr(audio_mod, "ffmpeg_path", lambda: None)
    with pytest.raises(audio_mod.DecodeUnavailable) as caught:
        with audio_mod.working_copy(source):
            pass
    assert "nothing has been lost" in str(caught.value)
    assert source.exists()


class FakeWhisper:
    calls: list[tuple[tuple, dict]] = []
    refuse = False

    def __init__(self, *args, **kwargs):
        FakeWhisper.calls.append((args, kwargs))
        if FakeWhisper.refuse:
            raise RuntimeError("not in the local cache")


@pytest.fixture
def fake_faster_whisper(monkeypatch):
    FakeWhisper.calls = []
    FakeWhisper.refuse = False
    module = types.ModuleType("faster_whisper")
    module.WhisperModel = FakeWhisper
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    return FakeWhisper


def test_a_name_is_never_fetched(fake_faster_whisper):
    engine, runtime = transcribe_mod._load_uncached("small", "int8")
    ((args, kwargs),) = fake_faster_whisper.calls
    assert kwargs.get("local_files_only") is True
    assert runtime == {"kind": "cache", "engine": "faster-whisper", "files": None}


def test_no_cache_and_no_download_is_a_sentence_not_a_fetch(fake_faster_whisper):
    fake_faster_whisper.refuse = True
    with pytest.raises(audio_mod.DecodeUnavailable) as caught:
        transcribe_mod._load_uncached("small", "int8")
    assert "has not been downloaded" in str(caught.value)


def test_pinned_weights_load_from_disk_and_say_so(fake_faster_whisper, monkeypatch):
    monkeypatch.setattr(Store, "is_ready", lambda self, bundles: True)
    engine, runtime = transcribe_mod._load_uncached("small", "int8")
    ((args, kwargs),) = fake_faster_whisper.calls
    assert args[0].endswith(manifest.SPEECH.id)
    assert "local_files_only" not in kwargs
    assert runtime["kind"] == "bundled"
    assert runtime["files"]["model.bin"] == f"sha256:{manifest.SPEECH.files[0].sha256}"


def test_where_the_weights_came_from_does_not_retranscribe_history():
    """The digest is the settings. Pinned or cached, the same weights are the same work."""
    before = transcribe_mod.settings_digest("small", "int8", "en")
    assert "runtime" not in transcribe_mod._settings_dict("small", "int8", "en")
    assert transcribe_mod.settings_digest("small", "int8", "en") == before
