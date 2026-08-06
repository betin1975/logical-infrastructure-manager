#!/usr/bin/env python3
"""Bounded, read-only system log collector for LIM."""
from __future__ import annotations
import json, subprocess, sys
MAX_EVENTS = 500
MAX_BYTES = 262144

def _run(command):
    completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=20)
    if completed.returncode not in (0, 1):
        return []
    return completed.stdout.splitlines()

def main():
    lines = _run(("journalctl", "--since=-15min", "--priority=warning..emerg", "--output=json", "--no-pager"))
    events, used = [], 0
    for line in lines[:MAX_EVENTS]:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        event = {
            "timestamp": str(item.get("__REALTIME_TIMESTAMP", "")),
            "source": str(item.get("SYSLOG_IDENTIFIER") or item.get("_SYSTEMD_UNIT") or "system")[:128],
            "priority": str(item.get("PRIORITY", ""))[:8],
            "message": str(item.get("MESSAGE", ""))[:4096],
        }
        encoded = json.dumps(event, separators=(",", ":")).encode()
        if used + len(encoded) > MAX_BYTES:
            break
        used += len(encoded)
        events.append(event)
    json.dump({"schema_version":1,"window":"15m","event_count":len(events),"events":events}, sys.stdout, separators=(",", ":"))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
