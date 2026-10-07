"""Chat session and message persistence.

Every session read is scoped by client_id: a session that belongs to another
client is indistinguishable from one that does not exist.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from pymongo import ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase

from app.db.mongo import MESSAGES, SESSIONS

Role = Literal["user", "assistant"]
MessageStatus = Literal["complete", "stopped", "error"]

TITLE_MAX = 80


def utcnow() -> datetime:
    return datetime.now(UTC)


class SessionRepo:
    def __init__(self, db: AsyncDatabase, ttl_days: int):
        self.col = db[SESSIONS]
        self.messages = db[MESSAGES]
        self.ttl = timedelta(days=ttl_days)

    async def create(
        self, *, client_id: str, market_id: str | None, title: str, provider: str, model: str
    ) -> dict[str, Any]:
        now = utcnow()
        doc = {
            "_id": str(uuid4()),
            "client_id": client_id,
            "market_id": market_id,
            "title": title.strip()[:TITLE_MAX],
            "provider": provider,
            "model": model,
            "message_count": 0,
            "created_at": now,
            "updated_at": now,
            "expires_at": now + self.ttl,
        }
        await self.col.insert_one(doc)
        return doc

    async def get_owned(self, session_id: str, client_id: str) -> dict[str, Any] | None:
        return await self.col.find_one({"_id": session_id, "client_id": client_id})

    async def list_for_client(
        self, client_id: str, market_id: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        query: dict[str, Any] = {"client_id": client_id}
        if market_id:
            query["market_id"] = market_id
        cursor = self.col.find(query).sort("updated_at", -1).limit(limit)
        return await cursor.to_list()

    async def reserve_messages(self, session_id: str, client_id: str, count: int, limit: int) -> bool:
        """Atomically claim room for `count` messages. False when the session is full.

        Claimed up front, so two concurrent requests cannot both squeeze past the
        limit; the assistant message is saved whatever the outcome, so the claim
        is never left unused.
        """
        now = utcnow()
        updated = await self.col.find_one_and_update(
            {"_id": session_id, "client_id": client_id, "message_count": {"$lte": limit - count}},
            {"$inc": {"message_count": count}, "$set": {"updated_at": now, "expires_at": now + self.ttl}},
            return_document=ReturnDocument.AFTER,
        )
        return updated is not None

    async def touch(self, session_id: str) -> None:
        now = utcnow()
        await self.col.update_one({"_id": session_id}, {"$set": {"updated_at": now, "expires_at": now + self.ttl}})

    async def delete(self, session_id: str, client_id: str) -> bool:
        result = await self.col.delete_one({"_id": session_id, "client_id": client_id})
        if result.deleted_count:
            await self.messages.delete_many({"session_id": session_id})
            return True
        return False


class MessageRepo:
    def __init__(self, db: AsyncDatabase):
        self.col = db[MESSAGES]

    async def add(
        self,
        *,
        session_id: str,
        role: Role,
        content: str,
        status: MessageStatus = "complete",
        message_id: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        usage: dict[str, int] | None = None,
        latency_ms: int | None = None,
    ) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "_id": message_id or str(uuid4()),
            "session_id": session_id,
            "role": role,
            "content": content,
            "status": status,
            "tool_calls": tool_calls or [],
            "created_at": utcnow(),
        }
        if usage:
            doc["usage"] = usage
        if latency_ms is not None:
            doc["latency_ms"] = latency_ms
        await self.col.insert_one(doc)
        return doc

    async def recent_for_model(self, session_id: str, limit: int) -> list[dict[str, Any]]:
        """Last `limit` user/assistant text messages, oldest first. Tool output is never stored."""
        cursor = (
            self.col.find(
                {"session_id": session_id, "role": {"$in": ["user", "assistant"]}, "content": {"$ne": ""}},
                {"role": 1, "content": 1},
            )
            .sort("created_at", -1)
            .limit(limit)
        )
        docs = await cursor.to_list()
        return list(reversed(docs))

    async def list_for_display(self, session_id: str, limit: int = 200) -> list[dict[str, Any]]:
        cursor = self.col.find({"session_id": session_id}).sort("created_at", 1).limit(limit)
        return await cursor.to_list()
