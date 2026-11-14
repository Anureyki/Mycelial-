#!/usr/bin/env python3
"""Build gate for Stage 2: security_advisory_lookup (CVE / Ubuntu Security Notices
correlation). Pure correlation is tested with synthetic advisory fixtures - NO network.

Proves:
  - a USN that names (release, package, candidate_version) -> matched, with its CVE ids
    and a severity taken only from the advisory data (max CVE priority)
  - a USN that names a DIFFERENT version -> no_match, severity stays unknown (a miss is
    not a severity)
  - an unreachable source (fetch returns None) -> source_unavailable, integrated False,
    unknown - never treated as 'no vulnerability'
  - severity stays unknown when the advisory data carries no priority (never inferred)
  - from_scan correlates the open os_security_patch findings
  - strictly read-only: GET only, no POST body, no apt/subprocess/apply path, and the
    request URL carries only package + release (no personal data)
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from agents.security_agent.security_agent import SecurityAgent  # noqa: E402

fails = []


def check(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    if not cond:
        fails.append(name)


a = object.__new__(SecurityAgent)
a.agent_id, a.log = "security_agent", (lambda *x, **k: None)

REL = "resolute"
NOTICES = [
    {"id": "USN-7000-1", "cves": ["CVE-2026-1111", "CVE-2026-2222"],
     "release_packages": {REL: [{"name": "libc6", "version": "2.43-2ubuntu2.7"}]}},
    {"id": "USN-6999-1", "cves": ["CVE-2026-0001"],
     "release_packages": {REL: [{"name": "libc6", "version": "2.43-2ubuntu2.5"}]}},  # older version
]
PRIO = {"CVE-2026-1111": "medium", "CVE-2026-2222": "high", "CVE-2026-0001": "low"}

# matched: correct release+package+version
m = a._correlate_advisories("libc6", "2.43-2ubuntu2.7", REL, NOTICES, PRIO)
check("matched USN -> status matched, carries its CVE ids",
      m["correlation_status"] == "matched" and m["integrated"] is True
      and m["advisories"] == ["USN-7000-1"]
      and m["cve_ids"] == ["CVE-2026-1111", "CVE-2026-2222"])
check("severity is the max CVE priority FROM the advisory data (medium,high -> high)",
      m["cve_severity"] == "high")

# version mismatch -> no match
mm = a._correlate_advisories("libc6", "2.43-2ubuntu2.9", REL, NOTICES, PRIO)
check("a version with no matching USN -> no_match, severity unknown (a miss is not a severity)",
      mm["correlation_status"] == "no_match" and mm["integrated"] is True
      and mm["cve_ids"] == [] and mm["cve_severity"] == "unknown")

# source unavailable (fetch returned None)
su = a._correlate_advisories("libc6", "2.43-2ubuntu2.7", REL, None, None)
check("unreachable advisory source -> source_unavailable, integrated False, unknown",
      su["correlation_status"] == "source_unavailable" and su["integrated"] is False
      and su["cve_severity"] == "unknown")

# non-match but the survey was incomplete (page cap hit) -> coverage_partial, NOT a false no_match
cp = a._correlate_advisories("libc6", "2.43-2ubuntu2.7", REL, [], None, surveyed_complete=False)
check("non-match with an incomplete survey -> coverage_partial, not a false no_match",
      cp["correlation_status"] == "coverage_partial" and cp["cve_severity"] == "unknown")

# matched but no priority data -> severity stays unknown (never inferred)
np = a._correlate_advisories("libc6", "2.43-2ubuntu2.7", REL, NOTICES, None)
check("matched but no priority data -> severity stays unknown (never inferred)",
      np["correlation_status"] == "matched" and np["cve_ids"] and np["cve_severity"] == "unknown")

# end-to-end with monkeypatched fetch (still no network) + from_scan
a._distro_release = lambda: REL
a._load_findings = lambda: [
    {"category": "os_security_patch", "status": "open", "package": "libc6", "candidate": "2.43-2ubuntu2.7"},
    {"category": "os_security_patch", "status": "open", "package": "openssl", "candidate": "9.9"},
    {"category": "secret", "status": "open", "package": None, "candidate": None},  # ignored
]
fetched_urls = []


def fake_fetch(url, timeout=15):
    # Simulate the paginated release-notices query: full corpus on offset=0, empty after.
    fetched_urls.append(url)
    if "notices.json" in url and ("release=" + REL) in url:
        if "offset=0" in url:
            return {"notices": NOTICES, "total_results": len(NOTICES)}
        return {"notices": [], "total_results": len(NOTICES)}
    return None


a._advisory_fetch = fake_fetch
res = a.security_advisory_lookup({"from_scan": True})
by = {r["package"]: r for r in res["results"]}
check("from_scan correlates the open os_security_patch findings (libc6 + openssl, secret ignored)",
      set(by) == {"libc6", "openssl"})
check("libc6 matched to its USN; openssl absent from the surveyed corpus -> no_match (not a false severity)",
      by["libc6"]["correlation_status"] == "matched" and by["libc6"]["advisories"] == ["USN-7000-1"]
      and by["libc6"]["cve_severity"] == "unknown"              # severity enrichment deferred
      and by["openssl"]["correlation_status"] == "no_match")
check("the survey uses the release filter (not a package text-search) and carries no personal data",
      any(("release=" + REL) in u for u in fetched_urls)
      and all("details=" not in u for u in fetched_urls)
      and all("@" not in u and "ssn" not in u.lower() for u in fetched_urls))

# structural read-only proof
src = open(os.path.join(ROOT, "agents", "security_agent", "security_agent.py"), encoding="utf-8").read()
block = src[src.index("def _advisory_fetch"):src.index("def handle_task")]
forbidden = ["subprocess", "Popen", "os.system", "_execute_local", "systemctl", "apt-get",
             "apt ", "sudo ", "data=", "method=\"POST\"", "shutdown", "os.remove"]
check("Stage 2 is read-only: GET only, no POST body, no apt/subprocess/apply path",
      not any(tok in block for tok in forbidden) and "urlopen" in block)

print("check_security_advisory: " + ("ALL OK" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
