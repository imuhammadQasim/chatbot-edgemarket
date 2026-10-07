"""Server-Sent Events framing."""

import json
from typing import Any

from fastapi.responses import StreamingResponse

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",  # nginx: do not buffer this response
    "Connection": "keep-alive",
}

PING = ": ping\n\n"


def format_sse(event: str, data: Any) -> str:
    """One SSE event. json.dumps never emits a raw newline, so `data` stays one line."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"


def sse_error_response(code: str, message: str, status_code: int) -> StreamingResponse:
    """A stream that carries a single `error` event, for failures before streaming starts.

    The body is SSE whatever the status, so the widget parses every response the
    same way; the status code is there for logs and proxies.
    """

    async def body():
        yield format_sse("error", {"code": code, "message": message})

    return StreamingResponse(body(), status_code=status_code, media_type="text/event-stream", headers=SSE_HEADERS)
