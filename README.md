# Codex GLM Proxy

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)

**English** | [中文](README_CN.md)

> **Note:** This project is based on [JichinX/codex-glm-proxy](https://github.com/JichinX/codex-glm-proxy/), copied and then debugged and optimized for better stability and compatibility.

A local proxy that enables **OpenAI Codex CLI** to work with **GLM (智谱 AI)** models by converting the Responses API format to Chat Completions format.

## Features

- Streaming responses — real-time SSE streaming
- Tool calling — supports `apply_patch`, `exec`, and all Codex tools
- Multi-turn conversations — maintains full conversation context
- Single file, no external dependencies

## Quick Start

### Prerequisites

- Python 3.8+
- [GLM API key](https://open.bigmodel.cn/)
- [OpenAI Codex CLI](https://github.com/openai/codex) installed

### 1. Start the proxy

```bash
git clone https://gitee.com/jidzhang/codex-glm-proxy.git
cd codex-glm-proxy
# GLM Coding Plan（Default）
# export GLM_BASE_URL=https://open.bigmodel.cn/api/coding/paas/v4
# or GLM API
# export GLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
# or ZAI Coding Plan
# export GLM_BASE_URL=https://api.z.ai/api/coding/paas/v4
# or ZAI API
# export GLM_BASE_URL=https://api.z.ai/api/paas/v4
export GLM_API_KEY="your_glm_api_key"
python3 proxy.py
# Proxy runs on http://localhost:18765
```

Or use the background script:

```bash
./scripts/start.sh    # start in background
./scripts/stop.sh     # stop
```

### 2. Configure Codex CLI

Create `~/.codex/config.toml`:

```toml
model_provider = "glm"
model = "glm-5.2"

model_catalog_json = "./models.json"

[model_providers.glm]
name = "GLM via Proxy"
base_url = "http://localhost:18765"
wire_api = "responses"
```

### 3. Set up model catalog

Copy the provided `models.json` to `~/.codex/models.json` (or create your own).

### 4. Test

```bash
mkdir test-codex && cd test-codex && git init
codex exec "Create a Python hello world program"
```

## Architecture

```
Codex CLI ──Responses API──▶ Proxy (localhost:18765) ──Chat Completions──▶ GLM API
```

The proxy converts bidirectionally:
- **Request:** Responses API → Chat Completions (model mapping, tool conversion)
- **Response:** Chat Completions SSE → Responses API SSE

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GLM_API_KEY` | *(none)* | GLM API key (required) |
| `GLM_BASE_URL` | `https://open.bigmodel.cn/api/coding/paas/v4` | GLM API endpoint |
| `PROXY_HOST` | `127.0.0.1` | Proxy listen address (set to `0.0.0.0` to expose on all interfaces) |
| `PROXY_PORT` | `18765` | Proxy listen port |
| `GLM_HTTP_PROXY` | *(none)* | Upstream HTTP proxy for GLM requests, e.g. `http://host:port` |
| `GLM_HTTPS_PROXY` | *(none)* | Upstream HTTPS proxy for GLM requests; falls back to `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` if unset |
| `NO_PROXY` | *(none)* | Comma-separated hosts that bypass the upstream proxy |

#### Upstream Proxy

If your machine reaches the internet through a corporate or local HTTP proxy, set
`GLM_HTTP_PROXY` / `GLM_HTTPS_PROXY` and the proxy will tunnel GLM requests through it
(via HTTP `CONNECT`). If unset, connections go direct — the default behavior is unchanged.

```bash
export GLM_HTTPS_PROXY=http://127.0.0.1:7890   # or GLM_HTTP_PROXY for http:// base URLs
export NO_PROXY=localhost,127.0.0.1             # optional: hosts to bypass
```

### Supported GLM Models

`glm-5.2` · `glm-5.1` · `glm-5-turbo` · `glm-5` · `glm-4.7`

GLM model names are passed through directly.

### OpenAI Model Mapping

When Codex CLI requests an OpenAI model, the proxy automatically substitutes the corresponding GLM model:

| Codex Request | Replaced With |
|---------------|---------------|
| `gpt-5.5` | `glm-5.2` |
| `gpt-5.4` | `glm-5.2` |
| `gpt-5.4-mini` | `glm-4.7` |
| `gpt-4.5` | `glm-5.2` |
| `gpt-4.1` | `glm-5.2` |
| `gpt-4.1-mini` | `glm-4.7` |
| `gpt-4o` | `glm-5.2` |
| `gpt-4o-mini` | `glm-4.7` |
| `gpt-4-turbo` | `glm-5.2` |
| `gpt-4` | `glm-5.2` |
| `o3` | `glm-5.2` |
| `o3-mini` | `glm-4.7` |
| `o1` | `glm-5.2` |
| `o1-mini` | `glm-4.7` |

Flagship-tier OpenAI models (including `o`-series flagships) map to `glm-5.2`;
economy/`*-mini` models map to `glm-4.7`. Unrecognized or missing model names
fall back to `glm-5.2` (the default).

## Management

```bash
./scripts/start.sh                        # start in background
curl http://localhost:18765/health         # health check
tail -f /tmp/codex-glm-proxy.log          # view logs
./scripts/stop.sh                         # stop
```

### Windows Service (NSSM)

On Windows you can run the proxy as a service via [NSSM](https://nssm.cc/) (run the scripts
as Administrator, in a shell that has `GLM_API_KEY` set):

```bat
install-service.bat     :: install and start the service (codex-glm-proxy)
stop-service.bat        :: stop the running service
restart-service.bat     :: stop then start
remove-service.bat      :: stop and uninstall the service
```

The installer injects `GLM_API_KEY` (and `GLM_BASE_URL` / `PROXY_PORT` if set) into the
service environment. Logs are written to `nssm.out.log` / `nssm.err.log`.

## Troubleshooting

**"Streaming complete, sent 0 chunks"**
Model name not recognized. Use a known model in config (e.g., `glm-5.2`).

**Codex loops / repeats actions**
Tool call history handling issue. Update to latest proxy version.

**Connection refused**
Proxy not running. Start with `python3 proxy.py` or `./scripts/start.sh`.

**SSL certificate error on macOS**
Install certifi: `pip install certifi`.

## License

[MIT](LICENSE)

## Reasoning Effort

Codex CLI supports reasoning effort levels (`low` / `medium` / `high` / `max`) to control how deeply the model thinks before responding.

GLM-5.2 honors all four levels. On a complex coding task (3 runs averaged), `max` produces ~44% longer reasoning content than `high`:

| Effort | reasoning_tokens | reason_len |
|--------|------------------|------------|
| high   | 2175             | 6274 chars |
| **max** | **2146**        | **9078 chars** |

**Recommendation:** `high` (the default in `models.json`) is sufficient for everyday coding; switch to `max` for complex multi-step reasoning.

The proxy transparently maps Codex's legacy `xhigh` to `max` — GLM silently ignores `xhigh`, so this ensures clients still sending it get the strongest tier on GLM-5.2.

For detailed test results and analysis, see [docs/reasoning-effort.md](docs/reasoning-effort.md).

To run your own tests:

```bash
python3 tests/test_reasoning_effort.py                  # glm-5.2 only (default)
python3 tests/test_reasoning_effort.py --all-models     # all GLM models
```
