#!/usr/bin/env python3
"""
Logging & Auditing Service – Pure HTTP service.
Stores structured logs with event types, correlation IDs, and policy-based pinning.
"""
import os
import json
import sqlite3
import uuid
import requests
from datetime import datetime
from flask import Flask, request, jsonify

app = Flask(__name__)

BASE = os.path.expanduser("~/mycelial")
DB_PATH = os.path.join(BASE, "state", "audit.db")
POLICY_SERVICE_URL = "http://localhost:8008/evaluate"
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS logs (
                id TEXT PRIMARY KEY,
                agent_id TEXT,
                event_type TEXT,
                task TEXT,
                result TEXT,
                level TEXT,
                metadata TEXT,
                timestamp TEXT,
                correlation_id TEXT,
                pinned INTEGER DEFAULT 0
            )
        ''')
        # Indexes for common query patterns
        conn.execute('CREATE INDEX IF NOT EXISTS idx_agent ON logs(agent_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON logs(timestamp)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_correlation ON logs(correlation_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_event_type ON logs(event_type)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_task ON logs(task)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_level ON logs(level)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_pinned ON logs(pinned)')
        conn.commit()

init_db()

def now():
    return datetime.now().isoformat()

def ask_policy(namespace):
    """Ask Policy Service if this type of log should be pinned."""
    try:
        resp = requests.post(
            POLICY_SERVICE_URL,
            json={"type": "pin", "context": {"namespace": namespace}},
            timeout=3
        )
        if resp.status_code == 200:
            return resp.json().get("decision") == "pin"
    except:
        pass
    return False

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "service": "logging_auditing"})

@app.route("/log", methods=["POST"])
def log():
    data = request.json or {}
    agent_id = data.get("agent_id")
    event_type = data.get("event_type", "SYSTEM")
    task = data.get("task")
    result = data.get("result")
    level = data.get("level", "info")
    metadata = data.get("metadata", {})
    correlation_id = data.get("correlation_id")
    # Do not accept pin from agents – policy decides
    namespace = data.get("namespace", "system")  # used for policy decision

    if not agent_id or not task:
        return jsonify({"success": False, "error": "Missing agent_id or task"}), 400

    # Ask Policy Service if this should be pinned
    should_pin = ask_policy(namespace)

    entry_id = str(uuid.uuid4())[:8]
    timestamp = now()
    metadata_json = json.dumps(metadata)

    with get_db() as conn:
        conn.execute(
            "INSERT INTO logs (id, agent_id, event_type, task, result, level, metadata, timestamp, correlation_id, pinned) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (entry_id, agent_id, event_type, task, result, level, metadata_json, timestamp, correlation_id, 1 if should_pin else 0)
        )
        conn.commit()

    return jsonify({
        "success": True,
        "entry": {
            "id": entry_id,
            "agent_id": agent_id,
            "event_type": event_type,
            "task": task,
            "result": result,
            "level": level,
            "metadata": metadata,
            "timestamp": timestamp,
            "correlation_id": correlation_id,
            "pinned": 1 if should_pin else 0
        }
    })

@app.route("/logs", methods=["GET"])
def get_logs():
    agent_id = request.args.get("agent_id")
    event_type = request.args.get("event_type")
    task = request.args.get("task")
    level = request.args.get("level")
    pinned = request.args.get("pinned")
    correlation_id = request.args.get("correlation_id")
    limit = int(request.args.get("limit", 100))
    offset = int(request.args.get("offset", 0))

    with get_db() as conn:
        query = "SELECT * FROM logs WHERE 1=1"
        params = []
        if agent_id:
            query += " AND agent_id = ?"
            params.append(agent_id)
        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)
        if task:
            query += " AND task = ?"
            params.append(task)
        if level:
            query += " AND level = ?"
            params.append(level)
        if pinned is not None:
            query += " AND pinned = ?"
            params.append(1 if pinned.lower() == "true" else 0)
        if correlation_id:
            query += " AND correlation_id = ?"
            params.append(correlation_id)
        query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cur = conn.execute(query, params)
        rows = cur.fetchall()
        total = conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]

        return jsonify({
            "success": True,
            "total": total,
            "offset": offset,
            "limit": limit,
            "entries": [dict(row) for row in rows]
        })

@app.route("/logs/<entry_id>", methods=["GET"])
def get_log_entry(entry_id):
    with get_db() as conn:
        cur = conn.execute("SELECT * FROM logs WHERE id = ?", (entry_id,))
        row = cur.fetchone()
        if row:
            return jsonify({"success": True, "entry": dict(row)})
        return jsonify({"success": False, "error": "Not found"}), 404

@app.route("/pins", methods=["GET"])
def list_pins():
    with get_db() as conn:
        cur = conn.execute("SELECT * FROM logs WHERE pinned = 1 ORDER BY timestamp DESC")
        rows = cur.fetchall()
        return jsonify({"success": True, "pins": [dict(row) for row in rows]})

@app.route("/export", methods=["GET"])
def export_logs():
    with get_db() as conn:
        cur = conn.execute("SELECT * FROM logs ORDER BY timestamp DESC")
        rows = cur.fetchall()
        return jsonify({"success": True, "logs": [dict(row) for row in rows]})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8009, debug=False)
