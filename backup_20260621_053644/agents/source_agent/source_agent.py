#!/usr/bin/env python3
"""
Source Agent – Queries the trusted sources database.
"""

import os, sys, json, sqlite3, argparse
from datetime import datetime

BASE = os.path.expanduser("~/mycelial")
DB_PATH = os.path.join(BASE, "databases", "sqlite", "trusted_sources.db")
LOG_FILE = os.path.join(BASE, "logs", "audit.log")

def log(msg):
    ts = datetime.now().isoformat()
    with open(LOG_FILE, "a") as f:
        f.write(f"{ts} | source_agent | {msg}\n")
    print(msg)

def query_sources(category=None, keyword=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    sql = "SELECT name, url, category, subcategory, description FROM trusted_sources WHERE active=1"
    params = []
    if category:
        sql += " AND category=?"
        params.append(category)
    if keyword:
        # Split keyword into individual words and search for each
        words = keyword.split()
        conditions = []
        for w in words:
            conditions.append("(name LIKE ? OR description LIKE ? OR url LIKE ?)")
            params.extend([f"%{w}%"] * 3)
        sql += " AND (" + " OR ".join(conditions) + ")"
    c.execute(sql, params)
    rows = c.fetchall()
    conn.close()
    return [{"name": r[0], "url": r[1], "category": r[2], "subcategory": r[3], "description": r[4]} for r in rows]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--category", help="Filter by category")
    parser.add_argument("--keyword", help="Search keyword")
    args = parser.parse_args()

    if args.task == "query":
        results = query_sources(args.category, args.keyword)
        print(json.dumps(results, indent=2))
    else:
        log(f"❌ Unknown task: {args.task}")
        sys.exit(1)

if __name__ == "__main__":
    main()

# ---------- SAVE TRUSTED SOURCE ----------
def save_source(name, url, category, subcategory, description):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT OR IGNORE INTO trusted_sources (name, url, category, subcategory, description, last_verified)
        VALUES (?, ?, ?, ?, ?, date('now'))
    """, (name, url, category, subcategory, description))
    conn.commit()
    conn.close()
    log(f"✅ Saved trusted source: {name}")

# Add to main dispatch
# We'll patch this
