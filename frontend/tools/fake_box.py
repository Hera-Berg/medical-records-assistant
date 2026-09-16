"""A stand-in for the inference box, misbehaving on request.

The settings screen has a connection test, and most of what it exists to tell
apart cannot be reached on a working laptop: a machine that refuses the key, a
machine that answers text and silently throws pictures away, a machine that
ignores the schema it was handed. Those are the states a person is stuck in and
reading the screen carefully, and they are the ones worth photographing.

So this is a **real OpenAI-compatible HTTP server** on loopback, which each
mode makes genuinely misbehave. The app under test is not stubbed anywhere: the
guard resolves this address for real, the client sends real requests, the probe
reads real answers, and the screenshot is of what the app actually did. A
picture of a mocked state is a picture of the mock.

It is not, and must never become, part of the application. It lives in
``frontend/tools`` beside the other developer tools, is imported by nothing in
``agent/``, and speaks only the two paths the client uses.

    python frontend/tools/fake_box.py --port 7999 --mode working

Modes:

``working``       answers everything correctly, and reads the probe image
``blind``         answers text perfectly and ignores every image — the trap
``unconstrained`` ignores ``response_format`` and replies with prose
``unauthorised``  401 to everything, with the key echoed in the body, which is
                  what a real server's error looks like and what must never
                  reach the browser
``mismatched``    offers and answers as a different model than it is asked for

The mode can be changed while it runs by POSTing to ``/mode`` — that is how one
run of the screenshot script photographs every failure without restarting.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

#: The text the probe draws into its test image and looks for in the answer.
#: Imported rather than copied would be neater; copied keeps this file free of
#: any dependency on the package it is pointed at.
PROBE_TEXT = "ZQ7 VERIFY 42"

MODEL = "Jundot/Qwen3.8-Flash-Next-oQ4e-mtp"
OTHER_MODEL = "llama-3.2-1b-instruct"

MODES = ("working", "blind", "unconstrained", "unauthorised", "mismatched")


class Box:
    """The mode, shared between threads and changeable while serving."""

    def __init__(self, mode: str = "working"):
        self.lock = threading.Lock()
        self._mode = mode

    @property
    def mode(self) -> str:
        with self.lock:
            return self._mode

    @mode.setter
    def mode(self, value: str) -> None:
        with self.lock:
            self._mode = value


def make_handler(box: Box):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # noqa: A003 - quiet by default
            pass

        # -- plumbing ---------------------------------------------------

        def _send(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _key(self) -> str:
            raw = self.headers.get("Authorization") or self.headers.get("X-API-Key") or ""
            return raw.removeprefix("Bearer ").strip()

        def _read(self) -> dict:
            length = int(self.headers.get("content-length") or 0)
            if not length:
                return {}
            try:
                return json.loads(self.rfile.read(length))
            except ValueError:
                return {}

        # -- the two paths a client uses --------------------------------

        def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's spelling
            mode = box.mode
            if mode == "unauthorised":
                # The body carries the key back, which is exactly what several
                # real servers do and exactly what must not reach a browser.
                return self._send(
                    401, {"error": {"message": f"invalid api key: {self._key()}"}}
                )
            if self.path.rstrip("/").endswith("/models"):
                name = OTHER_MODEL if mode == "mismatched" else MODEL
                return self._send(
                    200, {"object": "list", "data": [{"id": name, "object": "model"}]}
                )
            return self._send(404, {"error": "no such path"})

        def do_POST(self):  # noqa: N802
            body = self._read()
            if self.path.rstrip("/").endswith("/mode"):
                wanted = str(body.get("mode", "")).strip()
                if wanted not in MODES:
                    return self._send(400, {"error": f"unknown mode {wanted!r}"})
                box.mode = wanted
                return self._send(200, {"mode": wanted})

            mode = box.mode
            if mode == "unauthorised":
                return self._send(
                    401, {"error": {"message": f"invalid api key: {self._key()}"}}
                )
            if not self.path.rstrip("/").endswith("/chat/completions"):
                return self._send(404, {"error": "no such path"})

            answering_as = OTHER_MODEL if mode == "mismatched" else MODEL
            return self._send(200, _completion(body, mode, answering_as))

    return Handler


def _completion(body: dict, mode: str, model: str) -> dict:
    """What this box answers, given what it was asked and how it is behaving."""
    if body.get("response_format"):
        # A server honouring guided decoding returns the schema it was given.
        # One that does not narrates — which is what breaks every extraction.
        content = (
            "Sure! Here is the word you asked for: ready. Let me know if you need "
            "anything else."
            if mode == "unconstrained"
            else json.dumps({"word": "ready"})
        )
        return _envelope(content, model)

    saw_image = any(
        part.get("type") == "image_url"
        for message in body.get("messages", [])
        for part in (
            message.get("content") if isinstance(message.get("content"), list) else []
        )
    )
    if saw_image and mode != "blind":
        return _envelope(PROBE_TEXT, model)
    # `blind` is the whole point of the vision check: the image was accepted
    # and thrown away, so the answer describes nothing and names nothing.
    return _envelope("I'm not able to see an image in this conversation.", model)


def _envelope(content: str, model: str) -> dict:
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 64, "completion_tokens": 12, "total_tokens": 76},
    }


def serve(port: int, mode: str) -> tuple[ThreadingHTTPServer, Box]:
    """Start the box on loopback in a background thread. Returns it and its mode."""
    box = Box(mode)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(box))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, box


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=7999)
    parser.add_argument("--mode", default="working", choices=MODES)
    args = parser.parse_args()

    server, _ = serve(args.port, args.mode)
    print(f"fake box on http://127.0.0.1:{args.port}/v1 in {args.mode} mode")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
