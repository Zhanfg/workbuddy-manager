#!/usr/bin/env python3
"""测量已有 Chat / Responses SSE 网关的端到端延迟与有效输出速率。

不保存 Key、请求正文、响应正文、Cookie 或原始事件。默认使用无敏感信息的合成输入。
示例：
  python tools/bench_gateway_sse.py --url http://127.0.0.1:7864/v1/chat/completions \
      --model deepseek-v4.1-flash --requests 3 --concurrency 1
  python tools/bench_gateway_sse.py --url http://127.0.0.1:7864/v1/responses \
      --model glm-5.3-flash --requests 5 --concurrency 2
设置 WB_GATEWAY_KEY 环境变量，或在交互式提示中输入；本脚本不会打印它。
"""
from __future__ import annotations

import argparse
import concurrent.futures
import getpass
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx


@dataclass
class Sample:
    start: float
    end: float
    header_ms: float
    first_output_ms: float | None
    first_output_at: float | None
    output_tokens: int | None
    input_tokens: int | None
    sse_frames: int
    heartbeats: int
    received_bytes: int
    body_bytes: int

    @property
    def output_tps(self) -> float | None:
        # usage 的 output/completion_tokens 可能包括未显示的推理 token。
        # 此值是「usage 口径下的有效输出速率」，不是模型内部生成速率。
        if self.output_tokens is None or self.first_output_at is None:
            return None
        return self.output_tokens / max(self.end - self.first_output_at, 0.001)


def positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return value


def prepare_payload(args: argparse.Namespace) -> dict:
    if args.payload:
        # 只读现有文件，不新建任何存有请求内容的文件。
        body = json.loads(Path(args.payload).read_text(encoding="utf-8"))
        if not isinstance(body, dict):
            raise ValueError("payload must be a JSON object")
        body["stream"] = True
        return body

    prompt = ("benchmark input " * (args.prompt_kib * 1024 // 16 + 1))[:args.prompt_kib * 1024]
    if args.protocol == "responses":
        return {
            "model": args.model,
            "stream": True,
            "input": prompt,
            "max_output_tokens": args.max_output_tokens,
        }
    return {
        "model": args.model,
        "stream": True,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": args.max_output_tokens,
        "stream_options": {"include_usage": True},
    }


def extract_metrics(frame: dict, event: str) -> tuple[bool, int | None, int | None]:
    """返回（是否含输出正文, input_tokens, output_tokens），不保存正文。"""
    has_text = False
    usage = frame.get("usage")
    if isinstance(frame.get("choices"), list):
        for choice in frame["choices"]:
            if isinstance(choice, dict):
                delta = choice.get("delta")
                if isinstance(delta, dict) and (
                    delta.get("content") or delta.get("reasoning_content")
                ):
                    has_text = True

    kind = frame.get("type") or event
    if kind in ("response.output_text.delta", "response.reasoning_summary_text.delta"):
        has_text = bool(frame.get("delta"))
    if kind == "response.output_item.done":
        has_text = False

    response = frame.get("response")
    if isinstance(response, dict) and isinstance(response.get("usage"), dict):
        usage = response["usage"]
    if not isinstance(usage, dict):
        return has_text, None, None

    inp = usage.get("prompt_tokens", usage.get("input_tokens"))
    out = usage.get("completion_tokens", usage.get("output_tokens"))
    return (
        has_text,
        int(inp) if isinstance(inp, (int, float)) else None,
        int(out) if isinstance(out, (int, float)) else None,
    )


def measure(url: str, key: str, payload: dict, timeout: float) -> Sample:
    body_bytes = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    first_output_at = None
    input_tokens = output_tokens = None
    frames = heartbeats = received = 0
    start = time.perf_counter()
    with httpx.Client(
        timeout=httpx.Timeout(timeout, connect=10.0),
        follow_redirects=False,
        trust_env=False,
    ) as client:
        with client.stream(
            "POST",
            url,
            json=payload,
            headers={
                "Authorization": "Bearer " + key,
                "Accept": "text/event-stream",
            },
        ) as response:
            header_at = time.perf_counter()
            if response.status_code != 200:
                # 只打印状态码，错误正文可能回显 Key 或其他生产信息。
                raise RuntimeError(f"HTTP {response.status_code}")
            event_name = ""
            for line in response.iter_lines():
                now = time.perf_counter()
                received += len(line.encode("utf-8")) + 1
                if line.startswith(":"):
                    heartbeats += 1
                    continue
                if line.startswith("event:"):
                    event_name = line[6:].strip()
                    continue
                if not line.startswith("data:"):
                    if not line:
                        event_name = ""
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                frames += 1
                try:
                    parsed = json.loads(data)
                except ValueError:
                    continue
                if not isinstance(parsed, dict):
                    continue
                has_text, inp, out = extract_metrics(parsed, event_name)
                if has_text and first_output_at is None:
                    first_output_at = now
                if inp is not None:
                    input_tokens = inp
                if out is not None:
                    output_tokens = out
            end = time.perf_counter()
    return Sample(
        start=start,
        end=end,
        header_ms=(header_at - start) * 1000,
        first_output_ms=(first_output_at - start) * 1000 if first_output_at else None,
        first_output_at=first_output_at,
        output_tokens=output_tokens,
        input_tokens=input_tokens,
        sse_frames=frames,
        heartbeats=heartbeats,
        received_bytes=received,
        body_bytes=body_bytes,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", required=True, help="完整 /v1/chat/completions 或 /v1/responses URL")
    ap.add_argument("--protocol", choices=("chat", "responses"), default="chat")
    ap.add_argument("--model", default="deepseek-v4.1-flash")
    ap.add_argument("--payload", help="已有的 JSON 请求文件；不创建新文件")
    ap.add_argument("--prompt-kib", type=positive_int, default=1)
    ap.add_argument("--max-output-tokens", type=positive_int, default=512)
    ap.add_argument("--requests", type=positive_int, default=3)
    ap.add_argument("--concurrency", type=positive_int, default=1)
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument("--target-tps", type=float, default=340.0)
    args = ap.parse_args()
    if args.timeout <= 0 or args.target_tps <= 0:
        ap.error("timeout and target-tps must be > 0")
    if args.concurrency > args.requests:
        ap.error("concurrency cannot exceed requests")
    if not args.url.startswith(("http://", "https://")):
        ap.error("url must start with http:// or https://")
    expected_suffix = "/v1/responses" if args.protocol == "responses" else "/v1/chat/completions"
    if not args.url.rstrip("/").endswith(expected_suffix):
        ap.error(f"url must end with {expected_suffix}")

    key = os.environ.get("WB_GATEWAY_KEY", "")
    if not key:
        if not sys.stdin.isatty():
            ap.error("set WB_GATEWAY_KEY for non-interactive use")
        key = getpass.getpass("Gateway Key (not echoed): ")
    if not key:
        ap.error("Gateway Key is empty")

    payload = prepare_payload(args)
    results: list[Sample] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(measure, args.url, key, payload, args.timeout)
                   for _ in range(args.requests)]
        for task in concurrent.futures.as_completed(futures):
            try:
                results.append(task.result())
            except Exception as exc:
                print(f"request failed: {type(exc).__name__}: {exc}", file=sys.stderr)
                return 2

    print(f"requests={len(results)}, concurrency={args.concurrency}, "
          f"body_bytes={results[0].body_bytes}")
    print(f"header_ms median={statistics.median(s.header_ms for s in results):.1f}")
    ttfts = [s.first_output_ms for s in results if s.first_output_ms is not None]
    print(f"first_output_ms median={statistics.median(ttfts):.1f}" if ttfts
          else "first_output_ms=unavailable")
    per_stream = [s.output_tps for s in results if s.output_tps is not None]
    if per_stream:
        print(f"per_stream_usage_output_tps median={statistics.median(per_stream):.1f}")
    else:
        print("per_stream_usage_output_tps=unavailable (missing usage or text)")
    total_tokens = sum(s.output_tokens or 0 for s in results)
    elapsed = max(s.end for s in results) - min(s.start for s in results)
    combined = total_tokens / max(elapsed, 0.001) if total_tokens else None
    print(f"aggregate_usage_output_tps={combined:.1f}" if combined is not None
          else "aggregate_usage_output_tps=unavailable")
    print(f"frames={sum(s.sse_frames for s in results)}, "
          f"heartbeats={sum(s.heartbeats for s in results)}, "
          f"received_bytes={sum(s.received_bytes for s in results)}")
    if combined is not None:
        print(f"target_{args.target_tps:g}_usage_output_tps="
              f"{'MET' if combined >= args.target_tps else 'NOT_MET'}")
    print("Note: upstream prefill/token generation and hidden reasoning tokens cannot "
          "be separated by client-side measurements; run the same test against "
          "wb2api directly to compare gateway overhead.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
