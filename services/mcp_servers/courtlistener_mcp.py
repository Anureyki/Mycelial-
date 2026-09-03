#!/usr/bin/env python3
"""
CourtListener MCP server (stdio) - case law / RECAP docket search and monitoring.

Auth: set COURTLISTENER_API_TOKEN (either in the shell environment that starts
the Tool Service, or in config/mcp.d/courtlistener.json under "env") to a
CourtListener API token (https://www.courtlistener.com/help/api/rest/#authentication).
Search works without a token (public, rate-limited); creating alerts or
docket-alert subscriptions requires one.
"""
import sys
import os
import json
import re
import time
import requests

BASE_URL = "https://www.courtlistener.com/api/rest/v4"
API_TOKEN = os.environ.get("COURTLISTENER_API_TOKEN", "").strip()

NO_TOKEN_ERROR = (
    "COURTLISTENER_API_TOKEN is not set; this action requires an authenticated "
    "CourtListener account token. See "
    "https://www.courtlistener.com/help/api/rest/#authentication"
)


def _headers():
    headers = {"User-Agent": "mycelial-legal-agent/1.0"}
    if API_TOKEN:
        headers["Authorization"] = f"Token {API_TOKEN}"
    return headers



# ---------------------------------------------------------------------------
# CourtListener allows 5 requests per minute on this token.
#
# Found the hard way: verify_quote's retry loop was BURNING THE QUOTA and then
# reporting the throttling as "case not found" - the tool was manufacturing the
# failures it was retrying. HTTP 429 says "Rate limit exceeded: 5/min. Expected
# available in 11 seconds", which is not a transient blip to retry through, it
# is an instruction to wait.
#
# Two things follow. Honour the wait rather than hammering it. And CACHE: an
# opinion filed in 1959 does not change, so re-reading it should never cost a
# request. With 5/min, a cache is the difference between checking one citation
# and checking a document full of them.
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "state", "courtlistener_cache")
CACHE_TTL = 30 * 24 * 3600      # opinions are stable; searches drift slowly


def _cache_path(kind, key):
    import hashlib
    os.makedirs(CACHE_DIR, exist_ok=True)
    h = hashlib.sha256(f"{kind}:{key}".encode()).hexdigest()[:24]
    return os.path.join(CACHE_DIR, f"{kind}_{h}.json")


def _cache_get(kind, key):
    p = _cache_path(kind, key)
    try:
        if os.path.exists(p) and (time.time() - os.path.getmtime(p)) < CACHE_TTL:
            with open(p, encoding="utf-8") as fh:
                d = json.load(fh)
            d["_from_cache"] = True
            return d
    except Exception:
        pass
    return None


def _cache_put(kind, key, value):
    if not isinstance(value, dict) or value.get("error"):
        return value            # never cache a failure as if it were an answer
    try:
        with open(_cache_path(kind, key), "w", encoding="utf-8") as fh:
            json.dump(value, fh)
    except Exception:
        pass
    return value


def _get_with_backoff(url, params, timeout=45, tries=3):
    """GET, waiting the exact time a 429 asks for rather than guessing."""
    last = None
    for attempt in range(tries):
        resp = requests.get(url, params=params, headers=_headers(), timeout=timeout)
        if resp.status_code == 200:
            return resp, None
        if resp.status_code == 429:
            wait = 12
            m = re.search(r"available in (\d+)", resp.text or "")
            if m:
                wait = min(int(m.group(1)) + 2, 65)
            last = (f"rate limited (5 requests/minute); waited {wait}s")
            if attempt < tries - 1:
                time.sleep(wait)
                continue
            return None, ("CourtListener rate limit: 5 requests per minute. "
                          "The check did not run - this says nothing about the case.")
        last = f"HTTP {resp.status_code}"
        return None, f"CourtListener returned {last}: {resp.text[:200]}"
    return None, last

def search(arguments):
    query = arguments.get("q") or arguments.get("query")
    if not query:
        return {"error": "Missing required 'q' (search query)"}
    params = {"q": query, "type": arguments.get("type") or "r"}
    for key in ("court", "case_name", "party_name", "docket_number",
                "order_by", "filed_after", "filed_before"):
        if arguments.get(key):
            params[key] = arguments[key]
    ck = json.dumps(params, sort_keys=True)
    hit = _cache_get("search", ck)
    if hit:
        return hit
    try:
        resp, err = _get_with_backoff(f"{BASE_URL}/search/", params, timeout=30)
        if err:
            return {"error": err}
        data = resp.json()
        results = data.get("results", [])
        out = {
            "count": data.get("count", len(results)),
            "results": [
                {
                    "caseName": r.get("caseName"),
                    "court": r.get("court"),
                    "dateFiled": r.get("dateFiled"),
                    "docketNumber": r.get("docketNumber"),
                    "docket_id": r.get("docket_id"),
                    # An OPINION search returns cluster_id, not docket_id, and
                    # dropping it meant a caller could find a case and have no
                    # handle to open it - which came back as "case_not_found",
                    # the wrong answer to a search that had succeeded.
                    "cluster_id": r.get("cluster_id") or r.get("id"),
                    "absolute_url": f"https://www.courtlistener.com{r['absolute_url']}" if r.get("absolute_url") else None,
                    "snippet": (r.get("snippet") or (r.get("text", "")[:300] if r.get("text") else None)),
                }
                for r in results
            ]
        }
        return _cache_put("search", ck, out)
    except Exception as e:
        return {"error": str(e)}



def docket_documents(arguments):
    """List a docket's entries, and return the TEXT of one when asked.

    This was the gap. The server could `search` - find that a case exists - and
    could subscribe to alerts, and could not READ anything. So the Legal Agent
    could locate `Duell v. State of Hawaii` by name in seconds and then had no
    way to open the order that decided it; a human had to fetch and read it,
    which is the capture-layer problem one layer down.

    A docket entry is not the document. RECAP holds the court's own text only
    where someone has purchased and contributed it, so `is_available` is
    reported per document and an unavailable one says so rather than coming
    back empty.
    """
    docket_id = arguments.get("docket_id")
    if not docket_id:
        return {"error": "Missing required 'docket_id' (from a search result)"}
    number = arguments.get("document_number")
    try:
        params = {"docket_entry__docket": docket_id, "page_size": 50}
        fields = ["id", "document_number", "description", "is_available", "page_count"]
        if number is not None:
            params["document_number"] = str(number)
            fields.append("plain_text")
        params["fields"] = ",".join(fields)
        resp = requests.get(f"{BASE_URL}/recap-documents/", params=params,
                            headers=_headers(), timeout=45)
        if resp.status_code != 200:
            return {"error": f"CourtListener document fetch failed: HTTP {resp.status_code}",
                    "detail": resp.text[:400]}
        results = resp.json().get("results", [])
        if number is None:
            return {"docket_id": docket_id, "documents": [
                {k: r.get(k) for k in ("document_number", "description",
                                       "is_available", "page_count")}
                for r in results],
                "note": ("Entries only. Pass document_number to read one. "
                         "is_available false means RECAP holds no text for it - "
                         "nobody has contributed that PDF, which is a fact about "
                         "the archive and not about the document.")}
        if not results:
            return {"error": f"No document {number} on docket {docket_id}."}
        doc = results[0]
        text = doc.get("plain_text") or ""
        return {"docket_id": docket_id, "document_number": doc.get("document_number"),
                "description": doc.get("description"),
                "is_available": doc.get("is_available"),
                "page_count": doc.get("page_count"),
                "chars": len(text),
                "text": text[:120000],
                "truncated": len(text) > 120000,
                "read_not_summarised": ("This is the court's own text as filed. "
                                        "Nothing here is a paraphrase.")}
    except Exception as e:
        return {"error": str(e)}


def opinion_text(arguments):
    """The full text of a published opinion, so a QUOTE can be checked.

    The gap this closes was found the hard way. A complaint circulating on
    social media quoted United States v. Benabe for the proposition that courts
    must avoid summary dismissal of "sovereign citizen" claims. Benabe says the
    exact opposite - "These theories should be rejected summarily, however they
    are presented" - and the only way to know that is to READ IT.

    `search` could find the case existed. Nothing could open it. So the check
    that settles a fabricated quote had to be done by hand outside the system,
    which means the principal could not do it himself.
    """
    cluster = arguments.get("cluster_id")
    if not cluster:
        return {"error": "Missing required 'cluster_id' (from a search result)"}
    try:
        hit = _cache_get("opinion", str(cluster))
        if hit:
            return hit
        resp, err = _get_with_backoff(
            f"{BASE_URL}/opinions/",
            {"cluster__id": cluster, "fields": "id,plain_text,html_with_citations"},
            timeout=60)
        if err:
            return {"error": err}
        text = ""
        for o in resp.json().get("results", []):
            t = o.get("plain_text") or ""
            if not t and o.get("html_with_citations"):
                t = re.sub(r"<[^>]+>", " ", o["html_with_citations"])
            text += t + "\n"
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return {"cluster_id": cluster, "readable": False,
                    "why": ("CourtListener holds no text for this opinion. A fact about the "
                            "archive, not the opinion - nothing about what it says can be "
                            "asserted from here.")}
        return _cache_put("opinion", str(cluster),
                          {"cluster_id": cluster, "readable": True,
                           "chars": len(text), "text": text[:400000]})
    except Exception as e:
        return {"error": str(e)}

def create_alert(arguments):
    if not API_TOKEN:
        return {"error": NO_TOKEN_ERROR}
    name = arguments.get("name")
    query = arguments.get("query")
    rate = arguments.get("rate") or "dly"
    if not name or not query:
        return {"error": "Missing required 'name' and/or 'query'"}
    alert_type = arguments.get("alert_type") or (query.get("type") if isinstance(query, dict) else None) or "r"
    query_string = query if isinstance(query, str) else "&".join(
        f"{k}={v}" for k, v in query.items() if v
    )
    try:
        resp = requests.post(
            f"{BASE_URL}/alerts/",
            json={"name": name, "query": query_string, "rate": rate, "alert_type": alert_type},
            headers=_headers(), timeout=20
        )
        if resp.status_code not in (200, 201):
            return {"error": f"Failed to create alert: HTTP {resp.status_code}", "detail": resp.text[:500]}
        return resp.json()
    except Exception as e:
        return {"error": str(e)}


def subscribe_docket_alert(arguments):
    if not API_TOKEN:
        return {"error": NO_TOKEN_ERROR}
    docket = arguments.get("docket")
    if not docket:
        return {"error": "Missing required 'docket' (docket ID)"}
    try:
        resp = requests.post(
            f"{BASE_URL}/docket-alerts/",
            json={"docket": docket},
            headers=_headers(), timeout=20
        )
        if resp.status_code not in (200, 201):
            return {"error": f"Failed to subscribe to docket alert: HTTP {resp.status_code}", "detail": resp.text[:500]}
        return resp.json()
    except Exception as e:
        return {"error": str(e)}


TOOLS = {
    "search": {
        "description": "Search CourtListener case law, RECAP dockets/filings, or oral arguments.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "q": {"type": "string", "description": "Search query, e.g. a person's or party's name"},
                "type": {"type": "string", "description": "o=opinion, r=RECAP docket, rd=RECAP document, d=docket, oa=oral argument. Defaults to 'r'."},
                "court": {"type": "string"},
                "case_name": {"type": "string"},
                "party_name": {"type": "string"},
                "docket_number": {"type": "string"},
                "order_by": {"type": "string"},
                "filed_after": {"type": "string"},
                "filed_before": {"type": "string"},
            },
            "required": ["q"],
        },
        "handler": search,
    },
    "docket_documents": {
        "description": ("List the documents on a docket, or read the full text of one. "
                        "Pass docket_id alone to list; add document_number to read."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "docket_id": {"type": "integer",
                              "description": "docket_id from a search result"},
                "document_number": {"type": "string",
                                    "description": "entry number to read in full"},
            },
            "required": ["docket_id"],
        },
        "handler": docket_documents,
    },
    "opinion_text": {
        "description": ("Full text of a published opinion, by cluster_id from a search "
                        "result. Use it to check whether a quoted sentence is really in it."),
        "inputSchema": {
            "type": "object",
            "properties": {"cluster_id": {"type": "integer"}},
            "required": ["cluster_id"],
        },
        "handler": opinion_text,
    },
    "create_alert": {
        "description": "Create a recurring CourtListener search alert (requires COURTLISTENER_API_TOKEN).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "query": {"description": "URL query string or object of search params, e.g. {\"type\": \"r\", \"party_name\": \"Jane Doe\"}"},
                "rate": {"type": "string", "description": "rt|dly|wly|mly|off, default dly"},
                "alert_type": {"type": "string", "description": "o|r|d|oa - defaults to the query's 'type', or 'r'"},
            },
            "required": ["name", "query"],
        },
        "handler": create_alert,
    },
    "subscribe_docket_alert": {
        "description": "Subscribe to alerts for a specific docket ID (requires COURTLISTENER_API_TOKEN).",
        "inputSchema": {
            "type": "object",
            "properties": {"docket": {"type": "integer"}},
            "required": ["docket"],
        },
        "handler": subscribe_docket_alert,
    },
}


def handle_request(req):
    method = req.get("method")
    req_id = req.get("id")
    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "serverInfo": {"name": "courtlistener", "version": "1.0.0"},
            },
        }
    if method == "tools/list":
        return {
            "jsonrpc": "2.0", "id": req_id,
            "result": {"tools": [
                {"name": name, "description": t["description"], "inputSchema": t["inputSchema"]}
                for name, t in TOOLS.items()
            ]},
        }
    if method == "tools/call":
        params = req.get("params", {})
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        tool = TOOLS.get(tool_name)
        if not tool:
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}}
        result = tool["handler"](arguments)
        return {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": json.dumps(result)}]}}
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "Method not found"}}


def main():
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        try:
            req = json.loads(line)
            resp = handle_request(req)
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
        except Exception as e:
            sys.stderr.write(f"Error: {e}\n")
            sys.stderr.flush()


if __name__ == "__main__":
    main()
