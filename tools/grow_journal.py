#!/usr/bin/env python3
"""Build the grow journal page from Grow's live records.

    python3 tools/grow_journal.py --out state/grow/journal/grow_journal.html [--days 30]

Asks the running Grow agent for each plant's journal and status card (reads
only) and renders core/journal_render. The page is published as a private
artifact by Claude; this script only writes the file.
"""
import argparse
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from core.journal_render import render_page  # noqa: E402

PLANTS = ("current_plant", "gsc_auto_2")


def grow(task, args):
    req = urllib.request.Request("http://localhost:9009/execute",
                                 data=json.dumps({"task": task, "args": args}).encode(),
                                 headers={"Content-Type": "application/json"})
    r = json.loads(urllib.request.urlopen(req, timeout=300).read())
    while isinstance(r, dict) and "result" in r and isinstance(r["result"], dict):
        r = r["result"]
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--days", type=int, default=30)
    a = ap.parse_args()
    plants = []
    for pid in PLANTS:
        j = grow("journal", {"plant_id": pid, "days": a.days})
        c = grow("status_card", {"plant_id": pid, "days": a.days})
        if j.get("journal") is not None:
            plants.append((j, c))
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), mode=0o700, exist_ok=True)
    fd = os.open(a.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(render_page(plants))
    print(f"wrote {a.out} ({sum(len(j['journal']) for j, _ in plants)} days across {len(plants)} plants)")


if __name__ == "__main__":
    main()
