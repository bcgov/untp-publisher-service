import pymongo
from bson.objectid import ObjectId
from urllib.parse import urlparse

from config import settings


class MongoClientError(Exception):
    """Generic MongoClient Error."""


def _database_name_from_uri(uri: str) -> str | None:
    """Return database segment from a MongoDB URI path, or None if omitted."""
    # urlparse needs a scheme with // for netloc/path split
    normalised = uri.replace("mongodb+srv://", "https://", 1).replace(
        "mongodb://", "http://", 1
    )
    parsed = urlparse(normalised)
    path = (parsed.path or "").strip("/")
    if not path:
        return None
    return path.split("/")[0]


def _resolve_database_name() -> str:
    if settings.MONGO_URI:
        from_uri = _database_name_from_uri(settings.MONGO_URI)
        if from_uri:
            return from_uri
        if settings.MONGO_DB:
            return settings.MONGO_DB
        raise MongoClientError(
            "MONGO_URI has no database in the path; set MONGO_DB to the database name."
        )
    return settings.MONGO_DB


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
        self.db = self.client[_resolve_database_name()]

    def provision(self):
        self.db["IssuerInstanceRecord"].create_index([("id")], unique=True)
        self.db["CredentialRecord"].create_index([("id")], unique=True)
        self.db["StatusListRecord"].create_index([("id")], unique=True)
        self.db["StatusListRecord"].create_index(
            [("issuer", pymongo.ASCENDING), ("purpose", pymongo.ASCENDING), ("active", pymongo.ASCENDING)],
            name="issuer_purpose_active",
        )
        self.db["CredentialTemplateRecord"].create_index([("version")], unique=True)
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
