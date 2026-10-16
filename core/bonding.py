"""Bonding and surety - the rules Legal applies, read from the held text.

The principal's bonding module (2026-10-16). Every threshold here is READ
from the corpus through the caller's resolver - the simplified acquisition
threshold from FAR 2.101, the Bonds-statute (Miller Act) thresholds from FAR
28.102-1 - and a threshold that cannot be read is `unknown`, which stops any
conclusion that turns on it. A threshold typed into code is the recalled
figure this system refuses everywhere else: FAR 2.101 says $350,000 as of
2026-08-01, and the figure most people remember is $250,000.

Three classes of answer, kept apart:
    required   - the regulation says shall / requires
    permitted  - the regulation says may, and only in named situations
    prohibited - the regulation says shall not
Plus `unsupported`: a term with no basis in the held text, flagged for the
principal to raise with the contracting officer - never called a violation,
because the text does not say that.
"""
import re

# FAR 2.101: "construction, alteration, or repair (including dredging,
# excavating, and painting) of buildings, structures, or other real property".
# Classification is by SUBSTANCE, not by the label on the solicitation:
# K-Con, Inc. v. Sec'y of the Army, 908 F.3d 719 (Fed. Cir. 2018) - contracts
# issued on a commercial-items form were construction contracts, and the
# Bonds-statute requirements were read in under the Christian doctrine.
WORK = ("construct", "alteration", "alter ", "repair", "dredg", "excavat", "paint",
        "renovat", "demoli", "install", "site prep", "site work", "grading", "paving",
        "roofing", "concrete", "foundation", "erect")
REAL_PROPERTY = ("building", "structure", "real property", "bridge", "dam", "plant",
                 "highway", "street", "tunnel", "sewer", "main", "power line",
                 "pumping station", "railway", "airport", "terminal", "dock", "pier",
                 "wharf", "levee", "canal", "facility", "floor", "wall", "roof", "site",
                 "parking", "foundation", "slab", "pre-engineered", "metal building")
SERVICE_LABELS = ("service", "commercial", "janitorial", "custodial", "maintenance",
                  "supply", "supplies", "o&m")
# Personal property is excluded from "construction" by the same definition.
PERSONAL_PROPERTY = ("vessel", "aircraft", "vehicle", "equipment repair", "software")

POST_AWARD_RESERVATION = re.compile(
    r"(reserves? the right|may (?:later )?require|at (?:its|the government'?s) discretion)"
    r"[^.]{0,120}\b(bond|surety)\b[^.]{0,80}\b(after award|post-award|during performance|"
    r"at any time|subsequent)", re.I)


def _money(rx, text):
    m = re.search(rx, text or "", re.I)
    return float(m.group(1).replace(",", "")) if m else None


def thresholds(resolver):
    """-> {sat, miller_full, miller_alt_floor, basis} read from the corpus."""
    out = {"sat": None, "miller_full": None, "miller_alt_floor": None, "basis": {}}
    d = resolver("48 CFR 2.101") or {}
    out["sat"] = _money(r"Simplified acquisition threshold means \$([\d,]+)", d.get("text"))
    out["basis"]["sat"] = "FAR 2.101" if out["sat"] else "FAR 2.101 not readable - unknown"
    m = resolver("48 CFR 28.102-1") or {}
    t = m.get("text") or ""
    out["miller_full"] = _money(r"bonds for any construction contract exceeding \$([\d,]+)", t)
    out["miller_alt_floor"] = _money(r"greater than \$([\d,]+), but not greater than", t)
    out["basis"]["miller"] = ("FAR 28.102-1 (40 U.S.C. 3131-3133)" if out["miller_full"]
                              else "FAR 28.102-1 not readable - unknown")
    return out


def classify_solicitation(title="", label="", scope=""):
    """Construction, mixed, or non-construction - by what the work IS."""
    text = f"{title} {scope}".lower()
    work = sorted({w.strip() for w in WORK if w in text})
    prop = sorted({p for p in REAL_PROPERTY if p in text})
    pers = sorted({p for p in PERSONAL_PROPERTY if p in text})
    lab = (label or title or "").lower()
    labelled_service = any(s in lab for s in SERVICE_LABELS)
    if work and prop:
        kind = "mixed" if labelled_service or any(s in text for s in SERVICE_LABELS) else "construction"
    else:
        kind = "non_construction"
    out = {"classification": kind, "work_terms": work, "real_property_terms": prop,
           "personal_property_terms": pers, "label": label or None,
           "test": ("FAR 2.101 'construction' - construction, alteration or repair "
                    "(incl. dredging, excavating, painting) of buildings, structures or "
                    "other real property; personal property excluded")}
    if kind in ("construction", "mixed") and labelled_service:
        out["label_vs_substance"] = (
            "Labelled as services/commercial, but the scope includes construction-type work "
            "on real property. Courts classify by substance: in K-Con (908 F.3d 719) contracts "
            "on a commercial-items form were held to be construction and Bonds-statute bonding "
            "was read in under the Christian doctrine. That ambiguity is patent - ask the "
            "contracting officer in writing before bidding, or the contractor bears it.")
    if pers and not prop:
        out["note"] = "Work on personal property (vessels, aircraft, equipment) is not construction under FAR 2.101."
    return out


def bond_requirements(resolver, classification, value=None, gsa=False,
                      building_service=False, section_8a=False, abilityone=False,
                      situations=None, price_increase=False):
    """Which bonds are required, permitted or prohibited, and on which form."""
    th = thresholds(resolver)
    v = float(value) if value not in (None, "") else None
    sit = set(situations or [])
    req, perm, proh, notes = [], [], [], []
    cite = lambda c: {"citation": c, "held": bool(resolver(c))}
    if classification in ("construction", "mixed"):
        full, floor = th["miller_full"], th["miller_alt_floor"]
        if v is None or full is None:
            notes.append("Value or Bonds-statute threshold unknown - cannot say whether "
                         "performance and payment bonds are mandatory.")
        elif v > full:
            req.append({"bond": "performance and payment bonds (Bonds statute / Miller Act)",
                        "forms": ["SF 25", "SF 25-A"], "penal_sum": "100% of original contract price, "
                        "plus 100% of any increase (28.102-2)", "clause": "52.228-15",
                        **cite("48 CFR 28.102-1")})
            req.append({"bond": "bid guarantee", "forms": ["SF 24"],
                        "rule": "only separate bid guarantees for construction (28.101-1(b))",
                        **cite("48 CFR 28.101-1")})
        elif floor is not None and v > floor:
            req.append({"bond": "payment protection (payment bond or alternative)",
                        "clause": "52.228-13", **cite("48 CFR 28.102-1")})
        if classification == "mixed":
            notes.append("Mixed scope: the construction portion carries construction bonding "
                         "rules; confirm with the contracting officer how the contract is classified.")
    else:
        sat = th["sat"]
        proh.append({"rule": "agencies shall not generally require performance and payment bonds "
                     "for other than construction contracts", **cite("48 CFR 28.103-1")})
        named = {"government_property_or_funds", "successor_in_interest",
                 "progress_payments_before_delivery", "dismantling_demolition_removal"}
        hit = sorted(sit & named)
        if v is not None and sat is not None and v <= sat:
            proh.append({"rule": f"performance bonds only for contracts exceeding the simplified "
                         f"acquisition threshold (${sat:,.0f}); this one does not",
                         **cite("48 CFR 28.103-2")})
        elif hit:
            perm.append({"bond": "performance bond", "forms": ["SF 1418"], "when": hit,
                         "rule": "may be required when necessary to protect the Government's interest",
                         **cite("48 CFR 28.103-2")})
            perm.append({"bond": "payment bond", "forms": ["SF 1416"],
                         "rule": "only when a performance bond is required and in the Government's interest",
                         **cite("48 CFR 28.103-3")})
            perm.append({"bond": "annual performance bond (non-construction only)", "forms": ["SF 35"],
                         "rule": "one gross penal sum across all covered contracts",
                         **cite("48 CFR 28.104")})
        else:
            notes.append("None of the four 28.103-2(a) situations is present - no basis for a "
                         "performance bond.")
        if any(p.get("bond") == "performance bond" for p in perm):
            perm.append({"bond": "bid guarantee (any type, incl. annual bid bond)", "forms": ["SF 24", "SF 34"],
                         "rule": "only if a performance bond is also required (28.101-1(a)); all types "
                                 "acceptable for supply or service contracts (28.101-1(b))",
                         **cite("48 CFR 28.101-1")})
        if gsa and building_service:
            g = resolver("528.103-2") or {}
            src = {"citation": "GSAM 528.103-2", "held": bool(g), "standing": "agency guidance "
                   "(GSA Acquisition Manual, not codified in 48 CFR)"}
            if abilityone:
                proh.append({"rule": "no performance bond for building service contracts awarded to "
                             "AbilityOne nonprofit agencies", **src})
            else:
                notes.append("GSA building service contract: a performance bond needs a written "
                             "determination that it is essential and justifies its cost (528.103-2(a))"
                             + ("; 8(a) awards over $150,000 may require one on that determination "
                                "(528.103-2(b))" if section_8a else ""))
    if price_increase:
        perm.append({"bond": "additional bond protection", "forms": ["SF 1415", "SF 1418/SF 1416 if new surety"],
                     "rule": "the Government may require additional protection when the price increases",
                     **cite("48 CFR 28.103-2")})
    return {"classification": classification, "value": v, "thresholds": th,
            "required": req, "permitted": perm, "prohibited": proh, "notes": notes,
            "delivery": "executed bonds are furnished before the notice to proceed (28.103-1(b))"}


def check_bond_terms(text):
    """Flag solicitation language reserving a right to add bonds after award."""
    hits = [m.group(0) for m in POST_AWARD_RESERVATION.finditer(text or "")]
    return {"flags": [{"term": h[:240], "finding": "unsupported",
                       "why": ("FAR Part 28 allows additional bond protection after award only when "
                               "the contract price increases (28.103-2(b), 28.102-2). A reserved "
                               "right to require bonds after award for any other reason has no "
                               "basis in the held text - raise it with the contracting officer "
                               "before bidding.")} for h in hits],
            "checked": "post-award bond reservations"}


def modification_gate(original_price, new_price=None, scope_expands=False, novation=False,
                      new_surety=False, alternative_security=False, more_coverage_needed=False):
    """FAR 28.106-5 / 28.106-3: is consent of surety needed before signature?"""
    op = float(original_price)
    np_ = float(new_price) if new_price not in (None, "") else op
    change = np_ - op
    pct = abs(change) / op * 100 if op else None
    triggers = []
    if alternative_security:
        return {"consent_required": False, "form": None,
                "why": "secured by a 28.204 alternative (letter of credit, U.S. bonds or notes, "
                       "certified check) - no consent of surety is required (28.106-5(b))",
                "price_change": change, "pct_change": pct}
    if new_surety:
        triggers.append("additional bond obtained from other than the original surety (28.106-5(a)(1))")
    if novation:
        triggers.append("novation agreement (28.106-5(a)(3), subpart 42.12)")
    if not new_surety:
        if scope_expands:
            triggers.append("new work beyond the scope of the original contract (28.106-5(a)(2)(i))")
        elif pct is not None and (pct > 25 or abs(change) > 50000):
            triggers.append(f"price changes by {pct:.1f}% / ${abs(change):,.0f} - more than 25 percent "
                            f"or $50,000, no scope change (28.106-5(a)(2)(ii))")
    forms = []
    if triggers:
        forms.append("SF 1414 Consent of Surety (all contract types, 28.106-5(c))")
    if more_coverage_needed:
        forms.append("SF 1415 Consent of Surety and Increase of Penalty (original surety) - or "
                     "SF 25/25-A/1418/1416 if a new surety or alternative (28.106-3)")
    return {"consent_required": bool(triggers), "triggers": triggers, "forms": forms,
            "price_change": change, "pct_change": round(pct, 2) if pct is not None else None,
            "gate": ("HOLD - obtain consent of surety before the modification goes to signature"
                     if triggers else "no consent-of-surety trigger"),
            "why_it_matters": "without consent a surety can argue the change released it, "
                              "leaving the contract unsecured"}
