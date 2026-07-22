#!/bin/bash
# quarantine.sh – Move suspicious file to quarantine and lock it
# Usage: quarantine.sh <file_path> <reason>

FILE="$1"
REASON="$2"

if [[ -z "$FILE" ]] || [[ -z "$REASON" ]]; then
    printf "ERROR: quarantine requires file and reason.\n"
    exit 1
fi

if [[ ! -f "$FILE" ]]; then
    printf "ERROR: File %s does not exist.\n" "$FILE"
    exit 1
fi

# Create quarantine directory if it doesn't exist
QUARANTINE_DIR="$HOME/mycelial/quarantine"
mkdir -p "$QUARANTINE_DIR"

# Generate a unique quarantine name with timestamp
TIMESTAMP=$(date -Iseconds | tr ':' '-' | tr '+' 'Z')
BASENAME=$(basename "$FILE")
QUARANTINE_PATH="$QUARANTINE_DIR/${BASENAME}_${TIMESTAMP}"

# Move file to quarantine
mv "$FILE" "$QUARANTINE_PATH"

# Make it read-only to prevent accidental execution
chmod 444 "$QUARANTINE_PATH"

# Write quarantine record
printf "$(date -Iseconds) | security_agent | quarantine | %s | %s | moved to %s\n" "$FILE" "$REASON" "$QUARANTINE_PATH" >> ~/mycelial/logs/audit.log

# Create a metadata file
cat > "${QUARANTINE_PATH}.meta" << EOJ
Original path: $FILE
Reason: $REASON
Quarantined on: $(date -Iseconds)
EOJ

printf "OK: File quarantined at %s\n" "$QUARANTINE_PATH"
exit 0
