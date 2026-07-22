#!/usr/bin/env python3
"""
Legal Agent – Manages legal paperwork, deadlines, and filings.
"""

import os, sys, json, argparse
from datetime import datetime

BASE = os.path.expanduser("~/mycelial")
LOG_FILE = os.path.join(BASE, "logs", "audit.log")
DOCS_DIR = os.path.join(BASE, "documents", "legal")

def log(msg):
    ts = datetime.now().isoformat()
    with open(LOG_FILE, "a") as f:
        f.write(f"{ts} | legal_agent | {msg}\n")
    print(msg)

def track_document(doc_name, doc_type, deadline, status="pending"):
    os.makedirs(DOCS_DIR, exist_ok=True)
    doc_path = os.path.join(DOCS_DIR, f"{doc_name}.json")
    data = {
        "name": doc_name,
        "type": doc_type,
        "deadline": deadline,
        "status": status,
        "created": datetime.now().isoformat()
    }
    with open(doc_path, 'w') as f:
        json.dump(data, f, indent=2)
    log(f"📄 Document tracked: {doc_name}")
    return data

def list_documents(status=None):
    os.makedirs(DOCS_DIR, exist_ok=True)
    docs = []
    for f in os.listdir(DOCS_DIR):
        if f.endswith('.json'):
            with open(os.path.join(DOCS_DIR, f), 'r') as fp:
                data = json.load(fp)
                if not status or data.get('status') == status:
                    docs.append(data)
    return docs

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--name", help="Document name")
    parser.add_argument("--type", help="Document type (contract, filing, form, etc.)")
    parser.add_argument("--deadline", help="Deadline date (YYYY-MM-DD)")
    parser.add_argument("--status", help="Filter by status")
    args = parser.parse_args()

    if args.task == "track":
        if not args.name or not args.type or not args.deadline:
            log("❌ name, type, and deadline are required.")
            sys.exit(1)
        result = track_document(args.name, args.type, args.deadline)
        print(json.dumps(result, indent=2))
    elif args.task == "list":
        result = list_documents(args.status)
        print(json.dumps(result, indent=2))
    else:
        log(f"❌ Unknown task: {args.task}")
        sys.exit(1)

if __name__ == "__main__":
    main()
