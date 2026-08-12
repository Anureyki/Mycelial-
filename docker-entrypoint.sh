#!/bin/bash
# Container entrypoint. start_all.sh backgrounds every agent/service and
# returns immediately (it was written to run inside a persistent tmux
# session, not as a container's PID 1) - so this wrapper keeps the
# container alive after start_all.sh hands back control, and brings up
# the Ollama daemon first since inference/service.py shells out to the
# `ollama` CLI and expects a running server.
set -e
cd /root/mycelial

mkdir -p logs
ollama serve > logs/ollama.log 2>&1 &

./start_all.sh

# core/base_agent.py binds every agent (including Anansi) to 127.0.0.1 only,
# so Docker's published port can't reach it directly from outside the
# container. Forward the container's external interface, on a different
# port (9081), to Anansi's loopback socket on 8081 - the kernel won't let
# both bind the same port number even on different addresses.
socat TCP-LISTEN:9081,fork,reuseaddr TCP:127.0.0.1:8081 > logs/socat.log 2>&1 &

exec tail -F logs/*.log
