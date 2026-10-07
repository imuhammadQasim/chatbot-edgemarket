"""MongoDB connection and index setup (PyMongo async API)."""

import logging

from pymongo import ASCENDING, DESCENDING, AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import OperationFailure

log = logging.getLogger(__name__)

SESSIONS = "chat_sessions"
MESSAGES = "chat_messages"


def connect(uri: str) -> AsyncMongoClient:
    # tz_aware so datetimes read back as UTC-aware, matching what we write.
    return AsyncMongoClient(uri, tz_aware=True, appname="edgemarket-chatbot", serverSelectionTimeoutMS=5000)


async def ensure_indexes(db: AsyncDatabase, ttl_days: int) -> None:
    sessions = db[SESSIONS]
    messages = db[MESSAGES]

    await sessions.create_index(
        [("client_id", ASCENDING), ("market_id", ASCENDING), ("updated_at", DESCENDING)],
        name="client_market_updated",
    )
    # expires_at is pushed forward on every message, so idle sessions age out.
    await sessions.create_index("expires_at", expireAfterSeconds=0, name="ttl_expires_at")

    await messages.create_index([("session_id", ASCENDING), ("created_at", ASCENDING)], name="session_created")
    # Messages age out on their own clock so an expired session leaves nothing
    # behind. A live session keeps its most recent ttl_days of messages.
    await _ensure_ttl(db, MESSAGES, "created_at", "ttl_created_at", ttl_days * 86400)


async def _ensure_ttl(db: AsyncDatabase, collection: str, field: str, name: str, seconds: int) -> None:
    try:
        await db[collection].create_index(field, expireAfterSeconds=seconds, name=name)
    except OperationFailure as exc:
        # Same index, different TTL (SESSION_TTL_DAYS changed): update in place.
        if exc.code not in (85, 86):  # IndexOptionsConflict / IndexKeySpecsConflict
            raise
        log.info("Updating TTL on %s.%s to %ss", collection, name, seconds)
        await db.command({"collMod": collection, "index": {"name": name, "expireAfterSeconds": seconds}})
