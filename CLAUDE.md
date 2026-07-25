# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running

```bash
# Start proxy (foreground)
python3 proxy.py

# Start/stop via scripts
./scripts/start.sh   # background, logs to proxy.log
./scripts/stop.sh

# Health check
curl http://localhost:18765/health

# Run tests
python3 test_proxy.py
```

Environment variables: `GLM_API_KEY` (required), `PROXY_HOST` (default `127.0.0.1`;
set to `0.0.0.0` to expose on all interfaces), `PROXY_PORT` (default 18765),
`GLM_BASE_URL` (default `https://open.bigmodel.cn/api/coding/paas/v4`). Optional upstream
proxy: `GLM_HTTP_PROXY` / `GLM_HTTPS_PROXY` (fall back to `HTTP_PROXY` / `HTTPS_PROXY` /
`ALL_PROXY`), with `NO_PROXY` for bypass hosts; when unset, connections are direct.

Windows service scripts (NSSM): `install-service.bat`, `stop-service.bat`,
`restart-service.bat`, `remove-service.bat` (run as Administrator with `GLM_API_KEY` set).

## Architecture

Single-file proxy (`proxy.py`) that lets OpenAI Codex CLI use GLM models by converting between API formats.

### Request Flow

```
Codex CLI → Proxy (Responses API) → GLM API (Chat Completions API) → Proxy → Codex CLI (Responses API SSE events)
```

### URL Routing

Any path ending in `/responses` is handled as a Responses API request; other paths (e.g., `/models`) are forwarded to the upstream GLM API as-is.

### Key Conversion Logic

- `convert_responses_to_chat()`: Transforms Codex's Responses API requests into Chat Completions format. Maps model names, converts tool/function call formats, filters unsupported tools (web_search, code_interpreter, etc.).
- `_stream_response()` / `_convert_stream_line()`: Converts streaming Chat Completions chunks into Responses API SSE events. Uses buffered reading (4KB chunks) and helper methods (`_sse`, `_init_stream`, `_handle_tool_call_delta`, `_build_finish_events`, `_build_done_events`).
- `_handle_responses()`: Entry point for Responses API calls (both streaming and non-streaming).
- `_forward()`: Pass-through for non-responses endpoints (e.g., `/models`).

### Codex CLI Configuration

Users configure `~/.codex/config.toml` with `model_provider`, `model`, and `model_catalog_json` pointing to `~/.codex/models.json`. The example config is in `codex-config.example.toml`.

## Dependencies

Python standard library only. Uses `certifi` for SSL on macOS (no pip install required — certifi ships with Python).
