"""Mime detection: the bytes decide, the filename only narrows."""

from __future__ import annotations

import pytest

from agent.ingest import mime

from .conftest import HEIC, JPEG, PDF, PNG, WEBM


# --- sniffing ---------------------------------------------------------------

@pytest.mark.parametrize(
    "head,expected",
    [
        (JPEG, "image/jpeg"),
        (PNG, "image/png"),
        (PDF, "application/pdf"),
        (HEIC, "image/heic"),
        (WEBM, "video/webm"),
        (b"GIF89a" + b"\x00" * 16, "image/gif"),
        (b"II*\x00" + b"\x00" * 16, "image/tiff"),
        (b"RIFF\x00\x00\x00\x00WEBP", "image/webp"),
        (b"RIFF\x00\x00\x00\x00WAVEfmt ", "audio/wav"),
        (b"fLaC\x00\x00\x00\x22", "audio/flac"),
        (b"OggS\x00\x02" + b"\x00" * 16, "audio/ogg"),
        (b"ID3\x03\x00" + b"\x00" * 16, "audio/mpeg"),
        (b"\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00", "audio/mp4"),
        (b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00", "video/mp4"),
        (b"\x00\x00\x00\x18ftypmif1\x00\x00\x00\x00", "image/heif"),
        (b"the headaches started around easter\n", "text/plain"),
    ],
)
def test_sniff_identifies_the_formats_this_project_receives(head, expected):
    assert mime.sniff(head) == expected


def test_sniff_gives_up_rather_than_guessing():
    assert mime.sniff(b"\x00\x01\x02\x03\x04\x05\x06\x07") is None


def test_binary_with_a_null_byte_is_not_text():
    assert mime.sniff(b"hello\x00world") is None


def test_truncated_multibyte_character_at_the_head_boundary_is_still_text():
    # The head is a fixed-size prefix, so it can cut a UTF-8 character in half.
    # That says nothing about the file and must not demote it to binary.
    assert mime.sniff("café au lait".encode()[:-1]) == "text/plain"


# --- deciding ---------------------------------------------------------------

def test_content_beats_a_lying_extension():
    """An iPhone photo saved as .jpg is the common case, not an edge case."""
    detected = mime.detect(HEIC, filename="IMG_4821.jpg")
    assert detected.mime == "image/heic"
    assert detected.source == mime.SOURCE_SNIFFED
    assert detected.extension() == "heic"


def test_content_beats_a_lying_declared_type():
    detected = mime.detect(PDF, filename="scan.txt", declared="text/plain")
    assert detected.mime == "application/pdf"


def test_declared_type_may_narrow_a_container_it_agrees_with():
    """A WebM header does not say whether it holds audio; the browser does."""
    detected = mime.detect(WEBM, filename="note", declared="audio/webm;codecs=opus")
    assert detected.mime == "audio/webm"
    assert detected.source == mime.SOURCE_REFINED_DECLARED


def test_extension_may_narrow_a_text_file():
    detected = mime.detect(b"# Notes\n\nsome text\n", filename="notes.md")
    assert detected.mime == "text/markdown"
    assert detected.source == mime.SOURCE_REFINED_EXTENSION


def test_extension_is_the_fallback_when_the_bytes_say_nothing():
    detected = mime.detect(b"\x00\x01\x02\x03", filename="scan.png")
    assert detected.mime == "image/png"
    assert detected.source == mime.SOURCE_EXTENSION


def test_unknown_is_admitted_rather_than_guessed():
    detected = mime.detect(b"\x00\x01\x02\x03", filename="mystery")
    assert detected.mime == mime.OCTET_STREAM
    assert detected.source == mime.SOURCE_UNKNOWN
    assert detected.is_guess


def test_unrecognised_format_keeps_the_extension_it_arrived_with():
    # Storing an unfamiliar file as .bin would need this app to decode the
    # folder, which is the thing the layout rules forbid.
    detected = mime.detect(b"\x00\x01\x02\x03", filename="study.dcm")
    assert detected.extension("study.dcm") == "dcm"


def test_absurd_extensions_fall_back_to_bin():
    assert mime.extension_for(mime.OCTET_STREAM, "x.thisisnotanextension") == "bin"
    assert mime.extension_for(mime.OCTET_STREAM, "x.J P G") == "bin"


def test_known_types_get_a_fixed_extension():
    # Never mimetypes.guess_extension, which returns .jpe for JPEG on some hosts
    # and would make stored filenames depend on the machine that ingested.
    assert mime.extension_for("image/jpeg") == "jpg"
    assert mime.extension_for("audio/mp4") == "m4a"
