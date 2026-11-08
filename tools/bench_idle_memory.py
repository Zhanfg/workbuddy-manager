#!/usr/bin/env python3
"""Measure real idle RSS/PSS for an isolated local WorkBuddy Manager process.

The measurement is synthetic: a fresh temporary SQLite DB and a temporary,
auto-generated admin account. No production Key or data is read or printed.
No connections are made to WorkBuddy upstream.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


def _proc_kb(pid: int, name: str) -> int | None:
    try:
        for line in Path(f"/proc/{pid}/smaps_rollup").read_text().splitlines():
            if line.startswith(f"{name}:"):
                return int(line.split()[1])
    except (FileNotFoundError, PermissionError, OSError):
        return None
    return None


def _status_kb(pid: int, name: str) -> int | None:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith(f"{name}:"):
                return int(line.split()[1])
    except (FileNotFoundError, PermissionError, OSError):
        return None
    return None


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def measure(root: Path, seconds: float) -> int:
    root = root.resolve()
    if not (root / "server" / "serve.py").is_file():
        raise ValueError("app root must contain server/serve.py")
    with tempfile.TemporaryDirectory(prefix="wb-idle-bench-") as tmp:
        work = Path(tmp)
        port = _port()
        env = os.environ.copy()
        env.update({
            "WB_DATA_DIR": str(work / "data"),
            "WB_DB": str(work / "data" / "bench.db"),
            "WB_USERS_FILE": str(work / "data" / "users.json"),
            "WB_AUTH_DIR": str(work / "auths"),
            "WB_UPSTREAM_CONFIG": str(work / "missing-upstream.json"),
            "WB_UPSTREAM_DIR": str(work / "upstream"),
            "WB2API_MODE": "native",
            "WB_MANAGER_HOST": "127.0.0.1",
            "WB_MANAGER_PORT": str(port),
            "WB_HTTP_CLIENT_IDLE_SECONDS": "15",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(root),
        })
        process = subprocess.Popen(
            [sys.executable, "-m", "server.serve", "--host", "127.0.0.1",
             "--port", str(port), "--env-file", "/dev/null"],
            cwd=root, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            health = f"http://127.0.0.1:{port}/api/healthz"
            deadline = time.monotonic() + 35
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"manager stopped during startup, rc={process.returncode}")
                try:
                    with urllib.request.urlopen(health, timeout=0.5) as response:
                        if response.status == 200:
                            break
                except (OSError, urllib.error.URLError, TimeoutError):
                    pass
                time.sleep(0.2)
            else:
                raise TimeoutError("manager health check did not become ready")

            # Allow startup work to settle, sample the *same* PID, not workers.
            time.sleep(1)
            rss, pss, swap = [], [], []
            until = time.monotonic() + seconds
            while time.monotonic() < until:
                r = _status_kb(process.pid, "VmRSS")
                p = _proc_kb(process.pid, "Pss")
                sw = _status_kb(process.pid, "VmSwap")
                if r is not None:
                    rss.append(r)
                if p is not None:
                    pss.append(p)
                if sw is not None:
                    swap.append(sw)
                time.sleep(0.2)
            if not rss:
                raise RuntimeError("no /proc measurements were available")
            # Only resource counters are printed; no credentials, request or log content.
            print(f"root={root.name}, pid_count=1, samples={len(rss)}")
            print(f"RSS_MiB_min={min(rss)/1024:.2f} "
                  f"median={statistics.median(rss)/1024:.2f} max={max(rss)/1024:.2f}")
            if pss:
                print(f"PSS_MiB_min={min(pss)/1024:.2f} "
                      f"median={statistics.median(pss)/1024:.2f} max={max(pss)/1024:.2f}")
            if swap:
                print(f"VmSwap_MiB_max={max(swap)/1024:.2f}")
        finally:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--seconds", type=float, default=10)
    args = parser.parse_args()
    if args.seconds < 1:
        parser.error("--seconds must be at least 1")
    raise SystemExit(measure(args.root, args.seconds))
