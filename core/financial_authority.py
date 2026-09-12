#!/usr/bin/env python3
"""What the Financial Domain may do, decided before it may touch anything.

    from core.financial_authority import may, VERBS, transferability

    may("accounting_agent", "analyze", "asset:checking_1234")
    may("accounting_agent", "execute", "asset:checking_1234")   # never

PHASE 0 OF THE FINANCIAL PROGRAMME, and it is deliberately first. The
principal's own ordering: "Define what the Financial Domain is allowed to do
before it touches financial data." An authority model written after the data
arrives is written by whoever needed an exception that week.

WHY THE ACL WAS NOT ENOUGH. core/acl.py already answers who may read, write
and execute a RESOURCE, and it stays the authority for that. It has no way to
express the distinction that matters with money: drafting a wire and sending
one are both "write", and they are not remotely the same act. So this adds a
LADDER of six verbs over the top, and the ACL still gates the resource
underneath. Two questions, both asked: may this agent touch this thing, and
may it take this KIND of step with it.

    read        look at a record. Changes nothing.
    analyze     compute over records. Changes nothing outside the system.
    prepare     produce a DRAFT - a filled form, a letter, a transfer packet.
                It stays on this machine. Nothing leaves.
    authorize   a HUMAN decides. No agent holds this verb, ever.
    execute     act on the outside world through an authorized interface.
    verify      go and look at whether the act actually landed.

THE LADDER IS NOT A HIERARCHY OF TRUST. It is a hierarchy of CONSEQUENCE.
`prepare` is allowed freely because a draft nobody sent is recoverable by
deleting a file. `execute` is not a higher level of the same thing; it is a
different category, because a misdirected payment and a premature filing are
not correctable afterwards. CLAUDE.md already draws this line for notifications
- tell / draft / send-to-a-third-party-is-refused - and for capital, where the
only unmediated authority is one whose every available action reduces
exposure. This is that rule, written once, for money.

NO AGENT HOLDS `authorize` OR `execute`. Not Accounting, not Legal, not Boss.
`authorize` is a human act by definition: an agent that could authorise its own
execution has no authorisation step, it has a spelling of one. And `execute` is
reached only by a human pressing something, through an interface that holds the
credential - never by an agent deciding the moment has come.

TRANSFERABILITY IS A SEPARATE, HARDER QUESTION, and the answer `unknown` is not
`yes`. Phase 8 of the programme moves resources into entities. Some resources
cannot move at all, and the system must refuse those structurally rather than
discover it at transfer time:

    38 U.S.C. 5301(a)(1) - payments of VA benefits due or to become due "shall
    not be assignable except to the extent specifically authorized by law", and
    are exempt from the claim of creditors before or after receipt.

That is not a policy choice this file makes. It is a statute, it is shelved in
Legal's corpus, and reference/_shared/account_layers.json already records the
VA entries' assignment chain as `never_existed` with that citation. This module
reads that record rather than keeping a second opinion about it.

A FIDUCIARY APPOINTMENT IS A SECOND GATE AND NOT THE SAME ONE. An active VA
fiduciary changes who may DIRECT a payment; 5301 changes whether the
entitlement may MOVE. A system that collapsed them would either block a lawful
act or permit an unlawful one, and which it did would depend on the day.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Ordered by consequence, not by trust. Closed set: a verb that is not here
# cannot be asked for, because a verb nobody named is a verb nobody decided
# about.
VERBS = ("read", "analyze", "prepare", "authorize", "execute", "verify")

# Verbs no agent may ever hold, whatever a config says. Enforced in code rather
# than configured, because a config that can grant `execute` is a config one
# edit away from an agent that can move money.
HUMAN_ONLY = ("authorize", "execute")

# What each verb is allowed to touch the world with.
EFFECT = {
    "read":      {"changes_records": False, "leaves_machine": False},
    "analyze":   {"changes_records": False, "leaves_machine": False},
    "prepare":   {"changes_records": True,  "leaves_machine": False},
    "authorize": {"changes_records": True,  "leaves_machine": False},
    "execute":   {"changes_records": True,  "leaves_machine": True},
    "verify":    {"changes_records": False, "leaves_machine": True},
}

# How far a transfer question has actually been answered. Same vocabulary shape
# as the rest of the system: the honest default is the one that blocks.
TRANSFERABILITY = ("transferable", "transferable_with_consent",
                   "requires_novation", "not_transferable", "unknown")
MAY_MOVE = ("transferable", "transferable_with_consent", "requires_novation")


class NotAVerb(ValueError):
    pass


def may(agent, verb, resource, acl_check=None):
    """-> (allowed: bool, why: str). Two gates, both asked.

    The LADDER first, then the ACL on the resource. Order matters: asking the
    ACL first would let a resource grant imply that `execute` was on the table
    at all, and it never is."""
    if verb not in VERBS:
        raise NotAVerb(
            f"{verb!r} is not a financial verb. The set is closed: "
            f"{list(VERBS)}. Add it here, with its effect, before using it.")
    if verb in HUMAN_ONLY:
        return False, (
            f"{verb!r} is reserved to a person. No agent holds it - an agent "
            f"that could authorise its own execution has not got an "
            f"authorisation step, it has a spelling of one. A human acts "
            f"through an interface that holds the credential.")
    if acl_check is None:
        from core.acl import check as acl_check           # noqa: PLC0415
    # The ladder verb maps onto the ACL's own three for the resource question.
    underlying = {"read": "read", "analyze": "read", "prepare": "write",
                  "verify": "read"}[verb]
    ok, why = acl_check(agent, resource, underlying)
    if not ok:
        return False, f"ACL refused {underlying} on {resource}: {why}"
    return True, f"{verb} permitted: {why}"


def _accounts():
    p = os.path.join(ROOT, "reference", "_shared", "account_layers.json")
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh).get("accounts") or {}
    except Exception:
        return {}


def transferability(account_id):
    """-> {state, why, citation, blocking}. Reads the record; keeps no opinion.

    THE ACCOUNT MODEL ALREADY KNOWS. Its `assignment` block distinguishes a
    chain nobody has traced (`not_checked`) from one that CANNOT EXIST because
    the law forbids the assignment (`never_existed`, with the statute). Keeping
    a second transferability field here would be two sources of truth about
    whether a thing can move, and the copy is always the one that drifts."""
    e = _accounts().get(account_id)
    if not e:
        return {"state": "unknown", "blocking": True,
                "why": (f"{account_id!r} is not in the account registry. An "
                        f"asset nobody has recorded cannot be cleared for "
                        f"transfer - that is a gap, not a permission."),
                "citation": None}
    a = e.get("assignment") or {}
    st = a.get("state")
    if st == "never_existed":
        return {"state": "not_transferable", "blocking": True,
                "why": (f"No assignment chain can exist for this account. "
                        f"{a.get('note') or ''}").strip(),
                "citation": a.get("citation")}
    if st in ("not_checked", None, "incomplete", "conflicting"):
        return {"state": "unknown", "blocking": True,
                "why": (f"The assignment position is {st!r}. Unknown is not "
                        f"permission: an asset whose transferability nobody "
                        f"established must not move on the assumption that it "
                        f"may."),
                "citation": a.get("citation")}
    return {"state": "transferable_with_consent", "blocking": False,
            "why": ("A chain exists and reads clear. Whether THIS transfer is "
                    "permitted is a contract question for Legal, not a "
                    "structural one - this only says nothing forbids it "
                    "outright."),
            "citation": a.get("citation")}


def may_transfer(account_id, to_entity):
    """-> (allowed, why). Phase 8's gate, and it defaults to no."""
    t = transferability(account_id)
    if t["blocking"]:
        return False, (f"REFUSED: {t['why']}"
                       + (f" [{t['citation']}]" if t.get("citation") else ""))
    return False, (
        f"Not refused structurally - {t['why']} - but no transfer is performed "
        f"here. Moving an asset to {to_entity!r} is an `execute` step, and "
        f"`execute` is reserved to a person acting through an authorised "
        f"interface. This function exists to say whether the door is closed, "
        f"never to open it.")
