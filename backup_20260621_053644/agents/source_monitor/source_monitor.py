#!/usr/bin/env python3
"""
Source Monitor Agent – Checks the health and freshness of all trusted sources.
"""

import os, sys, json, sqlite3, hashlib, requests, argparse
from datetime import datetime

BASE = os.path.expanduser("~/mycelial")
DB_PATH = os.path.join(BASE, "databases", "sqlite", "trusted_sources.db")
STATE_DIR = os.path.join(BASE, "state", "source_monitor")

def log(msg):
    # Log to file, but don't print to stdout (to keep JSON clean)
    with open(os.path.join(BASE, "logs", "audit.log"), "a") as f:
        f.write(f"{datetime.now().isoformat()} | source_monitor | {msg}\n")

def get_all_sources():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, name, url, category FROM trusted_sources WHERE active=1")
    rows = c.fetchall()
    conn.close()
    return rows

def check_url_health(url):
    try:
        resp = requests.head(url, timeout=10, allow_redirects=True)
        return resp.status_code < 400
    except:
        return False

def check_content_hash(url):
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200:
            return hashlib.md5(resp.content).hexdigest()
        return None
    except:
        return None

def update_source_status(source_id, status, hash_value=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE trusted_sources SET last_verified = date('now'), status = ? WHERE id = ?", (status, source_id))
    conn.commit()
    conn.close()

def monitor_all():
    os.makedirs(STATE_DIR, exist_ok=True)
    sources = get_all_sources()
    report = {"timestamp": datetime.now().isoformat(), "sources": []}

    for source_id, name, url, category in sources:
        healthy = check_url_health(url)
        current_hash = check_content_hash(url)

        hash_file = os.path.join(STATE_DIR, f"{source_id}.hash")
        stored_hash = None
        if os.path.exists(hash_file):
            with open(hash_file, 'r') as f:
                stored_hash = f.read().strip()

        status = "healthy"
        if not healthy:
            status = "unreachable"
        elif current_hash and stored_hash and current_hash != stored_hash:
            status = "updated"
            with open(hash_file, 'w') as f:
                f.write(current_hash)
        elif current_hash and not stored_hash:
            with open(hash_file, 'w') as f:
                f.write(current_hash)

        update_source_status(source_id, status, current_hash)
        report["sources"].append({"name": name, "url": url, "status": status})

    report_path = os.path.join(STATE_DIR, f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2)

    return report

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    args = parser.parse_args()

    if args.task == "monitor":
        report = monitor_all()
        # Only print the JSON – no other logs
        print(json.dumps(report, indent=2))
    else:
        sys.exit(1)

if __name__ == "__main__":
    main()
