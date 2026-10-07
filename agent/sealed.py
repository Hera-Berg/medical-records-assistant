"""Doctor's notes: a locked compartment inside the record.

A clinician types a note during the consultation; it is sealed with a
passphrase **in the browser** and reaches this program only as ciphertext. The
server never sees the passphrase or the words, so nothing it logs, caches or
writes can contain them, and no dependency was added to the pinned lock to do
cryptography here — the browser's own WebCrypto does it.

**Events, because the event log is the only source of truth.** A compartment is
``compartment.created`` and a note is ``compartment.note.sealed``. The
projection has no branch for either, so neither reaches ``wiki/``, the
timeline, the consultation summary, the index or any answer to a question —
and the reader model is never sent a byte of either. That is what "doctor
only" can mean in a folder of plain files: whoever holds the passphrase reads
the notes, and anyone else with the folder sees that a note exists and when it
was written, and nothing more.

**The folder outlives the app.** Every compartment carries, in words, how its
notes are sealed — so whoever opens the folder in five years with the
passphrase and no app can open them with any standard implementation. The
text names an algorithm, never a command.

**What is visible without the passphrase**: that a compartment exists, how many
notes it holds, and each note's timestamp and device. The author, the date
typed into the note and the text are inside the ciphertext.

**Append-only like everything else.** There is no route to delete or replace a
note, and none to change the passphrase: a re-sealed copy under a new
passphrase would leave the old ciphertext, openable by the old passphrase, in
the log for ever, and a passphrase change that does not revoke the old one is
worse than none.

**Validation is strict on the shape and blind to the content.** The payload
must have exactly the keys a sealed note has, each of the size it should be,
so a client bug cannot send ``{"text": ...}`` and have plaintext appended to a
file that syncs to a third party. Nothing here can tell ciphertext from
random-looking bytes, and nothing tries to.
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from typing import Any, Iterable

from .errors import HealthAgentError
from .events.envelope import Event

COMPARTMENT_CREATED = "compartment.created"
NOTE_SEALED = "compartment.note.sealed"

#: The one purpose a compartment has today. Recorded so a second kind of
#: compartment later is a new value, not a guess about what old ones held.
PURPOSE = "doctor-notes"

CIPHER = "AES-256-GCM"
KDF_NAME = "PBKDF2"
KDF_HASH = "SHA-256"
#: OWASP's 2023 figure for PBKDF2-HMAC-SHA256. A floor, not a fixed value: a
#: compartment may record more, and must not record fewer.
MIN_ITERATIONS = 600_000
MAX_ITERATIONS = 10_000_000

SALT_BYTES = 16
IV_BYTES = 12
TAG_BYTES = 16
#: A note is typed during a consultation. Generous, and still a bound: the log
#: is read whole on every rebuild.
MAX_CIPHERTEXT_BYTES = 64 * 1024

#: Written into every compartment, in words, so the folder can be opened
#: without this program. Names no command — stored text never does.
HOW_TO_OPEN = (
    "These notes are sealed with a passphrase that is not stored anywhere. "
    "The key is PBKDF2-HMAC-SHA256 over the passphrase as UTF-8, with this "
    "compartment's salt and iteration count, giving 32 bytes. Each note is "
    "AES-256-GCM with that key, the note's 12-byte iv, and this compartment's id "
    "as UTF-8 additional authenticated data; the last 16 bytes of the "
    "ciphertext are the tag. Salt, iv and ciphertext are standard base64. The "
    "opened note is UTF-8 JSON with author, written and text. The check field "
    "opens, with the same key and the same additional data, to the words in "
    "check_plaintext. Without the passphrase they cannot be opened by anyone."
)
CHECK_PLAINTEXT = "sealed doctor notes, check 1"

_ID_RE = re.compile(r"cmp-[0-9a-f]{32}")


class SealedPayloadError(HealthAgentError):
    """A sealed payload is not the shape a sealed payload has."""


@dataclass(frozen=True)
class Compartment:
    id: str
    event_id: str
    ts: str
    device: str
    kdf: dict[str, Any]
    check: dict[str, str]


@dataclass(frozen=True)
class SealedNote:
    event_id: str
    ts: str
    device: str
    compartment: str
    iv: str
    ciphertext: str


def _b64(value: object, field: str, *, exact: int | None = None, low: int = 1, high: int | None = None) -> str:
    if not isinstance(value, str) or not value:
        raise SealedPayloadError(f"{field} must be base64 text")
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise SealedPayloadError(f"{field} is not base64") from None
    if exact is not None and len(raw) != exact:
        raise SealedPayloadError(f"{field} must be {exact} bytes")
    if len(raw) < low or (high is not None and len(raw) > high):
        raise SealedPayloadError(f"{field} is the wrong size")
    return value


def _exact_keys(value: object, keys: set[str], field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SealedPayloadError(f"{field} must be an object")
    if set(value) != keys:
        raise SealedPayloadError(f"{field} must have exactly {', '.join(sorted(keys))}")
    return value


def _sealed_box(value: object, field: str) -> dict[str, str]:
    box = _exact_keys(value, {"iv", "ciphertext"}, field)
    return {
        "iv": _b64(box["iv"], f"{field}.iv", exact=IV_BYTES),
        "ciphertext": _b64(
            box["ciphertext"], f"{field}.ciphertext", low=TAG_BYTES + 1, high=MAX_CIPHERTEXT_BYTES
        ),
    }


def compartment_payload(body: object) -> dict[str, Any]:
    """The payload for a new compartment, from what the browser sent.

    The browser chooses the id, because the check is sealed with the id as its
    additional data and has to be sealed before anything is sent.
    """
    data = _exact_keys(body, {"id", "kdf", "check"}, "body")
    compartment_id = data["id"]
    if not isinstance(compartment_id, str) or not _ID_RE.fullmatch(compartment_id):
        raise SealedPayloadError("id must be cmp- and 32 lowercase hex characters")
    kdf = _exact_keys(data["kdf"], {"name", "hash", "iterations", "salt"}, "kdf")
    if kdf["name"] != KDF_NAME or kdf["hash"] != KDF_HASH:
        raise SealedPayloadError(f"kdf must be {KDF_NAME} with {KDF_HASH}")
    iterations = kdf["iterations"]
    if (
        not isinstance(iterations, int)
        or isinstance(iterations, bool)
        or not MIN_ITERATIONS <= iterations <= MAX_ITERATIONS
    ):
        raise SealedPayloadError(f"kdf.iterations must be at least {MIN_ITERATIONS}")
    return {
        "compartment": compartment_id,
        "purpose": PURPOSE,
        "cipher": CIPHER,
        "kdf": {
            "name": KDF_NAME,
            "hash": KDF_HASH,
            "iterations": iterations,
            "salt": _b64(kdf["salt"], "kdf.salt", exact=SALT_BYTES),
        },
        "check": _sealed_box(data["check"], "check"),
        "check_plaintext": CHECK_PLAINTEXT,
        "how_to_open": HOW_TO_OPEN,
    }


def note_payload(body: object, compartments: Iterable[Compartment]) -> dict[str, Any]:
    """The payload for a sealed note. Refuses anything carrying more than ciphertext."""
    data = _exact_keys(body, {"compartment", "iv", "ciphertext"}, "body")
    known = {c.id for c in compartments}
    if data["compartment"] not in known:
        raise SealedPayloadError("there is no such compartment in this record")
    box = _sealed_box({"iv": data["iv"], "ciphertext": data["ciphertext"]}, "note")
    return {"compartment": data["compartment"], "cipher": CIPHER, **box}


def read(events: Iterable[Event]) -> tuple[list[Compartment], list[SealedNote]]:
    """Every compartment and sealed note in the log, in log order.

    Permissive on read, as the log is: a line that does not have the shape is
    skipped here rather than raised, because it is history and the log's own
    read already reports what it could not parse.
    """
    compartments: list[Compartment] = []
    notes: list[SealedNote] = []
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type == COMPARTMENT_CREATED:
            kdf, check, cid = payload.get("kdf"), payload.get("check"), payload.get("compartment")
            if isinstance(cid, str) and isinstance(kdf, dict) and isinstance(check, dict):
                compartments.append(
                    Compartment(cid, event.id, event.ts, event.device, dict(kdf), dict(check))
                )
        elif event.type == NOTE_SEALED:
            cid, iv, ct = payload.get("compartment"), payload.get("iv"), payload.get("ciphertext")
            if all(isinstance(v, str) for v in (cid, iv, ct)):
                notes.append(SealedNote(event.id, event.ts, event.device, cid, iv, ct))
    return compartments, notes
