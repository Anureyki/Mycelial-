#!/usr/bin/env python3
"""Build gate for Anansi's read-only operational_status aggregation.

All sources are stubbed - no live agents, no network, no writes. Proves the
aggregation's honesty properties (scope: unavailable sources, stale records,
partial aggregation, duplicate findings, incorrect health assumptions):

  - a port that does not answer is NEVER counted healthy, even when the registry
    claims the agent is 'active' (measurement outranks the claim; divergence named)
  - an unreachable source is reported 'available: false' with its error, never as a
    healthy or empty state (no false 0 findings / 0 patches)
  - partial aggregation: some sources up, some down, each labelled independently
  - duplicate os_security_patch findings never inflate the pending-patch count (distinct packages)
  - every section names its owning source (provenance preserved; Anansi is not the authority)
  - the capability is structurally read-only (no write/apply/release path)
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Find the Anansi agent class by the verb it defines, whatever the class is named.
import importlib
_mod = importlib.import_module("agents.anansi.Anansi")
Anansi = next(v for v in vars(_mod).values()
              if isinstance(v, type) and v.__module__ == _mod.__name__
              and "operational_status" in v.__dict__)

fails = []


def check(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    if not cond:
        fails.append(name)


def make(registry, probe_live, security, cases):
    a = object.__new__(Anansi)
    a.agent_id, a.log = "anansi", (lambda *x, **k: None)
    a._read_registry = lambda: registry
    a._probe_health = lambda port, timeout=2.5: (probe_live.get(port, False), "stub")
    a._relay_to_security = lambda verb, args: security.get(verb, {"error": "unreachable"})
    a.send_a2a = lambda target, task, args=None, timeout=120: cases
    return a


REG = {
    "grow_agent": {"port": 9009, "status": "active", "last_seen": "2026-11-14T19:55:50"},
    "trading_agent": {"port": 9016, "status": "active", "last_seen": "2026-09-10T06:42:40"},  # claims active, dead
}
PROBE = {9009: True, 9016: False}
FINDINGS = {"list_findings": {"findings": [
    {"category": "os_security_patch", "severity": "high", "package": "libc6"},
    {"category": "os_security_patch", "severity": "high", "package": "libc6"},   # DUPLICATE package
    {"category": "os_security_patch", "severity": "high", "package": "locales"},
    {"category": "secret", "severity": "critical", "package": None},
], "count": 4},
    "list_holds": {"holds": [{"id": "h1"}]},
    # double-wrapped on purpose: list_pending_approvals returns {"result": {"pending": ...}}
    "list_pending_approvals": {"result": {"pending": [{"id": "a1"}], "count": 1}}}
CASES = {"case_ids": ["matter_abc", "matter_def"]}

# --- full aggregation, all sources up ---
r = make(REG, PROBE, FINDINGS, CASES).operational_status({})
s = r["sections"]
check("read-only flag set", r["read_only"] is True)
check("a dead port is not counted healthy though the registry claims 'active' (measurement wins)",
      s["health"]["agents_responding"] == 1 and "trading_agent" in s["health"]["registered_but_not_responding"]
      and next(x for x in s["health"]["agents"] if x["agent"] == "trading_agent")["port_responding"] is False)
check("duplicate os_security_patch findings do not inflate the pending-patch count (distinct packages)",
      s["patches"]["pending_security_patches"] == 2 and set(s["patches"]["packages"]) == {"libc6", "locales"})
check("open findings counted from the source; patches are a subset, not additive",
      s["security_findings"]["open_count"] == 4 and s["patches"]["available"] is True)
check("active operations relayed from the case layer with provenance",
      s["active_operations"]["available"] is True and s["active_operations"]["cases_tracked"] == 2
      and "legal_agent" in s["active_operations"]["owner"])
check("a double-wrapped {result:{pending}} read correctly (1 pending), not misreported unavailable",
      s["approvals"]["available"] is True and s["approvals"]["count"] == 1 and s["holds"]["count"] == 1)
check("every section names its owning source (provenance preserved)",
      all("owner" in s[k] for k in ("health", "security_findings", "patches", "holds", "approvals", "active_operations")))
check("summary names the divergent agent, not a clean bill of health",
      "trading_agent" in r["summary"] and "NOT answering" in r["summary"])

# --- security source unavailable: must be 'unavailable', never 0/healthy ---
r2 = make(REG, PROBE, {"list_holds": {"error": "down"}, "list_pending_approvals": {"error": "down"}}, CASES).operational_status({})
s2 = r2["sections"]
check("security findings UNAVAILABLE reported as unavailable, never as 0 open findings",
      s2["security_findings"]["available"] is False and "open_count" not in s2["security_findings"])
check("patch status UNAVAILABLE reported as unavailable, never as 'none pending'",
      s2["patches"]["available"] is False and "UNAVAILABLE" in s2["patches"]["note"])
check("held actions / approvals UNAVAILABLE reported as unavailable",
      s2["holds"]["available"] is False and s2["approvals"]["available"] is False)
check("summary states security UNAVAILABLE rather than implying all-clear",
      "UNAVAILABLE" in r2["summary"])

# --- partial aggregation: health + holds up, findings + cases down ---
r3 = make(REG, PROBE, {"list_holds": {"holds": []}, "list_pending_approvals": {"pending": [{"id": "p"}]}},
          {"error": "legal unreachable"}).operational_status({})
s3 = r3["sections"]
check("partial aggregation: each source labelled independently (health up, findings down, approvals up, cases down)",
      s3["health"]["available"] is True and s3["security_findings"]["available"] is False
      and s3["approvals"]["available"] is True and s3["approvals"]["count"] == 1
      and s3["active_operations"]["available"] is False)

# --- structural read-only proof ---
src = open(os.path.join(ROOT, "agents", "anansi", "Anansi.py"), encoding="utf-8").read()
block = src[src.index("def operational_status"):src.index("def _operational_summary")]
forbidden = ["release_hold", "deny_hold", "apply_updates", "set_status", "requests.post",
             "store_", "_save", "write", "approve", "os.remove", "subprocess"]
check("operational_status has no write/apply/release/approve path (read-only)",
      not any(tok in block for tok in forbidden))

print("check_operational_status: " + ("ALL OK" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
