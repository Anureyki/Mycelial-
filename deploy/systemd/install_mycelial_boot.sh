#!/bin/bash
set -e
cp /home/anureyki/mycelial/deploy/systemd/mycelial.service /etc/systemd/system/mycelial.service
systemctl daemon-reload
systemctl enable mycelial.service
echo "--- enabled (not started; the stack is already running) ---"
systemctl is-enabled mycelial.service
