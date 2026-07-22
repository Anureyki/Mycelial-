#!/bin/bash
# pre_deploy.sh – Run tests before deployment
# Usage: pre_deploy.sh <target_environment>

TARGET="${1:-local}"

echo "Running pre‑deployment checks for $TARGET..."

# Check that all tests pass (if test suite exists)
if [[ -f ~/AgTechAI/test.py ]]; then
    cd ~/AgTechAI && source venv/bin/activate && python test.py
    if [[ $? -ne 0 ]]; then
        echo "ERROR: Tests failed."
        exit 1
    fi
fi

# Verify that source of truth README is valid
if [[ ! -f ~/mycelial/README.md ]]; then
    echo "ERROR: Source of truth (README.md) missing."
    exit 1
fi

# Ensure Docker is running (if deploying container)
if command -v docker &> /dev/null; then
    docker ps > /dev/null 2>&1 || echo "WARNING: Docker daemon not responding."
fi

echo "✓ pre_deploy hook passed."
exit 0
