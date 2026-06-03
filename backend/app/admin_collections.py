"""Metadata for MongoDB collections exposed in the admin API and UI."""

from __future__ import annotations

from typing import Any

# id_field: document key used in GET /admin/api/collections/{name}/records/{id}
# list_columns: fields shown in the admin table (in order)
ADMIN_COLLECTIONS: dict[str, dict[str, Any]] = {
    "IssuerRecord": {
        "step": 1,
        "phase": "Setup",
        "title": "Issuers",
        "description": "Registered issuer identities.",
        "create_via": "",
        "admin_create": True,
        "admin_create_path": "/admin/api/issuers",
        "prerequisites": [],
        "next_steps": [],
        "id_field": "id",
        "list_columns": ["id", "name", "scope", "authorized_key"],
        "redact_fields": ["secret_hash"],
        "record_links": [],
    },
    "CredentialTypeRecord": {
        "step": 2,
        "phase": "Configuration",
        "title": "Credential types",
        "description": "Templates, JSON-LD context, OCA bundle, and status list binding",
        "create_via": "POST /registrations/credentials",
        "prerequisites": [
            "Issuer must exist (issuer field = issuer DID)",
            "Issuer should have a client secret before they issue VCs",
        ],
        "next_steps": [
            "Issuer calls credential issue APIs with a JWT from /auth/token",
        ],
        "id_field": "version",
        "list_columns": ["type", "version", "issuer", "subject_type"],
        "redact_fields": [],
        "record_links": [
            {"field": "issuer", "collection": "IssuerRecord", "id_field": "id"},
        ],
        "also_creates": ["StatusListRecord"],
    },
    "StatusListRecord": {
        "step": 2,
        "phase": "Configuration",
        "title": "Status lists",
        "description": "Bitstring status lists for revocation, suspension, and refresh",
        "create_via": "Created automatically with POST /registrations/credentials",
        "prerequisites": ["Register a credential type (same request creates the status list)"],
        "next_steps": [],
        "id_field": "id",
        "list_columns": ["id", "type", "version", "active", "endpoint"],
        "redact_fields": [],
        "truncate_fields": {"credential": 120},
        "record_links": [],
        "auto_created": True,
    },
    "CredentialRecord": {
        "step": 3,
        "phase": "Runtime",
        "title": "Credentials",
        "description": "Published verifiable credentials (issued by registered issuers)",
        "create_via": "Issuer APIs (JWT) — not created from the admin UI",
        "prerequisites": [
            "Issuer registered and authenticated",
            "Credential type registered",
        ],
        "next_steps": [],
        "id_field": "id",
        "list_columns": ["id", "type", "entity_id", "cardinality_id", "refresh", "revocation"],
        "redact_fields": [],
        "truncate_fields": {"vc_jwt": 80, "vc": 120},
        "record_links": [
            {
                "field": "type",
                "collection": "CredentialTypeRecord",
                "search": True,
                "label": "Credential type",
            },
        ],
    },
    "CredentialPickupRecord": {
        "step": 4,
        "phase": "Operations",
        "title": "Pickup records",
        "description": "Credential pickup queue entries (when pickup flow is enabled)",
        "create_via": "Pickup / delivery flow (if configured)",
        "prerequisites": ["Issued credentials"],
        "next_steps": [],
        "id_field": "id",
        "list_columns": ["id"],
        "redact_fields": [],
        "record_links": [],
        "optional": True,
    },
}

ADMIN_WORKFLOW: list[dict[str, Any]] = [
    {
        "step": 1,
        "title": "Register Issuer",
        "summary": "Create a DID Web issuer on the registry and store IssuerRecord.",
        "api": "POST /registrations/issuers",
        "collections": ["IssuerRecord"],
    },
    {
        "step": 2,
        "title": "Create Template",
        "summary": "Define the credential template, context, and OCA bundle; creates StatusListRecord + CredentialTypeRecord.",
        "api": "POST /registrations/credentials",
        "collections": ["CredentialTypeRecord", "StatusListRecord"],
    },
    {
        "step": 3,
        "title": "Issue Credentials",
        "summary": "Issuer publishes VCs through the publisher API using their token.",
        "api": "Issuer credential endpoints (authenticated)",
        "collections": ["CredentialRecord"],
    },
    {
        "step": 4,
        "title": "Pickup (optional)",
        "summary": "Operational queue for credential pickup, when that flow is in use.",
        "api": None,
        "collections": ["CredentialPickupRecord"],
        "optional": True,
    },
]


def collection_names() -> list[str]:
    """Collection names in recommended workflow order."""
    return sorted(
        ADMIN_COLLECTIONS.keys(),
        key=lambda name: (
            ADMIN_COLLECTIONS[name].get("step", 99),
            name,
        ),
    )


def get_collection_meta(name: str) -> dict[str, Any]:
    if name not in ADMIN_COLLECTIONS:
        raise KeyError(name)
    return ADMIN_COLLECTIONS[name]


def collection_public_meta(name: str) -> dict[str, Any]:
    """API/UI-safe metadata for one collection."""
    meta = get_collection_meta(name)
    return {
        "name": name,
        "step": meta.get("step"),
        "phase": meta.get("phase"),
        "title": meta["title"],
        "description": meta["description"],
        "create_via": meta.get("create_via"),
        "prerequisites": meta.get("prerequisites", []),
        "next_steps": meta.get("next_steps", []),
        "id_field": meta["id_field"],
        "list_columns": meta["list_columns"],
        "auto_created": meta.get("auto_created", False),
        "optional": meta.get("optional", False),
        "also_creates": meta.get("also_creates", []),
        "record_links": meta.get("record_links", []),
        "admin_create": meta.get("admin_create", False),
        "admin_create_path": meta.get("admin_create_path"),
    }
