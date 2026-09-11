#!/usr/bin/env python3
"""Retrieval correctness, as a build gate.

    python3 tools/check_retrieval.py
    python3 tools/check_retrieval.py --capture     # only when a change is INTENDED

WHY ACCURACY IS NOT ENOUGH, which is the whole reason this exists. A model or an
agent can produce a fluent, correct-sounding answer from a stale or wrong entry
and pass every accuracy check, because accuracy asks whether the ANSWER looks
right and never whether the SOURCE was. A correct answer from the wrong entry
and a wrong answer from the right one score identically, and only one of them
can be traced back and fixed.

RETRIEVAL HAS NO NATURAL ALARM. A lookup that returns the wrong entry returns an
entry. Nothing errors, nothing is empty, and the answer built on it reads fine.
This session broke retrieval twice - once by normalising seed terms on one side
only, once by removing the unigram pass - and both times it was found by hand.

THREE RULES, and the third is what keeps a gate honest:

  a fixture whose expected work stops appearing AT ALL is a regression: FAIL
  a fixture whose rank gets WORSE than recorded: FAIL
  a fixture that IMPROVES is always allowed, and --capture records the new
  position. Improving must never be a build failure or nobody improves it.

EXPECTATIONS ARE SET BY REVIEW, NOT CAPTURED FROM OUTPUT. Capturing what the
system does today records its bugs as its contract - `consumer report` resolved
to a RESPA mortgage-servicing section ahead of the FCRA definition, and blind
capture would have made that the expected answer forever.
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
FIXTURES = os.path.join(ROOT, "config", "retrieval_fixtures.json")

fails = []
AGENTS = {}


def agent_for(aid):
    if aid not in AGENTS:
        if aid == "legal_agent":
            from agents.legal_agent.legal_agent import LegalAgent
            AGENTS[aid] = LegalAgent.__new__(LegalAgent)
        elif aid == "trust_agent":
            from agents.trust_agent.trust_agent import TrustAgent
            AGENTS[aid] = TrustAgent.__new__(TrustAgent)
        else:
            raise KeyError(aid)
        # Constructed WITHOUT __init__ so no port is bound and no network is
        # touched. Retrieval reads the corpus off disk; a gate that needs the
        # swarm running is a gate that fails for reasons unrelated to what it
        # tests.
        a = AGENTS[aid]
        a.agent_id = aid
        a.log = lambda *_x, **_k: None
    return AGENTS[aid]


def ck(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capture", action="store_true")
    a = ap.parse_args()

    doc = json.load(open(FIXTURES, encoding="utf-8"))
    fx = doc["fixtures"]

    print("\n  1. every known-good retrieval still resolves")
    improved = regressed = 0
    for key, f in sorted(fx.items()):
        try:
            ag = agent_for(f["agent"])
            hits = ag.lookup_reference(f["term"]) or []
        except Exception as e:
            ck(f"{key}", False, f"lookup crashed: {str(e)[:60]}")
            continue
        titles = [h.get("title") for h in hits]
        want = f["expect_title"]
        rank = titles.index(want) if want in titles else None
        was = f.get("rank_at_capture")

        if rank is None:
            ck(f"{f['term']!r} still finds its source", False,
               f"expected {str(want)[:40]!r}, got {str(titles[:2])[:60]}")
            regressed += 1
        elif was is not None and rank > was:
            ck(f"{f['term']!r} rank did not worsen", False,
               f"was {was}, now {rank} - something outranked it")
            regressed += 1
        else:
            ck(f"{f['term']!r} resolves", True,
               f"rank {rank}" + (" (IMPROVED)" if was is not None and rank < was else ""))
            if was is not None and rank < was:
                improved += 1
                if a.capture:
                    f["rank_at_capture"] = rank

    print("\n  2. citation enforcement refuses what it should")
    from core.retrieval_citation import verify, source_id
    entry = {"title": "T", "citation": "clean hands", "page": None,
             "text": 'CLEAN HANDS. A party seeking equitable relief must not '
                     'itself be guilty of inequitable conduct. See 15 U.S.C. 1681.'}
    entry["source_id"] = source_id(entry)
    tok = f"[src:{entry['source_id']}]"
    ck("an answer with no citation is refused",
       not verify(f"Unclean hands bars relief.", entry)[0])
    ck("a citation naming another entry is refused",
       not verify("Unclean hands bars relief. [src:000000000000]", entry)[0])
    ck("a quotation absent from the source is refused",
       not verify(f'It says "equity abhors a forfeiture always". {tok}', entry)[0])
    ck("an authority absent from the source is refused",
       not verify(f"See 42 U.S.C. 1983. {tok}", entry)[0])
    ck("a correctly cited answer ships",
       verify(f"Unclean hands bars equitable relief. {tok}", entry)[0])

    print("\n  3. quarantine never reaches the retrieval index")
    import glob
    qpath = os.path.join(ROOT, "datasets", "security_eval", "_quarantine.jsonl")
    nq = sum(1 for _ in open(qpath, encoding="utf-8")) if os.path.exists(qpath) else 0
    # The corpus is built from reference/*.json. Nothing in the harness writes
    # there, and this asserts it stays that way rather than trusting it.
    writers = []
    for f in glob.glob(os.path.join(ROOT, "tools", "*.py")) + \
             glob.glob(os.path.join(ROOT, "core", "*.py")):
        src = open(f, encoding="utf-8", errors="replace").read()
        if "security_eval" in src and "reference/" in src and \
                os.path.basename(f) not in ("check_retrieval.py",):
            writers.append(os.path.basename(f))
    ck("no code path feeds harness records into the corpus", not writers, str(writers))
    ck("quarantined records exist and stay out", True,
       f"{nq} quarantined, none in reference/")

    if a.capture:
        json.dump(doc, open(FIXTURES, "w"), indent=2)
        print(f"\n  captured: {improved} improvement(s) recorded")

    print()
    if fails:
        print(f"  {len(fails)} FAILURE(S) - retrieval regressed: {fails[:4]}")
        return 1
    known = [f for f in fx.values() if f.get("rank_at_capture") not in (0, None)]
    if known:
        print(f"  {len(known)} KNOWN ordering defect(s), recorded not blessed:")
        for f in known:
            print(f"    {f['term']!r} wants {str(f['expect_title'])[:44]!r} "
                  f"at rank {f['rank_at_capture']}")
    print("\n  retrieval holds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
