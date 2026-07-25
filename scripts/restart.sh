#!/bin/bash
# Restart Codex GLM Proxy: stop (if running) then start.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# stop.sh tolerates an already-stopped proxy (exits 0), but aborts with a
# non-zero code only if it genuinely fails to kill the process. In that case
# we must NOT proceed to start, otherwise a second instance would come up.
"$SCRIPT_DIR/stop.sh" || { echo "Restart aborted: failed to stop proxy"; exit 1; }

exec "$SCRIPT_DIR/start.sh"
