#!/bin/bash
# eliminate.sh – Ask human for permission before permanently deleting a threat
# Usage: eliminate.sh <file_path> <reason> [source_url]

FILE="$1"
REASON="$2"
SOURCE_URL="${3:-unknown}"

if [[ -z "$FILE" ]] || [[ -z "$REASON" ]]; then
    printf "ERROR: eliminate requires file and reason.\n"
    exit 1
fi

if [[ ! -f "$FILE" ]]; then
    printf "ERROR: File %s does not exist.\n" "$FILE"
    exit 1
fi

# Display threat information
printf "\n⚠️  ELIMINATION REQUEST ⚠️\n"
printf "File: %s\n" "$FILE"
printf "Reason: %s\n" "$REASON"
printf "Source: %s\n" "$SOURCE_URL"
printf "\nThis action will PERMANENTLY DELETE the file.\n"

# Ask for permission
printf "Do you approve elimination? (y/N): "
read -r APPROVAL

if [[ "$APPROVAL" != "y" && "$APPROVAL" != "Y" ]]; then
    printf "❌ Elimination rejected by human.\n"
    printf "$(date -Iseconds) | security_agent | ELIMINATE_REJECTED | %s | %s\n" "$FILE" "$REASON" >> ~/mycelial/logs/audit.log
    exit 1
fi

# Ask for validation reason
printf "\nPlease provide a validation reason for this elimination: "
read -r VALIDATION

if [[ -z "$VALIDATION" ]]; then
    printf "❌ Validation reason required. Elimination aborted.\n"
    exit 1
fi

# Confirm again
printf "\nYou are about to delete: %s\n" "$FILE"
printf "Validation: %s\n" "$VALIDATION"
printf "Confirm final deletion? (y/N): "
read -r FINAL

if [[ "$FINAL" != "y" && "$FINAL" != "Y" ]]; then
    printf "❌ Elimination cancelled by human.\n"
    exit 1
fi

# Perform elimination
printf "$(date -Iseconds) | security_agent | ELIMINATE | %s | %s | source: %s | validation: %s\n" "$FILE" "$REASON" "$SOURCE_URL" "$VALIDATION" >> ~/mycelial/logs/audit.log

# Add source to blocklist (if provided and human approved)
if [[ "$SOURCE_URL" != "unknown" ]]; then
    BLOCKLIST="$HOME/mycelial/state/blocklist.txt"
    mkdir -p "$(dirname "$BLOCKLIST")"
    echo "$SOURCE_URL" >> "$BLOCKLIST"
    printf "OK: Source %s added to blocklist.\n" "$SOURCE_URL"
fi

# Permanently delete the file
rm -f "$FILE"
printf "OK: File %s eliminated (deleted).\n" "$FILE"

# Update state file
STATE_FILE="$HOME/mycelial/state/security_agent.json"
mkdir -p "$(dirname "$STATE_FILE")"
if [[ -f "$STATE_FILE" ]]; then
    if command -v jq &> /dev/null; then
        jq --arg file "$FILE" --arg reason "$REASON" --arg source "$SOURCE_URL" --arg validation "$VALIDATION" \
           '.eliminations += [{"file": $file, "reason": $reason, "source": $source, "validation": $validation, "timestamp": (now | todate)}]' \
           "$STATE_FILE" > "$STATE_FILE.tmp" && mv "$STATE_FILE.tmp" "$STATE_FILE"
    fi
fi

exit 0
