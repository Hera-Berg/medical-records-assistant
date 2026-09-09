"""Getting the shared objects out of the app, without a framework doing it.

FastAPI's dependency injection is a fine thing and this uses the smallest
possible amount of it: two functions that read what ``create_app`` put on
``app.state``. Everything the routes need is process-wide and lives for the life
of the server, so there is nothing to construct per request and nothing to tear
down after one.
"""

from __future__ import annotations

from fastapi import Request

from .index import Index
from .state import RecordState


def get_state(request: Request) -> RecordState:
    return request.app.state.record


def get_index(request: Request) -> Index:
    return request.app.state.index


def get_vault(request: Request):
    return request.app.state.record.vault
