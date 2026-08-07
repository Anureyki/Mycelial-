#!/bin/bash
# dashboard_orchestrator.sh – Start/stop/status the Mycelial web dashboard
# (the static webapp/ PWA - chat + agent status/grow/progress cards -
# served via webapp/serve.sh. This previously pointed at a dashboard/app.py
# Flask app that was never actually created.)

PID_FILE="$HOME/mycelial/state/dashboard.pid"
LOG_FILE="$HOME/mycelial/logs/dashboard.log"
WEBAPP_DIR="$HOME/mycelial/webapp"
PORT="${DASHBOARD_PORT:-8090}"

case "$1" in
    start)
        if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
            echo "✅ Dashboard already running (PID $(cat "$PID_FILE"))."
            exit 0
        fi
        cd "$WEBAPP_DIR" || exit 1
        nohup python3 -m http.server "$PORT" --bind 0.0.0.0 > "$LOG_FILE" 2>&1 &
        echo $! > "$PID_FILE"
        echo "✅ Dashboard started on port $PORT (PID $(cat "$PID_FILE"))."
        ;;
    stop)
        if [ -f "$PID_FILE" ]; then
            kill "$(cat "$PID_FILE")" 2>/dev/null
            rm -f "$PID_FILE"
            echo "✅ Dashboard stopped."
        else
            echo "⚠️ No PID file found. Attempting pkill..."
            pkill -f "http.server $PORT"
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
