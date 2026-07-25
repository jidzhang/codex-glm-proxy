#!/usr/bin/env python3
"""
OpenAI Responses API -> Chat Completions API Proxy

Converts the Responses API format to Chat Completions format
so that Codex CLI can work with GLM (智谱 AI).

Environment variables:
  GLM_API_KEY         GLM API key (required)
  GLM_BASE_URL        GLM API base URL (default: https://open.bigmodel.cn/api/coding/paas/v4)
  PROXY_HOST          Proxy listen address (default: 127.0.0.1; set to 0.0.0.0 to expose)
  PROXY_PORT          Proxy listen port (default: 18765)
  GLM_HTTP_PROXY      HTTP proxy for upstream requests (optional, e.g. http://host:port)
  GLM_HTTPS_PROXY     HTTPS proxy for upstream requests (optional)
                      Falls back to HTTP_PROXY / HTTPS_PROXY / ALL_PROXY if not set.
  NO_PROXY            Comma-separated hosts to bypass proxy.

License: MIT
"""

import json
import http.server
import socketserver
import http.client
import urllib.parse
import urllib.request
import ssl
import os
import sys
import logging
import threading
import select
import socket
import signal
import time
import base64

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(line_buffering=True)

PROXY_HOST = os.environ.get("PROXY_HOST", "127.0.0.1")
PROXY_PORT = int(os.environ.get("PROXY_PORT", "18765"))
MAX_BODY_SIZE = 50 * 1024 * 1024  # 50 MB

API_BASE = os.environ.get("GLM_BASE_URL", "https://open.bigmodel.cn/api/coding/paas/v4")
API_KEY = os.environ.get("GLM_API_KEY", "")
DEFAULT_USER_AGENT = "codex-tui/0.128.0"


def _get_proxy_url(scheme: str) -> str:
    upper = scheme.upper()
    for env_var in (f"GLM_{upper}_PROXY", upper + "_PROXY"):
        value = os.environ.get(env_var, os.environ.get(env_var.lower(), "")).strip()
        if value:
            return value
    return os.environ.get("ALL_PROXY", os.environ.get("all_proxy", "")).strip()


def _parse_proxy_url(proxy_url: str) -> tuple:
    """Parse http://user:pass@host:port/ into (host, port, username, password)."""
    parsed = urllib.parse.urlparse(proxy_url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"unsupported proxy scheme: {parsed.scheme}")
    if parsed.hostname is None:
        raise ValueError("proxy URL missing host")
    return (parsed.hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            parsed.username or "",
            parsed.password or "")


def _split_host_port(host_port: str, default_port: int) -> tuple:
    """Split 'host:port' or '[::1]:port' into (host, port)."""
    parsed = urllib.parse.urlparse(f"//{host_port}")
    return parsed.hostname or host_port, parsed.port or default_port


def _should_use_proxy(host: str) -> bool:
    """Return False if host matches NO_PROXY entries."""
    host = host.split(":")[0]
    return not urllib.request.proxy_bypass_environment(host)


def _proxy_auth_headers(proxy_url: str) -> dict:
    _, _, user, password = _parse_proxy_url(proxy_url)
    if not user:
        return {}
    credentials = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    return {"Proxy-Authorization": f"Basic {credentials}"}


# Proxy configuration is read on each connection attempt so that runtime
# changes to environment variables take effect (and tests stay isolated).
DEFAULT_MODEL = "glm-5.2"
MODEL_MAPPING = {
    # GLM models: pass through as-is
    "glm-5.2": "glm-5.2",
    "glm-5.1": "glm-5.1",
    "glm-5-turbo": "glm-5-turbo",
    "glm-5": "glm-5",
    "glm-4.7": "glm-4.7",
    # OpenAI GPT-5.x family
    "gpt-5.5": "glm-5.2",
    "gpt-5.4": "glm-5.2",
    "gpt-5.4-mini": "glm-4.7",
    # OpenAI GPT-4.x family
    "gpt-4.5": "glm-5.2",
    "gpt-4.1": "glm-5.2",
    "gpt-4.1-mini": "glm-4.7",
    "gpt-4o": "glm-5.2",
    "gpt-4o-mini": "glm-4.7",
    "gpt-4-turbo": "glm-5.2",
    "gpt-4": "glm-5.2",
    # OpenAI o-series reasoning models
    "o3": "glm-5.2",
    "o3-mini": "glm-4.7",
    "o1": "glm-5.2",
    "o1-mini": "glm-4.7",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("codex-proxy")


def _build_ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    return ssl.create_default_context()

SSL_CONTEXT = _build_ssl_context()


# ---------------------------------------------------------------------------
# Upstream proxy support (GLM_HTTP_PROXY / GLM_HTTPS_PROXY take priority)
# ---------------------------------------------------------------------------

# When an upstream proxy is configured, requests go through it via HTTP CONNECT
# tunneling (the TLS handshake stays end-to-end with the target, so the proxy
# cannot MITM). Because http.client re-issues CONNECT on a reused tunneled
# connection, such connections are NOT pooled -- each request builds a fresh
# tunnel (see ConnectionPool.release). Only http:// proxy URLs are supported.


# ---------------------------------------------------------------------------
# Connection pool — reuses TCP+TLS connections across concurrent Codex instances
# ---------------------------------------------------------------------------

class ConnectionPool:
    """Thread-safe pool of HTTP(S)Connection objects.

    Each connection is bound to one host.  On ``acquire()`` we try to
    recycle an idle connection; on ``release()`` the connection goes back
    into the pool for the next request.  Connections that fail are
    silently discarded so a fresh one is created next time.
    Stale connections (idle > 30 s) are also discarded.
    """

    MAX_IDLE_AGE = 30.0

    def __init__(self, max_idle: int = 8):
        self._max_idle = max_idle
        self._pool: list = []
        self._lock = threading.Lock()

    @staticmethod
    def _make_connection(host_port: str, default_port: int, use_ssl: bool, timeout: int):
        """Create a direct or proxied connection based on environment."""
        host, port = _split_host_port(host_port, default_port)
        proxy_url = _get_proxy_url("https" if use_ssl else "http")
        if proxy_url and _should_use_proxy(host):
            proxy_host, proxy_port, _, _ = _parse_proxy_url(proxy_url)
            # Log host:port only -- the raw proxy_url may carry credentials.
            log.debug("using proxy %s:%s for %s", proxy_host, proxy_port, host)
            headers = _proxy_auth_headers(proxy_url)
            conn = http.client.HTTPSConnection(proxy_host, proxy_port, timeout=timeout,
                                               context=SSL_CONTEXT)
            conn.set_tunnel(host, port, headers=headers)
            return conn
        if use_ssl:
            return http.client.HTTPSConnection(host, port=port, timeout=timeout, context=SSL_CONTEXT)
        return http.client.HTTPConnection(host, port=port, timeout=timeout)

    def acquire(self, host: str, timeout: int = 120, use_ssl: bool = True):
        now = time.time()
        with self._lock:
            # Iterate backwards so pop() doesn't skip elements
            i = len(self._pool) - 1
            while i >= 0:
                h, conn, t = self._pool[i]
                if h != host:
                    i -= 1
                    continue
                if (now - t) > self.MAX_IDLE_AGE:
                    try:
                        conn.close()
                    except Exception:
                        pass
                    self._pool.pop(i)
                    i -= 1
                    continue
                if self._conn_alive(conn):
                    self._pool.pop(i)
                    return conn
                try:
                    conn.close()
                except Exception:
                    pass
                self._pool.pop(i)
                i -= 1
        return self._make_connection(host, 443 if use_ssl else 80, use_ssl, timeout)

    @staticmethod
    def _conn_alive(conn) -> bool:
        sock = getattr(conn, "sock", None)
        if sock is None:
            return False
        try:
            err = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
            if err != 0:
                return False
            readable, _, _ = select.select([sock], [], [], 0)
            if readable:
                data = sock.recv(1, socket.MSG_PEEK)
                if len(data) == 0:
                    return False
            return True
        except Exception:
            return False

    def release(self, conn: http.client.HTTPSConnection) -> None:
        # Proxied tunnel connections (HTTP CONNECT via set_tunnel) are not
        # reused: http.client re-issues CONNECT on each request over a reused
        # connection, which the upstream proxy rejects or resets. Close them
        # instead of pooling so they don't linger as stale entries; direct
        # connections are pooled normally.
        if getattr(conn, "_tunnel_host", None):
            try:
                conn.close()
            except Exception:
                pass
            return
        with self._lock:
            if len(self._pool) >= self._max_idle:
                try:
                    conn.close()
                except Exception:
                    pass
                return
            host = conn.host
            self._pool.append((host, conn, time.time()))

    def close_all(self) -> None:
        with self._lock:
            for _, conn, _ in self._pool:
                try:
                    conn.close()
                except Exception:
                    pass
            self._pool.clear()


POOL = ConnectionPool()


# ---------------------------------------------------------------------------
# Format conversion: Responses API <-> Chat Completions
# ---------------------------------------------------------------------------

def convert_responses_to_chat(body: dict) -> dict:
    chat_body = {}

    model = body.get("model", DEFAULT_MODEL)
    if model not in MODEL_MAPPING:
        log.warning("unknown model %r, falling back to %s", model, DEFAULT_MODEL)
    chat_body["model"] = MODEL_MAPPING.get(model, DEFAULT_MODEL)

    messages = []

    instructions = body.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        messages.append({"role": "system", "content": instructions})

    if "input" in body:
        inp = body["input"]
        if isinstance(inp, str):
            messages.append({"role": "user", "content": inp})
        elif isinstance(inp, list):
            for item in inp:
                if not isinstance(item, dict) or "type" not in item:
                    continue
                if item["type"] == "message":
                    role = item.get("role", "user")
                    if role == "developer":
                        role = "system"
                    content = item.get("content", [])
                    if isinstance(content, list):
                        text_parts = []
                        for c in content:
                            if isinstance(c, dict):
                                ctype = c.get("type")
                                if ctype in ("input_text", "output_text"):
                                    text_parts.append(c.get("text", ""))
                                elif ctype:
                                    log.warning("Dropping unsupported content type '%s'", ctype)
                        if text_parts:
                            messages.append({"role": role, "content": " ".join(text_parts)})
                    elif isinstance(content, str):
                        messages.append({"role": role, "content": content})

                elif item["type"] == "function_call":
                    messages.append({
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": item.get("call_id", item.get("id", "")),
                            "type": "function",
                            "function": {
                                "name": item.get("name", ""),
                                "arguments": item.get("arguments", "{}"),
                            },
                        }],
                    })

                elif item["type"] == "function_call_output":
                    messages.append({
                        "role": "tool",
                        "tool_call_id": item.get("call_id", ""),
                        "content": item.get("output", ""),
                    })

        elif isinstance(inp, dict):
            if "messages" in inp:
                for msg in inp["messages"]:
                    role = msg.get("role", "user")
                    if role == "developer":
                        role = "system"
                    messages.append({"role": role, "content": msg.get("content", "")})
            elif "content" in inp:
                messages.append({"role": "user", "content": inp["content"]})

    chat_body["messages"] = messages

    for key in ["temperature", "top_p", "max_tokens", "stream", "frequency_penalty", "presence_penalty", "stop"]:
        if key in body:
            chat_body[key] = body[key]

    if "max_output_tokens" in body:
        chat_body["max_tokens"] = body["max_output_tokens"]

    if "tools" in body:
        chat_tools = []
        for tool in body["tools"]:
            if not isinstance(tool, dict):
                continue
            tool_type = tool.get("type", "")
            if tool_type in ["web_search", "code_interpreter", "file_search", "computer_use"]:
                log.info("Skipping unsupported tool type: %s", tool_type)
                continue
            if tool_type == "function":
                if "function" in tool:
                    chat_tools.append(tool)
                else:
                    chat_tool = {"type": "function", "function": {}}
                    if "name" in tool:
                        chat_tool["function"]["name"] = tool["name"]
                    if "description" in tool:
                        chat_tool["function"]["description"] = tool["description"]
                    if "parameters" in tool:
                        chat_tool["function"]["parameters"] = tool["parameters"]
                    chat_tools.append(chat_tool)
            elif "function" in tool:
                chat_tools.append(tool)
        if chat_tools:
            chat_body["tools"] = chat_tools

    if "tool_choice" in body:
        tc = body["tool_choice"]
        # Responses API nests name at top level; Chat Completions under "function".
        if isinstance(tc, dict) and tc.get("type") == "function" and "name" in tc:
            chat_body["tool_choice"] = {"type": "function", "function": {"name": tc["name"]}}
        else:
            chat_body["tool_choice"] = tc

    reasoning = body.get("reasoning")
    if reasoning:
        # GLM-5.2+ supports a `max` tier; Codex's `xhigh` is the closest
        # semantic match, so map it through. Earlier GLM versions silently
        # ignored xhigh, so this is a strict upgrade for 5.2 users.
        if isinstance(reasoning, dict) and reasoning.get("effort") == "xhigh":
            reasoning = {**reasoning, "effort": "max"}
        chat_body["reasoning"] = reasoning

    return chat_body


def _normalize_response_id(value: str) -> str:
    if not value or value.startswith("resp_"):
        return value
    return f"resp_{value}"


def convert_chat_to_responses(resp: dict) -> dict:
    outputs = []
    status = "completed"
    incomplete_details = None

    if "choices" in resp:
        choices = resp["choices"]
        multi = len(choices) > 1
        for idx, choice in enumerate(choices):
            msg = choice.get("message", {})
            finish = choice.get("finish_reason")

            if finish == "length":
                status = "incomplete"
                incomplete_details = {"reason": "max_output_tokens"}
            elif finish == "content_filter":
                status = "incomplete"
                incomplete_details = {"reason": "content_filter"}

            content = msg.get("content")
            if content and content.strip():
                rid = resp.get("id", "")
                if not rid.startswith("msg_"):
                    rid = f"msg_{rid}"
                # Disambiguate ids when upstream returns multiple choices
                # (n>1); otherwise Responses API clients see duplicates.
                if multi:
                    rid = f"{rid}_{idx}"
                outputs.append({
                    "type": "message",
                    "id": rid,
                    "status": "completed",
                    "role": msg.get("role", "assistant"),
                    "content": [{"type": "output_text", "text": content}],
                })
            for tc in msg.get("tool_calls", []):
                outputs.append({
                    "type": "function_call",
                    "id": f"fc_{tc.get('id', '')}",
                    "call_id": tc.get("id", ""),
                    "name": tc.get("function", {}).get("name", ""),
                    "arguments": tc.get("function", {}).get("arguments", "{}"),
                    "status": "completed",
                })

    result = {
        "id": _normalize_response_id(resp.get("id", "")),
        "object": "response",
        # Both keys emitted: Responses API spec uses created_at, but some
        # clients still read the legacy "created" field.
        "created": resp.get("created", 0),
        "created_at": resp.get("created", 0),
        "model": resp.get("model", ""),
        "output": outputs,
        "usage": resp.get("usage", {}),
        "status": status,
    }
    if incomplete_details:
        result["incomplete_details"] = incomplete_details
    return result


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        log.info(fmt, *args)

    def do_GET(self):
        if self.path == "/health":
            self._check_health()
        elif self.path.endswith("/models"):
            self._forward("GET", self.path)
        else:
            self._json(404, {"error": "not found"})

    def _check_health(self):
        try:
            url_parts = urllib.parse.urlparse(API_BASE)
            host = url_parts.netloc
            base_path = url_parts.path.rstrip("/")
            conn = POOL.acquire(host, timeout=10, use_ssl=(url_parts.scheme == "https"))
            released = False
            try:
                conn.request("GET", f"{base_path}/models",
                             headers={"Authorization": f"Bearer {API_KEY}"})
                upstream = conn.getresponse()
                body = upstream.read(MAX_BODY_SIZE + 1)
                if upstream.status < 400:
                    if not getattr(upstream, "will_close", True):
                        POOL.release(conn)
                        released = True
                    self._json(200, {"status": "ok", "upstream": "reachable"})
                else:
                    if not getattr(upstream, "will_close", True):
                        POOL.release(conn)
                        released = True
                    self._json(503, {"status": "degraded", "upstream": f"http {upstream.status}"})
            finally:
                if not released:
                    try:
                        conn.close()
                    except Exception:
                        pass
        except Exception as e:
            log.warning("Health check failed: %s", e)
            self._json(503, {"status": "degraded", "upstream": str(e)})

    def do_POST(self):
        if self.path.endswith("/responses"):
            self._handle_responses()
        else:
            self._forward("POST", self.path)

    def _handle_responses(self):
        try:
            raw_length = self.headers.get("Content-Length", "0")
            try:
                length = int(raw_length)
            except (ValueError, TypeError):
                self._json(400, {"error": "invalid Content-Length header"})
                return
            if length > MAX_BODY_SIZE:
                self._json(413, {"error": "request body too large"})
                return
            raw_body = self.rfile.read(length)
            try:
                body = json.loads(raw_body)
            except (json.JSONDecodeError, ValueError) as e:
                self._json(400, {"error": f"invalid JSON: {e}"})
                return
            if not isinstance(body, dict):
                self._json(400, {"error": "request body must be a JSON object"})
                return
            is_stream = body.get("stream", False)

            chat_body = convert_responses_to_chat(body)
            log.info("stream=%s model=%s -> %s", is_stream, body.get("model"), chat_body.get("model"))

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {API_KEY}",
                "Accept": "text/event-stream" if is_stream else "application/json",
                "User-Agent": self.headers.get("User-Agent", DEFAULT_USER_AGENT),
            }

            url_parts = urllib.parse.urlparse(API_BASE)
            host = url_parts.netloc
            base_path = url_parts.path.rstrip("/")
            use_ssl = (url_parts.scheme == "https")

            # One attempt normally; two if reasoning is present (so we can
            # retry once without it on a 400 that mentions "reasoning").
            max_attempts = 2 if "reasoning" in chat_body else 1
            for attempt in range(max_attempts):
                conn = POOL.acquire(host, timeout=120, use_ssl=use_ssl)
                released = False
                try:
                    conn, upstream = self._upstream_request(conn, host, base_path, chat_body, headers, use_ssl=use_ssl)
                    log.info("upstream status=%d content-type=%s", upstream.status, upstream.getheader("Content-Type"))

                    if upstream.status >= 400:
                        detail = self._read_upstream_error(upstream)
                        can_retry = (attempt == 0 and upstream.status == 400
                                     and "reasoning" in chat_body
                                     and "reasoning" in json.dumps(detail).lower())
                        if can_retry:
                            log.warning("Upstream rejected reasoning, retrying without it")
                            del chat_body["reasoning"]
                            continue
                        self._json(upstream.status, {"error": f"upstream {upstream.status}", "detail": detail})
                        return

                    if is_stream:
                        self._stream_response(upstream)
                    else:
                        resp_body = upstream.read(MAX_BODY_SIZE + 1)
                        if len(resp_body) > MAX_BODY_SIZE:
                            self._json(502, {"error": "upstream response too large"})
                            return
                        converted = convert_chat_to_responses(json.loads(resp_body))
                        self._json(200, converted)
                        if not getattr(upstream, "will_close", True):
                            POOL.release(conn)
                            released = True
                    return
                finally:
                    if not released:
                        try:
                            conn.close()
                        except Exception:
                            pass

        except Exception as e:
            log.error("proxy error: %s", e)
            self._json(500, {"error": str(e)})

    @staticmethod
    def _read_upstream_error(upstream) -> dict:
        """Read and cap upstream error body; return parsed JSON dict (or {'raw': ...})."""
        error_body = upstream.read(MAX_BODY_SIZE + 1)
        if len(error_body) > MAX_BODY_SIZE:
            log.error("upstream error body too large")
            error_body = error_body[:MAX_BODY_SIZE]
        log.error("upstream error: %d %s", upstream.status, error_body[:500])
        try:
            return json.loads(error_body)
        except (json.JSONDecodeError, ValueError):
            return {"raw": error_body.decode("utf-8", errors="replace")}

    # -- streaming ----------------------------------------------------------

    def _stream_response(self, upstream):
        self._seq = 0
        self._item_id = None
        self._response_id = None
        self._created = None
        self._model = None
        self._full_content = ""
        self._content_part_id = None
        self._tool_calls = {}
        self._finish_emitted = False
        self._done_emitted = False

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        buf = bytearray()
        chunk_count = 0
        raw_line_count = 0
        try:
            while True:
                data = upstream.read(4096)
                if not data:
                    break
                buf.extend(data)
                while True:
                    try:
                        idx = buf.index(0x0a)  # '\n'
                    except ValueError:
                        break
                    line = bytes(buf[:idx])
                    del buf[:idx + 1]
                    line = line.strip()
                    raw_line_count += 1
                    if not line:
                        continue
                    for converted in self._convert_stream_line(line):
                        self.wfile.write(converted)
                        self.wfile.flush()
                        chunk_count += 1
            if buf:
                raw_line_count += 1
                for converted in self._convert_stream_line(bytes(buf).strip()):
                    self.wfile.write(converted)
                    self.wfile.flush()
                    chunk_count += 1
            # If upstream closed without [DONE], emit completion ourselves
            if self._item_id and not self._done_emitted:
                if not self._finish_emitted:
                    self._finish_emitted = True
                    for converted in self._build_finish_events():
                        self.wfile.write(converted)
                        self.wfile.flush()
                        chunk_count += 1
                for converted in self._build_done_events():
                    self.wfile.write(converted)
                    self.wfile.flush()
                    chunk_count += 1
            log.info("streaming done, %d raw lines, %d converted chunks", raw_line_count, chunk_count)
        except (BrokenPipeError, ConnectionResetError) as e:
            log.warning("Client disconnected during stream: %s", e)
            try:
                upstream.close()
            except Exception:
                pass
        except Exception as e:
            log.error("stream error: %s", e)
            # Headers are already sent; without a terminal event the client
            # would block waiting for one. Emit finish + [DONE] if we haven't.
            if not self._done_emitted:
                try:
                    if self._item_id and not self._finish_emitted:
                        self._finish_emitted = True
                        for evt in self._build_finish_events():
                            self.wfile.write(evt)
                    for evt in self._build_done_events():
                        self.wfile.write(evt)
                    self.wfile.flush()
                except Exception:
                    pass
        finally:
            try:
                upstream.close()
            except Exception:
                pass
            self.close_connection = True

    def _next_seq(self):
        s = self._seq
        self._seq += 1
        return s

    def _convert_stream_line(self, line: bytes) -> list:
        # Defensive: tests may call this without going through _stream_response.
        self._finish_emitted = getattr(self, "_finish_emitted", False)
        self._done_emitted = getattr(self, "_done_emitted", False)

        if not line.startswith(b"data:"):
            return [line + b"\n"]

        # Tolerate "data:" with or without a trailing space (SSE spec
        # makes the space optional; GLM sends it but other上游 may not).
        data = line[5:].strip()
        if data == b"[DONE]":
            self._done_emitted = True
            results = []
            # If upstream skipped finish_reason, the finish events
            # (output_text.done / content_part.done / output_item.done) were
            # never emitted; emit them now before the terminal completed event.
            if self._item_id and not self._finish_emitted:
                self._finish_emitted = True
                results.extend(self._build_finish_events())
            results.extend(self._build_done_events())
            return results

        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            return [line + b"\n"]

        results = []

        if not self._item_id:
            results.extend(self._init_stream(chunk))

        if "choices" not in chunk:
            return results

        for choice in chunk["choices"]:
            delta = choice.get("delta", {})
            content = delta.get("content", "")
            finish = choice.get("finish_reason")

            if content:
                self._full_content += content
                evt = {
                    "type": "response.output_text.delta",
                    "sequence_number": self._next_seq(),
                    "output_index": 0, "content_index": 0,
                    "item_id": self._item_id, "delta": content,
                    "logprobs": [],
                }
                results.append(self._sse("response.output_text.delta", evt))

            for tc in delta.get("tool_calls", []):
                results.extend(self._handle_tool_call_delta(tc))

            if finish and not self._finish_emitted:
                self._finish_emitted = True
                results.extend(self._build_finish_events())

        return results

    # -- SSE helpers --------------------------------------------------------

    def _sse(self, event: str, data: dict) -> bytes:
        return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()

    def _init_stream(self, chunk: dict) -> list:
        self._response_id = chunk.get("id", "")
        if not self._response_id.startswith("resp_"):
            self._response_id = f"resp_{self._response_id}"
        self._created = chunk.get("created", 0)
        self._model = chunk.get("model", "")
        self._item_id = f"msg_{self._response_id}"
        self._content_part_id = f"cp_{self._response_id}"

        results = []
        results.append(self._sse("response.created", {
            "type": "response.created", "sequence_number": self._next_seq(),
            "response": {
                "id": self._response_id, "object": "response",
                "created_at": self._created, "model": self._model,
                "output": [], "status": "in_progress",
            },
        }))
        results.append(self._sse("response.output_item.added", {
            "type": "response.output_item.added", "sequence_number": self._next_seq(),
            "output_index": 0,
            "item": {"type": "message", "id": self._item_id,
                     "status": "in_progress", "role": "assistant", "content": []},
        }))
        results.append(self._sse("response.content_part.added", {
            "type": "response.content_part.added", "sequence_number": self._next_seq(),
            "output_index": 0, "content_index": 0, "item_id": self._item_id,
            "content_part": {"type": "output_text", "text": ""},
        }))
        return results

    def _handle_tool_call_delta(self, tc: dict) -> list:
        results = []
        tc_idx = tc.get("index", 0)
        tc_id = tc.get("id", "")
        tc_fn = tc.get("function", {})

        entry = self._tool_calls.get(tc_idx)
        if entry is None:
            entry = {"id": tc_id, "name": tc_fn.get("name", ""), "arguments": "",
                     "added_emitted": False}
            self._tool_calls[tc_idx] = entry
        elif tc_id and not entry["id"]:
            # Update id if it arrives in a later chunk
            entry["id"] = tc_id

        # Defer output_item.added until we have a real id; otherwise the
        # first added would carry an empty id while later deltas use the
        # real one, and clients cannot correlate them by item_id.
        if entry["id"] and not entry["added_emitted"]:
            entry["added_emitted"] = True
            results.append(self._sse("response.output_item.added", {
                "type": "response.output_item.added",
                "sequence_number": self._next_seq(),
                "output_index": tc_idx + 1,
                "item": {
                    "type": "function_call", "id": f"fc_{entry['id']}",
                    "call_id": entry["id"], "name": entry["name"],
                    "arguments": "", "status": "in_progress",
                },
            }))

        tc_args = tc_fn.get("arguments", "")
        if tc_args:
            entry["arguments"] += tc_args
            if entry["id"]:
                evt_id = entry["id"]
                results.append(self._sse("response.function_call_arguments.delta", {
                    "type": "response.function_call_arguments.delta",
                    "sequence_number": self._next_seq(),
                    "output_index": tc_idx + 1, "item_id": f"fc_{evt_id}",
                    "delta": tc_args, "call_id": evt_id,
                }))
        return results

    def _build_finish_events(self) -> list:
        results = []
        if self._tool_calls:
            for tc_idx, tc in self._tool_calls.items():
                fc_id = f"fc_{tc['id']}"
                results.append(self._sse("response.function_call_arguments.done", {
                    "type": "response.function_call_arguments.done",
                    "sequence_number": self._next_seq(),
                    "output_index": tc_idx + 1, "item_id": fc_id,
                    "arguments": tc["arguments"], "call_id": tc["id"],
                }))
                results.append(self._sse("response.output_item.done", {
                    "type": "response.output_item.done",
                    "sequence_number": self._next_seq(),
                    "output_index": tc_idx + 1,
                    "item": {
                        "type": "function_call", "id": fc_id,
                        "call_id": tc["id"], "name": tc["name"],
                        "arguments": tc["arguments"], "status": "completed",
                    },
                }))

        # Always emit text done events if we emitted a content_part.added
        if self._item_id:
            if self._full_content:
                results.append(self._sse("response.output_text.done", {
                    "type": "response.output_text.done",
                    "sequence_number": self._next_seq(),
                    "output_index": 0, "content_index": 0,
                    "item_id": self._item_id, "text": self._full_content,
                }))
            results.append(self._sse("response.content_part.done", {
                "type": "response.content_part.done",
                "sequence_number": self._next_seq(),
                "output_index": 0, "content_index": 0, "item_id": self._item_id,
                "content_part": {"type": "output_text", "text": self._full_content},
            }))
            results.append(self._sse("response.output_item.done", {
                "type": "response.output_item.done",
                "sequence_number": self._next_seq(),
                "output_index": 0,
                "item": {
                    "type": "message", "id": self._item_id,
                    "status": "completed", "role": "assistant",
                    "content": [{"type": "output_text", "text": self._full_content}],
                },
            }))
        return results

    def _build_done_events(self) -> list:
        results = []
        outputs = []
        # Message output — always present since _init_stream always fires
        if self._item_id:
            outputs.append({
                "type": "message", "id": self._item_id,
                "status": "completed", "role": "assistant",
                "content": [{"type": "output_text", "text": self._full_content}],
            })
        # Tool-call outputs — after message, matching event emission order
        for idx in sorted(self._tool_calls.keys()):
            tc = self._tool_calls[idx]
            outputs.append({
                "type": "function_call", "id": f"fc_{tc['id']}",
                "call_id": tc["id"], "name": tc["name"],
                "arguments": tc["arguments"], "status": "completed",
            })
        if self._response_id:
            results.append(self._sse("response.completed", {
                "type": "response.completed",
                "sequence_number": self._next_seq(),
                "response": {
                    "id": self._response_id, "object": "response",
                    "created_at": self._created or 0, "model": self._model or "",
                    "output": outputs, "status": "completed",
                },
            }))
        results.append(b"data: [DONE]\n\n")
        return results

    # -- direct forwarding --------------------------------------------------

    def _forward(self, method: str, path: str):
        """Forward a request to the upstream GLM API as-is."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > MAX_BODY_SIZE:
                self._json(413, {"error": "request body too large"})
                return
            body = self.rfile.read(length) if length > 0 else b''

            url_parts = urllib.parse.urlparse(API_BASE)
            host = url_parts.netloc
            base_path = url_parts.path.rstrip("/")

            # Strip any leading /v4 prefix from the incoming path before
            # appending to the base path, matching the original routing.
            forward_path = path
            if forward_path.startswith("/v4/"):
                forward_path = forward_path[3:]  # e.g. /models

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {API_KEY}",
                "User-Agent": self.headers.get("User-Agent", DEFAULT_USER_AGENT),
            }
            conn = POOL.acquire(host, timeout=30, use_ssl=(url_parts.scheme == "https"))
            released = False
            try:
                conn.request(method, f"{base_path}{forward_path}",
                             body=body, headers=headers)
                upstream = conn.getresponse()
                resp_body = upstream.read(MAX_BODY_SIZE + 1)
                if len(resp_body) > MAX_BODY_SIZE:
                    self._json(502, {"error": "upstream response too large"})
                    return
                self.send_response(upstream.status)
                self.send_header("Content-Type",
                                 upstream.getheader("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(resp_body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(resp_body)
                if not getattr(upstream, "will_close", True):
                    POOL.release(conn)
                    released = True
            finally:
                if not released:
                    try:
                        conn.close()
                    except Exception:
                        pass

        except Exception as e:
            log.error("forward error: %s", e)
            self._json(500, {"error": str(e)})

    def _upstream_request(self, conn, host: str, base_path: str, chat_body: dict, headers: dict, use_ssl: bool, is_pooled: bool = True):
        """Send request to upstream; retry once with a fresh connection on failure.
        Returns (connection, upstream_response)."""
        try:
            conn.request("POST", f"{base_path}/chat/completions",
                         body=json.dumps(chat_body).encode(), headers=headers)
            return conn, conn.getresponse()
        except (socket.timeout, ConnectionError, http.client.RemoteDisconnected, http.client.BadStatusLine) as e:
            if is_pooled:
                log.warning("Pooled connection failed (%s), retrying with fresh connection", type(e).__name__)
                try:
                    conn.close()
                except Exception:
                    pass
                conn = POOL._make_connection(host, 443 if use_ssl else 80, use_ssl, 120)
                conn.request("POST", f"{base_path}/chat/completions",
                             body=json.dumps(chat_body).encode(), headers=headers)
                return conn, conn.getresponse()
            raise

    # -- helpers ------------------------------------------------------------

    def _json(self, code: int, data: dict):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if not API_KEY:
        log.error("GLM_API_KEY not set.")
        sys.exit(1)

    log.info("Codex GLM proxy on port %d", PROXY_PORT)
    log.info("Press Ctrl+C to stop")

    with ThreadedHTTPServer((PROXY_HOST, PROXY_PORT), ProxyHandler) as httpd:
        def _shutdown(*_):
            log.info("Shutting down...")
            # shutdown() must be called from a different thread than
            # serve_forever(), otherwise it deadlocks.
            threading.Thread(target=httpd.shutdown, daemon=True).start()

        signal.signal(signal.SIGTERM, _shutdown)
        signal.signal(signal.SIGINT, _shutdown)

        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            log.info("Shutting down...")
        finally:
            POOL.close_all()


if __name__ == "__main__":
    main()
