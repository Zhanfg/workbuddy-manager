#!/bin/sh
# Inspect combined resource pressure without reading logs, env files or argv.
# Usage: sh tools/check_host_resources.sh [systemd unit names...]
# Default names are examples; override with your actual units.
set -u

if [ "$#" -eq 0 ]; then
    set -- workbuddy-web workbuddy2api cloudflared minecraft
fi

printf '%s\n' '=== Host memory (kB) ==='
awk '/^(MemTotal|MemAvailable|SwapTotal|SwapFree|Committed_AS):/ { print }' /proc/meminfo 2>/dev/null
printf '%s\n' '=== Memory PSI ==='
if [ -r /proc/pressure/memory ]; then
    cat /proc/pressure/memory
fi

for unit in "$@"; do
    case "$unit" in
        *[!A-Za-z0-9_.@-]*|'')
            printf 'skipping invalid unit name\n'
            continue
            ;;
    esac

    printf '\n=== %s ===\n' "$unit"
    if ! systemctl --quiet is-active "$unit" 2>/dev/null; then
        printf 'state=inactive or not found\n'
        continue
    fi

    systemctl show "$unit" --no-pager \
        -p MainPID -p MemoryCurrent -p MemoryPeak -p MemoryHigh \
        -p MemoryMax -p MemoryLow -p MemorySwapCurrent -p NRestarts

    pid=$(systemctl show "$unit" -P MainPID 2>/dev/null)
    case "$pid" in
        ''|0|*[!0-9]*) continue ;;
    esac

    if [ -r "/proc/$pid/smaps_rollup" ]; then
        awk '/^(Rss|Pss|Private_Clean|Private_Dirty|Swap):/ { print }' \
            "/proc/$pid/smaps_rollup"
    else
        printf 'PSS=unavailable (run with appropriate /proc permissions)\n'
    fi

    # cgroup values include children. Essential for multi-process Chromium and
    # Java wrapper scripts whose MainPID RSS would otherwise under-report.
    cg=$(systemctl show "$unit" -P ControlGroup 2>/dev/null)
    if [ -n "$cg" ] && [ -r "/sys/fs/cgroup$cg/memory.events" ]; then
        awk '/^(high|max|oom|oom_kill) / { print "cgroup_" $1 "=" $2 }' \
            "/sys/fs/cgroup$cg/memory.events"
    fi
done

printf '\n%s\n' '=== Journal storage ==='
if command -v journalctl >/dev/null 2>&1; then
    journalctl --disk-usage 2>/dev/null || true
fi
