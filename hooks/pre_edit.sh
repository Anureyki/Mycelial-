#!/bin/bash
# pre_edit.sh – Validate file before editing
# Usage: pre_edit.sh <file_path>

FILE="$1"

if [[ -z "$FILE" ]]; then
    echo "ERROR: No file specified."
    exit 1
fi

if [[ ! -f "$FILE" ]]; then
    echo "ERROR: File $FILE does not exist."
    exit 1
fi

# Prevent editing hooks and README
if [[ "$FILE" == *"/hooks/"* || "$FILE" == *"/README.md" ]]; then
    echo "ERROR: Cannot edit hooks or README without DID signature."
    exit 1
fi

# Prevent editing the central source of truth (root README)
if [[ "$FILE" == "$HOME/mycelial/README.md" ]]; then
    echo "ERROR: Cannot edit root README without DID signature."
    exit 1
fi

# Check if file is a Python or shell script – if so, check syntax
if [[ "$FILE" == *.py ]]; then
    python3 -m py_compile "$FILE" 2>/dev/null
    if [[ $? -ne 0 ]]; then
        echo "WARNING: Python syntax check failed before editing. Proceed with caution."
    fi
fi

echo "✓ pre_edit hook passed for $FILE"
exit 0
