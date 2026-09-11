#!/usr/bin/env python3
"""Which agent may see which secret. Deny by default.

    from core.sandbox.credentials import env_for
    env_for("trust_agent")      # -> only what Trust owns

THE STATE THIS REPLACES. Every agent process inherited the entire .env, because
os.environ is per-process and start_all.sh exported one file into all of them.
Measured on 2026-09-11: a Trust Agent process could read Legal's
COURTLISTENER_API_TOKEN (40 chars) and Trading's SOLANA_RPC_URL (98 chars) -
and CLAUDE.md records that for Solana the URL IS the credential.

So the acceptance test "a compromised Trust VM cannot read Legal's credentials"
was failing at the PROCESS level, well before any question about VMs. Putting
each agent in a container and passing the same .env would have containerised
the leak rather than closed it, which is why ownership is built first.

DENY BY DEFAULT, and the default matters more than the list. A key with no
entry is visible to nobody: a secret whose owner nobody wrote down is a secret
nobody is accountable for, and the failure mode of an allowlist that falls open
is indistinguishable from having no allowlist.

NOT A SUBSTITUTE FOR ISOLATION. This narrows what a process is GIVEN. It does
not stop a compromised process reading /proc or the .env file itself - that is
what the container, and later the microVM, are for. Both layers are needed and
neither is the other.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MANIFEST = os.path.join(ROOT, "config", "credential_owners.json")


class CredentialDenied(PermissionError):
    pass


def load_manifest(path=None):
    with open(path or MANIFEST, encoding="utf-8") as fh:
        return json.load(fh).get("credentials") or {}


def owners_of(key, manifest=None):
    m = manifest if manifest is not None else load_manifest()
    return list((m.get(key) or {}).get("owners") or [])


def may_see(agent_id, key, manifest=None):
    """-> bool. Unlisted means no."""
    o = owners_of(key, manifest)
    return bool(o) and (agent_id in o or "*" in o)


def env_for(agent_id, source=None, manifest=None):
    """-> the environment this agent should be given, and nothing else."""
    src = source if source is not None else os.environ
    m = manifest if manifest is not None else load_manifest()
    return {k: v for k, v in src.items() if may_see(agent_id, k, m)}


def audit(source=None, manifest=None):
    """-> what is present, who owns it, and what is unaccounted for.

    An UNLISTED key present in the environment is the finding this is for: it
    is denied to every agent, which is the safe direction, and it also means
    somebody added a secret and nobody recorded who it belongs to."""
    src = source if source is not None else os.environ
    m = manifest if manifest is not None else load_manifest()
    listed = set(m)
    present = {k for k in src if k.isupper()}
    out = {"listed": len(listed), "present_and_listed": sorted(present & listed),
           "listed_but_absent": sorted(listed - present),
           "present_but_unlisted": sorted(k for k in present - listed
                                          if not k.startswith(("LC_", "XDG_"))),
           "high_sensitivity": sorted(k for k in listed
                                      if (m[k] or {}).get("sensitivity") == "high")}
    return out


def leak_check(manifest=None):
    """-> [(agent, key, owner)] for every cross-department credential visible.

    The acceptance test, as a function. Runs against the manifest rather than a
    live process, so it answers 'would this agent be given it' - which is the
    question the launcher acts on."""
    m = manifest if manifest is not None else load_manifest()
    agents = sorted({a for v in m.values() for a in (v.get("owners") or [])
                     if a != "*"})
    leaks = []
    for agent in agents:
        for key, meta in m.items():
            owners = meta.get("owners") or []
            if "*" in owners or agent in owners:
                continue
            if may_see(agent, key, m):
                leaks.append((agent, key, owners))
    return leaks
