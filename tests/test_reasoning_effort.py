#!/usr/bin/env python3
"""
Test GLM API reasoning effort levels.

Tests a GLM model with different reasoning effort values (low, medium, high, xhigh, max)
and a baseline without the reasoning parameter. Outputs a comparison table showing
reasoning token usage and response time.

Usage:
    export GLM_API_KEY="your_key"
    python3 tests/test_reasoning_effort.py                          # default: glm-5.2 only
    python3 tests/test_reasoning_effort.py --all-models             # test all models
    python3 tests/test_reasoning_effort.py --model glm-5-turbo      # test specific model(s)
    python3 tests/test_reasoning_effort.py --question "your question"

Requirements:
    pip install certifi
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
import ssl
import argparse

try:
    import certifi
except ImportError:
    print("ERROR: certifi is required. Install with: pip install certifi")
    sys.exit(1)

API_BASE = os.environ.get("GLM_BASE_URL", "https://open.bigmodel.cn/api/coding/paas/v4")
API_KEY = os.environ.get("GLM_API_KEY", "")

ALL_MODELS = ["glm-5.2", "glm-5.1", "glm-5-turbo", "glm-5", "glm-4.7"]
DEFAULT_MODEL = "glm-5.2"
EFFORTS = [None, "low", "medium", "high", "xhigh", "max"]
DEFAULT_QUESTION = (
    "一个房间里有3个开关，分别控制隔壁房间的3盏灯。"
    "你只能进入隔壁房间一次。如何确定每个开关对应哪盏灯？"
)
DEFAULT_MAX_TOKENS = 2000


def test_one(model: str, effort, question: str, max_tokens: int) -> dict:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": question}],
        "max_tokens": max_tokens,
        "stream": False,
    }
    if effort is not None:
        body["reasoning"] = {"effort": effort}

    data = json.dumps(body).encode()
    url = f"{API_BASE}/chat/completions"
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}",
    })
    ctx = ssl.create_default_context(cafile=certifi.where())
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
            raw = resp.read()
            elapsed = time.time() - t0
            result = json.loads(raw)
    except urllib.error.HTTPError as e:
        elapsed = time.time() - t0
        err_body = e.read().decode("utf-8", errors="replace")
        return {"error": f"HTTP {e.code}: {err_body[:200]}", "elapsed": round(elapsed, 1)}
    except Exception as e:
        elapsed = time.time() - t0
        return {"error": str(e), "elapsed": round(elapsed, 1)}

    usage = result.get("usage", {})
    comp_details = usage.get("completion_tokens_details", {})
    choice = result.get("choices", [{}])[0]
    msg = choice.get("message", {})
    finish = choice.get("finish_reason", "")

    reasoning_content = msg.get("reasoning_content", "") or ""
    content = msg.get("content", "") or ""

    return {
        "total_tokens": usage.get("total_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "reasoning_tokens": comp_details.get("reasoning_tokens", 0),
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "finish_reason": finish,
        "elapsed": round(elapsed, 1),
        "reasoning_content_len": len(reasoning_content),
        "content_len": len(content),
        "has_reasoning_content": bool(reasoning_content),
        "error": None,
    }


def main():
    parser = argparse.ArgumentParser(description="Test GLM API reasoning effort levels")
    parser.add_argument("--all-models", action="store_true",
                        help="Test all models (default: glm-5.2 only)")
    parser.add_argument("--model", nargs="*", default=None,
                        help=f"Test specific model(s). Choices: {ALL_MODELS}")
    parser.add_argument("--question", default=DEFAULT_QUESTION,
                        help="Question to ask (default: classic 3-switch puzzle)")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS,
                        help=f"Max tokens (default: {DEFAULT_MAX_TOKENS})")
    args = parser.parse_args()

    if not API_KEY:
        print("ERROR: GLM_API_KEY not set")
        sys.exit(1)

    # Determine which models to test
    if args.model:
        models = args.model
    elif args.all_models:
        models = ALL_MODELS
    else:
        models = [DEFAULT_MODEL]

    print(f"API:        {API_BASE}")
    print(f"Model(s):   {', '.join(models)}")
    print(f"Question:   {args.question[:60]}...")
    print(f"max_tokens: {args.max_tokens}")
    print()

    all_results = {}
    for model in models:
        print(f"Testing {model} ...")
        all_results[model] = {}
        for effort in EFFORTS:
            label = effort if effort else "(no reasoning)"
            print(f"  effort={label:<18} ", end="", flush=True)
            r = test_one(model, effort, args.question, args.max_tokens)
            all_results[model][label] = r
            if r["error"]:
                print(f"ERROR: {r['error'][:60]}")
            else:
                print(f"tokens={r['total_tokens']:>5}  reasoning={r['reasoning_tokens']:>5}  "
                      f"reason_len={r['reasoning_content_len']:>5}  time={r['elapsed']:>5.1f}s  "
                      f"finish={r['finish_reason']}")
            time.sleep(0.5)
        print()

    # Summary table
    width = 130
    print("=" * width)
    print(f"{'Model':<15} {'Effort':<18} {'Total':>7} {'Reason':>8} {'Len':>6} {'Finish':>8} {'Time':>7}")
    print("-" * width)
    for model in models:
        for label in ["(no reasoning)", "low", "medium", "high", "xhigh", "max"]:
            r = all_results[model][label]
            if r["error"]:
                print(f"{model:<15} {label:<18} {'ERROR':>7} {r['error'][:40]}")
            else:
                print(f"{model:<15} {label:<18} {r['total_tokens']:>7} {r['reasoning_tokens']:>8} "
                      f"{r['reasoning_content_len']:>6} {r['finish_reason']:>8} {r['elapsed']:>6.1f}s")
        print()


if __name__ == "__main__":
    main()
