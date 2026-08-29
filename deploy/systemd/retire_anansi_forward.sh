#!/bin/bash
# Retire the plaintext Anansi forwarder on 9081.
#
# install_anansi_forward.sh put socat on 0.0.0.0:9081 forwarding to Anansi's
# loopback socket, and opened the LAN to it in ufw. That predates the TLS
# proxy. nginx on 8443 now serves the webapp AND proxies /execute and /health
# to the same Anansi, behind TLS 1.3 and basic auth - so the forwarder is a
# second, unauthenticated door to the identical endpoint.
#
# Needs sudo: the unit has Restart=always, so killing the process is not
# enough - systemd brings it straight back, and so does a reboot.
#
# NOTE: this does NOT touch docker-entrypoint.sh, which also uses port 9081.
# That forwarder runs INSIDE the container and is published to the host as
# 127.0.0.1:8081, which is loopback and not this problem.
set -e

echo "Before:"
systemctl is-active anansi-forward.service || true
ss -lptn 'sport = :9081' || true

sudo systemctl disable --now anansi-forward.service
sudo ufw delete allow from 192.168.1.0/24 to any port 9081 proto tcp || true
sudo ufw reload

echo
echo "After:"
systemctl is-active anansi-forward.service || echo "  inactive (correct)"
ss -lptn 'sport = :9081' || echo "  nothing listening on 9081 (correct)"
echo
echo "Verify the replacement still works:"
echo "  curl -sk https://localhost:8443/health     # expect {\"agent\":\"anansi\"...}"
echo "  curl -sk -o /dev/null -w '%{http_code}\\n' https://localhost:8443/   # expect 401"
