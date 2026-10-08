"""Request and response bodies."""

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

OBJECT_ID_RE = re.compile(r"^[0-9a-fA-F]{24}$")


def is_object_id(value: str | None) -> bool:
    return bool(value) and bool(OBJECT_ID_RE.fullmatch(value))


class ChatRequest(BaseModel):
    # Shown as the pre-filled body in /docs; omit session_id to start a new chat.
    model_config = ConfigDict(
        json_schema_extra={"examples": [{"market_id": "69ae9afbd3202882511cf348", "message": "Who is leading?"}]}
    )

    session_id: str | None = Field(default=None, max_length=64)
    market_id: str | None = None
    # Length is enforced in the route (after trimming) so it can answer with the
    # SSE `bad_request` event rather than a bare 422.
    message: str = Field(max_length=20_000)

    @field_validator("session_id", "market_id", mode="before")
    @classmethod
    def _blank_to_none(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value.strip() if isinstance(value, str) else value


class LegacyChatRequest(BaseModel):
    """Body the widget that is live today sends to POST /chat."""

    query: str = Field(max_length=20_000)
    user_id: str | None = None


class SessionOut(BaseModel):
    session_id: str
    market_id: str | None
    title: str
    message_count: int
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    message_id: str
    role: str
    content: str
    status: str
    created_at: datetime


class MessagesOut(BaseModel):
    session_id: str
    market_id: str | None
    messages: list[MessageOut]
