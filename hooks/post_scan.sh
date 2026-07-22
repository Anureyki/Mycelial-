#!/bin/bash
# post_scan.sh – Log scan result and update state
# Usage: post_scan.sh <file_path> <result> [details]

FILE="$1"
RESULT="$2"
DETAILS="${3:-no details}"

if [[ -z "$FILE" ]] || [[ -z "$RESULT" ]]; then
    printf "ERROR: post_scan requires file and result.\n"
    exit 1
fi

# Write to audit log
printf "$(date -Iseconds) | security_agent | post_scan | %s | %s | %s\n" "$FILE" "$RESULT" "$DETAILS" >> ~/mycelial/logs/audit.log

# Update state file (append to security_agent.json)
STATE_FILE="$HOME/mycelial/state/security_agent.json"
mkdir -p "$(dirname "$STATE_FILE")"

if [[ ! -f "$STATE_FILE" ]]; then
    echo '{"scans": []}' > "$STATE_FILE"
fi

# Use jq if available, otherwise simple append (temporary workaround)
if command -v jq &> /dev/null; then
    jq --arg file "$FILE" --arg result "$RESULT" --arg details "$DETAILS" \
       '.scans += [{"file": $file, "result": $result, "details": $details, "timestamp": (now | todate)}]' \
       "$STATE_FILE" > "$STATE_FILE.tmp" && mv "$STATE_FILE.tmp" "$STATE_FILE"
else
    # Fallback: simple echo (less robust but functional)
    echo "{\"file\":\"$FILE\",\"result\":\"$RESULT\",\"details\":\"$DETAILS\",\"timestamp\":\"$(date -Iseconds)\"}" >> "$STATE_FILE"
fi

printf "OK: post_scan logged for %s\n" "$FILE"
exit 0
