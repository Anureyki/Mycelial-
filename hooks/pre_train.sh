#!/bin/bash
# This hook runs before any training. It ensures the CSV exists.

if [ -z "$(ls -A ~/grower-node/sensor_data/*.csv 2>/dev/null)" ]; then
    echo "ERROR: No CSV found in ~/grower-node/sensor_data/"
    exit 1
fi
echo "✓ CSV found. Hook passed."
exit 0
