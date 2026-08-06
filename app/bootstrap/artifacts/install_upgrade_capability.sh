#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

MONITOR_USER="${MONITOR_USER:-monitor}"
MONITOR_HOME="${MONITOR_HOME:-/var/lib/monitor}"
AUTHORIZED_KEYS="$MONITOR_HOME/.ssh/authorized_keys"
DISPATCHER="/usr/local/libexec/lim/monitor-dispatch"
UPDATER="/usr/local/libexec/lim/update-collector"
SUDOERS="/etc/sudoers.d/lim-collector-updater"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this installer with sudo." >&2
  exit 1
fi

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

install -m 755 -o root -g root \
  "$SCRIPT_DIR/monitor_dispatch.py" \
  "$DISPATCHER"

install -m 755 -o root -g root \
  "$SCRIPT_DIR/update_collector.py" \
  "$UPDATER"

cat > "$SUDOERS" <<EOF
$MONITOR_USER ALL=(root) NOPASSWD: $UPDATER *
EOF
chmod 440 "$SUDOERS"
visudo -cf "$SUDOERS"

test -f "$AUTHORIZED_KEYS" || {
  echo "Authorized keys file not found: $AUTHORIZED_KEYS" >&2
  exit 1
}

python3 - "$AUTHORIZED_KEYS" "$DISPATCHER" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
dispatcher = sys.argv[2]
lines = path.read_text(encoding="utf-8").splitlines()
updated = []
found = False

for line in lines:
    if "lim-monitor" not in line:
        updated.append(line)
        continue

    match = re.search(r"(ssh-ed25519\s+\S+\s+.*lim-monitor.*)$", line)
    if not match:
        raise SystemExit("Could not parse the lim-monitor authorized key.")
    updated.append(f'command="{dispatcher}",restrict {match.group(1)}')
    found = True

if not found:
    raise SystemExit("No lim-monitor authorized key was found.")

path.write_text("\n".join(updated) + "\n", encoding="utf-8")
PY

chown "$MONITOR_USER:$MONITOR_USER" "$AUTHORIZED_KEYS"
chmod 600 "$AUTHORIZED_KEYS"

echo "Collector upgrade capability installed."
