"""Create baseline unique and query indexes."""

from __future__ import annotations

import pymongo

MIGRATION_ID = "001_initial_indexes"


def up(db: pymongo.database.Database) -> None:
    db["IssuerInstanceRecord"].create_index([("id")], unique=True)
    db["CredentialRecord"].create_index([("id")], unique=True)
    db["StatusListRecord"].create_index([("id")], unique=True)
    db["StatusListRecord"].create_index(
        [
            ("issuer", pymongo.ASCENDING),
            ("purpose", pymongo.ASCENDING),
            ("active", pymongo.ASCENDING),
        ],
        name="issuer_purpose_active",
    )
    db["CredentialTemplateRecord"].create_index([("version")], unique=True)
    db["CredentialPickupRecord"].create_index([("id")], unique=True)
