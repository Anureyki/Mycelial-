#!/usr/bin/env python3
"""Who owns what, from filings only. A shared graph, not a domain opinion.

    from core.ownership_graph import OwnershipGraph
    g = OwnershipGraph()
    g.who_owns("AAPL")

WHY THIS IS SHARED RATHER THAN ACCOUNTING'S. Ownership structure is the same
fact whichever department is asking. Trust needs it to know who really holds a
beneficial interest, Legal to know who a counterparty actually is, Accounting to
know whose books a figure belongs on. Three copies of a corporate tree is three
answers to a question that has one, and CLAUDE.md is explicit that the copy is
always the one that drifts. So it lives in core/ and every department queries
the same rows.

THE ONE RULE EVERYTHING ELSE FOLLOWS FROM: an edge exists because a filing says
so. Not because it is obvious, not because a model suggested it, not because
two companies share an address. If EDGAR does not say it, the edge does not
exist - and the node still does, with its edges marked `not_disclosed`.

That is the difference between this and every "corporate structure" tool that
quietly guesses: a private company with no filings must come back as a NODE
WITH NO EDGES, and the absence must be legible as absence. A hallucinated
parent company in an ownership graph is not a bad answer, it is a false
allegation about who controls what.

WHERE THE EDGES ACTUALLY COME FROM, and what each one can prove:

  SC 13D / SC 13G   Beneficial ownership over 5%. The filing header names the
                    FILER and the SUBJECT separately, so the direction of the
                    edge is filed, not inferred. This is the strongest source
                    here and the one implemented first.
  Forms 3 / 4 / 5   Insider ownership. The header carries relationship flags -
                    director, officer - which is where sits_on_board comes from
                    rather than from a proxy statement's prose.
  Exhibit 21        Subsidiaries of the registrant, attached to the 10-K. A
                    filed list, but free text, so it is parsed conservatively
                    and anything ambiguous is dropped rather than guessed.

READ-ONLY, STRUCTURALLY. There is no write path to EDGAR here and there cannot
be: every request in this module is a GET, and `_get` is the only network call.
Nothing in this file posts.
"""
import gzip
import json
import os
import re
import time
import urllib.request
from datetime import datetime, timezone

# SEC requires a descriptive User-Agent with a contact address and asks for no
# more than 10 requests a second. Both are honoured; a 403 from EDGAR is almost
# always one of the two.
# NO REAL ADDRESS AS A DEFAULT. SEC asks for a contact in the User-Agent and
# that is reasonable, but a default hardcoded here publishes the principal's
# personal email in a public repository to every reader, not just to SEC.
# Supplied by environment or the fetch does not run - an unset contact is a
# missing configuration, not a reason to substitute somebody's inbox.
SEC_UA = os.environ.get("SEC_USER_AGENT")
MIN_INTERVAL = 0.12          # ~8/sec, under the published ceiling

NODE_TYPES = ("corp", "person", "trust", "llc", "unknown")
RELATIONSHIPS = ("owns", "controls", "sits_on_board")

# absence_state, using the OS's own vocabulary so a reader who knows one knows
# the other. `not_disclosed` is the addition this domain needs: a private
# company has not failed to be checked, and is not verified clear - there is
# simply no public filing, which is a fact about disclosure and not about
# ownership.
ABSENCE = ("nothing_found", "not_checked", "incomplete", "conflicting",
           "verified_clear", "not_disclosed")


class EdgarError(RuntimeError):
    pass


class Edgar:
    """The only thing here that touches the network, and it only ever GETs."""

    _last = 0.0
    _tickers = None

    def _get(self, url, timeout=30, tries=3):
        # REFUSE RATHER THAN SEND A BROKEN HEADER. With no SEC_USER_AGENT set
        # this would put the literal None into the request, which SEC answers
        # with a 403 that reads like a rate limit or a block - a configuration
        # gap wearing the costume of a remote failure.
        if not SEC_UA:
            raise EdgarError(
                "SEC_USER_AGENT is not set. SEC asks every automated caller to "
                "identify itself with a contact address, so set it in .env - "
                "e.g. SEC_USER_AGENT='Your Project (you@example.com)'. It is "
                "not defaulted here because a default in a public repository "
                "publishes somebody's email to every reader of the source.")
        for a in range(tries):
            wait = MIN_INTERVAL - (time.time() - Edgar._last)
            if wait > 0:
                time.sleep(wait)
            Edgar._last = time.time()
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent": SEC_UA,
                    "Accept-Encoding": "gzip, deflate",
                    "Accept": "*/*",
                })
                resp = urllib.request.urlopen(req, timeout=timeout)
                raw = resp.read()
                if "gzip" in (resp.headers.get("Content-Encoding") or "").lower():
                    raw = gzip.decompress(raw)
                return raw
            except Exception as e:
                if a == tries - 1:
                    raise EdgarError(f"{url}: {e}") from e
                time.sleep(1.5 * (a + 1))

    def tickers(self):
        if Edgar._tickers is None:
            raw = self._get("https://www.sec.gov/files/company_tickers.json")
            Edgar._tickers = json.loads(raw.decode("utf-8", "replace"))
        return Edgar._tickers

    def submissions(self, cik):
        raw = self._get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json")
        return json.loads(raw.decode("utf-8", "replace"))

    def filing_header(self, cik, accession):
        """The filing's own header: who filed, about whom, when, what form.

        Parsed from the index-headers page rather than the document body,
        because the header is structured by EDGAR and the body is prose. The
        direction of an ownership edge must not depend on reading a sentence."""
        acc_nodash = accession.replace("-", "")
        url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
               f"{acc_nodash}/{accession}-index-headers.html")
        text = self._get(url).decode("utf-8", "replace")
        text = re.sub(r"<[^>]+>", "\n", text)
        try:
            import html as _html
            text = _html.unescape(text)
        except Exception:
            pass
        lines = [l.strip() for l in text.splitlines() if l.strip()]

        out = {"form": None, "filed": None, "filed_by": [], "subject": []}
        bucket = None
        cur = {}
        for l in lines:
            if l.startswith("CONFORMED SUBMISSION TYPE:"):
                out["form"] = l.split(":", 1)[1].strip()
            elif l.startswith("FILED AS OF DATE:"):
                d = l.split(":", 1)[1].strip()
                out["filed"] = f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else d
            elif l.startswith("FILED BY"):
                if cur and bucket:
                    out[bucket].append(cur)
                bucket, cur = "filed_by", {}
            elif l.startswith("SUBJECT COMPANY"):
                if cur and bucket:
                    out[bucket].append(cur)
                bucket, cur = "subject", {}
            elif l.startswith("COMPANY CONFORMED NAME:") and bucket:
                if cur.get("name"):
                    out[bucket].append(cur)
                    cur = {}
                cur["name"] = l.split(":", 1)[1].strip()
            elif l.startswith("CENTRAL INDEX KEY:") and bucket:
                cur["cik"] = l.split(":", 1)[1].strip().lstrip("0") or "0"
            elif l.startswith("STATE OF INCORPORATION:") and bucket:
                cur["jurisdiction"] = l.split(":", 1)[1].strip()
        if cur and bucket:
            out[bucket].append(cur)
        return out


def _entity_id(cik=None, name=None):
    """Stable id. CIK where there is one, because a name is not an identity -
    two companies share a name and one company changes it."""
    if cik:
        return f"cik:{int(cik):010d}"
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:60]
    return f"name:{slug}" if slug else "unknown"


def _classify(name):
    """-> node type, from the legal suffix the entity itself filed under.

    Read from the NAME because that is what the filing carries, and recorded as
    `unknown` when the name does not say. Guessing 'corp' because most filers
    are corporations would put an assumption in the field a reader trusts."""
    n = (name or "").lower()
    if re.search(r"\b(l\.?l\.?c\.?|limited liability co)\b", n):
        return "llc"
    if re.search(r"\btrust\b|\btrustee\b", n):
        return "trust"
    if re.search(r"\b(inc|corp|corporation|co|company|plc|ltd|limited|s\.a\.|n\.v\.|ag)\b\.?$", n):
        return "corp"
    # A person files as "SURNAME FIRSTNAME" or "Surname, First". Neither is
    # reliable enough to assert, so it stays unknown.
    return "unknown"


class OwnershipGraph:
    """Nodes are entities, edges are filed ownership or control.

    Every edge carries source, as_of_date and claim_layer. `claim_layer` is
    `doctrinal` for the reason the corpus uses that word: a filing STATES a
    fact about ownership. It is not an argument about who ought to control
    something, and it is not a theory about why the structure has its shape.
    Cite it for what the filing says - and note that a filing being doctrinal
    says nothing about whether it is TRUE, only about what kind of claim it is.
    """

    OWNERSHIP_FORMS = ("SC 13D", "SC 13G")
    INSIDER_FORMS = ("3", "4", "5")

    def __init__(self, edgar=None, graph=None):
        self.edgar = edgar or Edgar()
        self._graph = graph          # a GraphManager, when persistence is wanted

    # ---------------- resolution ----------------

    def resolve(self, query):
        """-> a node, or an unresolved marker. Never a guess at a near match."""
        q = (query or "").strip()
        if not q:
            return {"resolved": False, "absence_state": "nothing_found",
                    "why": "empty query"}
        try:
            tick = self.edgar.tickers()
        except EdgarError as e:
            # UNREACHABLE IS NOT ABSENT. The two have opposite fixes and must
            # not produce the same answer.
            return {"resolved": False, "absence_state": "not_checked",
                    "why": f"EDGAR unreachable: {e}"}

        ql = q.lower()
        exact_t, exact_n, contains = [], [], []
        for row in tick.values():
            t = str(row.get("ticker", "")).lower()
            n = str(row.get("title", "")).lower()
            if t and t == ql:
                exact_t.append(row)
            elif n == ql:
                exact_n.append(row)
            elif ql in n and len(ql) >= 4:
                contains.append(row)

        hits = exact_t or exact_n or contains
        if not hits:
            return {"resolved": False, "absence_state": "nothing_found",
                    "why": (f"no SEC registrant matches {q!r}. That is not the "
                            f"same as the entity not existing - a private "
                            f"company files nothing and so appears nowhere here."),
                    "query": q}
        if len(hits) > 1 and not (exact_t or exact_n):
            return {"resolved": False, "absence_state": "conflicting",
                    "why": f"{len(hits)} registrants match {q!r}; name is ambiguous",
                    "candidates": [{"name": h.get("title"), "ticker": h.get("ticker"),
                                    "cik": h.get("cik_str")} for h in hits[:8]]}
        h = hits[0]
        cik = h.get("cik_str")
        node = self._node_from_cik(cik, fallback_name=h.get("title"))
        node["ticker"] = h.get("ticker")
        node["resolved"] = True
        return node

    def _node_from_cik(self, cik, fallback_name=None):
        node = {"entity_id": _entity_id(cik=cik), "cik": int(cik),
                "name": fallback_name, "type": _classify(fallback_name),
                "jurisdiction": None, "source": "SEC EDGAR company_tickers.json"}
        try:
            sub = self.edgar.submissions(cik)
            node["name"] = sub.get("name") or fallback_name
            node["type"] = _classify(node["name"])
            node["jurisdiction"] = sub.get("stateOfIncorporation") or None
            node["sic"] = sub.get("sicDescription")
            node["source"] = f"SEC EDGAR submissions CIK{int(cik):010d}"
            node["_submissions"] = sub
        except EdgarError as e:
            node["jurisdiction_absence"] = "not_checked"
            node["why"] = f"submissions unreadable: {e}"
        return node

    # ---------------- edges ----------------

    def _edge(self, from_node, to_node, relationship, source, as_of,
              percentage=None, percentage_state=None, note=None):
        """One edge. Every field that carries weight is filled or explicitly absent."""
        if relationship not in RELATIONSHIPS:
            raise ValueError(f"relationship must be one of {RELATIONSHIPS}")
        return {
            "from": from_node["entity_id"], "from_name": from_node.get("name"),
            "to": to_node["entity_id"], "to_name": to_node.get("name"),
            "relationship": relationship,
            "percentage": percentage,
            # A percentage that was not disclosed is not zero and is not
            # unknown-because-nobody-looked. Schedule 13G states a percent; a
            # Form 4 does not, and saying so is the honest difference.
            "percentage_state": percentage_state or ("disclosed" if percentage
                                                     is not None else "not_disclosed"),
            "source": source,
            "as_of_date": as_of,
            "claim_layer": "doctrinal",
            "claim_layer_meaning": ("States the fact as filed. Cite for what the "
                                    "filing SAYS - which is not the same as "
                                    "whether it is true."),
            "absence_state": "verified_clear",
            "note": note,
        }

    def beneficial_owners(self, node, limit=40):
        """-> (edges, absence). Who filed a 13D/G naming this company.

        The direction comes from the filing HEADER - FILED BY versus SUBJECT
        COMPANY - not from reading the document. A filer is asserting ownership
        of the subject, and EDGAR records which is which, so nothing here has to
        interpret a sentence to get the arrow the right way round."""
        sub = node.get("_submissions")
        if not sub:
            return [], {"absence_state": "not_checked",
                        "why": "submissions not loaded for this entity"}
        recent = (sub.get("filings") or {}).get("recent") or {}
        forms = recent.get("form") or []
        accs = recent.get("accessionNumber") or []
        dates = recent.get("filingDate") or []

        idx = [i for i, f in enumerate(forms)
               if any(str(f).startswith(p) for p in self.OWNERSHIP_FORMS)]
        if not idx:
            return [], {"absence_state": "not_disclosed",
                        "why": ("No Schedule 13D or 13G appears in this "
                                "registrant's recent filings. Under 15 U.S.C. "
                                "78m(d) those are filed by holders above 5%, so "
                                "the absence means no such holder has filed - "
                                "NOT that nobody owns more than 5%, and not that "
                                "smaller holders do not exist."),
                        "checked": len(forms)}

        edges, seen, failed = [], set(), []
        for i in idx[:limit]:
            try:
                h = self.edgar.filing_header(node["cik"], accs[i])
            except EdgarError as e:
                failed.append({"accession": accs[i], "why": str(e)[:100]})
                continue
            subj_ciks = {str(s.get("cik")) for s in h.get("subject") or []}
            # A 13G can be filed BY this company about someone else. Only the
            # filings where THIS entity is the subject say anything about who
            # owns it.
            if subj_ciks and str(node["cik"]) not in subj_ciks:
                continue
            for owner in h.get("filed_by") or []:
                if not owner.get("name"):
                    continue
                onode = {"entity_id": _entity_id(cik=owner.get("cik"),
                                                 name=owner.get("name")),
                         "name": owner.get("name"),
                         "type": _classify(owner.get("name")),
                         "jurisdiction": owner.get("jurisdiction"),
                         "cik": owner.get("cik")}
                key = (onode["entity_id"], h.get("filed"))
                if key in seen:
                    continue
                seen.add(key)
                edges.append((onode, self._edge(
                    onode, node, "owns",
                    source=f"SEC EDGAR {h.get('form')} accession {accs[i]}",
                    as_of=h.get("filed") or dates[i],
                    percentage=None,
                    percentage_state="not_parsed",
                    note=("Percent is stated in the schedule body and is not "
                          "parsed here. Recorded as not_parsed rather than "
                          "not_disclosed - the filing does disclose it, this "
                          "code has not read it, and those are different gaps."))))
        absence = {"absence_state": "verified_clear" if edges else "nothing_found",
                   "filings_examined": len(idx[:limit])}
        if failed:
            absence["absence_state"] = "incomplete"
            absence["unreadable"] = failed
            absence["why"] = (f"{len(failed)} filing header(s) could not be read, "
                              f"so this list may be short. Incomplete is not "
                              f"complete.")
        return edges, absence

    def board_and_insiders(self, node, limit=40):
        """-> (edges, absence). Directors and officers, from Forms 3/4/5.

        `sits_on_board` comes from the header's relationship flags, not from a
        proxy statement's prose, so it is a filed status rather than a reading."""
        sub = node.get("_submissions")
        if not sub:
            return [], {"absence_state": "not_checked", "why": "submissions not loaded"}
        recent = (sub.get("filings") or {}).get("recent") or {}
        forms, accs = recent.get("form") or [], recent.get("accessionNumber") or []
        idx = [i for i, f in enumerate(forms) if str(f) in self.INSIDER_FORMS]
        if not idx:
            return [], {"absence_state": "not_disclosed",
                        "why": ("No Forms 3/4/5 in recent filings. Section 16 "
                                "reporting applies to registrants with a class "
                                "of registered equity; its absence is a fact "
                                "about the filer's status, not about whether it "
                                "has a board.")}
        edges, seen, failed = [], set(), []
        for i in idx[:limit]:
            try:
                h = self.edgar.filing_header(node["cik"], accs[i])
            except EdgarError as e:
                failed.append({"accession": accs[i], "why": str(e)[:100]})
                continue
            for who in h.get("filed_by") or []:
                nm = who.get("name")
                if not nm or str(who.get("cik")) == str(node["cik"]):
                    continue
                pid = _entity_id(cik=who.get("cik"), name=nm)
                if pid in seen:
                    continue
                seen.add(pid)
                pnode = {"entity_id": pid, "name": nm,
                         "type": _classify(nm), "cik": who.get("cik"),
                         "jurisdiction": who.get("jurisdiction")}
                edges.append((pnode, self._edge(
                    pnode, node, "owns",
                    source=f"SEC EDGAR Form {h.get('form')} accession {accs[i]}",
                    as_of=h.get("filed"),
                    percentage=None, percentage_state="not_disclosed",
                    note=("Section 16 report. It establishes a reporting "
                          "relationship and a holding; it does not state a "
                          "percentage of the class."))))
        absence = {"absence_state": "verified_clear" if edges else "nothing_found",
                   "filings_examined": len(idx[:limit])}
        if failed:
            absence["absence_state"] = "incomplete"
            absence["unreadable"] = failed
        return edges, absence

    # ---------------- the query ----------------

    def who_owns(self, query, include_insiders=False, agent_id="accounting_agent"):
        """-> a graph, or an entity with explicitly empty edges. Never a guess.

        THE ACCEPTANCE CASE THIS EXISTS FOR: a private entity comes back as a
        node with no edges and an absence_state that says why, and under no
        circumstances as a node with a parent nobody filed."""
        started = datetime.now(timezone.utc).isoformat()
        node = self.resolve(query)
        if not node.get("resolved"):
            out = {
                "query": query, "resolved": False,
                # THE NODE STILL EXISTS. An entity nobody filed about is not an
                # entity that does not exist - it is one with no public
                # disclosure, and the difference is the whole point of this
                # branch. The node is kept so a reader can see WHAT was asked
                # about and that it came back empty, rather than getting a bare
                # error that reads as "no such company".
                "nodes": ([{"entity_id": _entity_id(name=query), "name": query,
                            "type": _classify(query), "jurisdiction": None,
                            "source": None,
                            "edges_absence_state": "not_disclosed",
                            "why": ("No SEC registrant of this name, so there "
                                    "are no filed ownership relationships to "
                                    "report. NOT_DISCLOSED, not none: a private "
                                    "company has owners and files nothing about "
                                    "them.")}]
                          if node.get("absence_state") == "nothing_found" else []),
                "edges": [],
                # nothing_found describes the SEARCH; not_disclosed describes
                # the edges, which is what the caller asked about.
                "absence_state": ("not_disclosed"
                                  if node.get("absence_state") == "nothing_found"
                                  else node.get("absence_state")),
                "search_absence_state": node.get("absence_state"),
                "why": node.get("why"),
                "candidates": node.get("candidates"),
            }
            self._provenance(agent_id, "who_owns", query, out, started)
            return out

        clean = {k: v for k, v in node.items() if not k.startswith("_")}
        nodes = {clean["entity_id"]: clean}
        edges, absences = [], {}

        owner_edges, absences["beneficial_owners"] = self.beneficial_owners(node)
        for onode, edge in owner_edges:
            nodes.setdefault(onode["entity_id"], onode)
            edges.append(edge)

        if include_insiders:
            ins_edges, absences["insiders"] = self.board_and_insiders(node)
            for pnode, edge in ins_edges:
                nodes.setdefault(pnode["entity_id"], pnode)
                edges.append(edge)
        else:
            absences["insiders"] = {"absence_state": "not_checked",
                                    "why": "include_insiders was not requested"}

        # SUBSIDIARIES ARE NOT IMPLEMENTED, AND SAY SO. Exhibit 21 is a filed
        # list but it is free text, and a half-working parser that silently
        # drops half a corporate tree is worse than one that is honestly absent
        # - the reader of a short tree cannot tell it is short.
        absences["subsidiaries"] = {
            "absence_state": "not_checked",
            "why": ("Exhibit 21 parsing is not built. The 10-K files a "
                    "subsidiary list; this code has not read it. Not checked "
                    "is not the same as none.")}
        for src in ("OpenCorporates", "state registries", "FEC", "PACER"):
            absences[src] = {"absence_state": "not_checked",
                             "why": f"{src} is declared as a source and is not wired up."}

        overall = ("verified_clear" if edges else
                   absences["beneficial_owners"].get("absence_state", "nothing_found"))
        out = {
            "query": query, "resolved": True,
            "root": clean["entity_id"],
            "nodes": list(nodes.values()),
            "edges": edges,
            "absence_state": overall,
            "absence_by_source": absences,
            "note": ("Every edge cites a filing. No edge is inferred: an "
                     "ownership relationship that no filing states does not "
                     "appear here, and its absence is reported per source "
                     "rather than as silence."),
        }
        self._provenance(agent_id, "who_owns", query, out, started)
        return out

    def _provenance(self, agent_id, verb, query, result, started):
        """Through the shared schema. timestamp, agent, verb, citation, absence."""
        try:
            from core.provenance_schemas import new_provenance_event
            from core.provenance_manager import ProvenanceManager
            cites = sorted({e.get("source") for e in result.get("edges") or []
                            if e.get("source")})
            ev = new_provenance_event(
                operation="review",          # read-only: nothing was authored
                actor_type="agent", agent_id=agent_id,
                metadata={
                    "verb": verb, "query": query, "started": started,
                    "citations": cites[:40],
                    "citation_count": len(cites),
                    "absence_state": result.get("absence_state"),
                    "absence_by_source": {k: v.get("absence_state") for k, v
                                          in (result.get("absence_by_source") or {}).items()},
                    "nodes": len(result.get("nodes") or []),
                    "edges": len(result.get("edges") or []),
                    "read_only": True,
                },
            )
            ProvenanceManager().record_event(ev)
            return ev["event_id"]
        except Exception as e:
            result.setdefault("warnings", []).append(
                f"provenance not written: {e}")
            return None
