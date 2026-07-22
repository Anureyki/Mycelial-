#!/bin/bash
# pre_implement.sh – Validate environment before implementing changes
# Usage: pre_implement.sh <change_type> <target_file>

CHANGE_TYPE="$1"
TARGET_FILE="$2"

if [ -z "$CHANGE_TYPE" ] || [ -z "$TARGET_FILE" ]; then
    printf "ERROR: pre_implement requires change_type and target_file.\n"
    exit 1
fi

# Check if target file exists (if it's a modification)
if [ "$CHANGE_TYPE" = "modify" ] || [ "$CHANGE_TYPE" = "delete" ]; then
    if [ ! -f "$TARGET_FILE" ]; then
        printf "ERROR: Target file %s does not exist.\n" "$TARGET_FILE"
        exit 1
    fi
fi

# Check if we have write permission to the directory
DIR=$(dirname "$TARGET_FILE")
if [ ! -w "$DIR" ]; then
    printf "ERROR: No write permission for %s\n" "$DIR"
    exit 1
fi

# Check disk space (at least 1GB free)
AVAIL=$(df "$DIR" | awk 'NR==2 {print $4}')
if [ "$AVAIL" -lt 1048576 ]; then
    printf "ERROR: Insufficient disk space (%s). Need at least 1GB.\n" "$AVAIL"
    exit 1
fi

printf "OK: pre_implement passed for %s\n" "$TARGET_FILE"
exit 0
