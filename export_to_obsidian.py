#!/usr/bin/env python3
"""
Obsidian Exporter – reads Hermes memory and writes Markdown files.
Run this periodically (cron) or via A2A.
"""
import os
import json
import sys
from datetime import datetime

BASE = os.path.expanduser("~/mycelial")
MEMORY_FILE = os.path.join(BASE, "hermes_memory.json")
OBSIDIAN_VAULT = os.path.join(BASE, "obsidian_vault")

def load_memory():
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "r") as f:
            return json.load(f)
    return {}

def write_to_obsidian(memory):
    vault = os.path.expanduser(OBSIDIAN_VAULT)
    os.makedirs(vault, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    folder = os.path.join(vault, date_str)
    os.makedirs(folder, exist_ok=True)

    # Write each namespace as a separate file
    for namespace, data in memory.items():
        if not isinstance(data, dict):
            continue
        filename = os.path.join(folder, f"{namespace}.md")
        with open(filename, "w") as f:
            f.write(f"# {namespace.upper()}\n\n")
            for key, value in data.items():
                f.write(f"## {key}\n")
                f.write(f"- **Updated:** {datetime.now().isoformat()}\n")
                f.write(f"- **Value:** {value}\n")
                f.write("---\n\n")

    print(f"✅ Exported to Obsidian vault at {vault}")

if __name__ == "__main__":
    mem = load_memory()
    if mem:
        write_to_obsidian(mem)
    else:
        print("❌ No memory found.")
