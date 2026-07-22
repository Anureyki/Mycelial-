#!/usr/bin/env python3
"""
PQA Agent – Public Query Agent
Handles general‑purpose, external queries using self‑hosted SearXNG.
"""

import os
import sys
import json
import argparse
import requests
from datetime import datetime

BASE = os.path.expanduser("~/mycelial")
LOG_FILE = os.path.join(BASE, "logs", "audit.log")
STATE_FILE = os.path.join(BASE, "state", "pqa_agent.json")

SEARXNG_URL = "http://localhost:8082/search"

def log(msg):
    ts = datetime.now().isoformat()
    with open(LOG_FILE, "a") as f:
        f.write(f"{ts} | pqa_agent | {msg}\n")
    print(msg)

def search_web(query):
    """Search using self‑hosted SearXNG."""
    log(f"🔍 Public search for: {query}")
    params = {"q": query, "format": "json"}
    try:
        resp = requests.get(SEARXNG_URL, params=params, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("results", [])
            return [{"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")} for r in results[:10]]
        else:
            log(f"⚠️ SearXNG error: {resp.status_code}")
            return []
    except Exception as e:
        log(f"❌ SearXNG request error: {e}")
        return []

def main():
    parser = argparse.ArgumentParser(description="PQA Agent – Public Query Agent")
    parser.add_argument("--task", required=True, help="Task to execute")
    parser.add_argument("--args", nargs="*", help="Arguments for the task")
    args = parser.parse_args()

    if args.task == "search":
        query = " ".join(args.args) if args.args else ""
        if not query:
            log("❌ No query provided")
            sys.exit(1)
        results = search_web(query)
        print(json.dumps({"source": "searxng", "results": results}, indent=2))
    else:
        log(f"❌ Unknown task: {args.task}")
        sys.exit(1)

if __name__ == "__main__":
    main()
