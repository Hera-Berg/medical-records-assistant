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
``ungrounded``    answers a question with a citation it was never given, which
                  is the sentence the app must drop before anyone reads it

Questions (phase 10) are answered from the extracts the request actually
carried: the box parses them back out and restates them, so the citations in a
screenshot are real citations to real artefacts and the grounding rule is being
exercised rather than mimed. It cannot do otherwise — it knows nothing about the
record except what it was sent, which is the same constraint the real model is
under.

The mode can be changed while it runs by POSTing to ``/mode`` — that is how one
run of the screenshot script photographs every failure without restarting. The
same call takes a ``models`` list, so the state where a box offers several and
has to be asked which one can be photographed too.
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

MODES = ("working", "blind", "unconstrained", "unauthorised", "mismatched", "ungrounded")

#: The schema names ``agent.query`` asks under. Matched rather than assumed, so
#: this box answers a question as a question and an extraction as an extraction.
ANSWER_SCHEMA = "health_record_answer"
TERMS_SCHEMA = "health_record_search_terms"


class Box:
    """The mode and the model list, shared between threads and changeable live."""

    def __init__(self, mode: str = "working", models: list[str] | None = None):
        self.lock = threading.Lock()
        self._mode = mode
        self._models = list(models) if models is not None else [MODEL]

    @property
    def mode(self) -> str:
        with self.lock:
            return self._mode

    @mode.setter
    def mode(self, value: str) -> None:
        with self.lock:
            self._mode = value

    @property
    def models(self) -> list[str]:
        with self.lock:
            return list(self._models)

    @models.setter
    def models(self, value: list[str]) -> None:
        with self.lock:
            self._models = list(value)


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
                listed = [OTHER_MODEL] if mode == "mismatched" else box.models
                return self._send(
                    200,
                    {"object": "list", "data": [{"id": n, "object": "model"} for n in listed]},
                )
            return self._send(404, {"error": "no such path"})

        def do_POST(self):  # noqa: N802
            body = self._read()
            if self.path.rstrip("/").endswith("/mode"):
                if isinstance(body.get("models"), list):
                    box.models = [str(name) for name in body["models"]]
                wanted = str(body.get("mode", "")).strip()
                if wanted:
                    if wanted not in MODES:
                        return self._send(400, {"error": f"unknown mode {wanted!r}"})
                    box.mode = wanted
                return self._send(200, {"mode": box.mode, "models": box.models})

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


def _extracts(body: dict) -> list[tuple[str, str]]:
    """The ``[key] Title: value`` lines this request carried, as ``(key, line)``.

    Parsed back out of the prompt rather than invented. A box that answered from
    anything else would be photographing a mock instead of the pipeline.
    """
    found: list[tuple[str, str]] = []
    for message in body.get("messages", []):
        content = message.get("content")
        if not isinstance(content, str):
            continue
        for raw in content.splitlines():
            line = raw.strip()
            if not line.startswith("[") or "] " not in line:
                continue
            key, _, rest = line[1:].partition("] ")
            if key and rest:
                found.append((key, rest))
    return found


def _sentence(rest: str) -> str:
    """One extract restated as a sentence. Deliberately plain.

    This is a stand-in for a 9B model and it is not pretending to write like
    one: what the screenshots need to show is a sentence, its source and the
    document behind it, and prose that flattered the fake would make the shot
    less honest rather than more.
    """
    # The source is separated by " \u00b7 " precisely so that it can be cut off
    # without guessing where the value ends — a value can contain brackets.
    said = rest.split(" \u00b7 ")[0].strip()
    subject, separator, tail = said.partition(" \u2014 ")
    name = subject.split(" (")[0].strip()
    if not separator or ": " not in tail:
        return f"Your record says: {said}"
    predicate, _, value = tail.partition(": ")
    if " " in predicate:
        # A phrase rather than a slot name — "how it stands". "gives your
        # Perindopril how it stands as" is not English in any model's voice.
        return f"Your {name} is {value.strip()}."
    return f"Your record gives your {name} {predicate} as {value.strip()}."


def _answer(body: dict, mode: str) -> str:
    """A grounded answer to a question, built from the extracts it was sent."""
    extracts = _extracts(body)
    if not extracts:
        return json.dumps({"sentences": []})
    if mode == "ungrounded":
        # Fluent, confident, and citing a document that was never in front of
        # it. The app has to drop this rather than show it with a caveat.
        return json.dumps(
            {
                "sentences": [
                    {
                        "text": "Your record shows this has been stable for some time.",
                        "source": "ffffff",
                    }
                ]
            }
        )
    return json.dumps(
        {
            "sentences": [
                {"text": _sentence(rest), "source": key}
                for key, rest in extracts[:3]
            ]
        }
    )


def _completion(body: dict, mode: str, model: str) -> dict:
    """What this box answers, given what it was asked and how it is behaving."""
    schema_name = (
        (body.get("response_format") or {}).get("json_schema", {}).get("name", "")
    )
    if schema_name == ANSWER_SCHEMA and mode != "unconstrained":
        return _envelope(_answer(body, mode), model)
    if schema_name == TERMS_SCHEMA and mode != "unconstrained":
        # Search terms, taken off the question itself: a real model would do
        # better, and anything cleverer here would be this file guessing at the
        # record instead of the app retrieving from it.
        words = [
            word
            for message in body.get("messages", [])
            if isinstance(message.get("content"), str)
            for word in message["content"].split("QUESTION")[-1].split()
            if len(word) > 4
        ]
        return _envelope(json.dumps({"terms": words[:3]}), model)
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
