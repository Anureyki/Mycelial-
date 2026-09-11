#!/usr/bin/env python3
"""Phase 1: one container per agent. Phase 2 swaps this for Firecracker.

The Channel above does not know which of the two it is talking to, and that is
the point of the phasing - the boundary is the verb set, not the hypervisor.
Firecracker buys a separate KERNEL; a container shares the host's, so a kernel
escape crosses a container boundary and does not cross a microVM one. Until
then, this is a real boundary against the threats that are not kernel escapes:
a stolen credential, a read of another department's files, a browser profile
carrying someone else's session.

WHAT EACH AGENT GETS, AND NOTHING ELSE:

  its own writable volume        mycelial-sbx-<agent>, nothing shared
  its own credentials            from core/sandbox/credentials.py, injected at
                                 launch and never fetchable over the channel
  its own browser profile        inside its own volume
  no host mount                  the repo is NOT bind-mounted in; a sandbox
                                 that can see the source tree can see every
                                 other agent's code and the .env beside it
  a read-only root filesystem    with the volume as the one writable path
  no network by default          --network none unless the agent's allowlist
                                 says otherwise

`docker` is invoked by argv list, never through a shell, for the same reason
proc.exec is: a command string is a parser waiting to be escaped.
"""
import json
import os
import shutil
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
IMAGE = os.environ.get("MYCELIAL_SANDBOX_IMAGE", "python:3.12-slim")
PREFIX = "mycelial-sbx-"


class DockerUnavailable(RuntimeError):
    pass


def docker_ok():
    if not shutil.which("docker"):
        return False, "docker is not installed"
    try:
        r = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                           capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            return False, (r.stderr or "docker info failed").strip()[:160]
        return True, r.stdout.strip()
    except Exception as e:
        return False, str(e)[:160]


class DockerBackend:
    """Runs one container per agent and speaks the channel's verb set."""

    def __init__(self, image=IMAGE, network=False, log=None):
        self.image = image
        self.network = network
        self.log = log or (lambda *_a, **_k: None)

    def volume(self, agent_id):
        return f"{PREFIX}{agent_id}"

    def ensure(self, agent_id):
        """One volume per agent. Never shared, which is the isolation."""
        subprocess.run(["docker", "volume", "create", self.volume(agent_id)],
                       capture_output=True, text=True, timeout=30)
        return self.volume(agent_id)

    def _run(self, agent_id, argv, env=None, timeout=120):
        from core.sandbox.credentials import env_for
        self.ensure(agent_id)
        # ONLY WHAT THIS AGENT OWNS. env_for is the manifest; passing os.environ
        # here would hand every container the whole .env, which is the exact
        # state this layer exists to end.
        agent_env = env_for(agent_id)
        if env:
            agent_env.update(env)
        cmd = ["docker", "run", "--rm",
               "--read-only",                      # root fs immutable
               "--cap-drop", "ALL",
               "--security-opt", "no-new-privileges",
               "--pids-limit", "128",
               "--memory", "256m",
               "-v", f"{self.volume(agent_id)}:/work",
               "-w", "/work"]
        if not self.network:
            cmd += ["--network", "none"]
        for k, v in agent_env.items():
            cmd += ["-e", f"{k}={v}"]
        cmd += [self.image] + argv
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout or "container failed")
                               .strip()[:300])
        return r.stdout

    # ---------------- the verb set ----------------

    def execute(self, agent_id, verb, payload):
        if verb == "fs.read":
            return self._run(agent_id, ["cat", payload["path"]])
        if verb == "fs.list":
            return self._run(agent_id, ["ls", "-la", payload.get("path", ".")])
        if verb == "fs.write":
            # Content goes in via python -c reading argv, not a shell redirect.
            return self._run(agent_id, [
                "python3", "-c",
                "import sys,os;p=sys.argv[1];"
                "os.makedirs(os.path.dirname(p) or '.',exist_ok=True);"
                "open(p,'w').write(sys.argv[2]);print(len(sys.argv[2]))",
                payload["path"], payload["content"]])
        if verb == "proc.exec":
            return self._run(agent_id, payload["argv"],
                             timeout=int(payload.get("timeout", 120)))
        if verb == "net.fetch":
            if not self.network:
                raise RuntimeError(
                    "this sandbox was started with no network. net.fetch is "
                    "allowlisted at the channel and still needs a backend that "
                    "has a network - two gates, not one.")
            return self._run(agent_id, [
                "python3", "-c",
                "import sys,urllib.request;"
                "print(urllib.request.urlopen(sys.argv[1],timeout=20)"
                ".read(200000).decode('utf-8','replace'))",
                payload["url"]])
        if verb.startswith("browser."):
            raise RuntimeError(
                f"{verb} needs a browser image with a per-agent profile. "
                f"Not built: an unimplemented verb must fail loudly rather "
                f"than return an empty page that reads as a blank site.")
        raise RuntimeError(f"backend has no implementation for {verb}")


class NullBackend:
    """Records calls without running anything. For testing the BOUNDARY."""

    def __init__(self):
        self.calls = []

    def execute(self, agent_id, verb, payload):
        self.calls.append((agent_id, verb, payload))
        return {"simulated": True, "verb": verb}
