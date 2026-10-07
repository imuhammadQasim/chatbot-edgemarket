"""Chat routes: /api/v1 SSE API."""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import anyio
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from app.agent.prompts import build_turn_message
from app.agent.runner import ChatRunner, TurnContext
from app.api.sse import PING, SSE_HEADERS, format_sse, sse_error_response
from app.config import Settings
from app.db.repositories import MessageRepo, SessionRepo, utcnow
from app.schemas import ChatRequest, MessageOut, MessagesOut, SessionOut, is_object_id

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")

DISCONNECT_POLL_S = 1.0
_END = object()

# Finalizers run as detached tasks so a cancelled request cannot interrupt a
# save; this keeps a reference until each one finishes.
_background: set[asyncio.Task] = set()


@dataclass
class ChatDeps:
    settings: Settings
    sessions: SessionRepo
    messages: MessageRepo
    runner: ChatRunner


def get_deps(request: Request) -> ChatDeps:
    return request.app.state.deps


def get_client_id(x_client_id: str | None = Header(default=None, alias="X-Client-Id")) -> str:
    try:
        return str(UUID(x_client_id or ""))
    except ValueError:
        raise HTTPException(status_code=400, detail="X-Client-Id header must be a UUID") from None


class ChatTurn:
    """One user message and the assistant's streamed reply.

    The agent runs in its own task feeding a queue, so the response loop can
    send pings and notice a disconnect while a tool call is still running.
    """

    def __init__(self, deps: ChatDeps, session: dict[str, Any], viewing_market_id: str | None):
        self.deps = deps
        self.session = session
        self.viewing_market_id = viewing_market_id
        self.message_id = str(uuid4())
        self.context = TurnContext(viewing_market_id=viewing_market_id)
        self.queue: asyncio.Queue = asyncio.Queue()
        self.parts: list[str] = []
        self.status = "complete"
        self.usage: dict[str, int] | None = None
        self.started = time.perf_counter()
        self.task: asyncio.Task | None = None
        self._finalized = False

    def start(self, model_messages: list[BaseMessage]) -> None:
        self.task = asyncio.create_task(self._produce(model_messages))

    async def _produce(self, model_messages: list[BaseMessage]) -> None:
        try:
            async for event in self.deps.runner.stream(model_messages, self.context):
                if event["type"] == "token":
                    self.parts.append(event["text"])
                elif event["type"] == "final":
                    self.usage = event["usage"]
                    event = {"type": "done", "finish_reason": event["finish_reason"]}
                await self.queue.put(event)
        except asyncio.CancelledError:
            self.status = "stopped"
            raise
        except Exception:
            log.exception("Agent run failed (session=%s)", self.session["_id"])
            self.status = "error"
            await self.queue.put(
                {
                    "type": "error",
                    "code": "provider_error",
                    "message": "The AI service is unavailable right now. Please try again in a moment.",
                }
            )
        finally:
            self.queue.put_nowait(_END)

    async def events(self, is_disconnected: Callable[[], Awaitable[bool]]) -> AsyncIterator[dict | None]:
        """Agent events in order; None means "send a keep-alive ping". Stops early on disconnect."""
        last_activity = time.monotonic()
        last_poll = time.monotonic()
        while True:
            try:
                event = await asyncio.wait_for(self.queue.get(), timeout=DISCONNECT_POLL_S)
            except TimeoutError:
                event = None

            now = time.monotonic()
            if now - last_poll >= DISCONNECT_POLL_S:
                last_poll = now
                if await is_disconnected():
                    self.stop()
                    return

            if event is _END:
                return
            if event is None:
                if now - last_activity >= self.deps.settings.sse_ping_s:
                    last_activity = now
                    yield None
                continue
            last_activity = now
            yield event

    def stop(self) -> None:
        if self.task and not self.task.done():
            self.status = "stopped"
            self.task.cancel()

    async def finalize(self) -> None:
        """Save the assistant message whatever happened. Idempotent and safe to call from a cancelled request."""
        if self._finalized:
            return
        self._finalized = True
        self.stop()
        if self.task:
            await asyncio.gather(self.task, return_exceptions=True)
        await self.deps.messages.add(
            session_id=self.session["_id"],
            role="assistant",
            content="".join(self.parts),
            status=self.status,
            message_id=self.message_id,
            tool_calls=self.context.trace,
            usage=self.usage,
            latency_ms=round((time.perf_counter() - self.started) * 1000),
        )
        await self.deps.sessions.touch(self.session["_id"])
        log.info(
            "turn session=%s status=%s tools=%s ms=%s",
            self.session["_id"],
            self.status,
            [t["name"] for t in self.context.trace],
            round((time.perf_counter() - self.started) * 1000),
        )


async def finalize_shielded(turn: ChatTurn) -> None:
    task = asyncio.ensure_future(turn.finalize())
    _background.add(task)
    task.add_done_callback(_background.discard)
    with anyio.CancelScope(shield=True):
        try:
            await asyncio.shield(task)
        except Exception:
            log.exception("Failed to save assistant message (session=%s)", turn.session["_id"])


class TurnRejected(Exception):
    def __init__(self, code: str, message: str, status_code: int):
        self.code, self.message, self.status_code = code, message, status_code


async def begin_turn(
    deps: ChatDeps, client_id: str, session_id: str | None, market_id: str | None, text: str
) -> ChatTurn:
    """Validate, load or create the session, save the user message and start the agent."""
    settings = deps.settings
    text = text.strip()
    if not text or len(text) > settings.max_message_chars:
        raise TurnRejected(
            "bad_request", f"Message must be between 1 and {settings.max_message_chars} characters.", 400
        )
    if market_id is not None and not is_object_id(market_id):
        raise TurnRejected("bad_request", "market_id must be a 24-character hex id.", 400)

    if session_id:
        session = await deps.sessions.get_owned(session_id, client_id)
        if session is None:
            raise TurnRejected("bad_request", "Chat session not found.", 404)
    else:
        session = await deps.sessions.create(
            client_id=client_id,
            market_id=market_id,
            title=text,
            provider=settings.llm_provider,
            model=settings.llm_model,
        )

    # Room for this message and the reply, claimed atomically.
    if not await deps.sessions.reserve_messages(
        session["_id"], client_id, 2, settings.max_messages_per_session
    ):
        raise TurnRejected(
            "session_limit", "This chat is full. Start a new chat to keep going.", 409
        )

    viewing_market_id = market_id or session.get("market_id")

    # History first, then the new message, so it is not counted twice.
    history = await deps.messages.recent_for_model(session["_id"], settings.history_limit)
    await deps.messages.add(session_id=session["_id"], role="user", content=text)

    model_messages: list[BaseMessage] = [
        HumanMessage(doc["content"]) if doc["role"] == "user" else AIMessage(doc["content"]) for doc in history
    ]
    # Some providers require the conversation to open with a user turn.
    while model_messages and isinstance(model_messages[0], AIMessage):
        model_messages.pop(0)
    model_messages.append(HumanMessage(build_turn_message(text, viewing_market_id, utcnow())))

    turn = ChatTurn(deps, session, viewing_market_id)
    turn.start(model_messages)
    return turn


@router.post("/chat/stream")
async def chat_stream(
    request: Request,
    body: ChatRequest,
    client_id: str = Depends(get_client_id),
    deps: ChatDeps = Depends(get_deps),
):
    try:
        turn = await begin_turn(deps, client_id, body.session_id, body.market_id, body.message)
    except TurnRejected as rejected:
        return sse_error_response(rejected.code, rejected.message, rejected.status_code)

    async def sse() -> AsyncIterator[str]:
        try:
            yield format_sse("meta", {"session_id": turn.session["_id"], "message_id": turn.message_id})
            async for event in turn.events(request.is_disconnected):
                if event is None:
                    yield PING
                elif event["type"] == "token":
                    yield format_sse("token", {"text": event["text"]})
                elif event["type"] == "status":
                    yield format_sse("status", {"label": event["label"]})
                elif event["type"] == "done":
                    yield format_sse("done", {"message_id": turn.message_id, "finish_reason": event["finish_reason"]})
                elif event["type"] == "error":
                    yield format_sse("error", {"code": event["code"], "message": event["message"]})
        finally:
            await finalize_shielded(turn)

    return StreamingResponse(sse(), media_type="text/event-stream", headers=SSE_HEADERS)


def _session_out(doc: dict[str, Any]) -> SessionOut:
    return SessionOut(
        session_id=doc["_id"],
        market_id=doc.get("market_id"),
        title=doc.get("title", ""),
        message_count=doc.get("message_count", 0),
        created_at=doc["created_at"],
        updated_at=doc["updated_at"],
    )


@router.get("/chat/sessions", response_model=list[SessionOut])
async def list_sessions(
    market_id: str | None = Query(default=None),
    client_id: str = Depends(get_client_id),
    deps: ChatDeps = Depends(get_deps),
):
    docs = await deps.sessions.list_for_client(client_id, market_id or None)
    return [_session_out(d) for d in docs]


@router.get("/chat/sessions/{session_id}/messages", response_model=MessagesOut)
async def get_messages(session_id: str, client_id: str = Depends(get_client_id), deps: ChatDeps = Depends(get_deps)):
    session = await deps.sessions.get_owned(session_id, client_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    docs = await deps.messages.list_for_display(session_id)
    return MessagesOut(
        session_id=session_id,
        market_id=session.get("market_id"),
        messages=[
            MessageOut(
                message_id=d["_id"],
                role=d["role"],
                content=d["content"],
                status=d.get("status", "complete"),
                created_at=d["created_at"],
            )
            for d in docs
        ],
    )


@router.delete("/chat/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str, client_id: str = Depends(get_client_id), deps: ChatDeps = Depends(get_deps)):
    if not await deps.sessions.delete(session_id, client_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return Response(status_code=204)
