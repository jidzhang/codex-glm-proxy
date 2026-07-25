# Repository Guidelines

## Project Structure & Module Organization

```
codex-glm-proxy/
├── proxy.py                  # Single-file proxy (all logic)
├── test_proxy.py             # Test suite (171 tests)
├── tests/                    # Additional test fixtures / helpers
├── debug_proxy.py            # Debugging entry point (verbose logging)
├── docs/                     # Design notes and review reports
├── models.json               # Codex CLI model catalog
├── codex-config.example.toml # Example Codex config
├── scripts/start.sh, stop.sh # Background management (macOS/Linux)
├── install-service.bat       # NSSM service install (Windows)
├── stop-service.bat          # NSSM service stop
├── restart-service.bat       # NSSM service restart
├── remove-service.bat        # NSSM service remove
├── README.md / README_CN.md  # User docs
└── LICENSE                   # MIT
```

Everything lives in `proxy.py` — format converters, HTTP server, connection pool, entry point. No sub-packages.

## Build, Test, and Development Commands

```bash
GLM_API_KEY="key" python3 proxy.py   # Run proxy (foreground)
./scripts/start.sh                    # Start in background
./scripts/stop.sh                     # Stop
python3 test_proxy.py                 # Run tests (no API key needed)
curl http://localhost:18765/health    # Health check
```

No build step. Standard library only (`http.server`, `http.client`, `ssl`, `json`, `threading`). `certifi` is optional.

## Coding Style & Naming Conventions

- **Python 3.8+**, 4-space indent.
- `snake_case` functions/variables, `PascalCase` classes, `UPPER_SNAKE` module constants.
- `_` prefix for private helpers (`_sse`, `_build_finish_events`). No prefix for public API.
- No linter configured — follow existing style.

## Testing Guidelines

- **Framework:** `unittest` (stdlib, no `pytest`).
- **Test classes:** `TestModelMapping`, `TestConvertResponsesToChat` (30+ cases), `TestConvertChatToResponses`, `TestStreamingConversion`, `TestConnectionPool`, `TestHTTPIntegration` (full round-trip with mock upstream).
- **Naming:** `test_<what>_<condition>` (e.g., `test_empty_instructions_ignored`).
- New conversion paths must have corresponding tests. `test_proxy.py` is generated — append new classes at the end.

## Commit & Pull Request Guidelines

- **Messages:** short imperative English (e.g., `Add connection pool for multi-instance support`).
- **PRs:** clear description, reference issues, ensure `python3 test_proxy.py` passes.
- **Do not split** `proxy.py` into a package without prior discussion — single-file is intentional.

## Architecture Notes

```
Codex CLI → [Responses API] → proxy :18765 → [Chat Completions] → GLM API
```

Key functions in `proxy.py`:
- `convert_responses_to_chat()` / `convert_chat_to_responses()` — bidirectional format conversion.
- `ConnectionPool` — thread-safe connection reuse for concurrent Codex instances.
- `ProxyHandler._stream_response()` — buffered SSE conversion (4 KB reads).
- `ProxyHandler._forward()` — pass-through for `/models` and other non-responses endpoints.
