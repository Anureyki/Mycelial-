#!/usr/bin/env python3
"""
Standard Relationship Schema - the common shape every relationship-modeling
agent (Legal, Accounting, Trust, ...) stores its output in, and the shape
Boss's Graph Manager (core/graph_manager.py) ingests via update_graph.

Domain-specific agents may keep richer, domain-specific fields in their own
memory records (e.g. Legal Agent's contract_type/custodian, Accounting
Agent's principal_amount/interest_rate) - those live in `metadata`. The
top-level fields here are exactly what's needed to build graph nodes/edges
and answer cross-domain questions without knowing every agent's internal
schema.
"""
import uuid
from datetime import datetime

SCHEMA_VERSION = "1.0"

RELATIONSHIP_DOMAINS = {"legal", "financial", "trust", "system", "access"}

RELATIONSHIP_REQUIRED_FIELDS = [
    "relationship_id", "project_id", "domain", "parties", "assets",
    "obligations", "rights", "governing_law", "beneficiary",
    "service_provider", "fee_recipient", "metadata", "timestamp"
]

RELATIONSHIP_LIST_FIELDS = {"parties", "assets", "obligations", "rights"}
RELATIONSHIP_STRING_FIELDS = {
    "relationship_id", "project_id", "domain", "governing_law",
    "beneficiary", "service_provider", "fee_recipient", "timestamp"
}


def new_relationship(project_id="", domain="legal", parties=None, assets=None,
                      obligations=None, rights=None, governing_law="",
                      beneficiary="", service_provider="", fee_recipient="",
                      metadata=None, relationship_id=None, timestamp=None):
    """Build a canonical relationship dict. `parties` is a list of
    {"id": <entity_id>, "role": <str>} dicts (e.g. {"id": "acme_corp", "role": "creditor"})."""
    if domain not in RELATIONSHIP_DOMAINS:
        raise ValueError(f"domain must be one of {sorted(RELATIONSHIP_DOMAINS)}, got {domain!r}")
    return {
        "schema_version": SCHEMA_VERSION,
        "relationship_id": relationship_id or f"rel_{uuid.uuid4().hex[:12]}",
        "project_id": project_id or "",
        "domain": domain,
        "parties": parties or [],
        "assets": assets or [],
        "obligations": obligations or [],
        "rights": rights or [],
        "governing_law": governing_law or "",
        "beneficiary": beneficiary or "",
        "service_provider": service_provider or "",
        "fee_recipient": fee_recipient or "",
        "metadata": metadata or {},
        "timestamp": timestamp or datetime.now().isoformat(),
    }


def validate_relationship(doc):
    """Returns (is_valid, [error, ...]). Does not mutate doc."""
    errors = []
    if not isinstance(doc, dict):
        return False, ["relationship must be a dict"]
    for field in RELATIONSHIP_REQUIRED_FIELDS:
        if field not in doc:
            errors.append(f"missing field: {field}")
    if "domain" in doc and doc["domain"] not in RELATIONSHIP_DOMAINS:
        errors.append(f"invalid domain: {doc['domain']!r} (expected one of {sorted(RELATIONSHIP_DOMAINS)})")
    for field in RELATIONSHIP_LIST_FIELDS:
        if field in doc and not isinstance(doc[field], list):
            errors.append(f"field {field} must be a list")
    for field in RELATIONSHIP_STRING_FIELDS:
        if field in doc and not isinstance(doc[field], str):
            errors.append(f"field {field} must be a string")
    if "parties" in doc and isinstance(doc["parties"], list):
        for i, p in enumerate(doc["parties"]):
            if not isinstance(p, dict) or "id" not in p or "role" not in p:
                errors.append(f"parties[{i}] must be an object with 'id' and 'role'")
    return (len(errors) == 0), errors


def coerce_relationship(doc):
    """Fill in any missing required fields with schema defaults, in place-safe fashion.
    Use when normalizing output from an LLM extraction or a legacy record."""
    base = new_relationship()
    merged = {**base, **(doc or {})}
    for field in RELATIONSHIP_LIST_FIELDS:
        if not isinstance(merged.get(field), list):
            merged[field] = []
    for field in RELATIONSHIP_STRING_FIELDS:
        if not isinstance(merged.get(field), str):
            merged[field] = str(merged.get(field) or "")
    if not isinstance(merged.get("metadata"), dict):
        merged["metadata"] = {}
    return merged


def from_legacy_fields(doc, domain, project_id="", asset_field="asset"):
    """Adapt an existing Legal/Accounting-style flat extraction dict (entity_a/entity_b,
    beneficiary, service_provider, fee_recipient, obligations, rights, governing_law,
    applicable_statutes/asset/...) into the canonical relationship schema. Anything not
    covered by the canonical top-level fields is preserved under metadata."""
    doc = doc or {}
    parties = []
    if doc.get("entity_a"):
        parties.append({"id": doc["entity_a"], "role": "entity_a"})
    if doc.get("entity_b"):
        parties.append({"id": doc["entity_b"], "role": "entity_b"})
    if doc.get("creditor"):
        parties.append({"id": doc["creditor"], "role": "creditor"})
    if doc.get("debtor"):
        parties.append({"id": doc["debtor"], "role": "debtor"})
    if doc.get("settlor"):
        parties.append({"id": doc["settlor"], "role": "settlor"})
    if doc.get("trustee"):
        parties.append({"id": doc["trustee"], "role": "trustee"})

    assets = []
    asset_val = doc.get(asset_field) or doc.get("asset") or doc.get("trust_property")
    if isinstance(asset_val, list):
        assets.extend(str(a) for a in asset_val if a)
    elif asset_val:
        assets.append(str(asset_val))

    known_top_level = {
        "entity_a", "entity_b", "creditor", "debtor", "settlor", "trustee",
        "asset", "trust_property", "obligations", "rights", "beneficiary",
        "service_provider", "fee_recipient", "governing_law",
        "applicable_statutes", "governing_rules", "id", "created",
        "source_excerpt", "parse_error", "raw_model_output", "cache_sources",
        "disclaimer",
    }
    metadata = {k: v for k, v in doc.items() if k not in known_top_level}

    return new_relationship(
        project_id=project_id,
        domain=domain,
        parties=parties,
        assets=assets,
        obligations=list(doc.get("obligations") or []),
        rights=list(doc.get("rights") or []),
        governing_law=doc.get("governing_law", "") or "",
        beneficiary=doc.get("beneficiary", "") or "",
        service_provider=doc.get("service_provider", "") or "",
        fee_recipient=doc.get("fee_recipient", "") or "",
        metadata={
            **metadata,
            "applicable_statutes": doc.get("applicable_statutes", []),
            "governing_rules": doc.get("governing_rules", []),
            "source_record_id": doc.get("id"),
            "disclaimer": doc.get("disclaimer", ""),
        },
        relationship_id=f"rel_{doc['id']}" if doc.get("id") else None,
        timestamp=doc.get("created"),
    )
