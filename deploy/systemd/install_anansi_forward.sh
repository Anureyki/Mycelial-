#!/bin/bash
set -e
cp /home/anureyki/mycelial/deploy/systemd/anansi-forward.service /etc/systemd/system/anansi-forward.service
systemctl daemon-reload
systemctl enable --now anansi-forward.service
ufw allow from 192.168.1.0/24 to any port 9081 proto tcp
ufw reload
systemctl status anansi-forward.service --no-pager
