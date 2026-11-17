#!/usr/bin/env python3
"""Build gate for Maintenance's scoped disk reclaim (2026-11-16).

Proves the sudo scope cannot widen and the verb cannot fake success:
- the root wrapper takes NO arguments (nothing to inject into);
- the agent invokes sudo with the FIXED wrapper constant, never a caller value;
- _reclaim_system_space PROBES before acting and, when the scoped grant is
  absent, returns available=False with an install hint (not a false success);
- when the grant is present it parses the wrapper's freed_mb.

No network, no real sudo - subprocess.run is stubbed.
"""
import os
import sys

os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

fails = []


def check(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    if not cond:
        fails.append(name)


# 1. The wrapper is argument-free (no $1/$2/$@/getopts that could change behaviour).
wrapper = open(os.path.join(ROOT, "tools", "mycelial-reclaim-disk"), encoding="utf-8").read()
import re
# awk field refs ($1, $3) are not shell positional params - strip awk programs first.
wrapper_sh = re.sub(r"awk '[^']*'", "awk ''", wrapper)
check("wrapper consumes no shell positional args (no $1/$2/$@/getopts/shift)",
      not re.search(r'\$[1-9]|\bgetopts\b|\$@|\$\*|\bshift\b|\$\{[1-9]', wrapper_sh))
check("wrapper only touches journal/snap/apt-cache (declared safe reclaims)",
      "journalctl --vacuum" in wrapper and "snap " in wrapper and "apt-get clean" in wrapper)
check("wrapper does no open-ended deletion (no `rm -rf`, no find -delete)",
      "rm -rf" not in wrapper and "-delete" not in wrapper)

# 2/3/4. Behaviour of _reclaim_system_space under a stubbed sudo.
import agents.maintenance_agent.maintenance_agent as ma

WRAPPER = ma.MaintenanceAgent.RECLAIM_WRAPPER
check("the wrapper path is a fixed absolute constant under a root-only dir",
      WRAPPER == "/usr/local/sbin/mycelial-reclaim-disk")

agent = object.__new__(ma.MaintenanceAgent)

calls = []


class _R:
    def __init__(self, rc=0, out=""):
        self.returncode, self.stdout, self.stderr = rc, out, ""


def grant_absent(argv, **kw):
    calls.append(argv)
    # `sudo -n -l <wrapper>` -> not permitted
    if "-l" in argv:
        return _R(rc=1)
    return _R(rc=0, out='{"freed_mb": 999}')  # would run if reached


def grant_present(argv, **kw):
    calls.append(argv)
    if "-l" in argv:
        return _R(rc=0)                         # permitted, no password
    return _R(rc=0, out='applied\n{"freed_mb": 5120}')


# -- grant absent: must NOT act, must report honestly --
calls.clear()
ma.subprocess.run = grant_absent
res = agent._reclaim_system_space()
probed = any("-l" in c for c in calls)
acted = any(("-l" not in c and WRAPPER in c) for c in calls)
check("grant absent -> probes with `sudo -n -l` first", probed)
check("grant absent -> does NOT run the wrapper (no false actuation)", not acted)
check("grant absent -> available False, freed_mb None, with an install hint",
      res.get("available") is False and res.get("freed_mb") is None and "install" in res)

# -- grant present: acts via the FIXED path and parses freed_mb --
calls.clear()
ma.subprocess.run = grant_present
res = agent._reclaim_system_space()
check("grant present -> runs exactly the fixed wrapper path, no caller args appended",
      any(c == ["sudo", "-n", WRAPPER] for c in calls))
check("grant present -> parses freed_mb from the wrapper's machine-readable line",
      res.get("available") is True and res.get("freed_mb") == 5120)

# every sudo argv the agent built referenced only the constant wrapper, never a variable
check("no sudo call carried anything but the fixed wrapper path",
      all(WRAPPER in c and all(tok == WRAPPER or not tok.startswith("/") for tok in c)
          for c in calls if "sudo" in c))

print("check_reclaim_disk: " + ("ALL OK" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
