import gzip
import json
import os
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

HTML = """<!doctype html><html><head><title>Payment demo</title></head><body>
<h1>Checkout</h1><form id="payment">
<label>Full name <input id="customer" name="customer" data-testid="customer"></label>
<label>Password <input type="password" id="password" value="INITIAL_PASSWORD"></label>
<label>Card number <input name="card_number" autocomplete="cc-number"></label>
<label>CVC <input name="cvc" autocomplete="cc-csc"></label>
<label>Private note <input id="private" value="PRIVATE_VALUE"></label>
<div class="private">PRIVATE_TEXT</div>
<button type="submit" data-testid="pay">Pay now</button></form>
<a href="/popup" target="_blank">Open receipt</a>
<iframe title="Details" src="/frame"></iframe><div id="result" role="status"></div>
<script>
document.querySelector('form').onsubmit = async event => {
 event.preventDefault();
 const response = await fetch('/api/pay', {method:'POST', headers:{'Content-Type':'application/json',
 'Authorization':'Bearer AUTH_SECRET','X-Private':'HEADER_SECRET'}, body:JSON.stringify({customer:document.querySelector('#customer').value,
 password:document.querySelector('#password').value, card_number:document.querySelector('[name=card_number]').value, cvc:document.querySelector('[name=cvc]').value})});
 const data = await response.json();
 document.querySelector('#result').textContent = data.result;
 history.pushState({}, '', '/success');
 console.error('Demo console error');
 setTimeout(() => { throw new Error('Demo page error'); }, 0);
};
</script></body></html>"""


@pytest.fixture
def chromium_path():
    return os.environ.get("RECORDER_CHROMIUM") or shutil.which("chromium") or None


@pytest.fixture
def demo_server():
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def respond(self, body, kind="text/html", status=200, headers=None):
            data = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/":
                self.respond("", status=302, headers={"Location": "/checkout"})
            elif self.path == "/checkout":
                self.respond(HTML)
            elif self.path == "/frame":
                self.respond(
                    "<button onclick=\"this.textContent='Selected'\">Choose plan</button>"
                )
            elif self.path == "/popup":
                self.respond("<h1>Receipt</h1>")
            elif self.path in {"/api/chunked", "/api/no-type"}:
                data = json.dumps(
                    {"result": "Chunked ready", "token": "CHUNK_SECRET"}
                ).encode()
                self.send_response(200)
                if self.path != "/api/no-type":
                    self.send_header("Content-Type", "application/json")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                for part in (data[:10], data[10:]):
                    self.wfile.write(f"{len(part):x}\r\n".encode() + part + b"\r\n")
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            elif self.path in {"/api/gzip", "/api/gzip-large"}:
                body = {
                    "result": "Compressed ready"
                    if self.path == "/api/gzip"
                    else "X" * 10000
                }
                data = gzip.compress(json.dumps(body).encode())
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif self.path == "/api/large":
                self.respond(
                    json.dumps(
                        {"result": "Large result " * 800, "api_key": "BODY_SECRET"}
                    ),
                    "application/json",
                )
            elif self.path == "/api/binary":
                self.respond("BINARY_SECRET", "image/svg+xml")
            else:
                self.respond("<h1>Not found</h1>", status=404)

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.respond(
                json.dumps(
                    {"result": "Payment accepted", "access_token": "RESPONSE_SECRET"}
                ),
                "application/json",
                headers={"Set-Cookie": "session=COOKIE_SECRET"},
            )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join()
