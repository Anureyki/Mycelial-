#!/usr/bin/env python3
"""The contract the buyer brings to the table, assembled from the corpus.

    from core.contract_engine import draft
    draft("car_purchase", facts, layers, resolver)

WHAT THIS IS. The agents can trace any account across its layers - who
holds title, who services, who carries the debt, who benefits - and can
classify a ledger entry as gift, claim or unclassifiable. The corpus holds
versioned, citable statutes. This is the OUTPUT: a contract assembled from
those, so the buyer signs a document whose every clause names the layer it
governs and cites the authority it rests on, instead of the seller's form.

THREE RULES, each enforced rather than intended:

1. NO CLAUSE SHIPS WITHOUT A CITATION, AND THE CITATION MUST OPEN. Every
   clause names the section it rests on, and the resolver must return that
   section's text from a corpus - this agent's own or a peer's. A clause
   whose authority cannot be opened is refused before drafting. The agent
   does not invent terms; it assembles from what it can read.

2. THE FOUR LAYERS ARE NAMED, OR NOTHING DRAFTS. Title holder, servicer,
   debt carrier, beneficiary - each with a party and an evidence state.
   `unknown` blocks. And the refusal gate reads the map against the
   facts: if the seller claims to carry the financing and the map shows
   the buyer as debt_holder, that clause is refused as a layer conflict.
   A contract that papers over who carries the debt is the seller's form
   with the buyer's name on it.

3. TWO AGENTS DRAFTING THE SAME TRANSACTION PRODUCE BYTE-IDENTICAL
   CONTRACTS. Nothing here samples, ranks, dates, or asks a model. Clause
   order is fixed per family; the text comes from templates and the facts;
   the schedule lists each authority with the SHA-256 of the text it was
   resolved to, so two shelves holding the same enactment produce the
   same schedule and two holding different text do not - visibly. The
   resolver's provenance (own shelf or borrowed) is reported in the result
   and never written into the document.

THE DOCUMENT READS AS A CONTRACT, NOT A BRIEF. Parties, terms, payment
instrument, discharge conditions, signatures. No argument in the body.
The citations live in Schedule A so the seller reads an instrument and the
principal can check every clause against its source.

The four-layer vocabulary is core.account_model's, not a second copy:
`holder` is who may enforce, `debt_holder` is who CARRIES the obligation.
"""
import hashlib
import re

from core import account_model
from core import instrument_rules

# The layers a contract names. account_model.LAYERS carries `identifier`
# and `fiduciary` as well; a contract names the four the principal named -
# who holds title, who services, who carries the debt, who benefits.
CONTRACT_LAYERS = ("holder", "servicer", "debt_holder", "benefit")
assert all(l in account_model.LAYERS for l in CONTRACT_LAYERS)

EVIDENCE_OK = ("cited", "stated_by_principal", "system_verified")


class Refused(Exception):
    pass


# ----------------------------------------------------------------------
# Clause library. Each clause: id, block, heading, template, authority.
# `authority` is the list of citations that must resolve. `needs` are
# the fact keys the template consumes. Order within a family is fixed.
# ----------------------------------------------------------------------

def _c(cid, block, heading, template, authority, needs=(), when=None):
    return {"id": cid, "block": block, "heading": heading, "template": template,
            "authority": list(authority), "needs": tuple(needs), "when": when}


CAR_PURCHASE = [
    _c("parties", "parties", "Parties",
       "This Motor Vehicle Purchase Agreement is made between {buyer_name} "
       "(\"Buyer\") and {seller_name} (\"Seller\").",
       ["Tex. Bus. & Com. Code § 2.103"], needs=("buyer_name", "seller_name")),
    _c("goods", "terms", "The Vehicle",
       "Seller sells and Buyer buys the following motor vehicle (the \"Vehicle\"): "
       "{vehicle_description}, VIN {vin}, odometer reading {odometer} miles at delivery.",
       ["Tex. Bus. & Com. Code § 2.105", "Tex. Bus. & Com. Code § 2.106"],
       needs=("vehicle_description", "vin", "odometer")),
    _c("price", "terms", "Price",
       "The total purchase price is {price_usd} (the \"Price\"), inclusive of all "
       "charges. No fee, add-on, or charge not written in this Agreement is owed.",
       ["Tex. Bus. & Com. Code § 2.301", "Tex. Bus. & Com. Code § 2.202"],
       needs=("price_usd",)),
    _c("layers", "terms", "Who Holds What",
       "Title holder on delivery: {layer_holder}. Servicer of any obligation under "
       "this Agreement: {layer_servicer}. Carrier of any debt under this Agreement: "
       "{layer_debt_holder}. Beneficiary of the Vehicle: {layer_benefit}. No party "
       "carries an obligation this Agreement does not name.",
       ["Tex. Bus. & Com. Code § 2.401"],
       needs=("layer_holder", "layer_servicer", "layer_debt_holder", "layer_benefit")),
    _c("title", "title", "Title and Transfer",
       "Seller is the owner designated on the certificate of title and shall submit "
       "a transfer of ownership of the title to Buyer at delivery, in the manner the "
       "department prescribes, certifying Buyer as owner and certifying that no lien "
       "exists on the Vehicle or providing a release of each lien. Title passes to "
       "Buyer on delivery and completion of that transfer; a sale in violation of "
       "the Certificate of Title Act is void and title does not pass.",
       ["Tex. Transp. Code § 501.071", "Tex. Transp. Code § 501.073",
        "Tex. Bus. & Com. Code § 2.401"]),
    _c("warranty_express", "warranty", "Express Warranty",
       "Every affirmation of fact, promise, and description of the Vehicle made by "
       "Seller in this Agreement or in the sale is part of the basis of the bargain "
       "and is an express warranty that the Vehicle conforms to it. Seller's "
       "affirmations: {seller_affirmations}.",
       ["Tex. Bus. & Com. Code § 2.313"], needs=("seller_affirmations",)),
    _c("warranty_implied", "warranty", "Implied Warranties",
       "Seller is a merchant with respect to goods of this kind, and the implied "
       "warranty of merchantability applies. No disclaimer of an implied warranty is "
       "effective unless it is in this Agreement, in writing, and conspicuous.",
       ["Tex. Bus. & Com. Code § 2.314", "Tex. Bus. & Com. Code § 2.316"],
       when=lambda f: bool(f.get("seller_is_merchant"))),
    _c("warranty_implied_nonmerchant", "warranty", "Implied Warranties",
       "Seller is not a merchant with respect to goods of this kind. Any exclusion "
       "or modification of an implied warranty is effective only if written in this "
       "Agreement and conspicuous.",
       ["Tex. Bus. & Com. Code § 2.316"],
       when=lambda f: not f.get("seller_is_merchant")),
    _c("financing_seller", "payment", "Seller Financing",
       "Seller carries the financed balance of {financed_usd}. Buyer's obligation is "
       "to Seller alone, on the schedule in this Agreement, and to no assignee unless "
       "Buyer is notified in writing of the assignment.",
       ["Tex. Bus. & Com. Code § 9.406"],
       needs=("financed_usd",),
       when=lambda f: bool(f.get("seller_claims_to_carry_debt"))),
    # payment_instrument and discharge clauses are added per instrument below
    _c("acceptance", "terms", "Delivery and Acceptance",
       "Buyer may inspect the Vehicle before acceptance. Acceptance occurs when Buyer, "
       "after a reasonable opportunity to inspect, signifies that the Vehicle conforms "
       "or takes it despite nonconformity. Acceptance does not waive a nonconformity "
       "Buyer could not reasonably have discovered on inspection.",
       ["Tex. Bus. & Com. Code § 2.606", "Tex. Bus. & Com. Code § 2.607"]),
    _c("entire", "terms", "Entire Agreement",
       "This Agreement is the complete and exclusive statement of the terms. No "
       "prior agreement or contemporaneous oral agreement contradicts it.",
       ["Tex. Bus. & Com. Code § 2.202"]),
    _c("signatures", "signatures", "Signatures",
       "Buyer: ______________________  {buyer_name}\n"
       "Seller: _____________________  {seller_name}\n"
       "Date of delivery: {delivery_date}",
       ["Tex. Bus. & Com. Code § 2.201"], needs=("buyer_name", "seller_name", "delivery_date")),
]

# Families whose authorities are NOT yet on the shelf draft nothing until
# they are - the refusal gate names each missing citation, which is the
# acquisition list. The clauses are real; the refusals are honest.
HOUSE_PURCHASE = [
    _c("parties", "parties", "Parties",
       "This Real Property Purchase Agreement is made between {buyer_name} "
       "(\"Buyer\") and {seller_name} (\"Seller\").",
       ["Tex. Prop. Code § 5.001"], needs=("buyer_name", "seller_name")),
    _c("property", "terms", "The Property",
       "Seller conveys to Buyer the real property at {property_address}, legally "
       "described as {legal_description}, with all improvements.",
       ["Tex. Prop. Code § 5.021"], needs=("property_address", "legal_description")),
    _c("layers", "terms", "Who Holds What",
       "Title holder on closing: {layer_holder}. Servicer of any obligation: "
       "{layer_servicer}. Carrier of any debt: {layer_debt_holder}. Beneficiary: "
       "{layer_benefit}.",
       ["Tex. Prop. Code § 5.021"],
       needs=("layer_holder", "layer_servicer", "layer_debt_holder", "layer_benefit")),
    _c("signatures", "signatures", "Signatures",
       "Buyer: ______________________  {buyer_name}\n"
       "Seller: _____________________  {seller_name}",
       ["Tex. Prop. Code § 5.021"], needs=("buyer_name", "seller_name")),
]

LEASE = [
    _c("parties", "parties", "Parties",
       "This Residential Lease is made between {landlord_name} (\"Landlord\") and "
       "{tenant_name} (\"Tenant\").",
       ["Tex. Prop. Code § 92.001"], needs=("landlord_name", "tenant_name")),
    _c("premises", "terms", "Premises and Term",
       "Landlord leases to Tenant the premises at {premises_address} for a term of "
       "{term_months} months beginning {start_date}, at {rent_usd} per month.",
       ["Tex. Prop. Code § 92.001"],
       needs=("premises_address", "term_months", "start_date", "rent_usd")),
    _c("repairs", "terms", "Repairs",
       "Landlord shall make a diligent effort to repair or remedy a condition that "
       "materially affects the physical health or safety of an ordinary tenant after "
       "Tenant gives notice to the person to whom rent is normally paid.",
       ["Tex. Prop. Code § 92.052"]),
    _c("signatures", "signatures", "Signatures",
       "Landlord: ___________________  {landlord_name}\n"
       "Tenant: _____________________  {tenant_name}",
       ["Tex. Prop. Code § 92.001"], needs=("landlord_name", "tenant_name")),
]

SERVICE_AGREEMENT = [
    _c("parties", "parties", "Parties",
       "This Service Agreement is made between {client_name} (\"Client\") and "
       "{provider_name} (\"Provider\").",
       ["Tex. Bus. & Com. Code § 17.45"], needs=("client_name", "provider_name")),
    _c("services", "terms", "Services and Fee",
       "Provider shall perform the following services: {services_description}. "
       "Client shall pay {fee_usd} on completion, and nothing not written here.",
       ["Tex. Bus. & Com. Code § 17.46"], needs=("services_description", "fee_usd")),
    _c("signatures", "signatures", "Signatures",
       "Client: _____________________  {client_name}\n"
       "Provider: ___________________  {provider_name}",
       ["Tex. Bus. & Com. Code § 17.45"], needs=("client_name", "provider_name")),
]

FAMILIES = {
    "car_purchase": CAR_PURCHASE,
    "house_purchase": HOUSE_PURCHASE,
    "lease": LEASE,
    "service_agreement": SERVICE_AGREEMENT,
}

# Payment instrument blocks. Each carries its own discharge language, and
# the discharge language is explicit: what is payment, what is full
# satisfaction, what happens when the creditor accepts without objection.
INSTRUMENT_CLAUSES = {
    "cashiers_check": [
        _c("payment_instrument", "payment", "Payment Instrument",
           "Buyer pays the Price by cashier's check payable to Seller, delivered at "
           "delivery of the Vehicle.",
           ["Tex. Bus. & Com. Code § 3.104", "Tex. Bus. & Com. Code § 3.310"]),
        _c("discharge", "discharge", "Discharge",
           "When Seller takes the cashier's check, Buyer's obligation for the Price is "
           "discharged to the same extent as if money in that amount had been paid.",
           ["Tex. Bus. & Com. Code § 3.310"]),
    ],
    "check": [
        _c("payment_instrument", "payment", "Payment Instrument",
           "Buyer pays the Price by check payable to Seller, delivered at delivery of "
           "the Vehicle. The check bears the conspicuous statement \"Tendered as full "
           "satisfaction of the Price under this Agreement.\"",
           ["Tex. Bus. & Com. Code § 3.104", "Tex. Bus. & Com. Code § 3.311"]),
        _c("discharge", "discharge", "Discharge and Full Satisfaction",
           "On Seller's taking the check, Buyer's obligation for the Price is suspended "
           "until the check is paid or dishonored. Payment of the check discharges the "
           "obligation to the amount of the check. The check is tendered in good faith "
           "as full satisfaction of the Price; if Seller obtains payment of it, the "
           "Price is fully satisfied unless Seller tenders repayment of the amount "
           "within 90 days after payment.",
           ["Tex. Bus. & Com. Code § 3.310", "Tex. Bus. & Com. Code § 3.311"]),
    ],
    "wire": [
        _c("payment_instrument", "payment", "Payment Instrument",
           "Buyer pays the Price by funds transfer to the account Seller designates in "
           "writing, initiated on the delivery date.",
           ["Tex. Bus. & Com. Code § 4A.104"]),
        _c("discharge", "discharge", "Discharge",
           "Buyer's obligation for the Price is discharged when Seller's bank accepts "
           "the payment order for Seller's benefit.",
           ["Tex. Bus. & Com. Code § 4A.406"]),
    ],
    "escrow": [
        _c("payment_instrument", "payment", "Payment Instrument",
           "Buyer deposits the Price with {escrow_agent} as escrow agent, to be released "
           "to Seller on delivery of the Vehicle and transfer of title.",
           ["Tex. Fin. Code § 151.002"], needs=("escrow_agent",)),
        _c("discharge", "discharge", "Discharge",
           "Release of the escrowed funds to Seller discharges Buyer's obligation for "
           "the Price.",
           ["Tex. Fin. Code § 151.002"]),
    ],
    "promissory_note": [
        _c("payment_instrument", "payment", "Payment Instrument",
           "Buyer pays the Price by promissory note payable to Seller in the amount of "
           "{price_usd}, on the schedule in this Agreement.",
           ["Tex. Bus. & Com. Code § 3.104", "Tex. Bus. & Com. Code § 3.412"],
           needs=("price_usd",)),
        _c("discharge", "discharge", "Discharge",
           "On Seller's taking the note, Buyer's obligation for the Price is suspended "
           "until the note is paid or dishonored. Payment of the note discharges the "
           "obligation to the extent of the payment.",
           ["Tex. Bus. & Com. Code § 3.310"]),
    ],
}


# ----------------------------------------------------------------------
# Resolution and rendering
# ----------------------------------------------------------------------

def _section_key(citation):
    """The number the shelf is keyed by: 'Tex. Bus. & Com. Code § 2.313' -> '2.313'."""
    m = re.search(r"§\s*([0-9A-Za-z]+\.[0-9A-Za-z]+)", citation)
    return m.group(1) if m else citation


def resolve_all(citations, resolver):
    """-> {citation: entry|None}. `resolver(cite) -> entry dict or None`.
    An entry must carry `text`; `title` is taken as the shelf names it."""
    out = {}
    for c in citations:
        try:
            e = resolver(c)
        except Exception:
            e = None
        if isinstance(e, dict) and (e.get("text") or "").strip():
            out[c] = e
        else:
            out[c] = None
    return out


def _layer_lines(layers):
    """Validate the four layers. -> (fields, problems)."""
    fields, problems = {}, []
    for l in CONTRACT_LAYERS:
        v = (layers or {}).get(l)
        if not isinstance(v, dict) or not v.get("party"):
            problems.append(f"layer {l}: no party named")
            continue
        ev = str(v.get("evidence") or "unknown")
        if ev not in EVIDENCE_OK:
            problems.append(f"layer {l}: evidence {ev!r} - unknown blocks")
            continue
        fields[f"layer_{l}"] = str(v["party"])
    return fields, problems


def draft(family, facts, layers, resolver, instrument=None):
    """Assemble a contract. -> dict with document, schedule, refused, sha256.

    Nothing drafts unless the four layers are named with admissible
    evidence. Each clause then either resolves every authority and ships,
    or is refused with the reason. The document is deterministic in
    (family, facts, layers, instrument, and the resolved text)."""
    if family not in FAMILIES:
        raise Refused(f"unknown template family {family!r}; known: {sorted(FAMILIES)}")
    facts = dict(facts or {})
    layer_fields, layer_problems = _layer_lines(layers)
    if layer_problems:
        raise Refused("the four-layer map is not complete: " + "; ".join(layer_problems))

    # Layer conflict: the seller claims to carry the debt and the map says
    # the buyer carries it. Refused before drafting, by name.
    buyer = str(facts.get("buyer_name") or facts.get("tenant_name")
                or facts.get("client_name") or "").strip().lower()
    debt_party = layer_fields["layer_debt_holder"].strip().lower()
    layer_conflict = None
    if facts.get("seller_claims_to_carry_debt") and buyer and debt_party == buyer:
        layer_conflict = ("seller claims to carry the debt; the four-layer map names the "
                          "buyer as debt_holder")

    # The payment instrument must classify - maturity mandatory.
    inst_clauses, inst_class = [], None
    if instrument:
        kind = str(instrument.get("kind") or "")
        inst_class = instrument_rules.classify_instrument(
            kind, value=instrument.get("value", facts.get("price_usd")),
            maturity=instrument.get("maturity"), declared_by=instrument.get("declared_by"))
        if inst_class["kind"] not in INSTRUMENT_CLAUSES:
            raise Refused(f"no clause block for instrument {kind!r}; known: "
                          f"{sorted(INSTRUMENT_CLAUSES)}")
        inst_clauses = INSTRUMENT_CLAUSES[inst_class["kind"]]

    clauses = list(FAMILIES[family])
    # Instrument clauses sit after the layers/title/warranty group and
    # before acceptance - a fixed position, so order never depends on input.
    try:
        at = next(i for i, c in enumerate(clauses) if c["id"] == "acceptance")
    except StopIteration:
        at = len(clauses) - 1
    clauses = clauses[:at] + inst_clauses + clauses[at:]

    fields = dict(facts)
    fields.update(layer_fields)

    shipped, refused, schedule = [], [], []
    n = 0
    for c in clauses:
        if c["when"] is not None and not c["when"](facts):
            continue
        if c["id"] == "financing_seller" and layer_conflict:
            refused.append({"clause": c["id"], "reason": "layer_conflict",
                            "detail": layer_conflict})
            continue
        missing = [k for k in c["needs"] if not str(fields.get(k, "")).strip()]
        if missing:
            refused.append({"clause": c["id"], "reason": "facts_missing",
                            "detail": f"needs {missing}"})
            continue
        res = resolve_all(c["authority"], resolver)
        unresolved = [k for k, v in res.items() if v is None]
        if unresolved:
            refused.append({"clause": c["id"], "reason": "authority_not_in_corpus",
                            "detail": f"cannot open {unresolved}"})
            continue
        n += 1
        text = c["template"].format(**{k: fields[k] for k in fields})
        shipped.append({"n": n, "id": c["id"], "block": c["block"],
                        "heading": c["heading"], "text": text,
                        "authority": list(c["authority"])})
        for cite in c["authority"]:
            e = res[cite]
            body = re.sub(r"\s+", " ", str(e.get("text") or "")).strip()
            schedule.append({
                "clause": n, "citation": cite,
                "title": str(e.get("title") or ""),
                "section": str(e.get("citation") or _section_key(cite)),
                "text_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                "integrity": (e.get("integrity") or {}).get("state") if isinstance(
                    e.get("integrity"), dict) else e.get("integrity"),
                "held_by": e.get("held_by") or "own",
            })

    document = render(family, shipped, schedule)
    return {
        "family": family,
        "instrument": inst_class,
        "document": document,
        "sha256": hashlib.sha256(document.encode("utf-8")).hexdigest(),
        "clauses_shipped": [s["id"] for s in shipped],
        "refused": refused,
        "schedule": schedule,
        "layer_conflict": layer_conflict,
        "complete": not refused,
        # Provenance of the resolution - reported, never written into the
        # document, so two agents' documents can be identical while their
        # provenance differs.
        "resolved_from": sorted({s["held_by"] for s in schedule}),
    }


TITLES = {"car_purchase": "MOTOR VEHICLE PURCHASE AGREEMENT",
          "house_purchase": "REAL PROPERTY PURCHASE AGREEMENT",
          "lease": "RESIDENTIAL LEASE",
          "service_agreement": "SERVICE AGREEMENT"}


def render(family, shipped, schedule):
    """Plain text. The body reads as a contract; Schedule A carries the
    citations. No dates that the facts did not supply; no provenance."""
    lines = [TITLES[family], ""]
    for s in shipped:
        lines.append(f"{s['n']}. {s['heading']}")
        lines.append(s["text"])
        lines.append("")
    lines.append("SCHEDULE A - AUTHORITIES")
    lines.append("Each clause above rests on the provision listed against its number. "
                 "The hash is of the provision's text as read.")
    lines.append("")
    seen = set()
    for e in schedule:
        key = (e["clause"], e["citation"])
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"Clause {e['clause']}: {e['citation']} - {e['title']} "
                     f"[sha256 {e['text_sha256'][:16]}]")
    return "\n".join(lines).rstrip() + "\n"
