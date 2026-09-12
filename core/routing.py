"""Which department owns a request. Lifted out of the Boss Agent unchanged.

WHY IT MOVED. Anansi now routes user requests straight to the domain that owns
them, and Boss governs rather than forwards. That made this logic Anansi's, and
there were only three ways to get it there:

  copy it into Anansi   - two copies of a matcher whose every branch encodes a
                          real routing failure, and the copy is always the one
                          that drifts
  import it from Boss   - Anansi would then depend on the agent it no longer
                          talks to
  lift it to core/      - this

Nothing in it was ever Boss-specific. It reads the registry, asks each agent
what words it claims, and matches. The comments below are the record of what
happened when it got this wrong, and they are kept verbatim because each one
is a bug that was paid for once already:

  "repo" beating "credit report"      -> score by specificity, not hit count
  anchoring both ends of a term       -> killed every deliberate stem
  a 1.5B model outvoting a declaration-> silence outranks a guess
  caching an incomplete sweep         -> 30s TTL while any agent is silent

THE ONE RULE THAT MATTERS MOST is that this holds no domain vocabulary of its
own. It asks. An agent that declares nothing never matches, and a new domain
agent becomes routable by starting up - not by editing this file. That is what
stopped the orchestrator accumulating the vocabulary of every department under
it, and moving the code must not quietly undo it.
"""
import re
import time

import requests


class DomainRouter:
    """Routes by asking the agents themselves. Holds no domain words.

    `log` is the caller's logger and `agent_id` is who is asking - both are
    passed in rather than assumed, because this now serves Anansi and could
    serve anything else that needs to know which department owns a sentence."""

    def __init__(self, agent_id, log=None, registry="http://localhost:8004"):
        self.agent_id = agent_id
        self.log = log or (lambda *_a, **_k: None)
        self.registry = registry
        self._domain_cache = {"map": None, "at": 0, "ttl": 300, "owns": {}}

    def _domain_vocabulary(self):
        """Ask every registered agent what vocabulary claims a request for it.

        Boss holds no domain words of its own. It asks. An agent that declares
        nothing simply never matches, and a new domain agent becomes routable
        by starting up - not by editing this file, which is what made the
        orchestrator accumulate the vocabulary of every domain beneath it."""
        c = self._domain_cache
        if c["map"] is not None and time.time() - c["at"] < c.get("ttl", 300):
            return c["map"]
        vocab = {}
        try:
            resp = requests.post(f"{self.registry}/execute",
                                 json={"task": "list_agents", "args": [],
                                       "sender": self.agent_id}, timeout=5)
            agents = resp.json().get("result", []) if resp.status_code == 200 else []
        except Exception as e:
            self.log(f"routing: registry lookup failed: {e}")
            agents = []
        silent, declined = [], []
        owns = {}
        for a in agents:
            aid, url = a.get("agent_id"), a.get("url")
            if not aid or not url or aid == self.agent_id:
                continue
            try:
                r = requests.post(f"{url}/execute",
                                  json={"task": "routing_terms", "args": {},
                                        "sender": self.agent_id}, timeout=4)
                body = r.json() if r.status_code == 200 else {}
                while isinstance(body, dict) and "terms" not in body and "result" in body:
                    body = body["result"]
                terms = (body or {}).get("terms") or []
                if terms:
                    vocab[aid] = [t for t in terms if isinstance(t, str) and t]
                owned = (body or {}).get("owns") or []
                if isinstance(owned, list) and owned:
                    owns[aid] = [t for t in owned if isinstance(t, str) and t]
                elif isinstance(body, dict) and "terms" in body:
                    declined.append(aid)     # answered, and claims nothing
                else:
                    silent.append(aid)       # no usable answer
            except Exception:
                silent.append(aid)           # down, or still starting

        # An agent that was registered but did not answer is usually still
        # booting - start_all.sh brings Boss up before most of them. Caching
        # that gap for the full five minutes left the router blind to every
        # domain except the one that happened to be ready, so retry soon
        # instead of freezing an incomplete map.
        #
        # Declaring nothing is not the same as failing to answer. Anansi
        # narrates and Hermes brokers; neither owns a domain, so both answer
        # with an empty list on purpose and must not keep the router retrying.
        c["map"], c["at"] = vocab, time.time()
        c["owns"] = owns
        c["ttl"] = 30 if silent else 300
        self.log("routing vocabulary: " +
                 ", ".join(f"{k}={len(v)}" for k, v in sorted(vocab.items())) +
                 (f" | claims nothing: {', '.join(sorted(declined))}" if declined else "") +
                 (f" | silent: {', '.join(sorted(silent))} (retry in 30s)" if silent else "") +
                 (f" | owns: " + ", ".join(f"{k}={len(v)}" for k, v in sorted(owns.items()))
                  if owns else ""))
        return vocab


    def _owned_terms(self):
        """The definitive claims, per agent. Populated by the same sweep that
        builds the vocabulary, so it is never staler than the map it came
        with."""
        self._domain_vocabulary()
        return (self._domain_cache or {}).get("owns", {})


    def _roster(self):
        """Departments as they describe THEMSELVES - declared terms plus the
        capability names they registered. Nothing about any domain is written
        down here; a new agent becomes routable by starting up."""
        out = {}
        caps = {}
        try:
            r = requests.post(f"{self.registry}/execute",
                              json={"task": "list_agents", "args": [],
                                    "sender": self.agent_id}, timeout=5)
            for a in (r.json().get("result", []) if r.ok else []):
                if a.get("agent_id"):
                    caps[a["agent_id"]] = a.get("capabilities") or []
        except Exception as e:
            self.log(f"routing: registry unreachable for roster: {e}")
        for aid, terms in self._domain_vocabulary().items():
            if terms:
                out[aid] = {"terms": terms, "capabilities": caps.get(aid, [])}
        return out


    def _domain_by_terms(self, prompt, with_margin=False):
        """Which department's declared vocabulary the words point at, and by
        how much.

        The MARGIN is what makes this usable as evidence rather than a tiebreak.
        One agent matching three of its own declared terms while the next
        matches one generic word is a decisive signal; two agents matching once
        each is not, and the difference has to be visible to the caller."""
        lp = (prompt or "").lower()
        scores = {}
        for aid, terms in self._domain_vocabulary().items():
            n = 0
            for t in terms:
                try:
                    # SCORE BY SPECIFICITY, NOT BY COUNT.
                    #
                    # coding_agent's "repo" matched "my credit REPOrt shows a
                    # late payment" and beat Accounting, which had declared
                    # "credit report" outright - one hit each, and the tie went
                    # the wrong way.
                    #
                    # Anchoring both ends was the obvious fix and it was wrong:
                    # many terms are deliberate STEMS - `indemnif`, `enforceab`,
                    # `reconcil`, `delinquen` - and a trailing \b kills every one
                    # of them. "What is laches" went to the Security Agent
                    # inside a minute of trying it.
                    #
                    # So keep the prefix match and weigh each hit by how much of
                    # the sentence it actually accounts for. "credit report" is
                    # 13 characters of evidence; "repo" is 4. A longer term is a
                    # more specific claim, which is the thing being measured.
                    m = re.search(t if ("\\b" in t or "?" in t or "*" in t)
                                  else r"\b" + t, lp)
                    if m:
                        # A WILDCARD MAY NOT SCORE BY WHAT IT SWALLOWED.
                        #
                        # Length is a proxy for specificity and that holds for
                        # a literal term - "credit report" really is a more
                        # specific claim than "repo". It inverts for a pattern
                        # containing .* : the Security Agent's `is .* allowed`
                        # matched 22 characters of "is the trustee allowed to
                        # sell the property", and the sentence's own length was
                        # then read as evidence that Security was the subject.
                        #
                        # So a wildcard scores the LITERAL text it required,
                        # not the text it happened to absorb. `is .* allowed`
                        # is worth len("is allowed"), which is what it actually
                        # asserts.
                        if ".*" in t or ".+" in t:
                            n += len(re.sub(r"\.[*+]\??", "", t))
                        else:
                            n += len(m.group(0))
                except re.error:
                    continue
            if n:
                scores[aid] = n
        if not scores:
            return (None, 0, {}) if with_margin else None
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        best, best_n = ranked[0]
        runner = ranked[1][1] if len(ranked) > 1 else 0
        return (best, best_n - runner, scores) if with_margin else best
    def _resolve_intent(self, prompt):
        try:
            from core.intent import IntentResolver
        except Exception:
            return None
        if not hasattr(self, "_intent"):
            self._intent = IntentResolver(log=self.log, roster_fn=self._roster)
        try:
            pick, why = self._intent.resolve(prompt)
            if pick == "UNCLEAR":
                self.log(f"routing: intent unresolved ({why})")
            return pick
        except Exception as e:
            self.log(f"routing: intent resolver failed, using terms: {e}")
            return None


    def _domain_for(self, prompt):
        """Which department owns this request.

        Intent is RESOLVED first and word-matched only as a fallback. Counting
        regex hits is why "How's the system today" reached a code model and why
        "my water is two inches below the net pot" matched nothing about the
        grow: the terms were written to be matched, not to describe a
        department. A person should be able to speak plainly.

        The resolver picks from the live registry and can return nothing but an
        id that exists, so a wrong answer is a wrong ROUTE - recoverable,
        because the department says it does not own the question - never a
        wrong fact. See core/intent.py."""
        # AN OWNED TERM ENDS THE DECISION.
        #
        # Boss holds no domain knowledge and cannot judge whether a request is
        # really Grow's - but Grow can, and a plant it is actually tracking is
        # not a matter of opinion. So a department is allowed to say "this one
        # is definitively mine", and that is not a vote to be weighed against a
        # model's guess or another agent's keyword. It stops the routing.
        #
        # Two agents both claiming ownership is a real conflict and is logged
        # rather than silently resolved - the same reason the claim pipeline has
        # `contested` instead of quietly lowering a confidence number. It falls
        # through to the ordinary path so the request still gets answered, but
        # the collision is on the record and someone can go fix the vocabulary.
        lp_own = (prompt or "").lower()
        claimed = []
        for aid, terms in (self._owned_terms() or {}).items():
            for t in terms:
                try:
                    if re.search(t if ("\\b" in t or "?" in t or "*" in t) else r"\b" + t,
                                 lp_own):
                        claimed.append(aid)
                        break
                except re.error:
                    continue
        if len(set(claimed)) == 1:
            owner = claimed[0]
            self.log(f"routing: {owner} OWNS a term in this request - decision ends there")
            return owner
        if len(set(claimed)) > 1:
            self.log(f"routing: OWNERSHIP CONFLICT - {sorted(set(claimed))} all claim to own "
                     f"a term in this request. Falling through to the ordinary path; the "
                     f"vocabularies need fixing.")

        pick = self._resolve_intent(prompt)
        keyword, margin, scores = self._domain_by_terms(prompt, with_margin=True)

        # A DECLARED TERM IS EVIDENCE; A MODEL'S PICK IS A GUESS.
        #
        # Intent resolution used to win every disagreement, and on a 1.5B model
        # it loses badly: "when will gsc 2 flower" went to the SECURITY agent
        # and "when will the aloe flower" to PQA, while Grow was sitting there
        # having declared `flower`, `gsc`, `gsc\s*#?\s*2` and `aloe` as its own
        # routing terms. Neither of the agents chosen claimed anything at all.
        #
        # An agent declaring a term is a verifiable statement by the department
        # that practises the domain. A small model's answer is not checkable
        # against anything. So where the words point DECISIVELY at one
        # department - it claims, and by a clear margin over the next - that
        # wins, and the model is used for what it is actually good at: the
        # cases where nobody's vocabulary matches, or two match equally.
        #
        # This is the same rule as the port outranking the registry row.
        # A margin threshold was the wrong test - "when will the aloe flower"
        # gave Grow 2 hits against Trust's 1, a margin of 1, and PQA still won
        # on the model's say-so. The sharper question is not how much the winner
        # led by; it is whether the agent the MODEL chose claimed anything at
        # all. Security and PQA had matched zero terms in requests they were
        # handed.
        #
        # An agent that has not declared one word of the vocabulary in front of
        # it has said, in the only way this architecture lets it, that the
        # request is not its own. That silence outranks a guess.
        if pick and pick != "UNCLEAR" and pick != keyword and keyword \
                and not scores.get(pick):
            self.log(f"routing: intent={pick} claimed nothing; keywords={keyword} "
                     f"matched {scores.get(keyword)} of its own declared terms - "
                     f"took keywords")
            return keyword
        if keyword and margin >= 6 and pick and pick != "UNCLEAR" and pick != keyword:
            self.log(f"routing: intent={pick} keywords={keyword} (margin {margin}) - "
                     f"took keywords; a declared term outranks a model guess")
            return keyword
        if pick and pick != "UNCLEAR":
            if keyword and keyword != pick:
                self.log(f"routing: intent={pick} keywords={keyword} - took intent")
            return pick
        return keyword

    # ------------------------------------------------------------------
    # Public surface. Everything above is the matcher; this is what callers use.
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # ROLES. Declared in config, owned exclusively, and worth more than any
    # amount of keyword overlap - because a role is a subject the department
    # PRACTISES, and a keyword is a word that turned up in a sentence.
    # ------------------------------------------------------------------

    def roles(self):
        from core.roles import load_roles
        c = self._domain_cache
        if c.get("roles") is None or time.time() - c.get("roles_at", 0) > 300:
            r, conflicts = load_roles()
            c["roles"], c["roles_at"], c["role_conflicts"] = r, time.time(), conflicts
            if conflicts:
                # Loud, and at load. A routing conflict found while answering a
                # question has already produced a wrong answer.
                for x in conflicts:
                    self.log(f"ROLE CONFLICT: {x['role']!r} claimed by "
                             f"{x['claimed_by']} - owned by NEITHER until resolved")
        return c["roles"]

    def role_conflicts(self):
        self.roles()
        return (self._domain_cache or {}).get("role_conflicts") or []

    def domain_for(self, prompt):
        """-> the one agent id that owns this request, or None.

        A NAMED ROLE ENDS THE DECISION, the same way an owned term does. The
        difference is that a role is exclusive by construction and declared in
        config, so it cannot be quietly out-scored by a longer keyword match in
        another department."""
        from core.roles import score_roles, matched_roles
        rs = score_roles(prompt, self.roles())
        if rs:
            best = max(rs, key=rs.get)
            tied = [a for a, n in rs.items() if n == rs[best]]
            if len(tied) == 1:
                self.log(f"routing: {best} owns role(s) "
                         f"{matched_roles(prompt, self.roles()).get(best)} - "
                         f"decision ends there")
                return best
            # A TIE NARROWS THE FIELD; IT DOES NOT OPEN IT.
            #
            # Falling straight through to the keyword matcher here sent "is the
            # trustee's network access a security risk" to ACCOUNTING - an
            # agent that owns no role in the sentence at all - because the
            # fallback searched every department instead of the two that
            # actually claimed something. Two departments owning a role each is
            # a mixed question, and the answer is one of those two.
            #
            # So keywords break the tie AMONG THE TIED OWNERS - and where they
            # CANNOT, there is no winner and this must not invent one.
            #
            # IT USED TO INVENT ONE, AND THE INVENTION MOVED WITH THE WEATHER.
            # `max()` over equal values returns whichever key came first, so
            # "is the trustee's network access a security risk" - trustee to
            # Trust, network access to Security, 100 each, keywords 0 each -
            # routed to whichever agent the roster happened to list first. With
            # the swarm up, the agents' own live routing_terms gave Trust a
            # nonzero keyword score and Trust won. With the swarm down, the
            # roster came off disk in alphabetical order and Security won.
            #
            # So the answer depended on whether the system was running, which
            # made CI and this machine disagree about department ownership for
            # ten builds. CLAUDE.md already refuses exactly this one level up:
            # a contested role "belongs to NEITHER until somebody resolves it -
            # letting the alphabetically-first config keep it would decide
            # department ownership by accident, silently."
            #
            # The same rule applies at routing time. A tie nothing can break is
            # CONTESTED, and contested returns None. domains_for() surfaces both
            # claimants, which is the honest answer to a mixed question and the
            # one Anansi already consumes - it names the conflict rather than
            # adjudicating it, because it practises neither domain.
            # AND KEYWORDS DO NOT GET A VOTE HERE AT ALL.
            #
            # The first fix restricted the keyword tie-break to the tied
            # owners, which was better than searching every department but
            # still let noise decide. Measured on this exact prompt with the
            # swarm up:
            #
            #     trust_agent       5   matched "trust" INSIDE "trustee"
            #     security_agent    0   owns the role `network access` outright
            #     accounting_agent  7   owns no role in the sentence whatsoever
            #
            # Trust won a department-ownership decision on a five-character
            # substring of the word that gave it the role in the first place,
            # while the other genuine role-owner scored nothing. That is the
            # keyword matcher the role system was built to OVERRULE, brought
            # back in as a casting vote - and it moves with the swarm, because
            # agents contribute live terms at runtime.
            #
            # Roles are exclusive and equal in weight by construction. If two
            # departments own one each, neither owns it more, and there is no
            # evidence at the role level to separate them. Contested is the
            # answer, and it is the same answer in every environment.
            self.log(f"routing: CONTESTED between {sorted(tied)} - each owns a "
                     f"role in this request and nothing at the role level "
                     f"separates them. No primary. domains_for() surfaces both.")
            return None
        return self._domain_for(prompt)

    def contested_for(self, prompt):
        """-> [agent ids] tied on roles with nothing to separate them, or [].

        The companion to domain_for returning None. A caller that needs to say
        WHY there is no primary should be able to, without re-deriving it."""
        from core.roles import score_roles
        rs = score_roles(prompt, self.roles())
        if not rs:
            return []
        top = max(rs.values())
        tied = sorted(a for a, n in rs.items() if n == top)
        if len(tied) < 2:
            return []
        return tied

    def domains_for(self, prompt, min_share=0.30):
        """-> [agent ids] that SUBSTANTIALLY claim this request, best first.

        A request touching two departments is not a routing failure to be
        broken by a tiebreak - it is a request with two answers, and merging
        them silently is how a contradiction becomes a confident sentence.
        So this returns all of them and lets the caller decide.

        `min_share` is a fraction of the WINNER's score, not an absolute: the
        scores are character counts of matched vocabulary and scale with
        sentence length, so a fixed floor would make every long sentence
        multi-domain and every short one single. A department that accounts for
        at least 30% of what the winner accounts for is making a real claim on
        the sentence; one scoring a tenth of it has caught a stray word.
        """
        from core.roles import score_roles
        _, _, scores = self._domain_by_terms(prompt, with_margin=True)
        # Roles are added to the keyword scores rather than replacing them, so
        # a question naming one department's role and another's vocabulary
        # surfaces BOTH - which is what a mixed question is - with the role
        # owner on top because ROLE_WEIGHT dominates.
        for aid, n in score_roles(prompt, self.roles()).items():
            scores[aid] = scores.get(aid, 0) + n
        if not scores:
            d = self._domain_for(prompt)
            return [d] if d else []
        best = max(scores.values())
        if best <= 0:
            return []
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        return [aid for aid, n in ranked if n / best >= min_share]

    def vocabulary(self):
        """The live map, for whoever needs to show its work."""
        return dict(self._domain_vocabulary())
