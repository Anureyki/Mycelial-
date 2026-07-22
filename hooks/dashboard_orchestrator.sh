#!/bin/bash
# dashboard_orchestrator.sh – Start/stop/status the Flask dashboard

PID_FILE="$HOME/mycelial/state/dashboard.pid"
LOG_FILE="$HOME/mycelial/logs/dashboard.log"
APP_PATH="$HOME/mycelial/dashboard/app.py"
VENV_PYTHON="$HOME/mycelial/venv/bin/python"

case "$1" in
    start)
        if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
            echo "✅ Dashboard already running (PID $(cat "$PID_FILE"))."
            exit 0
        fi
        cd "$HOME/mycelial/dashboard"
        nohup "$VENV_PYTHON" app.py > "$LOG_FILE" 2>&1 &
        echo $! > "$PID_FILE"
        echo "✅ Dashboard started (PID $(cat "$PID_FILE"))."
        ;;
    stop)
        if [ -f "$PID_FILE" ]; then
            kill "$(cat "$PID_FILE")" 2>/dev/null
            rm -f "$PID_FILE"
            echo "✅ Dashboard stopped."
        else
            echo "⚠️ No PID file found. Attempting pkill..."
            pkill -f "app.py"
        fi
        ;;
    status)
        if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
            echo "✅ Dashboard is running (PID $(cat "$PID_FILE"))."
        else
            echo "⚠️ Dashboard is not running."
        fi
        ;;
    *)
        echo "Usage: $0 {start|stop|status}"
        exit 1
        ;;
esac
