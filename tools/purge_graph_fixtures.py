#!/usr/bin/env python3
"""Remove development fixtures from the knowledge graph.

The graph was built during the legal and accounting work and filled with the
data those pipelines were tested against: John Doe, Alice Corp, Bob LLC, XYZ
Inc, projects named `determinism_test` and `det3`. It then surfaced on the
principal's dashboard, where a graph of someone else's unit tests is
indistinguishable from a map of their own affairs.

Reporting that as a caveat was the wrong call. A note saying "these are test
fixtures" still puts a stranger's name on the page, and the principal has to
read a disclaimer to learn that what they are looking at is not theirs. The
right move is for it not to be there.

Deletion rather than voiding, deliberately. This project's rule is *void, do not
delete*, and it applies to EVIDENCE - a record that was once believed, an amount
once claimed, a reading once taken. Fixtures are none of those. They were never
about anything, so there is no history to preserve and nothing a later reader
would be misled by losing.

**And no backup by default.** A copy of test data is still test data, sitting
where something can restore it into a real graph later. The safety net is the
dry run - which is the default, prints every node and why it matched, and writes
nothing. Once a human has read that list, a saved copy of the thing they just
decided was worthless only adds a way to put it back by accident. `--keep-backup`
exists for a graph that has real work in it.
"""
import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "state", "graph.db")

# A name matches a fixture, or the node predates any real work. Both tests are
# shown to the operator before anything happens - a purge that decides silently
# is how real data gets swept out with the test data.
FIXTURE_NAMES = ("john doe", "jane doe", "alice corp", "bob llc", "xyz inc",
                 "abc corp", "acme", "doug", "foo", "bar", "example corp")
FIXTURE_PROJECT_MARKERS = ("test", "det3", "determinism", "doc_")


def is_fixture(node_id, node_type):
    low = (node_id or "").lower()
    if any(f in low for f in FIXTURE_NAMES):
        return "name matches a known fixture"
    if node_type == "project" and any(m in low for m in FIXTURE_PROJECT_MARKERS):
        return "project id marks it as a test or an untitled document"
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="actually remove. Without it, nothing is written.")
    ap.add_argument("--keep-backup", action="store_true",
                    help="copy the db aside first. Off by default: a backup of "
                         "fixtures is a way to restore fixtures.")
    ap.add_argument("--all", action="store_true",
                    help="remove EVERY node and edge, not only matched fixtures. "
                         "For the case where the whole graph is development data.")
    a = ap.parse_args()

    if not os.path.exists(DB):
        print(f"no graph at {DB}")
        return 0
    conn = sqlite3.connect(DB)
    nodes = conn.execute("SELECT id, type, created_at FROM nodes").fetchall()
    if not nodes:
        print("graph is already empty")
        return 0

    if a.all:
        doomed = [(i, t, c, "whole-graph purge requested") for i, t, c in nodes]
    else:
        doomed = [(i, t, c, why) for i, t, c in nodes
                  if (why := is_fixture(i, t))]
    keep = [n for n in nodes if n[0] not in {d[0] for d in doomed}]

    print(f"{len(nodes)} nodes total")
    print(f"  {len(doomed)} to remove:")
    for i, t, c, why in doomed[:40]:
        print(f"    {t:<14} {i[:44]:<44} {c[:10]}  ({why})")
    print(f"  {len(keep)} to keep:")
    for i, t, c in keep[:40]:
        print(f"    {t:<14} {i[:44]:<44} {c[:10]}")
    if not doomed:
        return 0
    if not a.apply:
        print("\nDRY RUN - nothing written. Re-run with --apply.")
        return 0

    if a.keep_backup:
        backup = DB + f".bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(DB, backup)
        print(f"\nbacked up -> {backup}")
    else:
        print("\nno backup (--keep-backup to make one). The dry run was the review step.")

    ids = [d[0] for d in doomed]
    q = ",".join("?" * len(ids))
    cur = conn.cursor()
    e = cur.execute(f"DELETE FROM edges WHERE from_id IN ({q}) OR to_id IN ({q})",
                    ids + ids).rowcount
    n = cur.execute(f"DELETE FROM nodes WHERE id IN ({q})", ids).rowcount
    r = 0
    if a.all:
        r = cur.execute("DELETE FROM relationships").rowcount
    else:
        projects = [d[0] for d in doomed if d[1] == "project"]
        if projects:
            pq = ",".join("?" * len(projects))
            r = cur.execute(f"DELETE FROM relationships WHERE project_id IN ({pq})",
                            projects).rowcount
    conn.commit()
    left = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    conn.close()
    print(f"removed {n} nodes, {e} edges, {r} relationship rows. {left} nodes remain.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
