#!/bin/bash
# post_analyze.sh – Validate report output after analyzer runs

# Check that reports directory exists and is writable
if [ ! -d ~/mycelial/reports ]; then
    mkdir -p ~/mycelial/reports
    printf "INFO: Created reports directory.\n"
fi

if [ ! -w ~/mycelial/reports ]; then
    printf "ERROR: Reports directory not writable.\n"
    exit 1
fi

printf "OK: post_analyze passed.\n"
exit 0
