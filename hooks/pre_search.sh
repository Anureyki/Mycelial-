#!/bin/bash
# pre_search.sh – Validate search request before execution

QUERY="$1"
SOURCE="$2"

if [ -z "$QUERY" ]; then
    printf "ERROR: Search query required.\n"
    exit 1
fi

if [ -z "$SOURCE" ]; then
    printf "ERROR: Search source required (github, darkweb, nvd).\n"
    exit 1
fi

# Check if Boss approval is cached (or we can prompt later)
# For now, just log and proceed

printf "OK: pre_search passed for query: %s\n" "$QUERY"
exit 0
