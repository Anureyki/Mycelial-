#!/usr/bin/env python3
"""Instrument custody and benefit accounting: who received what, when, what
was credited, what was returned - as a ledger, never a brief.

    from core.instrument_custody import ledger
    ledger(case_record)

THE PRINCIPLE, in the principal's words (2026-09-14): a veteran's documents -
DD-214, VA award letter, SGLI certificate - are instruments carrying value.
When a shelter, program or fiduciary receives the ORIGINAL, they receive a
sold document, not a filing. The credit the instrument carries must be
posted to the veteran's account; if they cannot return the original, they
owe the credit. A VA check written on the veteran's behalf for room and
board is the veteran's money, not the custodian's revenue: the custodian may
deduct the cost of services rendered, and the surplus is returned when the
veteran leaves.

THE AGENT NEVER ARGUES THE LAW. It audits the chain. Every line in the
ledger is one of three things: an EVENT somebody recorded with a date and a
reference, a SUM of recorded amounts, or a FINDING that names the pattern
the events make and the rule the pattern is judged by - with that rule's
evidence state carried from core.instrument_rules, so "conversion" arrives
labelled `stated_by_principal` and "sold instrument" arrives labelled
`stated_by_principal`, never as a conclusion of law. An amount nobody
evidenced is flagged and held apart; it is never deducted and never
asserted. That is "unverifiable claims are flagged, not asserted",
enforced rather than intended.

THE LIFECYCLE is a closed set of event kinds. An event outside it is
refused, because a custody chain with a free-text step is a chain nobody
can audit:

    issued       the issuer created the instrument for the veteran
    transmitted  the veteran sent it - form: original | copy | attestation
    received     the custodian acknowledged receipt
    credited     the custodian posted the credit to the veteran's account
    enabled      a transaction the instrument unlocked (a check, a payment)
    deducted     a service the custodian charged against the benefit
    returned     the original went back to the veteran

Absence is a finding with its own name. `received` with no `credited`
after it is a MISSING LEDGER ENTRY. `received` with no `returned` and no
`credited` is the binary demand: return the instrument or return the
credit. Nothing here guesses which.

THE VALUE OF A SOLD DOCUMENT IS NOT GUESSED. It is the sum of every
transaction the document enabled, each logged with a date, an amount and
a reference. That total is the claim. Amounts are Decimal and the ledger
is rendered in a fixed order from sorted inputs, so two agents given the
same case produce the same bytes - which is checked, not assumed.
"""
import hashlib
import json
import os
import re
from decimal import Decimal, InvalidOperation

from core import instrument_rules

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE = os.path.join(ROOT, "private", "custody")

EVENTS = ("issued", "transmitted", "received", "credited", "enabled", "deducted",
          "returned")
FORMS = ("original", "copy", "attestation")
EVIDENCE = ("cited", "system_verified", "stated_by_principal", "unknown")
VERIFICATION_PURPOSES = ("verify_service", "verify_identity", "verify_eligibility",
                         "verify_income")


class Refused(ValueError):
    pass


def _money(x, what):
    try:
        d = Decimal(str(x).replace(",", "").replace("$", "").strip())
    except (InvalidOperation, AttributeError):
        raise Refused(f"{what}: amount {x!r} is not a number")
    if d < 0:
        raise Refused(f"{what}: amount {x!r} is negative; record a credit as its own event")
    return d.quantize(Decimal("0.01"))


def _fmt(d):
    return f"${d:,.2f}"


def validate_event(ev):
    """One event, checked against the closed set. Raises Refused."""
    if not isinstance(ev, dict):
        raise Refused("an event is a dict")
    kind = str(ev.get("kind") or "")
    if kind not in EVENTS:
        raise Refused(f"event kind {kind!r} is not in the lifecycle {EVENTS}")
    if not ev.get("instrument"):
        raise Refused(f"{kind}: no instrument named")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(ev.get("date") or "")):
        raise Refused(f"{kind} on {ev.get('instrument')}: date must be YYYY-MM-DD, "
                      f"got {ev.get('date')!r}")
    ev_state = str(ev.get("evidence") or "unknown")
    if ev_state not in EVIDENCE:
        raise Refused(f"{kind}: evidence {ev_state!r} not in {EVIDENCE}")
    if kind == "transmitted" and str(ev.get("form") or "") not in FORMS:
        raise Refused(f"transmitted: form must be one of {FORMS}")
    if kind in ("credited", "enabled", "deducted"):
        _money(ev.get("amount"), f"{kind} on {ev['instrument']}")
    if kind in ("received", "credited", "deducted") and not ev.get("by"):
        raise Refused(f"{kind}: `by` (the custodian) is required")
    return ev


def _sorted_events(events):
    # Date, then lifecycle order, then instrument, then reference: total and
    # fixed, so the same set in any input order renders the same.
    return sorted(events, key=lambda e: (str(e.get("date")), EVENTS.index(e["kind"]),
                                         str(e.get("instrument")), str(e.get("ref") or ""),
                                         str(e.get("amount") or "")))


def ledger(case):
    """-> {case_id, chains, enabled, deductions, surplus, flags, text, sha256}.

    `case` = {case_id, veteran, custodian, instruments: {id: {kind, purpose}},
              events: [...]}. Every event is validated; the ledger is built
    from the sorted set."""
    if not isinstance(case, dict) or not case.get("case_id"):
        raise Refused("a case needs a case_id")
    veteran = str(case.get("veteran") or "the veteran")
    custodian = str(case.get("custodian") or "")
    if not custodian:
        raise Refused("a case needs a custodian - the party that received")
    instruments = case.get("instruments") or {}
    events = [validate_event(e) for e in (case.get("events") or [])]
    for e in events:
        if e["instrument"] not in instruments:
            raise Refused(f"event names instrument {e['instrument']!r} the case does not declare")
    events = _sorted_events(events)

    rules = {r["id"]: r for r in instrument_rules.RULES}

    def finding(fid, instrument, what, rule_id, detail=""):
        rule = rules.get(rule_id) or {}
        return {"finding": fid, "instrument": instrument, "what": what,
                "rule": rule_id, "rule_statement": rule.get("statement", ""),
                # A finding carries the rule's standing. It is a pattern the
                # events make, judged by a rule with THIS evidence state -
                # not a conclusion of law.
                "basis": "stated_by_principal", "detail": detail}

    chains, flags = {}, []
    enabled_total, deducted_ok, deducted_unverified = Decimal("0"), Decimal("0"), Decimal("0")
    enabled_rows, deduction_rows, credit_rows = [], [], []

    for iid in sorted(instruments):
        meta = instruments[iid] or {}
        kind = str(meta.get("kind") or "")
        try:
            cls = instrument_rules.classify_instrument(kind, value=meta.get("value"),
                                                       maturity=meta.get("maturity"))
        except instrument_rules.Unclassifiable as exc:
            raise Refused(f"instrument {iid}: {exc}")
        evs = [e for e in events if e["instrument"] == iid]
        steps = [{"date": e["date"], "kind": e["kind"], "by": e.get("by"),
                  "form": e.get("form"), "amount": (_fmt(_money(e["amount"], iid))
                                                    if e.get("amount") is not None else None),
                  "ref": e.get("ref"), "evidence": e.get("evidence") or "unknown"}
                 for e in evs]
        kinds = [e["kind"] for e in evs]
        received = [e for e in evs if e["kind"] == "received"]
        credited_all = [e for e in evs if e["kind"] == "credited"]
        # A credit booked as the CUSTODIAN's revenue is not a credit to the
        # veteran. It is the inversion itself, and the chain must not read
        # "credited" because a ledger line exists somewhere.
        credited = [e for e in credited_all
                    if "revenue" not in str(e.get("booked_as") or "").lower()]
        returned = [e for e in evs if e["kind"] == "returned"]
        transmitted = [e for e in evs if e["kind"] == "transmitted"]
        status = {
            "received": bool(received),
            "credited": ("credited" if credited else
                         ("booked_as_custodian_revenue" if credited_all else
                          ("not_credited" if received else "not_applicable"))),
            "returned": ("returned" if returned else
                         ("not_returned" if received else "not_applicable")),
            "original_out_of_hand": any(e.get("form") == "original" for e in transmitted)
                                    and not returned,
        }
        # Sold instrument: the ORIGINAL was received when an attestation
        # would have sufficed for the purpose declared.
        purpose = str(meta.get("purpose") or "")
        if received and status["original_out_of_hand"] and purpose in VERIFICATION_PURPOSES:
            flags.append(finding(
                "sold_instrument", iid,
                f"{custodian} received the original for {purpose}, which a verified "
                f"attestation from the VA or DoD would have satisfied.",
                "transmitted_original_is_sold",
                "The moment the original changed hands for a purpose an attestation "
                "serves, it became a sold document."))
        if received and not credited:
            flags.append(finding(
                "missing_ledger_entry", iid,
                f"{custodian} received the instrument on {received[0]['date']} and no "
                f"credit is posted to {veteran}'s account.",
                "shelter_inversion",
                "A received instrument with no credit posted is a missing ledger entry."))
        if received and not credited and not returned:
            flags.append(finding(
                "binary_demand", iid,
                "Neither credited nor returned.",
                "shelter_inversion",
                "The demand is binary: return the instrument or return the credit. "
                "Which one is not decided here."))
        for e in evs:
            if e["kind"] == "credited":
                amt = _money(e["amount"], iid)
                booked = str(e.get("booked_as") or "")
                credit_rows.append({"date": e["date"], "instrument": iid, "by": e.get("by"),
                                    "amount": _fmt(amt), "booked_as": booked,
                                    "ref": e.get("ref"), "evidence": e.get("evidence")})
                if "revenue" in booked.lower():
                    flags.append(finding(
                        "conversion", iid,
                        f"{e.get('by')} booked {_fmt(amt)} as {booked} on {e['date']}.",
                        "cashing_is_conversion",
                        "A received instrument cashed as ordinary revenue. The rule is the "
                        "principal's; the shelf holds no authority for it, and the "
                        "finding is labelled so."))
            elif e["kind"] == "enabled":
                amt = _money(e["amount"], iid)
                enabled_total += amt
                enabled_rows.append({"date": e["date"], "instrument": iid, "amount": _fmt(amt),
                                     "payee": e.get("payee"), "ref": e.get("ref"),
                                     "evidence": e.get("evidence")})
            elif e["kind"] == "deducted":
                amt = _money(e["amount"], iid)
                ok = str(e.get("evidence") or "unknown") in ("cited", "system_verified")
                row = {"date": e["date"], "instrument": iid, "amount": _fmt(amt),
                       "service": e.get("service"), "by": e.get("by"), "ref": e.get("ref"),
                       "evidence": e.get("evidence") or "unknown",
                       "counted": ok}
                deduction_rows.append(row)
                if ok:
                    deducted_ok += amt
                else:
                    deducted_unverified += amt
                    flags.append(finding(
                        "unverified_deduction", iid,
                        f"{e.get('by')} claims {_fmt(amt)} for {e.get('service')!r} on "
                        f"{e['date']} with evidence {row['evidence']!r}.",
                        "flag_is_not_fact",
                        "Flagged, not deducted, not asserted. A deduction with no "
                        "evidence reference is a claim; the surplus below does not "
                        "subtract it."))
        chains[iid] = {"kind": cls["kind"], "role": cls["role"], "maturity": cls["maturity"],
                       "purpose": purpose, "steps": steps, "status": status,
                       "lifecycle_seen": sorted(set(kinds), key=EVENTS.index)}

    surplus = enabled_total - deducted_ok
    flags.sort(key=lambda f: (f["instrument"], f["finding"], f["what"]))
    out = {
        "case_id": case["case_id"], "veteran": veteran, "custodian": custodian,
        "chains": chains,
        "enabled": {"rows": enabled_rows, "total": _fmt(enabled_total),
                    "meaning": "the sum of every transaction the instruments enabled - the claim"},
        "credits_posted": credit_rows,
        "deductions": {"rows": deduction_rows, "evidenced_total": _fmt(deducted_ok),
                       "unverified_total": _fmt(deducted_unverified)},
        "surplus_owed_back": _fmt(surplus if surplus > 0 else Decimal("0")),
        "shortfall_if_any": _fmt(-surplus if surplus < 0 else Decimal("0")),
        "flags": flags,
        "method": ("Audit of the recorded chain. Every amount is a recorded event; "
                   "every finding names the events and the rule, with the rule's "
                   "evidence state. Unverified deductions are flagged and not "
                   "subtracted. Nothing here argues the law."),
    }
    out["text"] = render(out)
    out["sha256"] = hashlib.sha256(out["text"].encode("utf-8")).hexdigest()
    return out


def render(L):
    lines = [f"CUSTODY AND BENEFIT LEDGER - case {L['case_id']}",
             f"Veteran: {L['veteran']}    Custodian: {L['custodian']}", ""]
    lines.append("1. CUSTODY CHAIN")
    for iid in sorted(L["chains"]):
        c = L["chains"][iid]
        lines.append(f"  {iid} ({c['kind']}, {c['role']}, maturity {c['maturity']}"
                     + (f", purpose {c['purpose']}" if c["purpose"] else "") + ")")
        for s in c["steps"]:
            bits = [s["date"], s["kind"]]
            if s.get("form"):
                bits.append(f"form={s['form']}")
            if s.get("by"):
                bits.append(f"by={s['by']}")
            if s.get("amount"):
                bits.append(s["amount"])
            if s.get("ref"):
                bits.append(f"ref={s['ref']}")
            bits.append(f"[{s['evidence']}]")
            lines.append("    " + "  ".join(bits))
        st = c["status"]
        lines.append(f"    status: received={st['received']} credited={st['credited']} "
                     f"returned={st['returned']} original_out_of_hand={st['original_out_of_hand']}")
    lines.append("")
    lines.append("2. TRANSACTIONS THE INSTRUMENTS ENABLED")
    for r in L["enabled"]["rows"]:
        lines.append(f"  {r['date']}  {r['instrument']}  {r['amount']}  payee={r['payee']}  "
                     f"ref={r['ref']}  [{r['evidence']}]")
    lines.append(f"  TOTAL ENABLED (the claim): {L['enabled']['total']}")
    lines.append("")
    lines.append("3. CREDITS POSTED TO THE VETERAN")
    if not L["credits_posted"]:
        lines.append("  none recorded")
    for r in L["credits_posted"]:
        lines.append(f"  {r['date']}  {r['instrument']}  {r['amount']}  by={r['by']}  "
                     f"booked_as={r['booked_as']}  ref={r['ref']}  [{r['evidence']}]")
    lines.append("")
    lines.append("4. SERVICES DEDUCTED")
    for r in L["deductions"]["rows"]:
        lines.append(f"  {r['date']}  {r['instrument']}  {r['amount']}  service={r['service']}  "
                     f"by={r['by']}  ref={r['ref']}  [{r['evidence']}]  "
                     f"{'counted' if r['counted'] else 'FLAGGED - not counted'}")
    lines.append(f"  EVIDENCED DEDUCTIONS: {L['deductions']['evidenced_total']}")
    lines.append(f"  UNVERIFIED (flagged, not deducted): {L['deductions']['unverified_total']}")
    lines.append("")
    lines.append("5. SURPLUS OWED BACK")
    lines.append(f"  {L['surplus_owed_back']}"
                 + (f"   (shortfall {L['shortfall_if_any']})" if L["shortfall_if_any"] != "$0.00" else ""))
    lines.append("")
    lines.append("6. FINDINGS")
    if not L["flags"]:
        lines.append("  none")
    for f in L["flags"]:
        lines.append(f"  [{f['finding']}] {f['instrument']}: {f['what']}")
        lines.append(f"      rule: {f['rule']} ({f['basis']})")
    return "\n".join(lines).rstrip() + "\n"


# ----------------------------------------------------------------------
# Store: one file per case under private/custody, 0600
# ----------------------------------------------------------------------

def _path(case_id):
    cid = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(case_id)).strip("_")
    if not cid:
        raise Refused("case_id is empty after sanitising")
    return os.path.join(STORE, cid + ".json")


def load_case(case_id):
    p = _path(case_id)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def save_case(case):
    from core.fs_boundary import ensure_dir
    from core.asset_registry import guard_fields
    # No identifier lands in a custody file: instrument ids, refs and
    # notes are prose the same guard reads.
    flat = {}
    for k, v in (case.get("instruments") or {}).items():
        flat[f"instrument.{k}"] = json.dumps(v, sort_keys=True)
    for i, e in enumerate(case.get("events") or []):
        flat[f"event.{i}"] = json.dumps(e, sort_keys=True)
    guard_fields(flat)
    ensure_dir(STORE, 0o700)
    p = _path(case["case_id"])
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(case, fh, indent=2, sort_keys=True)
    return p


def add_event(case_id, event, veteran=None, custodian=None, instruments=None):
    """Append one validated event to a case, creating the case if needed."""
    validate_event(event)
    case = load_case(case_id) or {"case_id": case_id, "veteran": veteran,
                                  "custodian": custodian, "instruments": {}, "events": []}
    if veteran:
        case["veteran"] = veteran
    if custodian:
        case["custodian"] = custodian
    for k, v in (instruments or {}).items():
        case["instruments"][k] = v
    if event["instrument"] not in case["instruments"]:
        raise Refused(f"instrument {event['instrument']!r} is not declared on case {case_id}; "
                      f"pass `instruments` with its kind")
    case["events"].append(event)
    save_case(case)
    return case
