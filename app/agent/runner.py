"""Builds the tool-calling agent and turns its token stream into plain event dicts."""

import logging
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, ToolCallLimitMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessageChunk, BaseMessage, ToolMessage
from langchain_core.tools import BaseTool

from app.agent.prompts import build_system_prompt

log = logging.getLogger(__name__)

STATUS_LABELS = {
    "get_market_details": "Looking up the market…",
    "get_market_stats": "Checking live market stats…",
    "search_markets": "Searching markets…",
    "get_recent_activity": "Checking recent predictions…",
}

TOOL_ERROR_PREFIX = "ERROR:"


@dataclass
class TurnContext:
    """Per-turn runtime context, visible to tools and middleware."""

    viewing_market_id: str | None = None
    trace: list[dict[str, Any]] = field(default_factory=list)


class ToolTraceMiddleware(AgentMiddleware):
    """Records name, args, success and duration of every tool call into the turn context."""

    async def awrap_tool_call(self, request, handler):
        started = time.perf_counter()
        ok = False
        try:
            result = await handler(request)
            ok = isinstance(result, ToolMessage) and result.status != "error" and not str(
                result.content
            ).startswith(TOOL_ERROR_PREFIX)
            return result
        finally:
            context = request.runtime.context
            if isinstance(context, TurnContext):
                context.trace.append(
                    {
                        "name": request.tool_call["name"],
                        "args": request.tool_call.get("args", {}),
                        "ok": ok,
                        "ms": round((time.perf_counter() - started) * 1000),
                    }
                )


def content_to_text(content: Any) -> str:
    """Visible text of a message chunk.

    OpenAI chat completions and Groq send a string. Gemini and the OpenAI
    Responses API send a list of parts, where only `text` parts are meant for
    the user (others are reasoning, tool calls or signatures).
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("type") in ("text", "output_text"):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


class ChatRunner:
    def __init__(self, model: BaseChatModel, tools: Sequence[BaseTool], max_tool_calls: int = 6):
        self.agent = create_agent(
            model,
            list(tools),
            system_prompt=build_system_prompt(),
            middleware=[ToolTraceMiddleware(), ToolCallLimitMiddleware(run_limit=max_tool_calls)],
            context_schema=TurnContext,
        )

    async def stream(self, messages: list[BaseMessage], context: TurnContext) -> AsyncIterator[dict[str, Any]]:
        """Yields {"type": "status"|"token"|"final", ...}. Raises on provider errors."""
        announced: set[Any] = set()
        usage = {"input_tokens": 0, "output_tokens": 0}
        has_usage = False
        finish_reason: str | None = None

        async for chunk, metadata in self.agent.astream(
            {"messages": messages},
            context=context,
            stream_mode="messages",
            config={"recursion_limit": 25},
        ):
            # Only the model node produces user-visible text; the tools node
            # streams ToolMessages, which must never reach the user.
            if metadata.get("langgraph_node") != "model" or not isinstance(chunk, AIMessageChunk):
                continue

            for call in chunk.tool_call_chunks or []:
                name = call.get("name")
                key = call.get("id") or (name, call.get("index"))
                if name and key not in announced:
                    announced.add(key)
                    yield {"type": "status", "label": STATUS_LABELS.get(name, "Working…")}

            text = content_to_text(chunk.content)
            if text:
                yield {"type": "token", "text": text}

            if chunk.usage_metadata:
                has_usage = True
                usage["input_tokens"] += chunk.usage_metadata.get("input_tokens", 0) or 0
                usage["output_tokens"] += chunk.usage_metadata.get("output_tokens", 0) or 0

            reason = (chunk.response_metadata or {}).get("finish_reason")
            if reason:
                finish_reason = str(reason).lower()

        yield {"type": "final", "finish_reason": finish_reason or "stop", "usage": usage if has_usage else None}
