#!/usr/bin/env bash
set -Eeuo pipefail
if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo." >&2
  exit 1
fi
DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
install -m 755 -o root -g root "$DIR/collect_logs.py" /usr/local/libexec/lim/collect-logs
install -m 755 -o root -g root "$DIR/monitor_dispatch.py" /usr/local/libexec/lim/monitor-dispatch
echo "Log collection capability installed."
