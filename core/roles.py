#!/usr/bin/env python3
"""Roles: what an agent OWNS, as opposed to words that happen to appear near it.

WHY ROLES REPLACED KEYWORD SCORING. The matcher scored a term by the length of
the text it matched, on the reasoning that a longer term is a more specific
claim - which was right for the bug it fixed ("credit report" beating "repo")
and catastrophically wrong against a wildcard. The Security Agent had declared
the pattern `is .* allowed`. On "is the trustee allowed to sell the property"
it matched 22 characters and won, because a greedy wildcard swallows the
sentence and the sentence's length is then read as evidence of specificity. The
Trust Agent scored 5, for the word "trust", and did not declare "trustee" at
all - Accounting did.

Two defects, and only one of them was about scoring:

  a pattern that matches a SENTENCE SHAPE is not vocabulary. "is X allowed" is
  a question form, not a subject. Every domain gets permission questions.

  the subject word of an entire department - trustee - was owned by a different
  department. No amount of scoring fixes a vocabulary that says the wrong thing.

A ROLE IS A SUBJECT THE AGENT PRACTISES, not a word that appears in its
sentences. Roles are declared in `config/agent_configs/<agent>.json`, never in
code, so changing what a department owns is a config edit and is visible in one
place rather than distributed through a matcher.

EXCLUSIVE BY CONSTRUCTION. Two agents may not own the same role, and the
collision is caught at LOAD rather than at runtime - a routing conflict
discovered while answering a question has already produced a wrong answer.
"""
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(ROOT, "config", "agent_configs")

# A role match is worth more than any keyword match can be. Fixed, not scaled
# by length - scaling by length is what the wildcard exploited, and a role is
# either named or it is not.
ROLE_WEIGHT = 100


class RoleConflict(Exception):
    """Two agents claim the same role. Raised at load, never at routing time."""


def _norm(s):
    return " ".join(re.findall(r"[a-z0-9]+", (s or "").lower()))


def load_roles(config_dir=None):
    """-> ({agent: [roles]}, [conflicts]). Reads the configs, nothing else."""
    d = config_dir or CONFIG_DIR
    roles, owner, conflicts, contested = {}, {}, [], set()
    if not os.path.isdir(d):
        return roles, conflicts
    for f in sorted(os.listdir(d)):
        if not f.endswith(".json"):
            continue
        try:
            cfg = json.load(open(os.path.join(d, f), encoding="utf-8"))
        except Exception:
            continue
        aid = cfg.get("agent_id") or os.path.splitext(f)[0]
        declared = cfg.get("roles") or []
        if not isinstance(declared, list):
            continue
        clean = []
        for r in declared:
            if not isinstance(r, str) or not r.strip():
                continue
            key = _norm(r)
            if not key:
                continue
            if key in owner and owner[key] != aid:
                conflicts.append({"role": key, "claimed_by": sorted([owner[key], aid]),
                                  "why": ("A role has exactly one owner. Two "
                                          "departments claiming one subject is a "
                                          "routing conflict, and discovering it "
                                          "while answering a question means a "
                                          "wrong answer already went out.")})
                contested.add(key)
                continue
            owner[key] = aid
            clean.append(key)
        if clean:
            roles[aid] = clean

    # A CONTESTED ROLE BELONGS TO NOBODY.
    #
    # The first draft let whichever config loaded first keep the role, which
    # means an ALPHABETICAL accident decided which department owns a subject -
    # and silently, since the loser simply had fewer roles. That is the shape
    # this system names `contested` everywhere else: where two domains disagree,
    # the conflict is surfaced and not resolved by outranking. Stripping it from
    # both makes the gap visible at the next routing decision instead of hiding
    # behind a coin toss.
    for aid in list(roles):
        roles[aid] = [r for r in roles[aid] if r not in contested]
        if not roles[aid]:
            del roles[aid]
    return roles, conflicts


def score_roles(prompt, roles):
    """-> {agent: score}. A named role boosts its owner, flat, regardless of
    which other words are in the sentence."""
    p = _norm(prompt)
    if not p:
        return {}
    out = {}
    for aid, rs in (roles or {}).items():
        hit = [r for r in rs if r and r in p]
        if hit:
            out[aid] = ROLE_WEIGHT * len(hit)
    return out


def matched_roles(prompt, roles):
    """Which roles fired, per agent - so a routing decision can show its work."""
    p = _norm(prompt)
    return {aid: [r for r in rs if r and r in p]
            for aid, rs in (roles or {}).items()
            if any(r in p for r in rs)}
