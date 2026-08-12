FROM python:3.14-slim

# System deps: build tools for native wheels (h5py, ctranslate2, PyAudio),
# ffmpeg for faster-whisper/av, git for GitPython, curl for the Ollama installer
# and container health checks.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        portaudio19-dev \
        ffmpeg \
        git \
        curl \
        zstd \
        socat \
    && rm -rf /var/lib/apt/lists/*

# inference/service.py shells out to the `ollama` CLI directly (not HTTP),
# so the binary has to live in the same container/network namespace.
RUN curl -fsSL https://ollama.com/install.sh | sh

# start_all.sh does `cd ~/mycelial`, so the app has to live at $HOME/mycelial
# (root's default HOME is /root) for that to resolve the same way it does
# when the script runs directly on a host.
WORKDIR /root/mycelial

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p logs state

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/root/mycelial

# Only Anansi is meant to be reachable from outside the container; every
# other agent/service talks to its neighbors over localhost inside this
# same container, matching how they're wired today (see core/base_agent.py).
# 9081 is the socat forwarder in front of Anansi's actual loopback-bound 8081.
EXPOSE 9081

RUN chmod +x docker-entrypoint.sh start_all.sh
CMD ["./docker-entrypoint.sh"]
