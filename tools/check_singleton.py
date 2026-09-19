#!/usr/bin/env python3
"""One agent per agent. Census the running swarm and name any duplicate.

    python3 tools/check_singleton.py            # census, exit 1 on a duplicate
    python3 tools/check_singleton.py --quiet    # just the verdict

THE PRINCIPAL'S RULE, 2026-09-19: "There should only be one grow agent, one
legal agent, one accounting agent, one trust agent, one boss agent, one
Anansi, one maintenance agent, one coding agent. There should not be
duplicates of any agent."

WHY A DUPLICATE IS NOT MERELY UNTIDY. Only one process can hold the port, so
the second is unreachable over HTTP and looks absent. It is not absent.
AgentBase.setup_mqtt() runs in __init__, BEFORE the HTTP server, so a
duplicate is on the bus from the moment it starts: subscribed to the agent's
A2A topic and, for Grow, to `mycelial/sensor/+/reading`. A second subscriber
double-counts every published reading, into a record whose uptake and mass
balance are DIFFERENCES between consecutive readings - so one duplicated row
corrupts both intervals touching it, and every individual number still looks
reasonable afterwards.

Measured 2026-09-19: two grow_agent processes, the second up since
2026-09-18 21:33 having never bound 9009. Its log said "HTTP server started
on port 9009".

THREE THINGS NOW STAND IN THE WAY, at different layers:

  core/base_agent.py   a process that cannot bind REFUSES to run
                       (tools/check_agent_bind.py gates it)
  start_all.sh         refuses to launch over a running system
  this tool            says out loud what is actually running

The first two prevent; this one VERIFIES, because a prevention nobody can
check is a claim. "Verify the effect, not the exit code."

DELIBERATELY NOT A CI GATE. In GitHub Actions no agent is running, so this
would census zero processes, find no duplicates and report ok - a green tick
measuring nothing, which is the false-success shape this repo hunts. Worse,
running it in ci_local.sh but not ci.yml would make the two disagree
depending on whether the swarm happened to be up, and this codebase has
already been bitten by a check whose answer moved with the environment. It
is an operator tool: run it on the machine that is actually running MycOS.
The CI-gateable half of this - that a process which cannot bind refuses to
run - is tools/check_agent_bind.py, which proves itself live in any
environment.

ON-DEMAND AGENTS ARE NOT MISSING. trading, ag_agent and quantum wake on
demand (CLAUDE.md), so `down` is their normal state and is reported as a
fact, never as a fault. The fault this tool hunts is TWO, never ZERO.
"""
import argparse
import os
import re
import socket
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# port -> what should answer there. From CLAUDE.md's tables; config/agent_configs
# is authoritative for agents and is cross-checked below.
EXPECTED = {
    8000: "boss_agent", 8001: "coding_agent", 8002: "hermes", 8003: "maintenance_agent",
    8081: "anansi", 9006: "analyzer_agent", 9009: "grow_agent", 9010: "security_agent",
    9011: "legal_agent", 9012: "accounting_agent", 9013: "trust_agent",
}
ON_DEMAND = {9016: "trading_agent", 9015: "ag_agent", 9014: "quantum_agent"}


def running():
    """-> {module: [pid, ...]}. Read from the process table, not from a registry.

    CLAUDE.md: the Registry said the Security Agent was active and the port
    said nothing was listening; the system believed the port. A registry row
    is a claim about state. ps is an observation."""
    out = subprocess.run(["ps", "-eo", "pid,lstart,args"], capture_output=True,
                         text=True).stdout
    found = {}
    for line in out.splitlines():
        m = re.search(r"python3?\s+(?:-u\s+)?(?:-m\s+(agents\.[A-Za-z0-9_.]+)"
                      r"|(services/[a-z_]+/[a-z_]+\.py))", line)
        if not m:
            continue
        if "check_singleton" in line or "grep" in line:
            continue
        mod = m.group(1) or m.group(2)
        pid = line.split()[0]
        started = " ".join(line.split()[1:6])
        found.setdefault(mod, []).append((pid, started))
    return found


def listening(port):
    s = socket.socket()
    s.settimeout(0.4)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    procs = running()
    dupes = {m: v for m, v in procs.items() if len(v) > 1}

    if not a.quiet:
        print("Running processes (one line per module):")
        for mod in sorted(procs):
            inst = procs[mod]
            mark = f"  <<< {len(inst)} INSTANCES" if len(inst) > 1 else ""
            pids = ", ".join(f"{p} since {t}" for p, t in inst)
            print(f"  {mod:52} {len(inst)}  [{pids}]{mark}")

        print("\nExpected agents, by port:")
        for port in sorted(EXPECTED):
            up = listening(port)
            print(f"  {port}  {EXPECTED[port]:20} {'up' if up else 'DOWN'}")
        print("\nOn-demand (down is their normal state, not a fault):")
        for port in sorted(ON_DEMAND):
            print(f"  {port}  {ON_DEMAND[port]:20} {'up' if listening(port) else 'down'}")

    print()
    if dupes:
        print("check_singleton: FAIL - duplicate agents are running")
        for mod, inst in sorted(dupes.items()):
            print(f"  - {mod}: {len(inst)} processes")
            for p, t in inst:
                print(f"      pid {p}, started {t}")
            print("      Only one holds the port. The others are on MQTT and invisible "
                  "over HTTP.")
            print(f"      Kill the newer pid(s) BY PID - never `pkill -f {mod}`, which "
                  f"takes the live one too.")
        return 1

    print(f"check_singleton: ok - {len(procs)} modules running, exactly one process each")
    return 0


if __name__ == "__main__":
    sys.exit(main())
