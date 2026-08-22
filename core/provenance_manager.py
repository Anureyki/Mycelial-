#!/usr/bin/env python3
"""
Provenance Manager - SQLite-backed store for provenance events and the
artifacts they describe. Backs services/provenance/service.py, same split
as core/graph_manager.py backs the Registry/Boss KAG layer: this module
owns the storage + derivation logic, the Flask service is a thin HTTP
wrapper around it.

Immutability: events are append-only. A modification to an artifact is
recorded as a NEW artifact_id with parent_artifact_id pointing at the
previous version - record_event() refuses to change an existing artifact's
artifact_hash in place. This is what "do not overwrite previous
provenance" (spec section 6) means in practice: lineage is a chain of
artifact versions, not a single row that gets silently mutated.
"""
import os
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from .provenance_schemas import (
    validate_event, classify_origin, verification_status, sha256_hex, now_iso,
)

BASE = os.path.expanduser("~/mycelial")
DEFAULT_DB_PATH = os.path.join(BASE, "state", "provenance.db")

_JSON_EVENT_FIELDS = {"tools_used", "input_artifacts", "output_artifacts", "metadata"}


class ArtifactConflictError(Exception):
    """Raised when a caller tries to change an existing artifact's content
    hash in place instead of recording a new, child artifact_id."""


class ProvenanceManager:
    def __init__(self, db_path=None):
        self.db_path = db_path or DEFAULT_DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    parent_artifact_id TEXT,
                    artifact_hash TEXT,
                    parent_artifact_hash TEXT,
                    provenance_hash TEXT,
                    origin_classification TEXT,
                    verification_status TEXT NOT NULL DEFAULT 'RECORDED',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    execution_id TEXT,
                    artifact_id TEXT,
                    parent_artifact_id TEXT,
                    actor_type TEXT NOT NULL,
                    actor_id TEXT,
                    operation TEXT NOT NULL,
                    agent_id TEXT,
                    model_id TEXT,
                    tools_used TEXT NOT NULL DEFAULT '[]',
                    timestamp TEXT NOT NULL,
                    input_artifacts TEXT NOT NULL DEFAULT '[]',
                    output_artifacts TEXT NOT NULL DEFAULT '[]',
                    human_contribution INTEGER NOT NULL DEFAULT 0,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    recorded_at TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_artifact ON events(artifact_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_execution ON events(execution_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_parent ON artifacts(parent_artifact_id)")
            conn.commit()

    # ---------- internal helpers ----------
    def _row_to_event(self, row):
        d = dict(row)
        for f in _JSON_EVENT_FIELDS:
            d[f] = json.loads(d[f]) if d.get(f) else ([] if f != "metadata" else {})
        d["human_contribution"] = bool(d["human_contribution"])
        return d

    def _get_artifact_row(self, conn, artifact_id):
        return conn.execute(
            "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
        ).fetchone()

    def _events_for_artifact(self, conn, artifact_id):
        rows = conn.execute(
            "SELECT * FROM events WHERE artifact_id = ? ORDER BY timestamp ASC, recorded_at ASC",
            (artifact_id,),
        ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def _compute_provenance_hash(self, artifact_id, events):
        """Hash chain over an artifact's full recorded event history, so a
        later request can detect if the stored event log itself has been
        tampered with (recompute and compare)."""
        chain = "|".join(
            f"{e['event_id']}:{e['timestamp']}:{e['operation']}:{e['actor_type']}:{e.get('agent_id') or e.get('actor_id') or ''}"
            for e in events
        )
        return sha256_hex(f"{artifact_id}|{chain}")

    # ---------- writes ----------
    def record_event(self, event, artifact_content=None):
        """Validate and persist a provenance event. If event['artifact_id']
        is new, creates an artifacts row (hashing artifact_content if
        given). If it already exists and artifact_content is given, the
        computed hash must match the stored one - use a new artifact_id
        (with parent_artifact_id set) to record a modification instead."""
        is_valid, errors = validate_event(event)
        if not is_valid:
            raise ValueError(f"invalid provenance event: {errors}")

        artifact_id = event.get("artifact_id")
        recorded_at = now_iso()

        with self._conn() as conn:
            if artifact_id:
                existing = self._get_artifact_row(conn, artifact_id)
                new_hash = sha256_hex(artifact_content)

                if existing is None:
                    parent_id = event.get("parent_artifact_id")
                    parent_row = self._get_artifact_row(conn, parent_id) if parent_id else None
                    conn.execute(
                        "INSERT INTO artifacts (artifact_id, parent_artifact_id, artifact_hash, "
                        "parent_artifact_hash, provenance_hash, origin_classification, "
                        "verification_status, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (artifact_id, parent_id, new_hash,
                         parent_row["artifact_hash"] if parent_row else None,
                         None, None, "RECORDED", recorded_at, recorded_at),
                    )
                elif new_hash is not None and existing["artifact_hash"] is not None \
                        and new_hash != existing["artifact_hash"]:
                    raise ArtifactConflictError(
                        f"artifact_id {artifact_id!r} already has a different recorded hash; "
                        f"record the modification under a new artifact_id with "
                        f"parent_artifact_id={artifact_id!r} instead of overwriting it"
                    )
                elif new_hash is not None and existing["artifact_hash"] is None:
                    # First time we've seen content for a previously content-less record.
                    conn.execute(
                        "UPDATE artifacts SET artifact_hash = ?, updated_at = ? WHERE artifact_id = ?",
                        (new_hash, recorded_at, artifact_id),
                    )

            conn.execute(
                "INSERT INTO events (event_id, execution_id, artifact_id, parent_artifact_id, "
                "actor_type, actor_id, operation, agent_id, model_id, tools_used, timestamp, "
                "input_artifacts, output_artifacts, human_contribution, metadata, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event["event_id"], event.get("execution_id"), artifact_id,
                    event.get("parent_artifact_id"), event["actor_type"], event.get("actor_id"),
                    event["operation"], event.get("agent_id"), event.get("model_id"),
                    json.dumps(event.get("tools_used") or []), event["timestamp"],
                    json.dumps(event.get("input_artifacts") or []),
                    json.dumps(event.get("output_artifacts") or []),
                    1 if event.get("human_contribution") else 0,
                    json.dumps(event.get("metadata") or {}), recorded_at,
                ),
            )

            if artifact_id:
                events = self._events_for_artifact(conn, artifact_id)
                provenance_hash = self._compute_provenance_hash(artifact_id, events)
                origin = classify_origin(events)
                conn.execute(
                    "UPDATE artifacts SET provenance_hash = ?, origin_classification = ?, "
                    "updated_at = ? WHERE artifact_id = ?",
                    (provenance_hash, origin, recorded_at, artifact_id),
                )

            conn.commit()

        return event

    # ---------- reads ----------
    def get_artifact_history(self, artifact_id):
        with self._conn() as conn:
            artifact_row = self._get_artifact_row(conn, artifact_id)
            if artifact_row is None:
                return None
            events = self._events_for_artifact(conn, artifact_id)
        return {"artifact": dict(artifact_row), "events": events}

    def get_execution_events(self, execution_id):
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE execution_id = ? ORDER BY timestamp ASC, recorded_at ASC",
                (execution_id,),
            ).fetchall()
            return [self._row_to_event(r) for r in rows]

    def get_lineage(self, artifact_id):
        """Returns {"ancestors": [root, ..., self], "descendants": [child, ...]}.
        Ancestors walk parent_artifact_id up to the root; descendants are
        direct children only (one level - callers can recurse further via
        repeated calls, which also naturally prevents cycles from causing
        unbounded walks)."""
        with self._conn() as conn:
            ancestors = []
            current = self._get_artifact_row(conn, artifact_id)
            seen = set()
            while current is not None and current["artifact_id"] not in seen:
                seen.add(current["artifact_id"])
                ancestors.insert(0, dict(current))
                parent_id = current["parent_artifact_id"]
                current = self._get_artifact_row(conn, parent_id) if parent_id else None

            descendant_rows = conn.execute(
                "SELECT * FROM artifacts WHERE parent_artifact_id = ?", (artifact_id,)
            ).fetchall()
            descendants = [dict(r) for r in descendant_rows]

        return {"ancestors": ancestors, "descendants": descendants}

    def classify_artifact_origin(self, artifact_id):
        with self._conn() as conn:
            events = self._events_for_artifact(conn, artifact_id)
        return classify_origin(events)

    def verify_artifact(self, artifact_id, current_content=None):
        with self._conn() as conn:
            row = self._get_artifact_row(conn, artifact_id)
            has_record = row is not None
            stored_hash = row["artifact_hash"] if has_record else None
            current_hash = sha256_hex(current_content)
            status = verification_status(has_record, stored_hash, current_hash)
            if has_record and current_hash is not None:
                conn.execute(
                    "UPDATE artifacts SET verification_status = ?, updated_at = ? WHERE artifact_id = ?",
                    (status, now_iso(), artifact_id),
                )
                conn.commit()
        return {
            "artifact_id": artifact_id,
            "status": status,
            "stored_hash": stored_hash,
            "current_hash": current_hash,
        }
