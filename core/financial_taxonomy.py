#!/usr/bin/env python3
"""What a thing IS, not what the institution calls it - and what moved money.

    from core.financial_taxonomy import classify_movement, commodity_position

PHASE 1B'S CLASSIFICATION LAYER. Phase 1A's nine resource kinds answer "is
this an account or a benefit". They do not answer "is this gold I own, gold
somebody owes me, or shares in a company that mines gold" - and those have
almost nothing in common except the word.

WHY GOLD IS THE WORKED EXAMPLE. One shiny metal, and humans built a dozen
different legal relationships around it. "I have a $50,000 gold position"
tells you the number and nothing that matters:

    allocated          specific bars held for you. Yours, in custody
    unallocated        a CLAIM against the institution for a quantity. You
                       are an unsecured creditor; there are no bars with your
                       name on them, and in an insolvency that is the whole
                       story
    physical           bars or coins you actually hold
    equity             shares in a miner. Correlated to gold, not gold
    fund_interest      shares in a vehicle. A security
    receivable         somebody owes you metal
    payable            you owe somebody metal
    collateral         metal securing a borrowing. The financing is the
                       relationship; the metal is encumbered
    leased             advanced under financing terms
    premium            the amount paid ABOVE spot. A price component, not an
                       asset on its own

An allocated holding and an unallocated claim are the same sentence in
marketing and opposite positions in a bankruptcy.

THE MOVEMENT RULE, which is the other half of this file:

    Never classify an economic result from the movement of money. Determine
    what legal relationship produced the movement.

$100,000 arriving is not "an asset acquired". It is earned income, or loan
proceeds, or a refinancing that never meaningfully became yours, or the
release of your own collateral - and those have radically different
consequences. classify_movement() REFUSES to answer without a stated,
evidenced funding source.

AND PAYING A DEBT IS NOT REFINANCING IT.

    paid         you transferred your own resources. The obligation is gone.
                 debt down, cash down.
    refinanced   a new creditor satisfied the old one. The old obligation is
                 extinguished and a NEW one exists. Debt down, debt up, and
                 the cash may never have been meaningfully yours.
    converted    an asset was transferred to satisfy it. Needs the asset, the
                 valuation AND ITS DATE, and whether the creditor accepted it
                 as FULL satisfaction - because partial acceptance leaves a
                 deficiency that behaves nothing like a discharge.
    assumed      somebody else took the obligation on.
    forgiven     the creditor released it. Usually income, which is the
                 surprise.

Recording all five as "payment" is the classic error, and it is the one that
makes a balance sheet look settled while the obligation is alive under a new
name.

THIS FILE DECIDES NOTHING ABOUT TAX OR LAW. It records what the instrument
says and refuses what it cannot establish. Accounting and Legal interpret.
"""

# ---------------------------------------------------------------- instruments

INSTRUMENT_FORMS = (
    "cash", "deposit_account", "credit_facility", "loan", "receivable",
    "payable", "security", "fund_interest", "commodity_physical",
    "commodity_allocated", "commodity_unallocated_claim", "contractual_right",
    "liability", "benefit_stream", "collateral_interest", "unknown",
)

# The commodity forms, kept separate because the word is the only thing they
# share. `unknown` is here and blocks, as everywhere.
COMMODITY_FORMS = (
    "physical", "allocated", "unallocated_claim", "custodial_account",
    "equity_exposure", "fund_interest", "linked_instrument", "collateral",
    "leased", "receivable", "payable", "premium_component", "unknown",
)

# What must be established before a commodity position means anything. A
# position missing any of these is UNRESOLVED, not "approximately known".
COMMODITY_FACETS = (
    "legal_form", "quantity", "unit", "beneficial_interest", "custody",
    "allocation_status", "counterparty", "valuation_source",
    "valuation_date", "encumbrance", "settlement_terms",
)

# ------------------------------------------------------------------- movement

# Where money came from. NOT inferable from the amount or the direction.
FUNDING_SOURCES = (
    "earned_income", "benefit_payment", "loan_proceeds", "refinancing_proceeds",
    "credit_facility_draw", "sale_proceeds", "investment_redemption",
    "internal_transfer", "capital_contribution", "gift", "debt_forgiveness",
    "collateral_release", "reimbursement", "refund", "unknown",
)

TRANSACTION_TYPES = (
    "purchase", "sale", "deposit", "withdrawal", "custody", "allocation",
    "unallocated_claim", "loan", "advance", "refinancing", "debt_assumption",
    "debt_conversion", "debt_settlement", "collateralization", "lease",
    "repurchase", "redemption", "exchange", "transfer", "writeoff",
    "forgiveness", "unknown",
)

# How an obligation ended. Five different economic events, one English word.
SETTLEMENT_KINDS = ("paid", "refinanced", "converted", "assumed", "forgiven",
                    "unknown")

# What each kind cannot be recorded without.
SETTLEMENT_REQUIRES = {
    "paid": ("obligation_id", "amount", "source_of_funds"),
    "refinanced": ("obligation_id", "amount", "new_obligation_id",
                   "new_creditor"),
    "converted": ("obligation_id", "amount", "asset_transferred",
                  "valuation", "valuation_date", "accepted_as_full"),
    "assumed": ("obligation_id", "amount", "assumed_by"),
    "forgiven": ("obligation_id", "amount", "forgiven_by"),
}


class Unclassifiable(ValueError):
    """Refused. The evidence does not establish what this is."""


def classify_movement(amount, direction, funding_source=None, evidence=None,
                      citation=None):
    """-> a classification, or refuse. Money moving is not a conclusion.

    THE RULE: never classify an economic result from the movement of money.
    $100,000 arriving is not an asset acquired - it is income, or borrowing,
    or a refinancing the money never meaningfully belonged to you in, or your
    own collateral coming back. The amount and the direction are identical in
    every one of those."""
    if direction not in ("in", "out"):
        raise Unclassifiable("direction must be 'in' or 'out'")
    if not funding_source or funding_source == "unknown":
        raise Unclassifiable(
            f"{amount!r} moving {direction} is not classifiable. What produced "
            f"the movement has not been established, and the amount and "
            f"direction look identical for earned income, loan proceeds, a "
            f"refinancing and a collateral release. State the funding source "
            f"with its evidence: {list(FUNDING_SOURCES)}")
    if funding_source not in FUNDING_SOURCES:
        raise Unclassifiable(f"{funding_source!r} is not a funding source")
    if evidence not in ("cited", "system_verified", "stated_by_principal"):
        raise Unclassifiable(
            f"a funding source on {evidence!r} is refused. This is the field "
            f"that decides whether money arriving is yours or borrowed.")
    if not citation:
        raise Unclassifiable("the funding source needs its document")
    creates_liability = funding_source in (
        "loan_proceeds", "refinancing_proceeds", "credit_facility_draw")
    return {
        "amount": amount, "direction": direction,
        "funding_source": funding_source, "evidence": evidence,
        "citation": citation,
        "creates_liability": creates_liability,
        "is_own_resources": funding_source in (
            "earned_income", "benefit_payment", "sale_proceeds",
            "investment_redemption", "collateral_release", "refund"),
        "note": ("Money arriving from borrowing increases a liability by the "
                 "same stroke. A balance that rose because a facility was "
                 "drawn is not a balance that rose because something was "
                 "earned." if creates_liability else
                 "Recorded from the stated source, not inferred from the "
                 "movement."),
        "not_a_conclusion": ("The accounting and tax consequences are "
                             "Accounting's and Legal's. This records what "
                             "produced the movement."),
    }


def settle(kind, **facts):
    """-> a settlement record, or refuse. Paying is not refinancing.

    Recording all five as "payment" makes a balance sheet look settled while
    the obligation is alive under a new name."""
    if kind not in SETTLEMENT_KINDS or kind == "unknown":
        raise Unclassifiable(
            f"{kind!r} is not a settlement kind. Choose deliberately: "
            f"{[k for k in SETTLEMENT_KINDS if k != 'unknown']} are five "
            f"different economic events, not five words for payment.")
    # PRESENCE, NOT TRUTHINESS. `accepted_as_full=False` is an ANSWER - and
    # the one that matters most, because a creditor who did not accept a
    # transfer as full satisfaction leaves a deficiency. Checking
    # `not facts.get(f)` refused to record exactly the case this field exists
    # for. The same trap as treating 0 as missing on an amount.
    missing = [f for f in SETTLEMENT_REQUIRES[kind]
               if f not in facts or facts[f] is None or facts[f] == ""]
    if missing:
        raise Unclassifiable(
            f"a {kind!r} settlement needs {missing}. "
            + {"refinanced": "Without the new obligation this records a debt "
                             "disappearing, when what happened is that it "
                             "moved.",
               "converted": "Without the valuation, its date, and whether the "
                            "creditor accepted it as FULL satisfaction, a "
                            "deficiency is indistinguishable from a "
                            "discharge.",
               "forgiven": "Forgiveness is usually income to the debtor, "
                           "which is the part people are surprised by.",
               }.get(kind, "Each field changes what actually happened."))
    out = dict(facts)
    out["settlement_kind"] = kind
    # THREE FACTS, BECAUSE ONE OF THEM ALONE MISLEADS.
    #
    # A refinancing DOES extinguish the original obligation - Creditor A is
    # paid off. Recording only that reads as "the debt is gone", which is
    # precisely the error: a new obligation of the same size exists and the
    # cash may never have been meaningfully the debtor's. So the extinction,
    # the creation and the NET are all stated, and the net is the one that
    # survives being skim-read.
    out["obligation_extinguished"] = (
        kind in ("paid", "forgiven", "assumed")
        or (kind == "converted" and bool(facts.get("accepted_as_full"))))
    out["new_obligation_created"] = kind == "refinanced"
    if kind == "refinanced":
        out["obligation_extinguished"] = True
        out["net_obligation_change"] = 0
        out["what_actually_happened"] = (
            "The obligation MOVED. The original is discharged and a new one "
            "of the same size exists against a different creditor. Net debt "
            "is unchanged, and recording this as a payment makes a balance "
            "sheet look settled while the obligation is alive under a new "
            "name.")
    elif kind == "assumed":
        out["net_obligation_change"] = "-amount for this debtor only"
        out["what_actually_happened"] = (
            "The obligation left this debtor and landed on another party. It "
            "has not been paid by anyone yet.")
    elif out["obligation_extinguished"]:
        out["net_obligation_change"] = "-amount"
    else:
        out["net_obligation_change"] = "unresolved"
    if kind == "converted" and not facts.get("accepted_as_full"):
        out["deficiency_possible"] = True
        out["why"] = ("The creditor did not accept the transfer as full "
                      "satisfaction, so a balance may survive it.")
    return out


def commodity_position(**facets):
    """-> a position, with every unestablished facet named as unresolved.

    "A $50,000 gold position" is a number and a word. Whether it is metal you
    hold, metal held for you, a claim against a bank, or shares in a miner
    changes everything about what you own - and in an insolvency an allocated
    holding and an unallocated claim are opposite positions."""
    form = facets.get("legal_form")
    if form not in COMMODITY_FORMS:
        raise Unclassifiable(
            f"legal_form must be one of {list(COMMODITY_FORMS)}. 'gold' is "
            f"not a legal form - it is what the thing is made of.")
    unresolved = [f for f in COMMODITY_FACETS if not facets.get(f)]
    out = dict(facets)
    out["unresolved"] = unresolved
    out["absence_state"] = "incomplete" if unresolved else "verified_clear"
    if form == "unallocated_claim":
        out["creditor_exposure"] = True
        out["why"] = ("An unallocated claim is a contractual right against "
                      "the institution for a quantity. There are no bars with "
                      "your name on them, and in an insolvency you are an "
                      "unsecured creditor - which reads identically to an "
                      "allocated holding on a statement.")
    if form in ("equity_exposure", "fund_interest", "linked_instrument"):
        out["holds_metal"] = False
        out["why"] = (f"A {form} is a SECURITY. It may track the metal price "
                      f"and it is not ownership of metal.")
    if form == "premium_component":
        out["is_position"] = False
        out["why"] = ("The amount paid above the reference price is a price "
                      "component, not a holding of its own.")
    return out
