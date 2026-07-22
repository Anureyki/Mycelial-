#!/bin/bash
# pre_scan.sh – Validate scan environment and file before scanning
# Usage: pre_scan.sh <file_path>

FILE="$1"

if [[ -z "$FILE" ]]; then
    printf "ERROR: No file specified for pre-scan.\n"
    exit 1
fi

if [[ ! -f "$FILE" ]]; then
    printf "ERROR: File %s does not exist.\n" "$FILE"
    exit 1
fi

# Check file size (reject files > 10MB to avoid analysis overload)
FILE_SIZE=$(stat -c%s "$FILE" 2>/dev/null || stat -f%z "$FILE" 2>/dev/null)
if [[ "$FILE_SIZE" -gt 10485760 ]]; then
    printf "ERROR: File %s exceeds size limit (10MB).\n" "$FILE"
    exit 1
fi

# Check file type (only allow known safe types initially)
FILE_TYPE=$(file -b --mime-type "$FILE")
case "$FILE_TYPE" in
    text/plain|text/csv|application/json|text/xml|application/xml|image/*)
        printf "OK: File type %s allowed.\n" "$FILE_TYPE"
        ;;
    *)
        printf "WARNING: File type %s is not explicitly allowed. Proceed with caution.\n" "$FILE_TYPE"
        ;;
esac

printf "OK: pre_scan passed for %s\n" "$FILE"
exit 0
