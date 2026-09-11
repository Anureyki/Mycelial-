#!/usr/bin/env python3
"""Four layers plus the benefit. One schema for every account, no special cases.

    from core.account_model import trace, layers_of
    trace("trulink_card")

THE MODEL. Any account can be decomposed into five questions, and they are
different questions even when one entity answers several of them:

    identifier   what names the account and who issued that name
    fiduciary    who holds legal title / owes duties of loyalty and care
    servicer     who administers it day to day, for a fee
    debt_holder  who carries the obligation if it goes wrong
    benefit      what the account exists to deliver, and to whom

THE LABEL ON THE DOOR IS NOT ONE OF THE FIVE. A thing called a "trust" whose
fiduciary is a corporation, whose servicer is the same corporation, and whose
debt_holder is the person who funded it, is a custodial product. Calling it a
trust does not move a duty; only an instrument does. The label is evidence
about marketing, not about structure - which is why it is stored in its own
field and never consulted when the layers are read.

WHY A SCHEMA AND NOT RULES IN CODE. A statute changes, an instruction is
reissued, a servicer is replaced - and every one of those is a data change. If
the layers were branches in an agent, each change would be a code change, a
review and a deploy, and the agent would drift from the law between them. The
entries live in reference/_shared/account_layers.json, versioned and cited, and
this module only reads them.

DETERMINISM IS A REQUIREMENT, NOT A PROPERTY. Two agents tracing one account
must produce the same map, or the map is an opinion. Nothing here samples,
scores, ranks by similarity, or asks a model. It reads fields and returns them
in a fixed order.

WHAT IS NOT FILLED IN IS SAID. A layer nobody verified is `unverified` with the
reason, never a plausible guess. An account map that invents a debt-holder is
worse than one that admits it does not know: the whole point of the trace is to
find out who carries the obligation, and a guess there is the answer somebody
would act on.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS = os.path.join(ROOT, "reference", "_shared", "account_layers.json")

LAYERS = ("identifier", "fiduciary", "servicer", "debt_holder", "benefit")

# How much a field is actually known. Same vocabulary the corpus uses, because
# a reader who knows one knows the other.
EVIDENCE = ("cited", "stated_by_principal", "unverified", "not_applicable")


class CorpusUnavailable(Exception):
    pass


def load(path=None):
    p = path or CORPUS
    try:
        with open(p, encoding="utf-8") as fh:
            doc = json.load(fh)
    except Exception as e:
        raise CorpusUnavailable(f"{p}: {e}") from e
    if "accounts" not in doc:
        raise CorpusUnavailable(f"{p} has no accounts map")
    return doc


def layers_of(entry):
    """-> [{layer, entity, evidence, citation, note}] in FIXED order."""
    out = []
    for name in LAYERS:
        v = (entry.get("layers") or {}).get(name) or {}
        out.append({
            "layer": name,
            "entity": v.get("entity"),
            # SECTOR TRAVELS. It was dropped here and detect_inversion reads
            # it, so structure_handed_down could never fire - the detector ran,
            # found sector None, and returned nothing. A field lost at a
            # boundary takes the check that needed it with it, and the check
            # still reports a clean result.
            "sector": v.get("sector"),
            "evidence": v.get("evidence", "unverified"),
            "citation": v.get("citation"),
            "note": v.get("note"),
        })
    return out


def distinct_entities(entry):
    """-> how many DIFFERENT parties are stacked on this one account.

    Counting entities rather than layers is the point: one company can be
    fiduciary and servicer and issuer at once, and that is a different
    arrangement from three independent parties even though both have five
    layers."""
    seen = []
    for l in layers_of(entry):
        e = l["entity"]
        if e and e not in seen and l["layer"] != "benefit":
            seen.append(e)
    return seen


def detect_inversion(entry):
    """-> [findings]. Traced from the fields, never argued.

    Two inversions, and they are mirror images:

      privatised_benefit   something that exists to deliver a public benefit
                           has a private servicer taking a fee out of it and a
                           private party holding the paper.
      structure_handed_down a corporate form is presented to a person as
                           though it were theirs, while the duties and the
                           obligation stayed with the corporation - or worse,
                           the obligation moved to the person and the control
                           did not.

    Each finding names the fields it was read from. An inversion nobody can
    trace back to a field is an argument, and this module does not make
    arguments."""
    L = {l["layer"]: l for l in layers_of(entry)}
    found = []

    label = (entry.get("label") or "").lower()
    ben = L["benefit"]
    fid, srv, debt = L["fiduciary"], L["servicer"], L["debt_holder"]

    # 1. A PUBLIC BENEFIT WITH A PRIVATE SERVICER AND PRIVATE PAPER.
    if (entry.get("benefit_origin") == "public" and srv.get("entity")
            and srv.get("sector") == "private"):
        found.append({
            "inversion": "privatised_benefit",
            "read_from": ["benefit_origin", "servicer", "debt_holder"],
            "detail": {"benefit": ben.get("entity"),
                       "servicer": srv.get("entity"),
                       "debt_holder": debt.get("entity")},
            "why": ("A benefit that originates as a public entitlement is being "
                    "delivered through a private servicer. That is not "
                    "automatically improper - it is how most benefit delivery "
                    "works - but it means the fee, the float and the failure "
                    "mode sit with a private party while the entitlement is "
                    "public."),
            "not_a_conclusion": ("Traced from the fields. Whether it is lawful "
                                 "or contractual is Legal's question, not this "
                                 "module's."),
        })

    # 2. THE LABEL CLAIMS A FIDUCIARY THAT THE LAYERS DO NOT SHOW.
    if any(w in label for w in ("trust", "fiduciary", "estate")):
        holder = (entry.get("funding_obligation_holder")
                  or debt.get("entity"))
        principal_funded = entry.get("funded_by") == "the person"
        if principal_funded and holder and holder == entry.get("funded_by"):
            found.append({
                "inversion": "custodial_product_labelled_trust",
                "read_from": ["label", "funded_by", "funding_obligation_holder"],
                "detail": {"label": entry.get("label"),
                           "funded_by": entry.get("funded_by"),
                           "funding_obligation_holder": holder,
                           "fiduciary": fid.get("entity")},
                "why": ("The label says trust. The funding obligation never "
                        "transferred - the person who funded it still carries "
                        "it - so no settlor parted with anything and no "
                        "beneficiary gained a claim against a fund. That is a "
                        "custodial arrangement with a trust-shaped name."),
                "not_a_conclusion": ("A structural reading of the recorded "
                                     "fields. Whether any instrument in fact "
                                     "created a trust is decided by reading "
                                     "the instrument, which Trust owns."),
            })

    # 3. A CORPORATE FORM HANDED TO A PERSON WHO CARRIES ITS PAPER.
    if fid.get("entity") and fid.get("sector") == "corporate" \
            and debt.get("entity") == "the person" \
            and entry.get("control_holder") not in ("the person", None):
        found.append({
            "inversion": "structure_handed_down",
            "read_from": ["fiduciary", "debt_holder", "control_holder"],
            "detail": {"fiduciary": fid.get("entity"),
                       "control_holder": entry.get("control_holder"),
                       "debt_holder": debt.get("entity")},
            "why": ("The obligation sits with the person and the control does "
                    "not. Duty and control coming apart is the condition every "
                    "fiduciary doctrine exists to address, and it is worth "
                    "naming wherever it appears."),
            "not_a_conclusion": "Traced, not argued.",
        })
    return found


def trace(account_id, path=None):
    """-> the full four-layer map for one account. Deterministic."""
    doc = load(path)
    entry = (doc.get("accounts") or {}).get(account_id)
    if not entry:
        return {"account": account_id, "found": False,
                "absence_state": "nothing_found",
                "why": (f"{account_id!r} has no entry. The schema takes any "
                        f"account type and this one has not been recorded - "
                        f"which is a gap in the corpus, not a finding about "
                        f"the account."),
                "known_accounts": sorted((doc.get("accounts") or {}))}
    ls = layers_of(entry)
    stacked = distinct_entities(entry)
    unverified = [l["layer"] for l in ls if l["evidence"] == "unverified"]
    return {
        "account": account_id,
        "found": True,
        "label": entry.get("label"),
        "label_note": ("The label is recorded and is NOT one of the layers. It "
                       "is evidence about how the thing is presented, not about "
                       "who holds what."),
        "schema_version": doc.get("schema_version"),
        "entry_version": entry.get("version"),
        "layers": ls,
        "entities_stacked": stacked,
        "entities_stacked_count": len(stacked),
        "carries_the_debt": next((l["entity"] for l in ls
                                  if l["layer"] == "debt_holder"), None),
        "inversions": detect_inversion(entry),
        "unverified_layers": unverified,
        "absence_state": ("incomplete" if unverified else "verified_clear"),
        "citations": sorted({l["citation"] for l in ls if l["citation"]}),
    }
