#!/bin/bash
# post_search.sh – Log search results and validate
# Usage: post_search.sh <result_count> <source> [calling_agent]

RESULT_COUNT="$1"
SOURCE="$2"
AGENT="${3:-unknown_agent}"

if [ "$RESULT_COUNT" -gt 0 ]; then
    printf "INFO: Found %s results from %s\n" "$RESULT_COUNT" "$SOURCE"
else
    printf "INFO: No results from %s\n" "$SOURCE"
fi

# Log to audit
printf "$(date -Iseconds) | %s | post_search | source=%s | count=%s\n" "$AGENT" "$SOURCE" "$RESULT_COUNT" >> ~/mycelial/logs/audit.log
printf "OK: post_search completed.\n"
exit 0
