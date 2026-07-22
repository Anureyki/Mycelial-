#!/bin/bash
# post_edit.sh – Validate after editing (multi‑language support)
FILE="$1"

if [[ -z "$FILE" ]]; then
    printf "ERROR: No file specified.\n"
    exit 1
fi

if [[ ! -f "$FILE" ]]; then
    printf "ERROR: File %s does not exist.\n" "$FILE"
    exit 1
fi

# Run language‑specific checks
case "$FILE" in
    *.py)
        python3 -m py_compile "$FILE" && printf "OK: Python syntax valid.\n" || { printf "ERROR: Python syntax invalid.\n"; exit 1; }
        ;;
    *.go)
        go vet "$FILE" >/dev/null 2>&1 && printf "OK: Go vet passed.\n" || { printf "ERROR: Go vet failed.\n"; exit 1; }
        ;;
    *.rs)
        rustc --crate-type lib --emit=metadata "$FILE" >/dev/null 2>&1 && printf "OK: Rust metadata valid.\n" || { printf "ERROR: Rust invalid.\n"; exit 1; }
        ;;
    *.js)
        node --check "$FILE" >/dev/null 2>&1 && printf "OK: JavaScript syntax valid.\n" || { printf "ERROR: JavaScript invalid.\n"; exit 1; }
        ;;
    *.sh)
        bash -n "$FILE" && printf "OK: Shell syntax valid.\n" || { printf "ERROR: Shell invalid.\n"; exit 1; }
        ;;
    *)
        printf "WARNING: Unknown file type %s. Skipping syntax check.\n" "$FILE"
        ;;
esac

# Log the change
printf "$(date -Iseconds) | post_edit | %s\n" "$FILE" >> ~/mycelial/logs/audit.log
printf "OK: post_edit hook passed for %s\n" "$FILE"
exit 0
