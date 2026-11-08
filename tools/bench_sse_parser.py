#!/usr/bin/env python3
"""合成 SSE CPU 解析基准，无网络、无密钥、无真实会话内容。

340 帧是固定批量，不代表上游模型生成 340 token/s。正常 Chat 流中
首个正文后只需解析末帧 usage，故测量快路径与首字定位路径。
"""
from __future__ import annotations

import argparse
import time

from server.routers.gateway import _scan_sse


def run(frames: int, loops: int, check_content: bool) -> float:
    regular = 'data: {"choices":[{"delta":{"content":"x"}}]}\n'
    terminal = 'data: {"choices":[],"usage":{"prompt_tokens":100,"completion_tokens":340}}\n'
    payload = regular * frames + terminal
    started = time.perf_counter()
    for _ in range(loops):
        usage: dict = {}
        remaining, found = _scan_sse(payload, usage, check_content=check_content)
        if remaining or usage.get("completion_tokens") != 340:
            raise RuntimeError("SSE usage regression")
        if found != check_content:
            raise RuntimeError("SSE first-content detection regression")
    seconds = max(time.perf_counter() - started, 0.000001)
    return frames * loops / seconds


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--frames", type=int, default=340)
    p.add_argument("--loops", type=int, default=300)
    args = p.parse_args()
    if args.frames <= 0 or args.loops <= 0:
        p.error("frames and loops must be positive")
    full = run(args.frames, args.loops, True)
    steady = run(args.frames, args.loops, False)
    print(f"first-content detection={full:.0f} frames/s")
    print(f"steady-state usage tracking={steady:.0f} frames/s")
    print(f"340 frames/s CPU processing target={'MET' if min(full, steady) >= 340 else 'NOT_MET'}")
    return 0 if min(full, steady) >= 340 else 1


if __name__ == "__main__":
    raise SystemExit(main())
