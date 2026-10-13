#!/usr/bin/env python3
"""A memory write that reports success has changed the row.

    python3 tools/check_memory_store.py

2026-10-13: eleven finding_index rows had been inserted into memories.db on
2026-09-11 with a NULL id. /store updated "WHERE id=?" - which matches nothing
for NULL - and still returned success, so every agent's findings index froze
for a month while every write to it said it worked. This runs the service
against a scratch database, plants a NULL-id row, and proves /store and
/update really change it.
"""
import os
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
fails = []


def ck(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def main():
    tmp = tempfile.mkdtemp()
    os.environ["MEMORY_DB_PATH"] = os.path.join(tmp, "memory.db")
    import importlib
    import services.memory.service as svc
    importlib.reload(svc)
    c = svc.app.test_client()
    with sqlite3.connect(os.environ["MEMORY_DB_PATH"]) as db:
        db.execute("INSERT INTO memories (id, namespace, key, value, timestamp, version) "
                   "VALUES (NULL, 'agent_x', 'finding_index', '[\"a\"]', '2026-09-11', 1)")
    print("a row written with no id")
    r = c.post("/store", json={"namespace": "agent_x", "key": "finding_index", "value": '["a","b"]'})
    got = c.get("/retrieve?namespace=agent_x&key=finding_index").get_json()["entry"]
    ck("/store success means the value changed", r.get_json().get("success") and got["value"] == '["a","b"]',
       got["value"])
    ck("and the row now has an id", bool(got["id"]))
    with sqlite3.connect(os.environ["MEMORY_DB_PATH"]) as db:
        db.execute("UPDATE memories SET id=NULL WHERE key='finding_index'")
    r = c.post("/update", json={"namespace": "agent_x", "key": "finding_index", "value": '["c"]'})
    got = c.get("/retrieve?namespace=agent_x&key=finding_index").get_json()["entry"]
    ck("/update success means the value changed", r.get_json().get("success") and got["value"] == '["c"]',
       got["value"])
    print("an ordinary row")
    c.post("/store", json={"namespace": "agent_x", "key": "k", "value": "1"})
    c.post("/store", json={"namespace": "agent_x", "key": "k", "value": "2"})
    got = c.get("/retrieve?namespace=agent_x&key=k").get_json()["entry"]
    ck("a second store updates and bumps the version", got["value"] == "2" and got["version"] == 2)
    if fails:
        print(f"\nFAIL: {len(fails)} check(s): {fails}")
        return 1
    print("\nOK - a memory write that says it worked, worked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
