#!/bin/bash
# fl_orchestrator.sh – Control Flower FL server (start/stop/status)

PID_FILE="$HOME/mycelial/state/fl_server.pid"

case "$1" in
    start)
        # Check if already running
        if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
            echo "✅ FL server already running (PID $(cat "$PID_FILE"))."
            exit 0
        fi
        cd ~/AgTechAI
        source venv/bin/activate
        nohup python fl_server.py > ~/mycelial/logs/fl_server.log 2>&1 &
        echo $! > "$PID_FILE"
        echo "✅ FL server started (PID $(cat "$PID_FILE"))."
        ;;
    stop)
        if [ -f "$PID_FILE" ]; then
            kill "$(cat "$PID_FILE")" 2>/dev/null
            rm -f "$PID_FILE"
            echo "✅ FL server stopped."
        else
            echo "⚠️ No PID file found. Attempting pkill..."
            pkill -f fl_server.py
        fi
        ;;
    status)
        if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
            echo "✅ FL server is running (PID $(cat "$PID_FILE"))."
        else
            echo "⚠️ FL server is not running."
        fi
        ;;
    *)
        echo "Usage: $0 {start|stop|status}"
        exit 1
        ;;
esac
