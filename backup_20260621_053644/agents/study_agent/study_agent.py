#!/usr/bin/env python3
"""
Study Agent – Reads documents, stores in IPFS, and saves CID to knowledge base.
"""

import os, sys, json, sqlite3, argparse, subprocess
from datetime import datetime

BASE = os.path.expanduser("~/mycelial")
LOG_FILE = os.path.join(BASE, "logs", "audit.log")
KB_PATH = os.path.join(BASE, "databases", "sqlite", "knowledge_base.db")

def log(msg):
    ts = datetime.now().isoformat()
    with open(LOG_FILE, "a") as f:
        f.write(f"{ts} | study_agent | {msg}\n")
    print(msg)

def pin_to_ipfs(content, filename="document.txt"):
    """Add content to IPFS and return the CID."""
    # Write content to a temp file
    temp_path = f"/tmp/{filename}"
    with open(temp_path, 'w') as f:
        f.write(content)
    # Add to IPFS
    result = subprocess.run(
        ["ipfs", "add", temp_path, "--pin"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        log(f"❌ IPFS add failed: {result.stderr}")
        return None
    # Parse the CID from output
    lines = result.stdout.strip().split('\n')
    if lines:
        parts = lines[-1].split()
        if parts:
            cid = parts[1]
            log(f"✅ Document pinned to IPFS with CID: {cid}")
            return cid
    return None

def study_document(content, source_name, source_url, content_type):
    """Study document, pin to IPFS, and save CID to knowledge base."""
    log(f"📖 Studying document: {source_name}")

    # Pin to IPFS
    cid = pin_to_ipfs(content, f"{source_name.replace(' ', '_')}.txt")
    if not cid:
        return {"error": "Failed to pin to IPFS"}

    # Save to knowledge base with CID
    conn = sqlite3.connect(KB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO knowledge_base (source_name, source_url, content_type, cid, processed_date)
        VALUES (?, ?, ?, ?, date('now'))
    """, (source_name, source_url, content_type, cid))
    conn.commit()
    conn.close()
    log(f"✅ Document stored in knowledge base with CID: {cid}")

    return {"status": "stored", "cid": cid, "source_name": source_name}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--content", help="Document content to study")
    parser.add_argument("--source_name", help="Name of the source")
    parser.add_argument("--source_url", help="URL of the source")
    parser.add_argument("--content_type", help="Type of content (pdf, html, text)")
    args = parser.parse_args()

    if args.task == "study":
        if not args.content or not args.source_name:
            log("❌ --content and --source_name are required.")
            sys.exit(1)
        result = study_document(args.content, args.source_name, args.source_url, args.content_type)
        print(json.dumps(result, indent=2))
    else:
        log(f"❌ Unknown task: {args.task}")
        sys.exit(1)

if __name__ == "__main__":
    main()
# Add embedding + vector storage
def embed_and_store(text, metadata):
    # Use a local embedding model (e.g., sentence-transformers)
    # Store in Chroma/LanceDB
    pass
