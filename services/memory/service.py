#!/usr/bin/env python3
import os, json, sqlite3, uuid
from datetime import datetime
from flask import Flask, request, jsonify
from contextlib import contextmanager

app = Flask(__name__)
BASE = os.path.expanduser("~/mycelial")
DB_PATH = os.path.join(BASE, "state", "memory.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

@contextmanager
def get_db():
    """Connection that is actually CLOSED when the block ends.

    Every call site says `with get_db() as conn:`, which reads like it closes
    the connection and does not - `with` on a sqlite3 Connection manages the
    TRANSACTION, committing or rolling back, and leaves the handle open. So one
    file descriptor leaked per request until the process hit its 1024 limit,
    after which the Memory Service could no longer open its own database and the
    whole platform went blind: every agent lost its state while the data sat
    intact on disk. Found at 1,019 open handles to memory.db.

    A generator-based context manager keeps every existing call site working and
    closes in a finally, so the handle is released even when a query raises."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    with get_db() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY, namespace TEXT, key TEXT, value TEXT,
            timestamp TEXT, pinned INTEGER DEFAULT 0, version INTEGER DEFAULT 1,
            UNIQUE(namespace, key)
        )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS pinned_memories (
            id TEXT PRIMARY KEY, namespace TEXT, key TEXT, value TEXT,
            pinned_at TEXT, version INTEGER
        )''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_namespace_key ON memories(namespace, key)')
        conn.commit()
init_db()

def now(): return datetime.now().isoformat()

@app.route("/health")
def health(): return jsonify({"status":"healthy","service":"memory"})

@app.route("/store", methods=["POST"])
def store():
    data = request.json or {}
    namespace, key, value = data.get("namespace"), data.get("key"), data.get("value")
    pin = data.get("pin", False)
    if not namespace or not key:
        return jsonify({"success":False,"error":"Missing namespace or key"}), 400
    with get_db() as conn:
        cur = conn.execute("SELECT id, version FROM memories WHERE namespace=? AND key=?", (namespace, key))
        row = cur.fetchone()
        if row:
            entry_id, version = row["id"], row["version"] + 1
            conn.execute("UPDATE memories SET value=?, timestamp=?, version=? WHERE id=?", (value, now(), version, entry_id))
        else:
            entry_id = str(uuid.uuid4())[:8]
            version = 1
            conn.execute("INSERT INTO memories (id, namespace, key, value, timestamp, version) VALUES (?,?,?,?,?,?)",
                         (entry_id, namespace, key, value, now(), version))
        if pin:
            conn.execute("INSERT OR REPLACE INTO pinned_memories (id, namespace, key, value, pinned_at, version) VALUES (?,?,?,?,?,?)",
                         (entry_id, namespace, key, value, now(), version))
            conn.execute("UPDATE memories SET pinned=1 WHERE id=?", (entry_id,))
        conn.commit()
    return jsonify({"success": True, "entry": {"id": entry_id, "namespace": namespace, "key": key, "value": value, "timestamp": now(), "version": version, "pinned": 1 if pin else 0}})

@app.route("/retrieve", methods=["GET"])
def retrieve():
    namespace, key = request.args.get("namespace"), request.args.get("key")
    if not namespace or not key:
        return jsonify({"success":False,"error":"Missing namespace or key"}), 400
    with get_db() as conn:
        cur = conn.execute("SELECT * FROM memories WHERE namespace=? AND key=?", (namespace, key))
        row = cur.fetchone()
        if row: return jsonify({"success":True,"entry":dict(row)})
        return jsonify({"success":False,"error":"Not found"}), 404

@app.route("/update", methods=["POST"])
def update():
    data = request.json or {}
    namespace, key, value = data.get("namespace"), data.get("key"), data.get("value")
    if not namespace or not key:
        return jsonify({"success":False,"error":"Missing namespace or key"}), 400
    with get_db() as conn:
        cur = conn.execute("SELECT id, version FROM memories WHERE namespace=? AND key=?", (namespace, key))
        row = cur.fetchone()
        if not row: return jsonify({"success":False,"error":"Not found"}), 404
        new_version = row["version"] + 1
        conn.execute("UPDATE memories SET value=?, timestamp=?, version=? WHERE id=?", (value, now(), new_version, row["id"]))
        conn.commit()
        return jsonify({"success":True,"message":"Updated"})

@app.route("/forget", methods=["DELETE"])
def forget():
    data = request.json or {}
    namespace, key = data.get("namespace"), data.get("key")
    if not namespace or not key:
        return jsonify({"success":False,"error":"Missing namespace or key"}), 400
    with get_db() as conn:
        conn.execute("DELETE FROM memories WHERE namespace=? AND key=?", (namespace, key))
        conn.commit()
        return jsonify({"success":True,"message":"Deleted"})

@app.route("/search", methods=["GET"])
def search():
    query = request.args.get("q", "").lower()
    if not query:
        return jsonify({"success":False,"error":"Missing query"}), 400
    with get_db() as conn:
        cur = conn.execute("SELECT * FROM memories WHERE LOWER(key) LIKE ? OR LOWER(value) LIKE ?", (f"%{query}%", f"%{query}%"))
        return jsonify({"success":True,"results":[dict(r) for r in cur.fetchall()]})

@app.route("/pin", methods=["POST"])
def pin_entry():
    data = request.json or {}
    namespace, key = data.get("namespace"), data.get("key")
    if not namespace or not key:
        return jsonify({"success":False,"error":"Missing namespace or key"}), 400
    with get_db() as conn:
        cur = conn.execute("SELECT * FROM memories WHERE namespace=? AND key=?", (namespace, key))
        row = cur.fetchone()
        if not row: return jsonify({"success":False,"error":"Entry not found"}), 404
        conn.execute("INSERT OR REPLACE INTO pinned_memories (id, namespace, key, value, pinned_at, version) VALUES (?,?,?,?,?,?)",
                     (row["id"], row["namespace"], row["key"], row["value"], now(), row["version"]))
        conn.execute("UPDATE memories SET pinned=1 WHERE id=?", (row["id"],))
        conn.commit()
        return jsonify({"success":True,"message":"Entry pinned"})

@app.route("/pins", methods=["GET"])
def list_pins():
    with get_db() as conn:
        cur = conn.execute("SELECT * FROM pinned_memories")
        return jsonify({"success":True,"pins":[dict(r) for r in cur.fetchall()]})

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8007, debug=False)
