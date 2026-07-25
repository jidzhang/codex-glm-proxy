# Codex GLM 代理

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)

[English](README.md) | **中文**

> **声明：** 本项目基于 [JichinX/codex-glm-proxy](https://github.com/JichinX/codex-glm-proxy/) 复制，并在此基础上进行了调试和优化，以提升稳定性和兼容性。

本地代理，将 OpenAI Codex CLI 的 Responses API 格式转换为 Chat Completions 格式，使 Codex 能够使用 **GLM（智谱 AI）** 模型。

## 特性

- 流式响应 — 实时 SSE 流式输出
- 工具调用 — 支持 `apply_patch`、`exec` 等所有 Codex 工具
- 多轮对话 — 完整保持对话上下文
- 单文件，无外部依赖

## 快速开始

### 前置要求

- Python 3.8+
- [GLM API 密钥](https://open.bigmodel.cn/)
- 已安装 [OpenAI Codex CLI](https://github.com/openai/codex)

### 1. 启动代理

```bash
git clone https://gitee.com/jidzhang/codex-glm-proxy.git
cd codex-glm-proxy
# GLM Coding Plan（默认使用Coding Plan，可以不填写）
# export GLM_BASE_URL=https://open.bigmodel.cn/api/coding/paas/v4
# GLM 通用API（按量计费）
# export GLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
# or ZAI Coding Plan
# export GLM_BASE_URL=https://api.z.ai/api/coding/paas/v4
# or ZAI API
# export GLM_BASE_URL=https://api.z.ai/api/paas/v4
export GLM_API_KEY="你的_GLM_API_密钥"
python3 proxy.py
# 代理运行在 http://localhost:18765
```

或使用后台脚本：

```bash
./scripts/start.sh    # 后台启动
./scripts/stop.sh     # 停止
```

### 2. 配置 Codex CLI

创建 `~/.codex/config.toml`：

```toml
model_provider = "glm"
model = "glm-5.2"

model_catalog_json = "./models.json"

[model_providers.glm]
name = "GLM via Proxy"
base_url = "http://localhost:18765"
wire_api = "responses"
```

### 3. 设置模型目录

将 `models.json` 的内容复制到 `~/.codex/models.json`。

### 4. 测试

```bash
mkdir test-codex && cd test-codex && git init
codex exec "创建一个 Python hello world 程序"
```

## 架构

```
Codex CLI ──Responses API──▶ 代理 (localhost:18765) ──Chat Completions──▶ GLM API
```

代理进行双向格式转换：
- **请求：** Responses API → Chat Completions（模型名映射、工具格式转换）
- **响应：** Chat Completions SSE → Responses API SSE

## 配置说明

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `GLM_API_KEY` | *(无)* | GLM API 密钥（必填） |
| `GLM_BASE_URL` | `https://open.bigmodel.cn/api/coding/paas/v4` | GLM API 端点 |
| `PROXY_HOST` | `127.0.0.1` | 代理监听地址（设为 `0.0.0.0` 则监听所有接口） |
| `PROXY_PORT` | `18765` | 代理监听端口 |
| `GLM_HTTP_PROXY` | *(无)* | 上游 HTTP 代理，如 `http://host:port` |
| `GLM_HTTPS_PROXY` | *(无)* | 上游 HTTPS 代理；未设置时回退到 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` |
| `NO_PROXY` | *(无)* | 不走上游代理的主机，逗号分隔 |

#### 上游代理

如果你的机器需要通过公司或本地 HTTP 代理访问外网，设置 `GLM_HTTP_PROXY` /
`GLM_HTTPS_PROXY`，代理会通过 HTTP `CONNECT` 隧道转发 GLM 请求。未设置时直连，
行为与之前完全一致。

```bash
export GLM_HTTPS_PROXY=http://127.0.0.1:7890   # http:// 开头的 BASE_URL 用 GLM_HTTP_PROXY
export NO_PROXY=localhost,127.0.0.1             # 可选：不走代理的主机
```

### 支持的 GLM 模型

`glm-5.2` · `glm-5.1` · `glm-5-turbo` · `glm-5` · `glm-4.7`

GLM 模型名直接透传，无需映射。

### OpenAI 模型映射

当 Codex CLI 请求以下 OpenAI 模型时，自动替换为对应 GLM 模型：

| Codex 请求 | 替换为 |
|------------|--------|
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

OpenAI 旗舰档模型（含 `o` 系列旗舰）映射到 `glm-5.2`；经济型 / `*-mini`
模型映射到 `glm-4.7`。未识别或缺失的模型名一律回退到 `glm-5.2`（默认模型）。

## 管理命令

```bash
./scripts/start.sh                        # 后台启动
curl http://localhost:18765/health         # 健康检查
tail -f /tmp/codex-glm-proxy.log          # 查看日志
./scripts/stop.sh                         # 停止
```

### Windows 服务（NSSM）

Windows 下可借助 [NSSM](https://nssm.cc/) 把代理作为服务运行（以下脚本需以管理员身份、
在已设置 `GLM_API_KEY` 的命令行中执行）：

```bat
install-service.bat     :: 安装并启动服务（codex-glm-proxy）
stop-service.bat        :: 停止运行中的服务
restart-service.bat     :: 先停止再启动
remove-service.bat      :: 停止并卸载服务
```

安装脚本会把 `GLM_API_KEY`（以及若已设置的 `GLM_BASE_URL` / `PROXY_PORT`）写入服务环境。
日志输出到 `nssm.out.log` / `nssm.err.log`。

## 故障排除

**"Streaming complete, sent 0 chunks"**
模型名未识别。在配置中使用已知模型名（如 `glm-5.2`）。

**Codex 循环/重复操作**
工具调用历史处理问题。更新到最新版本的代理。

**Connection refused**
代理未运行。使用 `python3 proxy.py` 或 `./scripts/start.sh` 启动。

**macOS 上 SSL 证书错误**
安装 certifi：`pip install certifi`。

## 许可证

[MIT](LICENSE)

## Reasoning Effort（推理深度）

Codex CLI 支持 reasoning effort 级别（`low` / `medium` / `high` / `max`），用于控制模型回答前的推理深度。

GLM-5.2 对四档都生效。在复杂编码任务上（3 轮平均），`max` 的推理内容长度比 `high` 长约 44%：

| Effort | reasoning_tokens | reason_len |
|--------|------------------|------------|
| high   | 2175             | 6274 字符  |
| **max** | **2146**        | **9078 字符** |

**推荐：** 日常编码用 `high`（`models.json` 默认值）即可；复杂多步推理任务可切换至 `max`。

代理会把 Codex 历史遗留的 `xhigh` 透明映射到 `max` —— GLM 静默忽略 `xhigh`，这样可保证仍在发 `xhigh` 的客户端在 GLM-5.2 上拿到最强档。

详细的测试结果和分析见 [docs/reasoning-effort.md](docs/reasoning-effort.md)。

自行验证：

```bash
python3 tests/test_reasoning_effort.py                  # 默认只测 glm-5.2
python3 tests/test_reasoning_effort.py --all-models     # 测试所有 GLM 模型
```
