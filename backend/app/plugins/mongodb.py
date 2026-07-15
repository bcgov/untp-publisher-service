import pymongo
from bson.objectid import ObjectId

from config import settings


class MongoClientError(Exception):
    """Generic MongoClient Error."""


class MongoClient:
    def __init__(self):
        if settings.MONGO_URI:
            self.client = pymongo.MongoClient(settings.MONGO_URI)
        else:
            auth_source = settings.MONGO_AUTH_SOURCE or settings.MONGO_DB
            self.client = pymongo.MongoClient(
                f"{settings.MONGO_HOST}:{settings.MONGO_PORT}",
                username=settings.MONGO_USER,
                password=settings.MONGO_PASSWORD,
                authSource=auth_source,
            )
        self.db = self.client[settings.MONGO_DB]

    def provision(self):
        self.db["IssuerInstanceRecord"].create_index([("id")], unique=True)
        self.db["CredentialRecord"].create_index([("id")], unique=True)
        self.db["StatusListRecord"].create_index([("id")], unique=True)
        self.db["StatusListRecord"].create_index(
            [("issuer", pymongo.ASCENDING), ("purpose", pymongo.ASCENDING), ("active", pymongo.ASCENDING)],
            name="issuer_purpose_active",
        )
        # Prefer (type, version); drop legacy unique-on-version-only if present.
        try:
            self.db["CredentialTemplateRecord"].drop_index("version_1")
        except pymongo.errors.OperationFailure:
            pass
        self.db["CredentialTemplateRecord"].create_index(
            [("type", pymongo.ASCENDING), ("version", pymongo.ASCENDING)],
            unique=True,
            name="type_version",
        )
        self.db["CredentialPickupRecord"].create_index([("id")], unique=True)

    def insert(self, collection, item):
        try:
            self.db[collection].insert_one(item)
        except pymongo.errors.DuplicateKeyError:
            raise MongoClientError()

    def find(self, collection, query):
        return self.db[collection].find(query, {"_id": False}, sort=[("_id", pymongo.DESCENDING)])

    def find_page(self, collection, query, *, skip: int = 0, limit: int = 50):
        return list(
            self.db[collection]
            .find(query, {"_id": False})
            .sort([("_id", pymongo.DESCENDING)])
            .skip(skip)
            .limit(limit)
        )

    def count(self, collection, query):
        return self.db[collection].count_documents(query)

    def find_one(self, collection, query):
        return self.db[collection].find_one(query, {"_id": False}, sort=[("_id", pymongo.DESCENDING)])

    def find_by_id(self, collection, object_id):
        return self.db[collection].find_one({"_id": ObjectId(object_id)})

    def replace(self, collection, query, new_item):
        self.db[collection].replace_one(query, new_item)

    def delete(self, collection, query):
        self.db[collection].delete_one(query)

    def claim_status_list_index(self, *, issuer_id: str, purpose: str) -> dict | None:
        """Atomically claim one free index from the active status list for ``purpose``.

        Uses ``$pop`` on ``indexes`` so concurrent publishers cannot share an index.
        Returns ``{"index": <claimed>, "endpoint": <str>, "id": <str>}`` or ``None``.
        """
        before = self.db["StatusListRecord"].find_one_and_update(
            {
                "issuer": issuer_id,
                "purpose": purpose,
                "active": True,
                "indexes.0": {"$exists": True},
            },
            {"$pop": {"indexes": 1}},
            projection={"_id": False, "id": True, "endpoint": True, "indexes": True},
            return_document=pymongo.ReturnDocument.BEFORE,
            sort=[("_id", pymongo.DESCENDING)],
        )
        if not before:
            return None
        indexes = before.get("indexes") or []
        if not indexes:
            return None
        return {
            "index": indexes[-1],
            "endpoint": before.get("endpoint"),
            "id": before.get("id"),
        }
