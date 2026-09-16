"""A stand-in for ``llama-server``, for testing the supervisor without a model.

Speaks just enough of the real server's surface, as observed on the pinned
build: ``/health`` answers without a key, everything else wants
``Authorization: Bearer $LLAMA_API_KEY``, ``/props`` reports alias, build and
modalities, and the listening line is printed in the same words.

Behaviour switches come from the environment, never from argv, so the argv the
supervisor builds is exactly what a test inspects.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, required=True)
parser.add_argument("--alias", default="")
args, _ = parser.parse_known_args()

KEY = os.environ.get("LLAMA_API_KEY", "")
BUILD = os.environ.get("FAKE_BUILD", "b10997-fccf7166f")
ALIAS = os.environ.get("FAKE_ALIAS", args.alias)
VISION = os.environ.get("FAKE_VISION", "1") == "1"
EXIT_AFTER = float(os.environ.get("FAKE_EXIT_AFTER", "0"))
EXIT_CODE = int(os.environ.get("FAKE_EXIT_CODE", "1"))
RECORD = os.environ.get("FAKE_RECORD")
ENV_DUMP = os.environ.get("FAKE_ENV_DUMP")

if ENV_DUMP:
    Path(ENV_DUMP).write_text(json.dumps(sorted(os.environ)), encoding="utf-8")

if os.environ.get("FAKE_EXIT_AT_START"):
    print("error: couldn't load model", flush=True)
    sys.exit(EXIT_CODE)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorised(self) -> bool:
        if RECORD:
            with open(RECORD, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"path": self.path, "auth": self.headers.get("Authorization")}) + "\n")
        if self.headers.get("Authorization") == f"Bearer {KEY}":
            return True
        self._send(401, {"error": {"message": "Invalid API Key", "code": 401}})
        return False

    def do_GET(self):
        if self.path in ("/health", "/v1/health"):
            return self._send(200, {"status": "ok"})
        if not self._authorised():
            return None
        if self.path == "/props":
            return self._send(200, {
                "model_alias": ALIAS, "build_info": BUILD,
                "modalities": {"vision": VISION, "audio": False},
            })
        if self.path == "/v1/models":
            return self._send(200, {"data": [{"id": ALIAS}]})
        return self._send(404, {})

    def do_POST(self):
        if not self._authorised():
            return None
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        messages = body.get("messages") or []
        has_image = any(
            isinstance(m.get("content"), list)
            and any(part.get("type") == "image_url" for part in m["content"])
            for m in messages
        )
        if body.get("response_format"):
            content = json.dumps({"word": "ready"})
        elif has_image and VISION:
            content = "ZQ7 VERIFY 42"
        else:
            content = "nothing"
        return self._send(200, {
            "model": ALIAS,
            "system_fingerprint": BUILD,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 3},
        })


server = ThreadingHTTPServer((args.host, args.port), Handler)
print(f"main: server is listening on http://{args.host}:{args.port} - starting the main loop", flush=True)
if EXIT_AFTER:
    import threading

    threading.Thread(target=lambda: (time.sleep(EXIT_AFTER), os._exit(EXIT_CODE)), daemon=True).start()
server.serve_forever()
