#!/bin/bash
# Stop Codex GLM Proxy

PID_FILE="/tmp/codex-glm-proxy.pid"

stop_by_pid() {
    local PID=$1
    kill "$PID" 2>/dev/null || return 1

    # Wait up to 5 seconds for graceful shutdown
    for i in 1 2 3 4 5; do
        if ! ps -p "$PID" > /dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done

    # Force kill if still running
    kill -9 "$PID" 2>/dev/null
    sleep 1
    ! ps -p "$PID" > /dev/null 2>&1
}

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if ps -p "$PID" > /dev/null 2>&1; then
        echo "Stopping proxy (PID: $PID)..."
        if stop_by_pid "$PID"; then
            rm -f "$PID_FILE"
            echo "Proxy stopped"
        else
            echo "Failed to stop proxy (PID: $PID)"
            exit 1
        fi
    else
        echo "Proxy process not running (stale PID file)"
        rm -f "$PID_FILE"
    fi
else
    PID=$(pgrep -f "python3.*proxy.py" | head -1)
    if [ -n "$PID" ]; then
        echo "Stopping proxy (PID: $PID)..."
        if stop_by_pid "$PID"; then
            echo "Proxy stopped"
        else
            echo "Failed to stop proxy (PID: $PID)"
            exit 1
        fi
    else
        echo "Proxy is not running"
    fi
fi
