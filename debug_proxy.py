#!/usr/bin/env python3
"""Debug proxy: prints incoming HTTP request headers and body, then forwards to GLM API.

Usage:
    python3 debug_proxy.py
    # Then point Codex CLI base_url to http://localhost:18766

Environment variables:
    UPSTREAM  Target API base URL (default: https://open.bigmodel.cn/api/coding/paas/v4)
    PORT      Listen port (default: 18766)
"""

import http.client
import http.server
import json
import os
import sys
import urllib.parse

UPSTREAM = os.environ.get("UPSTREAM", "https://open.bigmodel.cn/api/coding/paas/v4")
PORT = int(os.environ.get("PORT", "18766"))


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _do(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length else None

        print(f"\n{'=' * 60}")
        print(f"METHOD: {self.command}")
        print(f"PATH:   {self.path}")
        print("HEADERS:")
        for k, v in self.headers.items():
            print(f"  {k}: {v}")
        if body:
            try:
                parsed = json.loads(body)
                print(f"BODY:\n{json.dumps(parsed, ensure_ascii=False, indent=2)}")
            except Exception:
                print(f"BODY:   {body[:500]!r}")
        print(f"{'=' * 60}\n")

        url = urllib.parse.urlparse(UPSTREAM)
        if url.scheme == "https":
            conn = http.client.HTTPSConnection(url.netloc, timeout=120)
        else:
            conn = http.client.HTTPConnection(url.netloc, timeout=120)

        try:
            forward_path = url.path + self.path
            headers = {k: v for k, v in self.headers.items()}
            headers["Host"] = url.netloc
            conn.request(self.command, forward_path, body=body, headers=headers)
            resp = conn.getresponse()

            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() not in ("transfer-encoding", "content-length"):
                    self.send_header(k, v)
            self.send_header("Content-Length", resp.getheader("Content-Length", "0"))
            self.end_headers()

            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                self.wfile.write(chunk)
        finally:
            conn.close()

    do_GET = _do
    do_POST = _do
    do_PUT = _do
    do_DELETE = _do
    do_OPTIONS = _do
    do_PATCH = _do


if __name__ == "__main__":
    server = http.server.HTTPServer(("", PORT), Handler)
    print(f"Debug proxy listening on http://localhost:{PORT}")
    print(f"Forwarding to: {UPSTREAM}")
    print(f"\nConfigure Codex CLI base_url to http://localhost:{PORT}")
    print("Then run a codex command to see the headers.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        sys.exit(0)
