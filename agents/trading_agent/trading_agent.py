#!/usr/bin/env python3
"""Trading Agent - discovery and refusal. It cannot trade.

THE CAPABILITY SURFACE IS THE BOUNDARY.

The desk this borrows from encodes its boundaries in prompt text: "You never
decide to buy", "You never reconsider", "You never recompute size". Those hold
until the model has a bad night. CLAUDE.md's rule is that an orchestrator which
CAN read domain content will eventually reason about it - not should not, WILL -
and the same applies here.

So this agent has no buy verb. No size verb. No fill, open, close, or
place_order. Not disabled, not guarded, not behind a flag - absent from the
dispatch table, which is the only version of "never" that survives a model swap.
`capability_surface` reports the absence so the boundary is inspectable rather
than asserted.

Under CLAUDE.md's capital-actuation doctrine this agent sits below Accounting and
Trustee: it does not define the financial reality it operates in. It produces a
ranked list and a pile of refusals. Somebody else decides what any of it is worth.
"""
import sys
import os
import re
import json
import time
import requests
from datetime import datetime, timezone

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from core.base_agent import AgentBase

DEX_BASE = "https://api.dexscreener.com/latest/dex"
SOLANA_RPC = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
HTTP_TIMEOUT = int(os.getenv("TRADING_HTTP_TIMEOUT", "20"))

# Thresholds. These are the borrowed desk's scar tissue, not general truths, and
# they are named constants so they read as settings rather than as findings.
# Anything derived FROM them says so.
TOP_WALLET_MAX_PCT = 5.0        # above this you are exit liquidity
LIQUIDITY_FLOOR_USD = 15000.0   # below this VET kills it anyway; SCAN drops it earlier
MIN_AGE_MINUTES = 15            # younger than this is unreadable, not promising
FEE_RATE = 0.0045               # the venue's rate on memecoins
FEE_FLOOR_USD = 0.95            # and its per-trade minimum
MAX_ROUND_TRIP_PCT = 2.0        # over this, no meme edge covers the entry

DISCLAIMER = (
    "Informational only. This agent ranks and refuses candidates; it holds no "
    "execution capability and this output is not a recommendation to buy or sell "
    "anything. Nothing here has been reconciled against a venue record."
)


class TradingAgent(AgentBase):
    ROUTING_TERMS = (
        "candidate", "token", "memecoin", "meme coin", "dexscreener",
        "liquidity", "market cap", "mcap", "rug", "honeypot",
        r"\bvet\b", r"\bscan\b", r"\bticker\b",
        r"top wallet", r"holder concentration", r"buy/?sell ratio",
    )

    def __init__(self):
        super().__init__(
            agent_id="trading_agent",
            port=9016,
            capabilities=[
                "scan_candidates", "vet_candidate", "vet_batch",
                "fee_viability", "concentration", "capability_surface",
                "list_rejections", "rejection_stats",
            ],
            role="agent",
        )
        self.log("Trading Agent initialized - discovery and refusal only, no execution surface.")

    # ---- discovery ------------------------------------------------------

    def _dex_search(self, query):
        r = requests.get(f"{DEX_BASE}/search", params={"q": query}, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        return (r.json() or {}).get("pairs") or []

    def _age_minutes(self, pair):
        created = pair.get("pairCreatedAt")
        if not created:
            return None
        return int((time.time() - created / 1000.0) / 60)

    def scan_candidates(self, args):
        """Rank launches. Produces a list; never an opinion about buying.

        RANK ON RATE OF CHANGE, NEVER ABSOLUTE SIZE. And the shape that matters
        most is the relationship between price and participation: price climbing
        faster than the buyer count is one wallet walking the price up, and the
        exit is that same wallet. It is marked and never ranked up.

        Holder COUNT is not in DexScreener's response and is not inferable from
        it. txns.buys is a count of transactions, not of people, and substituting
        one for the other is the exact shape of failure CLAUDE.md calls a guessed
        standing field. So holders is null, holders_source says why, and VET is
        told which check it therefore cannot run.
        """
        a = args if isinstance(args, dict) else {}
        query = a.get("query") or a.get("q") or "SOL"
        limit = min(int(a.get("limit", 10)), 10)
        want_chain = str(a.get("chain", "solana")).lower() or None
        try:
            pairs = self._dex_search(query)
        except Exception as exc:
            return {"error": f"DexScreener unreachable: {type(exc).__name__}: {exc}",
                    "degraded": True, "candidates": [], "disclaimer": DISCLAIMER}

        held, too_young, thin, off_chain = [], 0, 0, 0
        for p in pairs:
            if want_chain and str(p.get("chainId") or "").lower() != want_chain:
                off_chain += 1
                continue
            liq = ((p.get("liquidity") or {}).get("usd")) or 0.0
            if liq < LIQUIDITY_FLOOR_USD:
                thin += 1
                continue
            age = self._age_minutes(p)
            if age is not None and age < MIN_AGE_MINUTES:
                too_young += 1
                continue

            vol, chg = p.get("volume") or {}, p.get("priceChange") or {}
            tx1 = (p.get("txns") or {}).get("h1") or {}
            buys, sells = tx1.get("buys") or 0, tx1.get("sells") or 0
            ratio = (buys / sells) if sells else (float(buys) if buys else 0.0)

            # More sells than buys in the recent window drops the candidate
            # regardless of what price is doing.
            selling = sells > buys
            price_h1 = chg.get("h1") or 0.0

            # Participation proxy. NOT a holder count - transaction counts only.
            # It is a weaker signal and is labelled as one everywhere it appears.
            score = ratio * 10.0
            reasons = [f"buy/sell h1 {buys}/{sells} = {ratio:.2f}"]
            if selling:
                score -= 50.0
                reasons.append("more sells than buys in h1 - dropped")
            if price_h1 > 0 and ratio <= 1.0:
                score -= 30.0
                reasons.append(
                    "WARNING: price up while buys do not lead sells - the shape of "
                    "one wallet walking the price, marked and never ranked up")

            held.append({
                "ticker": (p.get("baseToken") or {}).get("symbol"),
                "contract": (p.get("baseToken") or {}).get("address"),
                "chain": p.get("chainId"), "dex": p.get("dexId"),
                "age_minutes": age,
                "holders": None,
                "holders_source": ("unavailable - DexScreener returns no holder count and "
                                   "txns.buys counts transactions, not people. VET cannot "
                                   "run its holders-flat check on this candidate."),
                "liquidity_usd": round(liq, 2),
                "mcap": p.get("marketCap"), "fdv": p.get("fdv"),
                "volume_h1": vol.get("h1"), "volume_h6": vol.get("h6"), "volume_h24": vol.get("h24"),
                "buys_h1": buys, "sells_h1": sells,
                "price_change_h1": price_h1,
                "_score": score, "rank_reason": "; ".join(reasons),
            })

        held.sort(key=lambda x: x["_score"], reverse=True)
        out = []
        for i, c in enumerate(held[:limit], 1):
            c.pop("_score", None)
            c["rank"] = i
            out.append(c)

        return {
            "query": query,
            "candidates": out,
            "returned": len(out),
            "dropped_below_liquidity_floor": thin,
            "dropped_too_young": too_young,
            "dropped_other_chain": off_chain,
            "chain": want_chain,
            "degraded": True,
            "degraded_because": ("No world-context source is wired, and holder counts are "
                                 "unavailable. Ranking is launch data alone."),
            "not_padded": ("Fewer than the limit is a normal result. This list is never padded "
                           "to look productive."),
            "no_opinion": "No buy opinion, target or size is produced here. Not this agent's seat.",
            "disclaimer": DISCLAIMER,
        }

    # ---- the check that is never optional -------------------------------

    def concentration(self, args):
        """Largest holder as a share of supply, from the chain itself.

        Two RPC calls, and BOTH must answer. An unreachable RPC is a REJECT and
        never a skip: CLAUDE.md's capital posture is that fail-closed is the
        default for anything touching money, and the cleanest statement of why is
        that a position you cannot measure is a position you do not hold.

        Measured 2026-09-05 across six keyless endpoints: none serve
        getTokenLargestAccounts. api.mainnet-beta.solana.com answers
        getTokenSupply and returns 429 for this one specifically; ankr, rpcpool
        and publicnode return 403 for both. So without SOLANA_RPC_URL pointing at
        a keyed provider this check cannot run, and this agent says so rather
        than passing candidates it did not check.
        """
        a = args if isinstance(args, dict) else {}
        mint = a.get("contract") or a.get("mint")
        if not mint:
            return {"error": "need a contract/mint address"}

        # WRONG CHAIN IS NOT AN UNREACHABLE RPC. Caught on this agent's first
        # live run: a robinhood-chain candidate was sent to a Solana RPC, which
        # correctly refused the address, and the refusal was reported as
        # "concentration could not be measured" - the fail-closed message meant
        # for a throttled or dead endpoint. Same REJECT, completely different
        # cause, and a reader chasing the first would go looking for an RPC key
        # that would not have helped.
        chain = str(a.get("chain") or "").lower()
        if chain and chain != "solana":
            return {"measurable": False, "why": f"candidate is on chain {chain!r}, not solana",
                    "verdict_contribution": "REJECT",
                    "reason_class": "unsupported_chain",
                    "note": ("This agent measures concentration on Solana only. A non-Solana "
                             "candidate is out of scope, NOT a failed measurement - an RPC key "
                             "would not change this outcome."),
                    "disclaimer": DISCLAIMER}

        def rpc(method, params):
            r = requests.post(SOLANA_RPC, timeout=HTTP_TIMEOUT,
                              headers={"Content-Type": "application/json"},
                              json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
            body = r.json() if r.content else {}
            if "error" in body:
                raise RuntimeError(f"{method}: rpc error {body['error'].get('code')} "
                                   f"{str(body['error'].get('message'))[:80]}")
            return body["result"]

        try:
            supply = rpc("getTokenSupply", [mint])["value"]
            largest = rpc("getTokenLargestAccounts", [mint])["value"]
        except Exception as exc:
            return {"measurable": False, "endpoint": SOLANA_RPC, "why": str(exc),
                    "verdict_contribution": "REJECT",
                    "note": ("An unmeasurable position is not a passable one. Set "
                             "SOLANA_RPC_URL to a keyed provider to run this check."),
                    "disclaimer": DISCLAIMER}

        total = float(supply.get("amount") or 0)
        if not total:
            return {"measurable": False, "why": "supply reported as zero",
                    "verdict_contribution": "REJECT", "disclaimer": DISCLAIMER}
        top = float((largest or [{}])[0].get("amount") or 0)
        pct = 100.0 * top / total
        return {
            "measurable": True, "endpoint": SOLANA_RPC,
            "top_wallet_percent": round(pct, 3),
            "threshold_percent": TOP_WALLET_MAX_PCT,
            "verdict_contribution": "REJECT" if pct > TOP_WALLET_MAX_PCT else "PASS",
            "accounts_examined": len(largest or []),
            "note": ("Concentration only. This is NOT a holder count, which needs an "
                     "indexer this agent does not have."),
            "disclaimer": DISCLAIMER,
        }

    # ---- arithmetic that kills most small entries -----------------------

    def fee_viability(self, args):
        """Round-trip cost at the intended size, and the bank that implies.

        The constraint nobody states: a per-trade FLOOR and a percent-of-bank
        CEILING are two different rules, and multiplying them produces a minimum
        viable account size that appears in neither. Below roughly $211 a ticket
        the floor dominates the rate, and a ticket large enough to escape it,
        held under a 6% cap, implies a bank in the low thousands.
        """
        a = args if isinstance(args, dict) else {}
        dollars = float(a.get("dollars") or 0)
        if dollars <= 0:
            return {"error": "need dollars (the intended ticket size)"}
        cap_pct = float(a.get("size_cap_pct", 6.0))
        one_way = max(FEE_RATE * dollars, FEE_FLOOR_USD) / dollars
        rt = one_way * 2 * 100
        return {
            "ticket_usd": dollars,
            "fee_each_way_pct": round(one_way * 100, 3),
            "round_trip_pct": round(rt, 3),
            "max_round_trip_pct": MAX_ROUND_TRIP_PCT,
            "verdict": "FEE_FLOOR" if rt > MAX_ROUND_TRIP_PCT else "VIABLE",
            "floor_dominates_below_usd": round(FEE_FLOOR_USD / FEE_RATE, 2),
            "floor_or_rate": ("the per-trade floor" if FEE_RATE * dollars < FEE_FLOOR_USD
                              else "the percentage rate"),
            "implied_minimum_bank_usd": round(dollars / (cap_pct / 100.0), 2),
            "implied_bank_basis": (f"a {dollars:.0f} ticket held under a {cap_pct}% "
                                   f"per-position cap. Derived from the two rules, not quoted."),
            "disclaimer": DISCLAIMER,
        }

    # ---- refusal --------------------------------------------------------

    def vet_candidate(self, args):
        """Kill candidates. Measured on what it correctly refuses.

        Checks run cheapest first and the first failure ends the run, so an
        expensive call is never made to confirm a decision already reached.

        A check that could not run is recorded as SKIPPED and never as passed -
        CLAUDE.md: a check that found nothing must say so, distinctly from a
        check that found the thing to be fine. A verdict carrying skips is
        PASS_PARTIAL, which is not PASS, so whatever reads it downstream knows
        the evidence is incomplete before it acts.
        """
        a = args if isinstance(args, dict) else {}
        ticker = a.get("ticker") or a.get("contract") or "unknown"
        run, skipped = [], []

        def reject(check, why, evidence=None):
            rec = {"ticker": ticker, "verdict": "REJECT", "failed_check": check,
                   "why": why, "evidence": evidence,
                   "checks_run": run, "checks_skipped": skipped,
                   "at": datetime.now(timezone.utc).isoformat(), "disclaimer": DISCLAIMER}
            self._remember_rejection(rec)
            return rec

        liq = a.get("liquidity_usd")
        run.append("liquidity_floor")
        if liq is None:
            return reject("liquidity_floor", "no liquidity figure supplied - unmeasured is not passable")
        if float(liq) < LIQUIDITY_FLOOR_USD:
            return reject("liquidity_floor",
                          f"liquidity ${float(liq):,.0f} under the ${LIQUIDITY_FLOOR_USD:,.0f} floor. "
                          f"A position you cannot leave is not a position.")

        age = a.get("age_minutes")
        run.append("age_and_shape")
        if age is not None and int(age) < MIN_AGE_MINUTES:
            return reject("age_and_shape",
                          f"{age} minutes old, under the {MIN_AGE_MINUTES}-minute floor. "
                          f"Too early to read is not the same as early.")

        buys, sells = a.get("buys_h1"), a.get("sells_h1")
        run.append("buy_sell_ratio")
        if buys is not None and sells is not None and sells > buys:
            return reject("buy_sell_ratio", f"more sells than buys in h1 ({buys}/{sells}).")

        run.append("already_priced")
        pc = a.get("price_change_h1")
        if pc is not None and float(pc) > float(a.get("already_priced_pct", 25)):
            return reject("already_priced",
                          f"h1 price change {pc}% - the move being reasoned from is already "
                          f"in the price. There is nothing left to take.")

        run.append("holders_flat_vs_price")
        if a.get("holders") is None:
            skipped.append({"check": "holders_flat_vs_price",
                            "why": a.get("holders_source") or "no holder count available"})

        if a.get("dollars"):
            run.append("fee_viability")
            fv = self.fee_viability({"dollars": a["dollars"]})
            if fv.get("verdict") == "FEE_FLOOR":
                return reject("fee_viability",
                              f"round trip {fv['round_trip_pct']}% at a ${a['dollars']} ticket, "
                              f"over the {MAX_ROUND_TRIP_PCT}% maximum.", fv)
        else:
            skipped.append({"check": "fee_viability", "why": "no intended size supplied"})

        run.append("top_wallet_concentration")
        contract = a.get("contract")
        if not contract:
            return reject("top_wallet_concentration",
                          "no contract address, so concentration cannot be measured. "
                          "This check is never optional.")
        conc = self.concentration({"contract": contract, "chain": a.get("chain")})
        if not conc.get("measurable"):
            if conc.get("reason_class") == "unsupported_chain":
                return reject("top_wallet_concentration",
                              str(conc.get("why")) + ". Out of scope rather than unmeasured - "
                              "this agent checks concentration on Solana only.", conc)
            return reject("top_wallet_concentration",
                          "concentration could not be measured: " + str(conc.get("why"))[:160]
                          + ". An unreachable RPC is a REJECT, not a skip.", conc)
        if conc["verdict_contribution"] == "REJECT":
            return reject("top_wallet_concentration",
                          f"top wallet holds {conc['top_wallet_percent']}% of supply, over the "
                          f"{TOP_WALLET_MAX_PCT}% limit. At that concentration you are exit liquidity.",
                          conc)

        verdict = "PASS_PARTIAL" if skipped else "PASS"
        rec = {"ticker": ticker, "verdict": verdict, "failed_check": None,
               "checks_run": run, "checks_skipped": skipped,
               "why": ("Survived every check that could be run. "
                       + ("Skips present, so this is not a clean pass and whatever reads it "
                          "should treat the evidence as incomplete." if skipped else
                          "No skips.")),
               "at": datetime.now(timezone.utc).isoformat(), "disclaimer": DISCLAIMER}
        self._remember_rejection(rec)
        return rec

    def vet_batch(self, args):
        a = args if isinstance(args, dict) else {}
        cands = a.get("candidates") or []
        results = [self.vet_candidate(dict(c, dollars=a.get("dollars"))) for c in cands]
        rej = [r for r in results if r["verdict"] == "REJECT"]
        return {"examined": len(results), "rejected": len(rej),
                "passed": len([r for r in results if r["verdict"] == "PASS"]),
                "passed_partial": len([r for r in results if r["verdict"] == "PASS_PARTIAL"]),
                "results": results,
                "empty_is_normal": ("An empty pass list is a valid and common output. "
                                    "Nothing is approved because the day was quiet."),
                "disclaimer": DISCLAIMER}

    # ---- the record -----------------------------------------------------

    def _remember_rejection(self, rec):
        try:
            key = f"vet_{int(time.time()*1000)}"
            self.store_own_memory(key, json.dumps(rec))
            idx = self._unwrap_value(self.retrieve_own_memory("vet_index"))
            idx = json.loads(idx) if idx else []
            idx.append(key)
            self.store_own_memory("vet_index", json.dumps(idx[-500:]))
        except Exception as exc:
            self.log(f"could not record vet result: {exc}")

    def _load_vet(self):
        try:
            idx = self._unwrap_value(self.retrieve_own_memory("vet_index"))
            keys = json.loads(idx) if idx else []
        except Exception:
            return []
        out = []
        for k in keys:
            raw = self._unwrap_value(self.retrieve_own_memory(k))
            if raw:
                try:
                    out.append(json.loads(raw))
                except Exception:
                    continue
        return out

    def list_rejections(self, args):
        a = args if isinstance(args, dict) else {}
        rows = [r for r in self._load_vet() if r.get("verdict") == "REJECT"]
        return {"rejections": rows[-int(a.get("limit", 20)):], "total": len(rows),
                "disclaimer": DISCLAIMER}

    def rejection_stats(self, args):
        rows = self._load_vet()
        rej = [r for r in rows if r.get("verdict") == "REJECT"]
        by = {}
        for r in rej:
            by[r.get("failed_check") or "unknown"] = by.get(r.get("failed_check") or "unknown", 0) + 1
        out = {"examined": len(rows), "rejected": len(rej),
               "rejection_rate": round(len(rej) / len(rows), 3) if rows else None,
               "by_check": dict(sorted(by.items(), key=lambda kv: -kv[1])),
               "disclaimer": DISCLAIMER}
        if rows and len(rej) / len(rows) < 0.5:
            out["filter_warning"] = ("Under half of everything examined was refused. On a "
                                     "discovery surface this usually means the filters are "
                                     "misconfigured, not that the candidates are good.")
        return out

    # ---- the boundary, made inspectable ---------------------------------

    def capability_surface(self, args):
        """What this agent cannot do, and why that is structural.

        A boundary you can only read about in a docstring is a promise. This
        reports the dispatch table, so the absence is checkable from outside -
        and `tools/check_inherited.py` compares declared against dispatched on
        every run, which is what would catch an execution verb being added here
        quietly."""
        return {
            "agent": self.agent_id,
            "can": sorted(self.capabilities),
            "cannot": ["buy", "sell", "open_position", "close_position", "size_position",
                       "place_order", "cancel_order", "transfer", "sign_transaction",
                       "connect_wallet"],
            "why": ("Absent from the dispatch table rather than disabled or flagged. Under "
                    "CLAUDE.md's capital-actuation doctrine an agent may act unmediated only "
                    "if every action available to it REDUCES exposure; this one takes no "
                    "actions at all, so the question does not arise for it."),
            "holds_no_credential": True,
            "holds_no_capital": True,
            "reconciliation": ("Not this agent's. Accounting owns it - reconcile, "
                               "log_transaction, check_ledger_integrity - because a desk that "
                               "assembles its own P&L from what its parts claim is grading its "
                               "own homework."),
            "disclaimer": DISCLAIMER,
        }

    # ---- dispatch -------------------------------------------------------

    def handle_task(self, task, args, sender):
        a = args if isinstance(args, dict) else {}
        if task == "scan_candidates":
            return self.scan_candidates(a)
        elif task == "vet_candidate":
            return self.vet_candidate(a)
        elif task == "vet_batch":
            return self.vet_batch(a)
        elif task == "fee_viability":
            return self.fee_viability(a)
        elif task == "concentration":
            return self.concentration(a)
        elif task == "capability_surface":
            return self.capability_surface(a)
        elif task == "list_rejections":
            return self.list_rejections(a)
        elif task == "rejection_stats":
            return self.rejection_stats(a)

        cag_result = self.try_handle_cag_task(task, a)
        if cag_result is not None:
            return cag_result
        return {"error": f"Unknown task: {task}", "disclaimer": DISCLAIMER}


if __name__ == "__main__":
    agent = TradingAgent()
    while True:
        time.sleep(60)
        agent.heartbeat()
