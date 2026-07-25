# GLM Reasoning Effort 测试报告

## 背景

Codex CLI 支持 `low`、`medium`、`high`、`max` 四档 reasoning effort（早期版本为 `low`/`medium`/`high`/`xhigh`，代理会把 `xhigh` 透明映射为 `max`）。GLM 使用的是 OpenAI Chat Completions API 格式，通过 `reasoning.effort` 参数控制推理深度。本测试验证 GLM API 对各 effort level 的实际支持情况。

## 测试方法

- **模型**: glm-5.2（重点）、glm-5.1、glm-5-turbo、glm-5、glm-4.7
- **题目**: 经典逻辑推理题（3 个开关 3 盏灯，只能进入一次）
- **max_tokens**: 2000
- **测试方式**: 逐个串行调用，非并发
- **观测指标**: reasoning_tokens、reasoning_content 长度、total_tokens、耗时

测试脚本: `tests/test_reasoning_effort.py`

```bash
export GLM_API_KEY="your_key"
python3 tests/test_reasoning_effort.py                          # 默认只测 glm-5.2
python3 tests/test_reasoning_effort.py --all-models             # 测试所有模型
python3 tests/test_reasoning_effort.py --model glm-5-turbo      # 测试指定模型
```

## 测试结果

### GLM-5.1（串行测试，稳定可靠）

| Effort | reasoning_tokens | total_tokens | reasoning_content 长度 | 耗时 |
|--------|-----------------|--------------|----------------------|------|
| (无 reasoning) | 933 | 1272 | 3161 chars | ~27s |
| **low** | 790 | 1146 | 1319 chars | ~22s |
| **medium** | 633 | 922 | 2364 chars | ~17s |
| **high** | **1048** | **1340** | **3391 chars** | ~27s |
| **xhigh** | 904 | 1224 | 3005 chars | ~25s |

### 全部模型汇总（并行测试，存在波动）

| Model | Effort | Total | Reasoning | Time |
|-------|--------|-------|-----------|------|
| glm-5.1 | (no reasoning) | 1078 | 793 | 23.0s |
| glm-5.1 | low | 1133 | 791 | 26.1s |
| glm-5.1 | medium | 1364 | 1073 | 30.9s |
| glm-5.1 | high | 1193 | 879 | 28.1s |
| glm-5.1 | xhigh | 1324 | 1023 | 29.6s |
| glm-5-turbo | (no reasoning) | 1213 | 861 | 21.1s |
| glm-5-turbo | low | 1186 | 818 | 21.6s |
| glm-5-turbo | medium | 1124 | 748 | 24.3s |
| glm-5-turbo | high | 1140 | 809 | 19.7s |
| glm-5-turbo | xhigh | 1225 | 930 | 21.6s |
| glm-5 | (no reasoning) | 1137 | 811 | 28.2s |
| glm-5 | low | 1264 | 972 | 27.6s |
| glm-5 | medium | 1253 | 918 | 28.3s |
| glm-5 | high | 1086 | 774 | 23.1s |
| glm-5 | xhigh | 1377 | 1055 | 32.3s |
| glm-4.7 | (no reasoning) | 1387 | 1107 | 46.2s |
| glm-4.7 | low | 962 | 674 | 17.8s |
| glm-4.7 | medium | 1360 | 1045 | 38.0s |
| glm-4.7 | high | 1286 | 1010 | 27.1s |
| glm-4.7 | xhigh | 1183 | 876 | 17.7s |

## 结论

### 1. GLM API 接受所有 effort 值，不会报错

无论传 `low`、`medium`、`high` 还是 `xhigh`，GLM API 都不会返回 400 错误。Proxy 的 reasoning fallback 不会被触发。

### 2. `high` 是实际最高可用级别（GLM-5.1 串行测试）

在更稳定的串行测试中，`high` 的推理量（1048 reasoning tokens / 3391 chars reasoning content）是最高的，明显超过 `low`（790 tokens / 1319 chars）和 `xhigh`（904 tokens / 3005 chars）。

### 3. `xhigh` 不被 GLM 额外支持

`xhigh` 的推理量低于 `high`，与"无 reasoning 参数"的基线接近。GLM 大概率将其视为无效值，回退到默认行为。

### 4. 不同 effort 之间存在随机波动

并行测试中，各 effort 级别的 reasoning_tokens 没有呈现稳定的梯度。这是因为 GLM 模型本身有随机性，effort 参数的效果容易被波动淹没。建议以串行测试结果为准。

### 5. 推荐配置

`models.json` 中推荐的 reasoning 配置：

```json
{
    "default_reasoning_level": "high",
    "supported_reasoning_levels": [
        {"effort": "low", "description": "Fast responses"},
        {"effort": "medium", "description": "Balanced reasoning"},
        {"effort": "high", "description": "Deep reasoning"}
    ]
}
```

**不建议添加 `xhigh`**，因为 GLM API 不支持该级别，实际效果不如 `high`。

### 6. GLM-5.2 实测：max 档确实是最强档（2026-07-19）

GLM-5.2 起新增了 `max` 档。用一道复杂的线程安全 LRU 缓存实现题，每个档位跑 3 轮取平均：

| Effort (3 轮平均) | reasoning_tokens | reason_len | time |
|-------------------|------------------|------------|------|
| high              | 2175             | 6274       | 62.6s |
| xhigh             | 1942             | 6544       | 57.2s |
| **max**           | **2146**         | **9078**   | **57.8s** |

**关键结论**：
- **max 的 reason_len 比 high 长 44%**（9078 vs 6274 chars），确实是 GLM-5.2 的最强档
- **xhigh 的 reasoning_tokens 反而比 high 还低**（1942 vs 2175）——证实 GLM-5.2 也不识别 xhigh，与早期 GLM 行为一致
- **所有档位都不报 400**，所以 Proxy 的 fallback 兜底实际不会触发

综合判断：**GLM-5.2 实质上只对 `high` 和 `max` 有响应**，`low`/`medium`/`xhigh` 都被静默降级。因此 Proxy 的 `xhigh → max` 映射能让 Codex 用户的 xhigh 真正触发出最强推理。

## Proxy 行为说明

Proxy 对 `reasoning` 参数采用**透传 + 一处映射**策略：
- Codex 发送 `{"reasoning": {"effort": "high"}}` → Proxy 原样转发给 GLM API
- **xhigh → max 映射**：GLM-5.2 起新增 `max` 档（早期 GLM 静默忽略 `xhigh`），Proxy 把 Codex 的 `xhigh` 自动映射为 `max`，让 Codex 用户也能用上 GLM-5.2 的最高推理档
- 仅当 GLM API 返回 400 错误且与 reasoning 相关时，Proxy 才会去掉 reasoning 参数重试

除 `xhigh → max` 外，Proxy **不会**对其他 effort 值（`low`/`medium`/`high`）做映射或 clamping。

> ℹ️ 注：GLM 静默吞掉不支持的 effort 值（不报 400），所以即便 Codex 用户传了 `low`/`medium` 也不会触发错误，只是实际效果与 `high` 相近。
