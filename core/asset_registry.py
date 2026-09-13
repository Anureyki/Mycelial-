#!/usr/bin/env python3
"""The inventory. What exists, who claims it, what proves it, what blocks it.

    from core.asset_registry import record, advance, get, unresolved
    record("chase_checking", kind="account", institution="Example Bank",
           account_type="checking", last_four="1234",
           evidence="stated_by_principal")

PHASE 1A. Inventory before restructuring, evidence before interpretation,
classification before movement. It reuses the primitives rather than
redesigning them: core/account_model.py owns the layer map and the assignment
chain, core/claim_assessment.py owns the eight-way rights ontology,
core/acl.py owns resource permission, core/financial_authority.py owns the
verb ladder, core/identifier_scan.py owns document scanning. This is the
cabinet they all file into.

THE DATA IS NOT IN GIT. `Anureyki/Mycelial-` is PUBLIC. The schema below
should be readable by anyone - that is how it earns trust - and the inventory
must not be, so it lives in private/, which .gitignore excludes. A reader can
audit exactly how this behaves without learning one true fact about the
principal's finances.

NINE KINDS, BECAUSE THEY ARE NOT INTERCHANGEABLE
================================================
The classic accounting error is putting everything with a dollar sign in one
bucket and calling it an asset. It is not a tidiness problem; it produces
wrong answers about what can move:

    benefit              a VA entitlement is a right to a payment stream. It
                         is not transferable property - 38 U.S.C. 5301(a)(1)
    account              a RELATIONSHIP plus the cash in it plus the contract
                         governing it. Three things, one name
    contractual_right    rights and obligations. Assignable only if the
                         contract says so, and often not
    liability            an obligation. Negative value, and it moves under
                         completely different rules from property
    claim                something owed that is not yet money
    cash_flow            a stream, which is not the thing producing it
    physical_property    a vehicle, equipment
    intellectual_property   code, marks, models - ownership turns on
                         authorship and assignment, not possession
    asset                the residual, and a kind that has to be EARNED. If a
                         thing is genuinely just property, say so deliberately

FIVE EVIDENCE STATES, AND `derived` IS NOT `verified`
====================================================
    cited                a document says so, and the document is referenced
    system_verified      this system went and checked, and can say how
    derived              CALCULATED from other records. Provenance recorded
    stated_by_principal  he said so. A real source, and not a second opinion
    unknown              nobody established it

If three cited statements imply a balance of about $4,200, that is `derived`
and it carries the inputs. It never becomes `system_verified`, because nothing
external confirmed it - and a derived number promoted to verified is exactly
how a plausible figure becomes a fact nobody can trace. There is no code path
that upgrades it.

`unknown` IS BLOCKING. It is not permission and it is not denial. An asset
whose owner nobody established cannot be acted on, and saying so is the whole
job - "we did not look" and "there is nothing there" are opposite findings.

THE WRITE PATH REFUSES BEFORE IT PERSISTS
=========================================
A full account number rejected after storage has already been stored. So the
refusal happens in record(), before the file is opened - the same write-time
rule tools/eval_harness.py learned: data admitted and checked later is in the
set by the time anybody looks.

Two different checks, deliberately both:
    identifier_scan.scan()   prose - SSNs, EDIPIs, VA file numbers
    _field_shape()           the FIELD CONTRACT - `last_four` holding 14
                             digits is a full account number whatever a prose
                             scanner thinks of it
"""
import json
import os
import re
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE = os.path.join(ROOT, "private", "assets.json")

KINDS = ("asset", "claim", "account", "benefit", "contractual_right",
         "liability", "physical_property", "intellectual_property",
         "cash_flow")

EVIDENCE = ("cited", "system_verified", "derived", "stated_by_principal",
            "unknown")
# What may be treated as established. `derived` is absent on purpose.
ESTABLISHED = ("cited", "system_verified")

LIFECYCLE = ("DISCOVERED", "IDENTIFIED", "EVIDENCE_PENDING", "VERIFIED",
             "CLASSIFIED", "RESTRICTION_CHECKED", "ACTIONABLE",
             "PREPARED", "AUTHORIZED", "EXECUTED", "VERIFIED_AFTER",
             "RECONCILED")

# States that stop the ladder. An asset can sit here indefinitely and that is
# a RESULT: "known asset, legally restricted action" is a third answer, and
# without it the only options are ignore it or move it.
BLOCKING = ("UNKNOWN", "CONFLICT", "RESTRICTED", "NON_TRANSFERABLE",
            "PROFESSIONAL_REVIEW_REQUIRED", "AUTHORIZATION_REQUIRED")

# Phase 1A implements up to ACTIONABLE. Everything past it is an act on the
# world and belongs to a later increment with a person in the loop.
IMPLEMENTED_THROUGH = "ACTIONABLE"

# Field names that must never hold a value, whatever the value is. Refused by
# NAME, because the point is that this registry has no business holding them
# at all - not that a particular one looked dangerous.
NEVER_STORED = ("password", "passcode", "pin", "secret", "mfa", "otp",
                "totp", "security_answer", "security_question", "token",
                "api_key", "apikey", "private_key", "seed_phrase",
                "mnemonic", "routing_number", "account_number", "full_ssn",
                "ssn", "cvv", "credentials", "login")


class Refused(ValueError):
    """The write did not happen. Raised, never returned as a status - a
    refusal that comes back as a value gets logged and ignored."""


def _now():
    return datetime.now(timezone.utc).isoformat()


def _field_shape(fields):
    """-> [problems]. The FIELD CONTRACT, which a prose scanner cannot check.

    identifier_scan reads documents and looks for identifiers in sentences.
    This reads a RECORD and enforces what each field is allowed to be. They
    catch different things and neither is a copy of the other: "last_four":
    "4111111111111111" contains no SSN and is a full card number."""
    problems = []
    for key, val in (fields or {}).items():
        low = key.lower()
        if any(banned in low for banned in NEVER_STORED):
            problems.append(
                f"field {key!r} is never stored in the asset registry. This is "
                f"an inventory of WHAT EXISTS, not a credential vault - an "
                f"institution and a last-four identify an account for every "
                f"purpose this registry has.")
            continue
        if not isinstance(val, str):
            continue
        digits = re.sub(r"\D", "", val)
        if low == "last_four":
            if not re.fullmatch(r"\d{4}", val.strip()):
                problems.append(
                    f"last_four must be exactly four digits; got {len(digits)} "
                    f"digit(s). A longer value is a full account number in a "
                    f"field named for a fragment.")
        elif len(digits) >= 9 and low not in ("notes", "note", "document_ref"):
            problems.append(
                f"field {key!r} holds a {len(digits)}-digit run. That is the "
                f"length of an account, routing or social security number; "
                f"record a last_four and a document reference instead.")
    return problems


# The keys this module requires from a scan() result. Named, so a contract
# change is a loud failure here instead of a silent clean report.
SCAN_CONTRACT = ("findings",)
FINDING_CONTRACT = ("kind", "last4")


def _identifier_problems(fields):
    """The prose check. EVERY failure mode refuses; none returns "clean".

    THE BUG CLASS THIS IS BUILT AROUND, because it already happened here. The
    first version read `finding["hits"]`. scan() returns `findings`. So the
    expression evaluated to None, `if not hits: return []` read that as
    "nothing found", and every SSN passed the check. Nothing raised, nothing
    logged, and the registry reported a clean write.

    That is the worst shape a security control can take: not a refusal that
    is too strict, and not a crash - a PASS produced by a control that never
    ran. It is the same failure as a push piped through 2>/dev/null, one layer
    down, and this repo has now met it three times in three different modules.

    So the rule here is absolute: this function returns an empty list ONLY
    when the scanner ran, returned the shape it promises, and found nothing.
    Every other outcome - an exception, a non-dict, a missing key, a
    non-list, a finding missing its own fields - raises Refused, and the
    caller never gets to persist. Absence of detection is not evidence of
    absence, and a control that cannot say which one it is must refuse."""
    try:
        from core.identifier_scan import scan
    except Exception as exc:                        # noqa: BLE001
        raise Refused(
            f"the identifier scanner could not be imported ({type(exc).__name__}"
            f": {exc}), so nothing was checked and nothing was written. An "
            f"unavailable control is not a passed control.") from exc

    blob = " ".join(str(v) for v in (fields or {}).values()
                    if isinstance(v, str))
    try:
        finding = scan(blob, context="asset_registry")
    except Exception as exc:                        # noqa: BLE001
        raise Refused(
            f"the identifier scanner raised {type(exc).__name__}: {exc}. "
            f"Nothing was written. A scanner that cannot complete has not "
            f"cleared the record; it has failed to examine it.") from exc

    if not isinstance(finding, dict):
        raise Refused(
            f"the identifier scanner returned {type(finding).__name__}, not a "
            f"result object. Nothing was written - this module cannot tell a "
            f"clean scan from an unrecognised one, so it refuses.")
    missing = [k for k in SCAN_CONTRACT if k not in finding]
    if missing:
        raise Refused(
            f"the identifier scanner result is missing {missing}. Nothing was "
            f"written. This is exactly the defect that let an SSN through "
            f"once: a field name read that the scanner does not return "
            f"evaluates to nothing, and nothing reads as clean.")
    hits = finding.get("findings")
    if not isinstance(hits, list):
        raise Refused(
            f"scan()['findings'] is {type(hits).__name__}, not a list. "
            f"Nothing was written.")

    problems = []
    for h in hits:
        if not isinstance(h, dict) or any(k not in h for k in FINDING_CONTRACT):
            raise Refused(
                "a finding from the identifier scanner is malformed, so this "
                "record cannot be cleared. Nothing was written. A finding "
                "that cannot be read is not a finding that can be dismissed.")
        problems.append(
            f"{h['kind']} present (ending {h['last4']}) - the registry stores "
            f"a reference to the document, never the identifier itself")
    return problems


def load(path=None):
    p = path or STORE
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {"schema_version": "1.0", "assets": {}}
    except Exception as exc:
        # A corrupt store is not an empty store. Returning {} would let the
        # next write create a clean file over the top of the old one.
        raise Refused(f"{p} exists and is unreadable: {exc}") from exc


def _save(doc, path=None):
    p = path or STORE
    # BORN 0700, not fixed later. os.makedirs applies the process umask, so
    # the same line produced 0775 under this service manager - and 1,507
    # files sat group- and world-readable until somebody audited. A private
    # store that depends on the umask it happened to inherit is not private.
    from core.fs_boundary import ensure_dir
    ensure_dir(os.path.dirname(p), 0o700)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
    os.chmod(p, 0o600)


def guard_fields(fields):
    """THE write guard, shared by every registry. Raises Refused, or returns.

    ONE IMPLEMENTATION, THREE CALLERS. The counterparty and contract
    registries need exactly this check, and copying it into each would be the
    defect tools/check_contracts.py exists to catch - a meaning computed in
    one place and re-derived in another, both working, only one right. Three
    copies of an identifier refusal is three places for it to rot, and the one
    that rots is whichever nobody looked at.

    Both layers, because they catch different things: identifier_scan reads
    PROSE and _field_shape enforces the FIELD CONTRACT. "last_four":
    "4111111111111111" contains no SSN and is a full card number."""
    problems = _field_shape(fields) + _identifier_problems(fields)
    if problems:
        raise Refused("nothing was written. " + " | ".join(problems))


def record(asset_id, kind, evidence="unknown", path=None, **fields):
    """Add or amend one resource. REFUSES BEFORE IT WRITES.

    Amends rather than replaces: a field not passed is left alone, the same
    choice amend_grow_system makes, and for the same reason - a rebuild from
    arguments silently drops everything the caller did not happen to mention."""
    if kind not in KINDS:
        raise Refused(f"{kind!r} is not a resource kind. The set is closed: "
                      f"{list(KINDS)}. A benefit is not an account and an "
                      f"account is not an asset; choosing deliberately is the "
                      f"point of the field.")
    if evidence not in EVIDENCE:
        raise Refused(f"{evidence!r} is not an evidence state: {list(EVIDENCE)}")
    guard_fields(fields)

    doc = load(path)
    rec = doc["assets"].get(asset_id) or {
        "asset_id": asset_id, "created": _now(),
        "lifecycle": "DISCOVERED", "blocking": [], "history": [],
    }
    rec.update(fields)
    rec["kind"] = kind
    rec["evidence"] = evidence
    rec["updated"] = _now()
    if evidence == "unknown" and "UNKNOWN" not in rec["blocking"]:
        rec["blocking"].append("UNKNOWN")
    if evidence in ESTABLISHED and "UNKNOWN" in rec["blocking"]:
        rec["blocking"].remove("UNKNOWN")
    doc["assets"][asset_id] = rec
    _save(doc, path)
    return rec


def derive(asset_id, field, value, inputs, method, path=None):
    """Record a CALCULATED value with what it was calculated from.

    Separate from record() so the provenance cannot be omitted: a derived
    number whose inputs were not captured is indistinguishable from one
    somebody typed. It writes evidence `derived` and there is deliberately no
    argument that would let a caller write `system_verified` here."""
    if not inputs:
        raise Refused("a derived value needs its inputs. Without them the "
                      "figure cannot be re-checked and is a guess with a "
                      "decimal point.")
    doc = load(path)
    rec = doc["assets"].get(asset_id)
    if not rec:
        raise Refused(f"{asset_id!r} is not in the registry")
    rec.setdefault("derived", {})[field] = {
        "value": value, "inputs": list(inputs), "method": method,
        "at": _now(),
        "not_verified": ("Calculated from the inputs listed. Nothing external "
                         "confirmed it, and no path in this module promotes a "
                         "derived value to system_verified."),
    }
    rec["updated"] = _now()
    doc["assets"][asset_id] = rec
    _save(doc, path)
    return rec


def transfer_position(asset_id, path=None):
    """-> what blocks movement. Reads the existing model; keeps no opinion."""
    from core.financial_authority import transferability
    rec = load(path)["assets"].get(asset_id) or {}
    linked = rec.get("account_model_id")
    if not linked:
        return {"state": "unknown", "blocking": True,
                "why": (f"{asset_id!r} is not linked to an account_layers "
                        f"entry, so its assignment position has never been "
                        f"established. Unknown blocks.")}
    return transferability(linked)


def advance(asset_id, to_state, path=None):
    """Move one step along the lifecycle. Forward only, one at a time."""
    if to_state not in LIFECYCLE:
        raise Refused(f"{to_state!r} is not a lifecycle state: {list(LIFECYCLE)}")
    doc = load(path)
    rec = doc["assets"].get(asset_id)
    if not rec:
        raise Refused(f"{asset_id!r} is not in the registry")
    if rec.get("blocking"):
        raise Refused(
            f"{asset_id} is blocked by {rec['blocking']} and cannot advance. "
            f"A blocking state is a finding to resolve, not a warning to pass.")
    here = LIFECYCLE.index(rec["lifecycle"])
    there = LIFECYCLE.index(to_state)
    if there <= here:
        raise Refused(f"{rec['lifecycle']} -> {to_state} is not forward. The "
                      f"lifecycle records what has been established; going "
                      f"back would erase that it ever was.")
    if there > here + 1:
        raise Refused(
            f"{rec['lifecycle']} -> {to_state} skips "
            f"{LIFECYCLE[here + 1:there]}. Each step is a thing somebody "
            f"checked, and skipping one asserts a check that did not happen.")
    if LIFECYCLE.index(to_state) > LIFECYCLE.index(IMPLEMENTED_THROUGH):
        raise Refused(
            f"{to_state} is past {IMPLEMENTED_THROUGH}, which is as far as "
            f"Phase 1A goes. Everything beyond it acts on the world and needs "
            f"the authorisation path, not a registry call.")
    rec["history"].append({"from": rec["lifecycle"], "to": to_state,
                           "at": _now()})
    rec["lifecycle"] = to_state
    rec["updated"] = _now()
    doc["assets"][asset_id] = rec
    _save(doc, path)
    return rec


def block(asset_id, reason, path=None):
    if reason not in BLOCKING:
        raise Refused(f"{reason!r} is not a blocking state: {list(BLOCKING)}")
    doc = load(path)
    rec = doc["assets"].get(asset_id)
    if not rec:
        raise Refused(f"{asset_id!r} is not in the registry")
    if reason not in rec["blocking"]:
        rec["blocking"].append(reason)
    rec["updated"] = _now()
    doc["assets"][asset_id] = rec
    _save(doc, path)
    return rec


def get(asset_id, path=None):
    return load(path)["assets"].get(asset_id)


def unresolved(path=None):
    """-> everything still needing work, which is the registry's real output.

    A registry that only listed settled assets would report an empty desk the
    day it was created."""
    out = []
    for aid, rec in sorted((load(path)["assets"]).items()):
        if rec.get("blocking") or rec.get("evidence") not in ESTABLISHED:
            out.append({"asset_id": aid, "kind": rec.get("kind"),
                        "lifecycle": rec.get("lifecycle"),
                        "blocking": rec.get("blocking"),
                        "evidence": rec.get("evidence"),
                        "needs": ("a document" if rec.get("evidence") in
                                  ("stated_by_principal", "unknown", "derived")
                                  else "resolution of its blocking states")})
    return out
