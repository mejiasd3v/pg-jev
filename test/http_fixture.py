import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@dataclass
class Response:
    status: int = 200
    body: bytes = b""
    headers: dict = field(default_factory=dict)
    chunks: list = field(default_factory=list)
    header_delay: float = 0
    close_early: bool = False

    @classmethod
    def json(cls, value, status=200, headers=None):
        return cls(
            status=status,
            body=json.dumps(value).encode(),
            headers={"Content-Type": "application/json", **(headers or {})},
        )


class CountingServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, _request, _client_address):
        pass

    def get_request(self):
        request, address = super().get_request()
        self.connection_count += 1
        return request, address


class HttpFixture:
    def __init__(self, responder):
        self.responder = responder
        self.requests = []
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _respond(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                fixture.requests.append({
                    "method": self.command,
                    "path": self.path,
                    "headers": dict(self.headers),
                    "body": body,
                })
                response = fixture.responder(fixture.requests[-1])
                if response.header_delay:
                    time.sleep(response.header_delay)
                self.send_response(response.status)
                headers = dict(response.headers)
                if not response.chunks and "Content-Length" not in headers:
                    headers["Content-Length"] = str(len(response.body))
                for name, value in headers.items():
                    self.send_header(name, value)
                self.end_headers()
                if response.close_early:
                    self.close_connection = True
                    return
                if response.chunks:
                    for delay, chunk in response.chunks:
                        time.sleep(delay)
                        try:
                            self.wfile.write(chunk)
                            self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError):
                            break
                    if "Content-Length" not in headers:
                        self.close_connection = True
                else:
                    try:
                        self.wfile.write(response.body)
                    except (BrokenPipeError, ConnectionResetError):
                        pass

            do_GET = _respond
            do_POST = _respond

            def log_message(self, *_args):
                pass

        self.server = CountingServer(("127.0.0.1", 0), Handler)
        self.server.connection_count = 0
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self):
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    @property
    def connection_count(self):
        return self.server.connection_count

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
