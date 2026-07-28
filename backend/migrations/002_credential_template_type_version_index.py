"""Replace CredentialTemplateRecord unique-on-version with (type, version)."""

from __future__ import annotations

import pymongo

MIGRATION_ID = "002_credential_template_type_version_index"


def up(db: pymongo.database.Database) -> None:
    try:
        db["CredentialTemplateRecord"].drop_index("version_1")
    except pymongo.errors.OperationFailure:
        pass
    db["CredentialTemplateRecord"].create_index(
        [("type", pymongo.ASCENDING), ("version", pymongo.ASCENDING)],
        unique=True,
        name="type_version",
    )
