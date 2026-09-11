#!/usr/bin/env python3
"""Routing must send the trustee question to Trust. CI fails if it does not.

    python3 tools/check_routing.py

WHY THIS IS A BUILD GATE AND NOT A NOTE. The vocabularies were wrong for weeks
and nothing said so: "is the trustee allowed to sell the property" scored
security_agent 22 against trust_agent 5, and the only reason anybody found out
was that a cross-domain check printed the scores on the way past. A routing
table has no natural alarm - every request gets an answer, and the wrong
department answering looks exactly like the right one answering, unless
somebody reads it.

Two classes of check, and the second is the one that will catch the next bug:

  ROUTES   named prompts must reach named departments.
  ROLES    no role may be claimed twice, and every role must be reachable.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.roles import load_roles, score_roles            # noqa: E402

# (prompt, expected owner, why this case exists)
ROUTES = [
    ("is the trustee allowed to sell the property", "trust_agent",
     "THE REGRESSION. Scored security_agent 22 / trust_agent 5, because "
     "Security had declared the wildcard `is .* allowed` and the matcher "
     "scored by matched length."),
    ("is the perimeter exposed to an intrusion threat", "security_agent",
     "Security must still win its own subject - a fix that only moved the "
     "bias would pass the case above and fail here."),
    ("is the trustee's network access a security risk", "trust_agent",
     "Mixed. Trust primary, and both surfaced."),
    ("what is my reservoir ppm", "grow_agent",
     "A domain with no role collision must be unaffected."),
]

MIXED_MUST_SURFACE = {
    "is the trustee's network access a security risk": {"trust_agent", "security_agent"},
}


def main():
    from core.routing import DomainRouter
    r = DomainRouter("ci", log=lambda *_a, **_k: None)
    roles, conflicts = load_roles()
    failures = []

    print("  roles:")
    for aid, rs in sorted(roles.items()):
        print(f"    {aid:20} {len(rs):3} role(s)")
    if conflicts:
        print("\n  ROLE CONFLICTS:")
        for c in conflicts:
            print(f"    {c['role']!r} claimed by {c['claimed_by']}")
            failures.append(f"role conflict: {c['role']}")
    if not roles:
        failures.append("no agent declares any role - the config is not loaded")

    print("\n  routes:")
    for prompt, expected, why in ROUTES:
        got = r.domain_for(prompt)
        ok = got == expected
        print(f"    [{'PASS' if ok else 'FAIL'}] {prompt[:46]:48} -> {got}")
        if not ok:
            print(f"           expected {expected}")
            print(f"           {why}")
            failures.append(f"{prompt[:40]!r} -> {got}, expected {expected}")

    print("\n  mixed questions surface every claimant:")
    for prompt, must in MIXED_MUST_SURFACE.items():
        got = set(r.domains_for(prompt))
        ok = must.issubset(got)
        print(f"    [{'PASS' if ok else 'FAIL'}] {sorted(got)}")
        if not ok:
            failures.append(f"mixed prompt surfaced {sorted(got)}, needs {sorted(must)}")

    # A declared role nothing can score is a role that does not work.
    print("\n  every declared role is reachable:")
    dead = []
    for aid, rs in roles.items():
        for role in rs:
            if score_roles(role, roles).get(aid, 0) <= 0:
                dead.append(f"{aid}:{role}")
    if dead:
        print(f"    [FAIL] unreachable: {dead[:6]}")
        failures.append(f"{len(dead)} declared role(s) cannot be matched")
    else:
        print(f"    [PASS] {sum(len(v) for v in roles.values())} role(s) all reachable")

    print()
    if failures:
        print(f"  {len(failures)} FAILURE(S) - routing is wrong, the build is red")
        for f in failures:
            print(f"    - {f}")
        return 1
    print("  routing holds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
