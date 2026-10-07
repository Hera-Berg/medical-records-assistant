"""Doctor's notes: a locked compartment that never reaches the wiki.

The browser seals; these tests seal the same way with ``cryptography`` (already
in the app's pinned lock), following only the words the compartment itself
stores in ``how_to_open``. That is the folder-outlives-the-app promise tested
directly: someone with the passphrase and no app can open a note from what the
log says about it.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from agent import projection, sealed
from agent.events import log

from .conftest import claim, confirm, ingested, note, on_day

PASSPHRASE = "correct horse battery staple"
ITERATIONS = sealed.MIN_ITERATIONS
NOTE = {"author": "Dr Nguyen", "written": "2026-10-07", "text": "Discussed sleep. Review in 3 months."}


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _key(passphrase: str, salt: bytes, iterations: int) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations)
    return kdf.derive(passphrase.encode("utf-8"))


def _seal(key: bytes, aad: str, plaintext: bytes) -> dict[str, str]:
    iv = os.urandom(12)
    return {"iv": _b64(iv), "ciphertext": _b64(AESGCM(key).encrypt(iv, plaintext, aad.encode()))}


def _create(client) -> tuple[str, bytes]:
    """Create the compartment as the browser does. Returns its id and key."""
    salt = os.urandom(16)
    key = _key(PASSPHRASE, salt, ITERATIONS)
    # The browser chooses the id, because the check is sealed with it.
    compartment = f"cmp-{os.urandom(16).hex()}"
    body = {
        "id": compartment,
        "kdf": {"name": "PBKDF2", "hash": "SHA-256", "iterations": ITERATIONS, "salt": _b64(salt)},
        "check": _seal(key, compartment, sealed.CHECK_PLAINTEXT.encode()),
    }
    response = client.post("/api/doctor-notes/compartment", json=body)
    assert response.status_code == 201, response.text
    (listed,) = response.json()["compartments"]
    assert listed["id"] == compartment
    return compartment, key


def _add(client, compartment: str, key: bytes, content: dict = NOTE):
    box = _seal(key, compartment, json.dumps(content).encode("utf-8"))
    return client.post("/api/doctor-notes", json={"compartment": compartment, **box})


def test_a_note_opens_from_the_folders_own_instructions(vault, client):
    compartment, key = _create(client)
    assert _add(client, compartment, key).status_code == 201

    events = log.read_all(vault.events_dir, vault.profile).events
    created = next(e for e in events if e.type == sealed.COMPARTMENT_CREATED)
    sealed_note = next(e for e in events if e.type == sealed.NOTE_SEALED)

    # Everything needed is on the lines themselves, in words a person can follow.
    assert created.payload["how_to_open"] == sealed.HOW_TO_OPEN
    kdf = created.payload["kdf"]
    rederived = _key(PASSPHRASE, base64.b64decode(kdf["salt"]), kdf["iterations"])
    opened = AESGCM(rederived).decrypt(
        base64.b64decode(sealed_note.payload["iv"]),
        base64.b64decode(sealed_note.payload["ciphertext"]),
        created.payload["compartment"].encode("utf-8"),
    )
    assert json.loads(opened) == NOTE


def test_the_words_are_in_no_file_in_the_folder(vault, client):
    compartment, key = _create(client)
    _add(client, compartment, key)
    projection.rebuild(vault, as_of="2026-10-07T00:00:00Z")
    for path in vault.root.rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            for secret in (PASSPHRASE, NOTE["text"], NOTE["author"]):
                assert secret.encode() not in data, f"{secret!r} found in {path}"


def test_notes_change_nothing_the_projection_writes(vault, client, tmp_path):
    """Wiki, timeline and anomalies are exactly what they were without the notes."""
    device = vault.identity.id
    dose = claim(
        device,
        "med:perindopril",
        "dose",
        {"amount": 5, "unit": "mg", "frequency": "daily"},
        ts=on_day(2, hour=10),
        occurred={"value": "2026-06-04", "precision": "day", "uncertainty_days": 0},
    )
    for event in (
        ingested(device, "a3f91c", ts=on_day(2)),
        note(device, ts=on_day(3), text="Headaches started around Easter."),
        dose,
        confirm(device, dose.id, ts=on_day(2, hour=11)),
    ):
        vault.append(event)

    as_of = "2026-10-07T00:00:00Z"
    before = projection.rebuild(vault, as_of=as_of)
    wiki_before = _tree(vault.root / "wiki")
    rows_before = len(before.projection.rows)

    compartment, key = _create(client)
    _add(client, compartment, key)

    after = projection.rebuild(vault, as_of=as_of)
    assert _tree(vault.root / "wiki") == wiki_before
    assert len(after.projection.rows) == rows_before
    assert list(after.projection.anomalies) == list(before.projection.anomalies)

    timeline = client.get("/api/timeline").json()
    assert all("compartment" not in json.dumps(row) for row in timeline["rows"])


def test_plaintext_shaped_bodies_are_refused(vault, client):
    compartment, key = _create(client)
    box = _seal(key, compartment, b"{}")
    for body in (
        {"compartment": compartment, **box, "text": NOTE["text"]},
        {"compartment": compartment, "text": NOTE["text"]},
        {"compartment": compartment, "iv": _b64(b"short"), "ciphertext": box["ciphertext"]},
        {"compartment": compartment, "iv": box["iv"], "ciphertext": "not base64!"},
        {"compartment": "cmp-elsewhere", **box},
    ):
        response = client.post("/api/doctor-notes", json=body)
        assert response.status_code == 400, body
    assert client.get("/api/doctor-notes").json()["notes"] == []


def test_a_weak_key_derivation_is_refused(client):
    body = {
        "id": f"cmp-{os.urandom(16).hex()}",
        "kdf": {"name": "PBKDF2", "hash": "SHA-256", "iterations": 1000, "salt": _b64(os.urandom(16))},
        "check": {"iv": _b64(os.urandom(12)), "ciphertext": _b64(os.urandom(40))},
    }
    assert client.post("/api/doctor-notes/compartment", json=body).status_code == 400


def test_one_passphrase_per_record(client):
    _create(client)
    salt = os.urandom(16)
    body = {
        "id": f"cmp-{os.urandom(16).hex()}",
        "kdf": {"name": "PBKDF2", "hash": "SHA-256", "iterations": ITERATIONS, "salt": _b64(salt)},
        "check": {"iv": _b64(os.urandom(12)), "ciphertext": _b64(os.urandom(40))},
    }
    assert client.post("/api/doctor-notes/compartment", json=body).status_code == 409


def test_notes_list_newest_first(client):
    compartment, key = _create(client)
    for text in ("first", "second"):
        _add(client, compartment, key, {**NOTE, "text": text})
    notes = client.get("/api/doctor-notes").json()["notes"]
    opened = [
        json.loads(
            AESGCM(key).decrypt(
                base64.b64decode(n["iv"]), base64.b64decode(n["ciphertext"]), compartment.encode()
            )
        )["text"]
        for n in notes
    ]
    assert opened == ["second", "first"]


def _tree(root) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }
