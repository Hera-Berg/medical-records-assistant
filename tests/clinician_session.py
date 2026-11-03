"""Signing a test client in to the clinician workspace.

The progress-note routes sit behind a clinician session. Tests that exercise
those routes, rather than the sign-in itself, need a session without depending
on the Jane-only provisioning command: this adds one account to the running
app's store and signs in through the real route, so the cookie, the CSRF token
and the same-origin checks are the ones a browser would meet.
"""

from __future__ import annotations

import secrets

from agent.clinician_auth import password_hash

USERNAME = "test.clinician"


def sign_in(app, client, *, name: str = "Dr Test Clinician", role: str = "Usual GP") -> dict:
    """Sign *client* in. Later writes carry the session's CSRF header automatically."""
    store = app.state.clinician_auth
    password = secrets.token_urlsafe(18)
    salt = secrets.token_hex(16)
    store.accounts[USERNAME] = {
        "username": USERNAME,
        "name": name,
        "role": role,
        "salutation": name,
        "patients": [store.patient],
        "password": {"algorithm": "pbkdf2-sha256", "salt": salt, "hash": password_hash(password, salt)},
    }
    response = client.post("/api/auth/clinician/login", json={"username": USERNAME, "password": password})
    assert response.status_code == 200, response.text
    session = response.json()
    client.headers["x-clinician-csrf"] = session["csrf"]
    return session
