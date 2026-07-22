#!/bin/bash
# pre_analyze.sh – Validate environment before analyzer runs

# Check knowledge directory exists and is readable
if [ ! -d ~/mycelial/knowledge ]; then
    printf "ERROR: Knowledge directory not found.\n"
    exit 1
fi

if [ ! -r ~/mycelial/knowledge ]; then
    printf "ERROR: Knowledge directory not readable.\n"
    exit 1
fi

printf "OK: pre_analyze passed.\n"
exit 0
