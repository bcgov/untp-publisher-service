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
        list_id = before.get("id")
        # Always emit URLs for the current PUBLISHER_DOMAIN (stored endpoint may
        # be stale after a domain change; lookup still works via list id).
        from app.services.composer import status_list_endpoint

        endpoint = (
            status_list_endpoint(str(list_id))
            if list_id
            else before.get("endpoint")
        )
        return {
            "index": indexes[-1],
            "endpoint": endpoint,
            "id": list_id,
        }

    def set_status_list_bit(
        self, *, endpoint: str, index: int, value: bool = True
    ) -> bool:
        """Set one bit on the StatusListCredential ``encodedList`` for ``endpoint``.

        Looks up ``StatusListRecord`` by ``endpoint``, then by trailing path id.
        Returns ``True`` when the encoded list was updated and persisted.
        """
        from app.plugins.status_list import BitstringStatusList, BitstringStatusListError

        endpoint = (endpoint or "").strip()
        if not endpoint:
            return False

        record = self.find_one("StatusListRecord", {"endpoint": endpoint})
        if not record:
            list_id = endpoint.rstrip("/").rsplit("/", 1)[-1]
            if list_id:
                record = self.find_one("StatusListRecord", {"id": list_id})
        if not record:
            return False

        credential = record.get("credential")
        if not isinstance(credential, dict):
            return False
        subject = credential.get("credentialSubject")
        if not isinstance(subject, dict):
            return False
        encoded = subject.get("encodedList")
        if not isinstance(encoded, str) or not encoded:
            return False

        try:
            subject["encodedList"] = BitstringStatusList().set_status_bit(
                encoded, int(index), value
            )
        except (BitstringStatusListError, ValueError, TypeError):
            return False

        credential["credentialSubject"] = subject
        record["credential"] = credential
        record.pop("_id", None)
        self.replace("StatusListRecord", {"id": record["id"]}, record)
        return True
