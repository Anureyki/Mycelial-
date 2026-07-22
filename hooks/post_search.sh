#!/bin/bash
# post_search.sh – Log search results and validate

RESULT_COUNT="$1"
SOURCE="$2"

if [ "$RESULT_COUNT" -gt 0 ]; then
    printf "INFO: Found %s results from %s\n" "$RESULT_COUNT" "$SOURCE"
else
    printf "INFO: No results from %s\n" "$SOURCE"
fi

# Log to audit
printf "$(date -Iseconds) | dgta_agent | post_search | source=%s | count=%s\n" "$SOURCE" "$RESULT_COUNT" >> ~/mycelial/logs/audit.log
printf "OK: post_search completed.\n"
exit 0
