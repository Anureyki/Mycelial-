#!/usr/bin/env python3
"""Build gate for Security's read-only security_patch_scan (Maintenance detects,
Security assesses). Synthetic detection only - nothing runs apt, nothing touches
the live findings store or the real Maintenance agent.

Proves:
  - a security-flagged critical-component update (glibc, openssl) is classified,
    carries system impact + post-update action, severity "high", CVE unknown
  - a NON-security version bump is not turned into a security finding
  - an unfamiliar security package is FLAGGED for investigation, not auto-classified,
    and its severity is not inflated
  - vulnerability/CVE severity is never inferred (stage 2 not integrated)
  - re-scanning does not duplicate findings (one per package)
  - an unreachable detector is "unknown", flags nothing (absence of scan != no patches)
  - the capability is structurally read-only: no apt/subprocess/restart/reboot path,
    and Maintenance's detection uses only read-only apt
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from agents.security_agent.security_agent import SecurityAgent  # noqa: E402

fails = []


def check(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    if not cond:
        fails.append(name)


DETECT = {"result": {
    "upgradable_count": 4,
    "packages": ["libc6 ...", "docker-ce ...", "zzz-sec ...", "openssl ..."],
    "details": [
        {"name": "libc6", "origin": "resolute-updates,resolute-security",
         "candidate": "2.43-2ubuntu2.7", "architecture": "amd64",
         "installed": "2.43-2ubuntu2.4", "security": True,
         "download_size_bytes": 3000000, "installed_size_kb": 12000},
        {"name": "docker-ce", "origin": "resolute",  # non-security bump
         "candidate": "5:29.9.3", "architecture": "amd64",
         "installed": "5:29.9.2", "security": False,
         "download_size_bytes": 1, "installed_size_kb": 1},
        {"name": "zzz-unknown-sec-lib", "origin": "resolute-security",  # unfamiliar
         "candidate": "1.2", "architecture": "all", "installed": "1.1",
         "security": True, "download_size_bytes": 100, "installed_size_kb": 5},
        {"name": "openssl", "origin": "resolute-security",
         "candidate": "3.0.1", "architecture": "amd64", "installed": "3.0.0",
         "security": True, "download_size_bytes": 500, "installed_size_kb": 10},
    ],
    "security_count": 3, "reboot_required": True, "reboot_required_packages": ["libc6"],
}}


def make_agent(detect):
    a = object.__new__(SecurityAgent)
    a.agent_id = "security_agent"
    a.log = lambda *x, **k: None
    a._store = []
    a._load_findings = lambda: a._store
    a._save_findings = lambda f: setattr(a, "_store", f)
    a.send_a2a = lambda target, task, args=None, timeout=120: (
        detect if (target == "maintenance_agent" and task == "check_updates") else False)
    return a


# --- normal assessment ---
a = make_agent(DETECT)
res = a.security_patch_scan({})
by_pkg = {f["package"]: f for f in res.get("findings", [])}
check("status ok and read-only flag set", res["status"] == "ok" and res["read_only"] is True)
check("the non-security docker-ce bump is NOT a security finding", "docker-ce" not in by_pkg)
check("security updates counted = 3 (libc6, openssl, unfamiliar)", res["security_updates"] == 3)
check("glibc classified as critical component, severity high, with impact + action",
      by_pkg["libc6"]["classification"] == "critical_component"
      and by_pkg["libc6"]["severity"] == "high"
      and by_pkg["libc6"]["component"] == "glibc"
      and bool(by_pkg["libc6"]["system_impact"]) and bool(by_pkg["libc6"]["post_update_action"]))
check("openssl classified critical component (criticality 'critical')",
      by_pkg["openssl"]["component"] == "openssl"
      and by_pkg["openssl"]["component_criticality"] == "critical")
check("unfamiliar security package is flagged for investigation, severity medium, no inferred impact",
      by_pkg["zzz-unknown-sec-lib"]["classification"] == "unfamiliar"
      and by_pkg["zzz-unknown-sec-lib"]["needs_investigation"] is True
      and by_pkg["zzz-unknown-sec-lib"]["severity"] == "medium"
      and by_pkg["zzz-unknown-sec-lib"]["component"] is None
      and by_pkg["zzz-unknown-sec-lib"]["system_impact"] is None)
check("vulnerability/CVE severity is never inferred (stage 2 not integrated)",
      all(f["cve_correlation"]["integrated"] is False
          and f["cve_correlation"]["cve_severity"] == "unknown"
          and "severity_basis" in f for f in res.get("findings", [])))
check("reboot_required propagates from the detector", res["reboot_required"] is True)
check("findings were published to the store for Anansi", len(a._store) == 3)

# --- idempotency: re-scan does not duplicate ---
a.security_patch_scan({})
sec_rows = [f for f in a._store if str(f.get("location", "")).startswith("security-patch:")]
check("re-scan does not duplicate findings (one per package)", len(sec_rows) == 3)

# --- failure handling: detector unreachable ---
b = make_agent(False)
rf = b.security_patch_scan({})
check("unreachable detector -> status 'unknown', nothing flagged",
      rf["status"] == "unknown" and rf.get("findings") == [] and b._store == [])

# --- malformed detector payload (no details) ---
c = make_agent({"result": {"upgradable_count": 0}})
rc = c.security_patch_scan({})
check("malformed detection (no details) -> unknown, nothing flagged",
      rc["status"] == "unknown" and c._store == [])

# --- structural read-only proof (source scan) ---
src = open(os.path.join(ROOT, "agents", "security_agent", "security_agent.py"), encoding="utf-8").read()
method = src[src.index("def security_patch_scan"):src.index("def handle_task")]
forbidden = ["subprocess", "Popen", "os.system", "_execute_local", "systemctl",
             "apt-get", "apt ", "sudo ", "shutil.rmtree", "os.remove", "os.rmdir"]
check("security_patch_scan contains no apt/subprocess/restart/reboot/delete path",
      not any(tok in method for tok in forbidden))
check("security_patch_scan reaches the package manager only via Maintenance detection",
      'send_a2a("maintenance_agent", "check_updates"' in method)

msrc = open(os.path.join(ROOT, "agents", "maintenance_agent", "maintenance_agent.py"), encoding="utf-8").read()
det = msrc[msrc.index("def _detect_updates"):msrc.index("def handle_task")]
check("Maintenance detection uses only read-only apt (list + cache show), never install/upgrade/sudo",
      "apt list --upgradable" in det and "apt-cache show" in det
      and "apt-get install" not in det and "apt-get upgrade" not in det
      and "sudo" not in det)

print("check_security_patch_scan: " + ("ALL OK" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
