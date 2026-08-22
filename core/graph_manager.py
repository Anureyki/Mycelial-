#!/usr/bin/env python3
"""
Graph Manager - a simple SQLite-backed property graph for Boss's KAG layer.

Nodes represent projects, entities (people/orgs), agents, documents, and
obligations. Edges represent typed relationships between them (PARTY,
ASSIGNED_AGENT, SIGNATORY, GOVERNS, ...). This is intentionally the
simplest thing that could work for a single-machine deployment - swap for
Neo4j (or similar) later behind the same method signatures if the graph
outgrows SQLite.

Safety note: query_graph() is reachable indirectly from the network (Boss's
`query_graph` A2A task). It is restricted to read-only SELECT statements -
both by keyword denylist AND by opening the connection itself in SQLite's
read-only URI mode, so even a denylist bypass can't mutate data.
"""
import os
import re
import json
import sqlite3
import uuid
from datetime import datetime

from .schemas import new_relationship_id, now_iso
from contextlib import contextmanager

BASE = os.path.expanduser("~/mycelial")
DEFAULT_DB_PATH = os.path.join(BASE, "state", "graph.db")

_WRITE_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|ATTACH|DETACH|PRAGMA|VACUUM|REINDEX)\b",
    re.IGNORECASE,
)


def now():
    return datetime.now().isoformat()


class GraphManager:
    def __init__(self, db_path=None):
        self.db_path = db_path or DEFAULT_DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    @contextmanager
    def _conn(self):
        """Closes the connection, not just the transaction.

        `with` on a sqlite3 Connection commits or rolls back and leaves the
        handle OPEN. Found by coding_agent's with_does_not_close scan, after
        the same leak took the Memory Service to 1,019 open descriptors and
        blinded every agent at once."""
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

    @contextmanager
    def _readonly_conn(self):
        """A connection that cannot write, regardless of what SQL is handed to it."""
        uri = f"file:{os.path.abspath(self.db_path)}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS nodes (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    properties TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS edges (
                    id TEXT PRIMARY KEY,
                    from_id TEXT NOT NULL,
                    to_id TEXT NOT NULL,
                    rel_type TEXT NOT NULL,
                    properties TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS relationships (
                    relationship_id TEXT PRIMARY KEY,
                    project_id TEXT,
                    domain TEXT,
                    data TEXT NOT NULL,
                    source_agent TEXT,
                    ingested_at TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_from ON edges(from_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_to ON edges(to_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_rel_type ON edges(rel_type)")
            conn.commit()

    # ---------- Nodes ----------
    def add_node(self, id, type, properties=None):
        """Create or fully overwrite a node. Use update_node() to merge properties instead."""
        properties = properties or {}
        ts = now()
        with self._conn() as conn:
            existing = conn.execute("SELECT created_at FROM nodes WHERE id = ?", (id,)).fetchone()
            created_at = existing["created_at"] if existing else ts
            conn.execute(
                "INSERT OR REPLACE INTO nodes (id, type, properties, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (id, type, json.dumps(properties), created_at, ts)
            )
            conn.commit()
        return self.get_node(id)

    def update_node(self, id, properties):
        """Merge `properties` into the node's existing properties (shallow merge).
        Creates the node with type 'unknown' if it doesn't exist yet."""
        existing = self.get_node(id)
        if existing is None:
            return self.add_node(id, "unknown", properties)
        merged = {**existing["properties"], **(properties or {})}
        with self._conn() as conn:
            conn.execute(
                "UPDATE nodes SET properties = ?, updated_at = ? WHERE id = ?",
                (json.dumps(merged), now(), id)
            )
            conn.commit()
        return self.get_node(id)

    def get_node(self, id):
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM nodes WHERE id = ?", (id,)).fetchone()
        return self._node_row_to_dict(row) if row else None

    def _node_row_to_dict(self, row):
        d = dict(row)
        try:
            d["properties"] = json.loads(d["properties"])
        except Exception:
            d["properties"] = {}
        return d

    def delete_node(self, id, cascade_edges=True):
        with self._conn() as conn:
            if cascade_edges:
                conn.execute("DELETE FROM edges WHERE from_id = ? OR to_id = ?", (id, id))
            conn.execute("DELETE FROM nodes WHERE id = ?", (id,))
            conn.commit()

    # ---------- Edges ----------
    def add_edge(self, from_id, to_id, rel_type, properties=None, dedupe=True):
        """Create an edge. With dedupe=True (default), skips creating an identical
        (from_id, to_id, rel_type, properties) edge that already exists, so repeated
        pushes of the same relationship don't balloon the edge table."""
        properties = properties or {}
        if dedupe:
            for edge in self.get_edges(from_id=from_id, to_id=to_id, rel_type=rel_type):
                if edge["properties"] == properties:
                    return edge
        edge_id = str(uuid.uuid4())[:12]
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO edges (id, from_id, to_id, rel_type, properties, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (edge_id, from_id, to_id, rel_type, json.dumps(properties), now())
            )
            conn.commit()
        return self.get_edge(edge_id)

    def get_edge(self, id):
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM edges WHERE id = ?", (id,)).fetchone()
        return self._edge_row_to_dict(row) if row else None

    def _edge_row_to_dict(self, row):
        d = dict(row)
        try:
            d["properties"] = json.loads(d["properties"])
        except Exception:
            d["properties"] = {}
        return d

    def get_edges(self, from_id=None, to_id=None, rel_type=None, limit=1000):
        query = "SELECT * FROM edges WHERE 1=1"
        params = []
        if from_id is not None:
            query += " AND from_id = ?"
            params.append(from_id)
        if to_id is not None:
            query += " AND to_id = ?"
            params.append(to_id)
        if rel_type is not None:
            query += " AND rel_type = ?"
            params.append(rel_type)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._edge_row_to_dict(r) for r in rows]

    def delete_edge(self, id):
        with self._conn() as conn:
            conn.execute("DELETE FROM edges WHERE id = ?", (id,))
            conn.commit()

    # ---------- Aggregate lookups ----------
    def get_entity_relationships(self, entity_id):
        """All nodes/edges directly connected to entity_id (its own node, if any,
        plus every edge touching it and the nodes on the other end)."""
        node = self.get_node(entity_id)
        edges = self.get_edges(from_id=entity_id) + self.get_edges(to_id=entity_id)
        # de-dupe (an edge could theoretically match both filters if from==to)
        edges_by_id = {e["id"]: e for e in edges}
        connected_ids = set()
        for e in edges_by_id.values():
            connected_ids.add(e["from_id"])
            connected_ids.add(e["to_id"])
        connected_ids.discard(entity_id)
        connected_nodes = [n for n in (self.get_node(nid) for nid in connected_ids) if n]
        return {
            "entity_id": entity_id,
            "node": node,
            "edges": list(edges_by_id.values()),
            "connected_nodes": connected_nodes,
        }

    def get_project_relationships(self, project_id):
        """All nodes/edges whose properties.project_id matches. Filtered in Python
        rather than via SQL json_extract for portability across SQLite builds."""
        with self._conn() as conn:
            node_rows = conn.execute("SELECT * FROM nodes").fetchall()
            edge_rows = conn.execute("SELECT * FROM edges").fetchall()
        nodes = [self._node_row_to_dict(r) for r in node_rows]
        edges = [self._edge_row_to_dict(r) for r in edge_rows]
        matched_nodes = [n for n in nodes if n["properties"].get("project_id") == project_id]
        matched_edges = [e for e in edges if e["properties"].get("project_id") == project_id]
        return {"project_id": project_id, "nodes": matched_nodes, "edges": matched_edges}

    # ---------- Raw query (read-only) ----------
    def query_graph(self, sql, params=None):
        """Run a read-only SELECT against the graph DB. Rejects anything that isn't
        a single SELECT statement, and additionally opens the connection itself in
        SQLite read-only mode so a denylist bypass still can't mutate data."""
        stripped = sql.strip().rstrip(";")
        if ";" in stripped:
            raise ValueError("query_graph: only a single statement is allowed")
        if not re.match(r"^\s*(SELECT|WITH)\b", stripped, re.IGNORECASE):
            raise ValueError("query_graph: only SELECT (or WITH ... SELECT) statements are allowed")
        if _WRITE_KEYWORDS.search(stripped):
            raise ValueError("query_graph: write/DDL keywords are not allowed")
        conn = self._readonly_conn()
        try:
            rows = conn.execute(stripped, params or []).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ---------- Convenience: build from a canonical relationship dict ----------
    def ingest_relationship(self, relationship, source_agent=None):
        """Turn a core.schemas relationship dict into graph nodes + edges, and
        archive the full raw relationship JSON in the `relationships` table
        (keyed by relationship_id) for audit/lookup by id.

        Creates/updates a relationship node, a project node (if project_id set),
        a node per party, and PARTY / GOVERNS / BENEFICIARY / SERVICE_PROVIDER /
        FEE_RECIPIENT edges. Idempotent (add_edge dedupes)."""
        rel_id = relationship.get("relationship_id") or new_relationship_id()
        project_id = relationship.get("project_id") or None
        domain = relationship.get("domain")
        ingested_at = now_iso()

        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO relationships "
                "(relationship_id, project_id, domain, data, source_agent, ingested_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (rel_id, project_id, domain, json.dumps(relationship), source_agent, ingested_at)
            )
            conn.commit()

        self.add_node(rel_id, "relationship", {
            "domain": domain,
            "project_id": project_id,
            "governing_law": relationship.get("governing_law"),
            "assets": relationship.get("assets", []),
            "obligations": relationship.get("obligations", []),
            "rights": relationship.get("rights", []),
            "source_agent": source_agent,
            "timestamp": relationship.get("timestamp"),
        })

        if project_id:
            self.add_node(project_id, "project", {})
            self.add_edge(project_id, rel_id, "HAS_RELATIONSHIP", {"project_id": project_id})

        for party in relationship.get("parties", []):
            pid, role = party.get("id"), party.get("role", "party")
            if not pid:
                continue
            self.add_node(pid, "entity", {})
            self.add_edge(pid, rel_id, "PARTY", {"role": role, "project_id": project_id})

        for role_field, rel_type in (
            ("beneficiary", "BENEFICIARY"),
            ("service_provider", "SERVICE_PROVIDER"),
            ("fee_recipient", "FEE_RECIPIENT"),
        ):
            entity_id = relationship.get(role_field)
            if entity_id:
                self.add_node(entity_id, "entity", {})
                self.add_edge(entity_id, rel_id, rel_type, {"project_id": project_id})

        if source_agent:
            self.add_node(source_agent, "agent", {})
            self.add_edge(source_agent, rel_id, "ASSIGNED_AGENT", {"project_id": project_id})

        return rel_id
