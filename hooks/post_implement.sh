#!/bin/bash
# post_implement.sh – Validate after implementing changes
# Usage: post_implement.sh <change_type> <target_file>

CHANGE_TYPE="$1"
TARGET_FILE="$2"

# If file was created or modified, check syntax
if [ "$CHANGE_TYPE" = "create" ] || [ "$CHANGE_TYPE" = "modify" ]; then
    if [ -f "$TARGET_FILE" ]; then
        # For shell scripts, check syntax
        if [[ "$TARGET_FILE" == *.sh ]]; then
            bash -n "$TARGET_FILE"
            if [ $? -ne 0 ]; then
                printf "ERROR: Shell script syntax error in %s\n" "$TARGET_FILE"
                exit 1
            fi
        fi
        # For Python scripts
        if [[ "$TARGET_FILE" == *.py ]]; then
            python3 -m py_compile "$TARGET_FILE"
            if [ $? -ne 0 ]; then
                printf "ERROR: Python syntax error in %s\n" "$TARGET_FILE"
                exit 1
            fi
        fi
        # For MD files, just check it's not empty
        if [[ "$TARGET_FILE" == *.md ]]; then
            if [ ! -s "$TARGET_FILE" ]; then
                printf "ERROR: MD file %s is empty.\n" "$TARGET_FILE"
                exit 1
            fi
        fi
    fi
fi

# Log the change
printf "$(date -Iseconds) | codingagent | post_implement | %s | %s\n" "$CHANGE_TYPE" "$TARGET_FILE" >> ~/mycelial/logs/audit.log
printf "OK: post_implement passed for %s\n" "$TARGET_FILE"
exit 0
