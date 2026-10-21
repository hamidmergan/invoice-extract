import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, List

import pytest


def is_vision(body: dict) -> bool:
    """True if any message part is an image."""
    for message in body.get("messages", []):
        content = message.get("content")
        if isinstance(content, list) and any(p.get("type") == "image_url" for p in content):
            return True
    return False


class FakeServer:
    """OpenAI-compatible chat endpoint. `reply` turns a request body into the assistant content."""

    def __init__(self):
        self.requests: List[dict] = []
        self.reply: Callable[[dict], str] = lambda body: "{}"
        self.status = 200
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length))
                outer.requests.append({"headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
                if outer.status != 200:
                    data = b'{"error": {"message": "fake failure"}}'
                    self.send_response(outer.status)
                else:
                    payload = {
                        "id": "x",
                        "object": "chat.completion",
                        "created": 0,
                        "model": body["model"],
                        "choices": [{
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": outer.reply(body)},
                        }],
                    }
                    data = json.dumps(payload).encode()
                    self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)  # requests run in parallel, like a real server
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_port}/v1"

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    s = FakeServer()
    yield s
    s.stop()


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in list(os.environ):
        if name.startswith("INVOICE_EXTRACT_") or name == "OPENAI_API_KEY":
            monkeypatch.delenv(name)
